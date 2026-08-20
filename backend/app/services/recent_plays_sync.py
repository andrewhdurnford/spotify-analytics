"""Reconciliation sync: derive completed plays from Spotify's recently-played
history instead of trying to catch live playback progress in a narrow window
(see app.tasks.sync_recent_tracks for what this replaces).

Core assumption this module depends on: a recently-played item's `played_at`
reflects roughly when Spotify considered that track's play *finished* --
the same end-of-play convention as `ts` in the Extended Streaming History
export that historical import already uses (app.services.ingest). Under
that assumption, the gap between one item's `played_at` and the *next*
item's `played_at` approximates how long the *later* item was actually
played before the user moved on, which is what lets us compute our own
played-ratio and reject likely skips instead of trusting whatever bar
Spotify used to include the item in this endpoint at all.

If that assumption is backwards (played_at is actually start-of-play), the
ratio credited to each item is measured against the wrong neighbor. Worth
confirming empirically before relying on this for anything beyond the
dashboard: skip a track partway through, run a sync, and check the stored
`played_ratio` lands where expected.

Because every item's ratio only depends on its own played_at and the
*previous* item's played_at (the sync cursor, or the prior item in the same
batch), every item in a fetched batch can be resolved in the same run --
there's no unresolved "last item" to carry over to the next poll the way
there would be under a start-of-play interpretation.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models import RecentPlaysSyncMeta, Stream
from .dashboard_cache import dashboard_cache
from .ingest import StreamingRecord, classify_new_records, load_existing_streams
from .ingest_errors import IssueReporter
from .playback_sync import ArtistDetailsFetcher, Warn, ensure_artists, ensure_track

logger = logging.getLogger(__name__)

# How much of a track must have played (per the delta-derived estimate
# below) to count as a real play rather than a skip. Deliberately looser
# than the old live-progress threshold (0.98) was on paper, but the two
# aren't measuring the same thing -- this is intentionally the number to
# tune if it over/under-counts in practice.
PLAY_RATIO_THRESHOLD = 0.9

_META_ROW_ID = 1


@dataclass(frozen=True)
class RecentPlaysSyncResult:
    inserted: int
    rejected_ratio: int
    duplicates: int


def _get_meta(db: Session) -> RecentPlaysSyncMeta:
    meta = db.get(RecentPlaysSyncMeta, _META_ROW_ID)
    if meta is None:
        meta = RecentPlaysSyncMeta(id=_META_ROW_ID)
        db.add(meta)
        db.flush()
    return meta


def _parse_played_at(value: str) -> datetime:
    # Spotify's `played_at` is ISO-8601 with a trailing "Z", e.g.
    # "2026-08-16T23:10:04.175Z". Stored/compared as naive UTC, matching
    # every other `played_at` in this codebase (see app.services.ingest).
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def cursor_epoch_ms(db: Session) -> int | None:
    """The stored cursor as a Spotify `after` epoch-ms value, or None if the
    sync has never run. Callers should seed the cursor (`seed_cursor_to_now`)
    rather than fetch with `after=None` on a cold start -- that would pull
    Spotify's full 50-item backlog, including plays historical import may
    already have covered.
    """
    meta = _get_meta(db)
    if meta.cursor_played_at is None:
        return None
    return int(meta.cursor_played_at.replace(tzinfo=UTC).timestamp() * 1000)


def seed_cursor_to_now(db: Session) -> None:
    meta = _get_meta(db)
    meta.cursor_played_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()


def get_last_synced_at(db: Session) -> datetime | None:
    meta = db.get(RecentPlaysSyncMeta, _META_ROW_ID)
    return meta.last_synced_at if meta else None


async def process_recently_played_items(
    db: Session,
    items: list[dict[str, Any]],
    *,
    fetch_artist_details: ArtistDetailsFetcher,
    warn: Warn | None = None,
    report_issue: IssueReporter | None = None,
) -> RecentPlaysSyncResult:
    """Resolve play ratios for a batch of recently-played items and store the
    ones that clear PLAY_RATIO_THRESHOLD.

    `items` must be exactly what the Spotify API returned for one or more
    pages fetched with `after=<cursor_epoch_ms(db)>` -- the caller owns
    fetching and pagination; this only processes and stores.
    """
    meta = _get_meta(db)

    parsed = sorted(
        ((_parse_played_at(item["played_at"]), item["track"]) for item in items),
        key=lambda pair: pair[0],
    )
    if not parsed:
        return RecentPlaysSyncResult(inserted=0, rejected_ratio=0, duplicates=0)

    prev_played_at = meta.cursor_played_at
    candidates: list[tuple[datetime, dict[str, Any], float]] = []
    rejected_ratio = 0

    for played_at, track in parsed:
        # Podcast episodes etc. still mark "something ended here" for the
        # next real track's gap calculation, but were never a play we'd
        # store -- same filter the old live-progress poller applied.
        if track.get("type") and track["type"] != "track":
            prev_played_at = played_at
            continue

        duration_ms = track.get("duration_ms")
        if prev_played_at is None or not duration_ms:
            # No known predecessor (very first item ever processed) or no
            # duration to compare against -- can't compute a ratio, so
            # don't guess either way.
            prev_played_at = played_at
            continue

        gap_ms = max(0, int((played_at - prev_played_at).total_seconds() * 1000))
        ratio = min(gap_ms, duration_ms) / duration_ms

        if ratio >= PLAY_RATIO_THRESHOLD:
            candidates.append((played_at, track, ratio))
        else:
            rejected_ratio += 1

        prev_played_at = played_at

    newest_played_at = parsed[-1][0]

    if not candidates:
        meta.cursor_played_at = newest_played_at
        meta.last_synced_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
        return RecentPlaysSyncResult(inserted=0, rejected_ratio=rejected_ratio, duplicates=0)

    date_min = min(played_at for played_at, _, _ in candidates)
    date_max = max(played_at for played_at, _, _ in candidates)
    existing = load_existing_streams(db, date_min, date_max)

    records = [
        StreamingRecord(
            track_name=track["name"],
            artist_name=track["artists"][0]["name"] if track.get("artists") else "Unknown",
            album_name=track.get("album", {}).get("name"),
            played_at=played_at,
            duration_ms=track.get("duration_ms") or 0,
            track_id=track["id"],
            artist_id=track["artists"][0]["id"] if track.get("artists") else "",
        )
        for played_at, track, _ in candidates
    ]
    # Same fuzzy 30s dedup window historical import uses, so a play synced
    # here and later re-covered by an export re-import (or vice versa)
    # doesn't double-count -- both paths write the same `streams` table.
    classified = classify_new_records(records, existing, fuzzy=True)
    duplicates = classified.dup_exact + classified.dup_fuzzy + classified.dup_name
    to_add_keys = {(r.track_id, r.played_at) for r in classified.to_add}

    inserted = 0
    for played_at, track, ratio in candidates:
        if (track["id"], played_at) not in to_add_keys:
            continue

        ensure_track(db, track, played_at)
        await ensure_artists(
            db,
            track,
            fetch_artist_details=fetch_artist_details,
            warn=warn,
            report_issue=report_issue,
        )
        db.add(
            Stream(
                track_name=track["name"],
                artist_name=track["artists"][0]["name"] if track.get("artists") else "Unknown",
                album_name=track.get("album", {}).get("name"),
                played_at=played_at,
                duration_ms=track.get("duration_ms"),
                track_id=track["id"],
                artist_id=track["artists"][0]["id"] if track.get("artists") else None,
                played_ratio=ratio,
            )
        )
        inserted += 1

    meta.cursor_played_at = newest_played_at
    meta.last_synced_at = datetime.now(UTC).replace(tzinfo=None)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    if inserted:
        dashboard_cache.clear()

    return RecentPlaysSyncResult(
        inserted=inserted,
        rejected_ratio=rejected_ratio,
        duplicates=duplicates,
    )
