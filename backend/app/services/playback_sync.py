from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Artist, Stream, Track
from app.services.dashboard_cache import dashboard_cache
from app.services.ingest import (
    StreamingRecord,
    classify_new_records,
    load_existing_streams,
    parse_spotify_date,
)
from app.services.ingest_errors import IssueReporter, spotify_api_issue

ArtistDetailsFetcher = Callable[[str], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class StoreRecentPlaysResult:
    inserted: int
    duplicates: int
    new_tracks: int
    new_artists: int


async def store_recently_played(
    db: Session,
    items: list[dict[str, Any]],
    *,
    fetch_artist_details: ArtistDetailsFetcher,
    report_issue: IssueReporter | None = None,
) -> StoreRecentPlaysResult:
    """Persist plays from GET /me/player/recently-played not stored yet.

    Spotify only lists plays that ran to completion -- skips are left out --
    and `played_at` is when the play ended, the same convention as `ts` in
    the history export. So entries are stored as-is. Anything already
    present (an earlier run, a history import, a copy from the standby, or a
    row from the old live poller, stamped a few seconds before the end) is
    caught by the same fuzzy 30s window historical import uses.
    """
    plays = [
        (parse_spotify_date(item["played_at"]), item["track"])
        for item in items
        if item.get("track")
        and item["track"].get("id")
        and item["track"].get("type", "track") == "track"
    ]
    if not plays:
        return StoreRecentPlaysResult(inserted=0, duplicates=0, new_tracks=0, new_artists=0)

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
        for played_at, track in plays
    ]
    existing = load_existing_streams(
        db,
        min(played_at for played_at, _ in plays),
        max(played_at for played_at, _ in plays),
    )
    classified = classify_new_records(records, existing, fuzzy=True)
    new_keys = {(record.track_id, record.played_at) for record in classified.to_add}

    new_tracks = 0
    new_artists = 0
    for played_at, track in sorted(plays, key=lambda play: play[0]):
        if (track["id"], played_at) not in new_keys:
            continue
        new_tracks += _ensure_track(db, track, played_at)
        new_artists += await _ensure_artists(
            db,
            track,
            fetch_artist_details=fetch_artist_details,
            report_issue=report_issue,
        )
        db.add(_stream_from_playback(track, played_at))

    # db.commit() flushes the new Stream rows and, via the before_flush hook
    # registered in app.models, the matching daily_listening_stats /
    # daily_track_stats rollup rows in the same transaction. If rollup
    # maintenance raises, the flush fails and nothing here is persisted —
    # roll back explicitly so a half-applied attempt doesn't linger on the
    # session for the next request that reuses it.
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    if new_keys:
        dashboard_cache.clear()

    return StoreRecentPlaysResult(
        inserted=len(new_keys),
        duplicates=len(plays) - len(new_keys),
        new_tracks=new_tracks,
        new_artists=new_artists,
    )


def _ensure_track(db: Session, track: dict[str, Any], first_played_at: datetime) -> int:
    track_record = db.execute(
        select(Track).where(Track.spotify_id == track["id"])
    ).scalar_one_or_none()
    if track_record:
        return 0

    album_image = None
    if track.get("album") and track["album"].get("images"):
        album_image = track["album"]["images"][0]["url"]

    db.add(
        Track(
            spotify_id=track["id"],
            name=track["name"],
            artist_name=track["artists"][0]["name"] if track.get("artists") else None,
            album_name=track.get("album", {}).get("name"),
            album_image=album_image,
            duration_ms=track.get("duration_ms"),
            popularity=track.get("popularity"),
            first_played_at=first_played_at,
        )
    )
    db.flush()
    return 1


async def _ensure_artists(
    db: Session,
    track: dict[str, Any],
    *,
    fetch_artist_details: ArtistDetailsFetcher,
    report_issue: IssueReporter | None,
) -> int:
    new_artists = 0
    for artist_data in track.get("artists") or []:
        artist_record = db.execute(
            select(Artist).where(Artist.spotify_id == artist_data["id"])
        ).scalar_one_or_none()
        if artist_record:
            continue

        try:
            artist_full = await fetch_artist_details(artist_data["id"])
        except Exception as exc:
            issue = spotify_api_issue(
                operation="playback_artist_metadata",
                message=f"Error fetching artist {artist_data['id']}: {exc}",
                exc=exc,
                identifiers=[artist_data["id"]],
            )
            if report_issue:
                report_issue(issue)
            continue

        if not artist_full:
            continue

        image_url = None
        if artist_full.get("images"):
            image_url = artist_full["images"][0]["url"]

        db.add(
            Artist(
                spotify_id=artist_data["id"],
                name=artist_data["name"],
                genres=artist_full.get("genres", []),
                popularity=artist_full.get("popularity"),
                image_url=image_url,
            )
        )
        db.flush()
        new_artists += 1

    return new_artists


def _stream_from_playback(track: dict[str, Any], played_at: datetime) -> Stream:
    return Stream(
        track_name=track["name"],
        artist_name=track["artists"][0]["name"] if track.get("artists") else "Unknown",
        album_name=track.get("album", {}).get("name"),
        played_at=played_at,
        duration_ms=track.get("duration_ms"),
        track_id=track["id"],
        artist_id=track["artists"][0]["id"] if track.get("artists") else None,
    )
