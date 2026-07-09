"""Incremental + bulk maintenance for the daily rollup tables.

`DailyListeningStats` and `DailyTrackStats` are derived, denormalized views
over `streams` that let the dashboard/detail queries avoid scanning every raw
play event. This module is the single place that knows how a `Stream` row
maps onto rollup deltas, used by both:

- the incremental path: a SQLAlchemy `before_flush` session event (registered
  below) that fires for *every* newly-added `Stream` row in any session,
  regardless of call site (playback sync, historical import, tests), and
  folds the rollup upserts into the same flush/transaction — so raw and
  rollup writes commit or roll back together.
- the bulk path: `rebuild_all_rollups`, a deterministic full rebuild from
  `streams` used by the backfill script and by anyone who needs to recover
  from drift (e.g. after a metadata enrichment pass changes Track.duration_ms
  for already-rolled-up plays — see module docstring caveat below).

Caveat: `listening_ms` is computed from `Track.duration_ms` *as of write
time*. If track metadata is enriched after a play is rolled up, that play's
contribution to `listening_ms` goes stale until `rebuild_all_rollups` reruns.
This mirrors the existing raw-query behavior (which also joins live
`Track.duration_ms`), so a rebuild is the equivalent of re-running the old
query — just not automatic on metadata change.

The `before_flush` session event that drives the incremental path is
registered in `app.models` (imported by every DB-touching module, so
registration is guaranteed regardless of call site) via a deferred import of
`apply_stream_to_rollups` to avoid a models<->services import cycle.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date as date_type

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from ..models import DailyListeningStats, DailyTrackStats, Stream, Track
from .timezone import utc_naive_to_display

NO_ALBUM_SENTINEL = ""
# Serializes incremental rollup maintenance and full rebuilds in PostgreSQL.
# Keep this value in sync with the Alembic migration's backfill lock.
ROLLUP_ADVISORY_LOCK_ID = 7_315_902_024


def local_date_for(played_at) -> date_type:
    """Convert a naive-UTC played_at timestamp to its Toronto-local calendar date."""
    return utc_naive_to_display(played_at).date()


def track_key_for(track_id: str | None, track_name: str) -> str:
    return track_id if track_id else f"name:{track_name}"


def artist_key_for(artist_id: str | None, artist_name: str) -> str:
    return artist_id if artist_id else f"name:{artist_name}"


def album_key_for(album_name: str | None) -> str:
    return album_name if album_name else NO_ALBUM_SENTINEL


@dataclass(frozen=True)
class _RollupKeys:
    date: date_type
    track_key: str
    artist_key: str
    album_key: str


def _keys_for_stream(stream: Stream) -> _RollupKeys:
    return _RollupKeys(
        date=local_date_for(stream.played_at),
        track_key=track_key_for(stream.track_id, stream.track_name),
        artist_key=artist_key_for(stream.artist_id, stream.artist_name),
        album_key=album_key_for(stream.album_name),
    )


def _acquire_rollup_write_lock(session: Session) -> None:
    """Serialize rollup writers/rebuilds on PostgreSQL.

    The lock is transaction-scoped, so it is released automatically on
    commit or rollback. SQLite tests are single-connection and need no
    database-specific lock.
    """
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        session.execute(select(func.pg_advisory_xact_lock(ROLLUP_ADVISORY_LOCK_ID)))


def _track_duration_ms(
    session: Session,
    track_id: str | None,
    cache: dict[str | None, int] | None = None,
) -> int:
    """Look up the current known Track.duration_ms for a play.

    Mirrors the historical raw-query semantics: unresolved track_id (missing
    or no matching Track row, or a Track with no duration on file) counts as
    zero — never falls back to Stream.duration_ms.
    """
    if cache is not None and track_id in cache:
        return cache[track_id]
    if not track_id:
        return 0
    with session.no_autoflush:
        duration = session.execute(
            select(Track.duration_ms).where(Track.spotify_id == track_id)
        ).scalar_one_or_none()
    result = duration or 0
    if cache is not None:
        cache[track_id] = result
    return result


def _get_or_create_daily(
    session: Session,
    date_: date_type,
    cache: dict[date_type, DailyListeningStats],
) -> DailyListeningStats:
    """Look up (or create) the DailyListeningStats row for a date.

    Cached per-flush-batch: within a single `before_flush` call, multiple new
    Stream rows can map to the same date, and a row created for an earlier
    stream in the batch is only *pending* (not yet flushed), so a plain
    SELECT wouldn't see it yet — the cache avoids issuing a second INSERT for
    the same date and hitting the unique constraint.
    """
    if date_ in cache:
        return cache[date_]
    with session.no_autoflush:
        daily = session.execute(
            select(DailyListeningStats).where(DailyListeningStats.date == date_)
        ).scalar_one_or_none()
    if daily is None:
        daily = DailyListeningStats(date=date_, play_count=0, listening_ms=0)
        session.add(daily)
    cache[date_] = daily
    return daily


def _get_or_create_track_row(
    session: Session,
    keys: _RollupKeys,
    stream: Stream,
    cache: dict[tuple, DailyTrackStats],
) -> DailyTrackStats:
    """Look up (or create) the DailyTrackStats row for a rollup key.

    Same per-flush-batch caching rationale as `_get_or_create_daily`.
    """
    cache_key = (keys.date, keys.track_key, keys.artist_key, keys.album_key)
    if cache_key in cache:
        track_row = cache[cache_key]
        # Last-seen-wins for display/filter columns within the batch too.
        track_row.track_id = track_row.track_id or stream.track_id
        track_row.artist_id = track_row.artist_id or stream.artist_id
        track_row.track_name = stream.track_name
        track_row.artist_name = stream.artist_name
        track_row.album_name = track_row.album_name or stream.album_name
        return track_row

    with session.no_autoflush:
        track_row = session.execute(
            select(DailyTrackStats).where(
                DailyTrackStats.date == keys.date,
                DailyTrackStats.track_key == keys.track_key,
                DailyTrackStats.artist_key == keys.artist_key,
                DailyTrackStats.album_key == keys.album_key,
            )
        ).scalar_one_or_none()
    if track_row is None:
        track_row = DailyTrackStats(
            date=keys.date,
            track_key=keys.track_key,
            artist_key=keys.artist_key,
            album_key=keys.album_key,
            track_id=stream.track_id,
            artist_id=stream.artist_id,
            track_name=stream.track_name,
            artist_name=stream.artist_name,
            album_name=stream.album_name,
            play_count=0,
            listening_ms=0,
        )
        session.add(track_row)
    else:
        # Last-seen-wins for display/filter columns, in case metadata was
        # missing on an earlier play and is now populated.
        track_row.track_id = track_row.track_id or stream.track_id
        track_row.artist_id = track_row.artist_id or stream.artist_id
        track_row.track_name = stream.track_name
        track_row.artist_name = stream.artist_name
        track_row.album_name = track_row.album_name or stream.album_name
    cache[cache_key] = track_row
    return track_row


def apply_stream_to_rollups(
    session: Session,
    stream: Stream,
    *,
    daily_cache: dict[date_type, DailyListeningStats] | None = None,
    track_cache: dict[tuple, DailyTrackStats] | None = None,
    duration_cache: dict[str | None, int] | None = None,
) -> None:
    """Read-modify-write the rollup rows for one newly-added Stream.

    `daily_cache`/`track_cache` should be shared across every stream in the
    same flush batch (see `maintain_rollups_before_flush`) so streams sharing
    a rollup key accumulate onto the same pending ORM object instead of each
    issuing a competing INSERT. Not safe to call twice for the *same* stream
    (would double count).
    """
    if daily_cache is None:
        daily_cache = {}
    if track_cache is None:
        track_cache = {}

    keys = _keys_for_stream(stream)
    duration_ms = _track_duration_ms(session, stream.track_id, duration_cache)

    daily = _get_or_create_daily(session, keys.date, daily_cache)
    daily.play_count += 1
    daily.listening_ms += duration_ms

    track_row = _get_or_create_track_row(session, keys, stream, track_cache)
    track_row.play_count += 1
    track_row.listening_ms += duration_ms


def maintain_rollups_before_flush(session: Session) -> None:
    """Fold rollup upserts for newly-added Stream rows into the current flush.

    Called from a `before_flush` event registered on the base ORM Session
    class in `app.models` (see that module), so it fires for every
    sessionmaker-derived session (app runtime and tests alike) — any code
    path that does `session.add(Stream(...))` gets rollups maintained in the
    same transaction, with no per-call-site wiring required.
    """
    new_streams = [obj for obj in session.new if isinstance(obj, Stream)]
    if not new_streams:
        return

    # The following SELECT + mutate/INSERT sequence is safe only when writers
    # sharing a rollup key cannot overlap. A transaction-scoped advisory lock
    # provides that guarantee for both existing and not-yet-created rows.
    _acquire_rollup_write_lock(session)

    daily_cache: dict[date_type, DailyListeningStats] = {}
    track_cache: dict[tuple, DailyTrackStats] = {}
    duration_cache: dict[str | None, int] = {}
    for stream in new_streams:
        apply_stream_to_rollups(
            session,
            stream,
            daily_cache=daily_cache,
            track_cache=track_cache,
            duration_cache=duration_cache,
        )


# --- Bulk rebuild -----------------------------------------------------------


@dataclass(frozen=True)
class RollupRebuildStats:
    daily_rows: int
    track_rows: int
    streams_processed: int


def _iter_stream_rollup_inputs() -> Select:
    """Select the columns needed to compute rollup deltas for every stream.

    Ordered by Stream.id (insertion order) so "last-seen-wins" display-column
    resolution is deterministic and reruns are idempotent/reproducible.
    """
    return (
        select(
            Stream.played_at,
            Stream.track_id,
            Stream.artist_id,
            Stream.track_name,
            Stream.artist_name,
            Stream.album_name,
            Track.duration_ms,
        )
        .outerjoin(Track, Track.spotify_id == Stream.track_id)
        .order_by(Stream.id)
    )


def rebuild_all_rollups(session: Session) -> RollupRebuildStats:
    """Deterministically rebuild both rollup tables from `streams`.

    Wholesale delete + recompute, in one transaction (caller commits). Uses
    the same key-derivation helpers as the incremental path so the two can
    never disagree on grouping. Idempotent: rerunning against unchanged
    `streams` data produces byte-identical rollup rows.
    """
    # Block incremental writers before taking the Stream snapshot and hold
    # the lock through the caller's commit. Otherwise a concurrent increment
    # could be erased by the delete/reinsert phase below.
    _acquire_rollup_write_lock(session)

    daily_totals: dict[date_type, list[int]] = defaultdict(lambda: [0, 0])  # [play_count, ms]
    track_totals: dict[tuple, dict] = {}

    streams_processed = 0
    rows = session.execute(_iter_stream_rollup_inputs()).all()
    for row in rows:
        streams_processed += 1
        played_at, track_id, artist_id, track_name, artist_name, album_name, duration_ms = row
        duration_ms = duration_ms or 0

        d = local_date_for(played_at)
        tk = track_key_for(track_id, track_name)
        ak = artist_key_for(artist_id, artist_name)
        albk = album_key_for(album_name)

        daily_entry = daily_totals[d]
        daily_entry[0] += 1
        daily_entry[1] += duration_ms

        key = (d, tk, ak, albk)
        entry = track_totals.get(key)
        if entry is None:
            entry = {
                "date": d,
                "track_key": tk,
                "artist_key": ak,
                "album_key": albk,
                "track_id": track_id,
                "artist_id": artist_id,
                "track_name": track_name,
                "artist_name": artist_name,
                "album_name": album_name,
                "play_count": 0,
                "listening_ms": 0,
            }
            track_totals[key] = entry
        else:
            entry["track_id"] = entry["track_id"] or track_id
            entry["artist_id"] = entry["artist_id"] or artist_id
            entry["track_name"] = track_name
            entry["artist_name"] = artist_name
            entry["album_name"] = entry["album_name"] or album_name
        entry["play_count"] += 1
        entry["listening_ms"] += duration_ms

    session.execute(delete(DailyTrackStats))
    session.execute(delete(DailyListeningStats))

    if daily_totals:
        session.add_all(
            DailyListeningStats(date=d, play_count=counts[0], listening_ms=counts[1])
            for d, counts in daily_totals.items()
        )
    if track_totals:
        session.add_all(DailyTrackStats(**entry) for entry in track_totals.values())

    return RollupRebuildStats(
        daily_rows=len(daily_totals),
        track_rows=len(track_totals),
        streams_processed=streams_processed,
    )
