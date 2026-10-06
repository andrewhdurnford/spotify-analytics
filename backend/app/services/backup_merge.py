"""Pull plays the standby database recorded while this one was offline.

The home server and the standby (Render + Supabase) each record playback
independently, so after a home-server outage the standby holds plays the home
database never saw. Both sides stamp `played_at` with their own clock at the
moment they noticed the track finishing, so the same play lands a few seconds
apart on each side -- matching is by track within a tolerance window, not by
exact timestamp.

The tolerance is half the track's duration (floored at MIN_MATCH_TOLERANCE):
two genuine plays of the same track are at least one full duration apart, so a
half-duration window can never merge distinct plays, while comfortably
covering the gap between the two pollers.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Artist, Stream, Track

MIN_MATCH_TOLERANCE = timedelta(seconds=90)
# Upper bound on any single stream's tolerance, used to widen the local query
# so plays near the window edge still find their counterpart.
MAX_MATCH_TOLERANCE = timedelta(minutes=30)

_TRACK_COLUMNS = [c.key for c in Track.__table__.columns if c.key not in ("id", "last_updated")]
_ARTIST_COLUMNS = [c.key for c in Artist.__table__.columns if c.key != "id"]
_STREAM_COLUMNS = [c.key for c in Stream.__table__.columns if c.key != "id"]


@dataclass(frozen=True)
class MergeStats:
    backup_streams: int
    matched: int
    inserted_streams: int
    inserted_tracks: int
    inserted_artists: int


def _match_key(stream: Stream) -> tuple:
    if stream.track_id:
        return ("id", stream.track_id)
    return ("name", stream.track_name, stream.artist_name)


def _tolerance(stream: Stream) -> timedelta:
    if not stream.duration_ms:
        return MIN_MATCH_TOLERANCE
    half = timedelta(milliseconds=stream.duration_ms / 2)
    return min(max(MIN_MATCH_TOLERANCE, half), MAX_MATCH_TOLERANCE)


def _copy(model, source, columns):
    return model(**{column: getattr(source, column) for column in columns})


def merge_missing_streams(local_db: Session, backup_db: Session, since: datetime) -> MergeStats:
    """Insert into `local_db` every stream in `backup_db` since `since` that
    has no counterpart locally. Adds the rows to the session without
    committing; the caller decides whether to commit. Daily rollups are kept
    in step by the Stream insert hook in app.models.
    """
    backup_streams = (
        backup_db.execute(
            select(Stream).where(Stream.played_at >= since).order_by(Stream.played_at)
        )
        .scalars()
        .all()
    )
    if not backup_streams:
        return MergeStats(0, 0, 0, 0, 0)

    local_streams = (
        local_db.execute(select(Stream).where(Stream.played_at >= since - MAX_MATCH_TOLERANCE))
        .scalars()
        .all()
    )
    unmatched_local: dict[tuple, list[Stream]] = {}
    for stream in local_streams:
        unmatched_local.setdefault(_match_key(stream), []).append(stream)

    missing: list[Stream] = []
    for stream in backup_streams:
        candidates = unmatched_local.get(_match_key(stream), [])
        tolerance = _tolerance(stream)
        best = min(
            (c for c in candidates if abs(c.played_at - stream.played_at) <= tolerance),
            key=lambda c: abs(c.played_at - stream.played_at),
            default=None,
        )
        if best is None:
            missing.append(stream)
        else:
            candidates.remove(best)

    inserted_tracks = _copy_missing(
        local_db, backup_db, Track, _TRACK_COLUMNS, {s.track_id for s in missing if s.track_id}
    )
    inserted_artists = _copy_missing(
        local_db, backup_db, Artist, _ARTIST_COLUMNS, {s.artist_id for s in missing if s.artist_id}
    )
    for stream in missing:
        local_db.add(_copy(Stream, stream, _STREAM_COLUMNS))
    local_db.flush()

    return MergeStats(
        backup_streams=len(backup_streams),
        matched=len(backup_streams) - len(missing),
        inserted_streams=len(missing),
        inserted_tracks=inserted_tracks,
        inserted_artists=inserted_artists,
    )


def _copy_missing(local_db: Session, backup_db: Session, model, columns, spotify_ids) -> int:
    """Copy `model` rows keyed by spotify_id that exist in backup but not locally."""
    if not spotify_ids:
        return 0
    existing = set(
        local_db.execute(
            select(model.spotify_id).where(model.spotify_id.in_(spotify_ids))
        ).scalars()
    )
    to_copy = spotify_ids - existing
    if not to_copy:
        return 0
    rows = backup_db.execute(select(model).where(model.spotify_id.in_(to_copy))).scalars().all()
    for row in rows:
        local_db.add(_copy(model, row, columns))
    local_db.flush()
    return len(rows)
