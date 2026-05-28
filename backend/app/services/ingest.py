from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Stream

FUZZY_WINDOW = timedelta(seconds=30)


@dataclass(frozen=True)
class StreamingRecord:
    track_name: str
    artist_name: str
    album_name: str | None
    played_at: datetime
    duration_ms: int
    track_id: str
    artist_id: str


@dataclass(frozen=True)
class ExistingStreamIndex:
    by_track_id: dict[str, list[datetime]]
    by_name: set[tuple[str, str, datetime]]


@dataclass(frozen=True)
class ImportClassification:
    to_add: list[StreamingRecord]
    dup_exact: int
    dup_fuzzy: int
    dup_name: int


def parse_spotify_date(date_string: str) -> datetime:
    """Parse Spotify export timestamp formats."""
    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_string, fmt)
        except ValueError:
            continue

    raise ValueError(f"Unable to parse date: {date_string}")


def is_skipped_item(item: dict[str, Any]) -> bool:
    """Return True if the export record indicates the track was not completed."""
    reason_end = item.get("reason_end")
    if reason_end and reason_end != "trackdone":
        return True

    keys_to_check = ("skipped", "is_skipped", "skipped_flag", "skipped?")
    for key in keys_to_check:
        if key not in item:
            continue

        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                return True
        elif isinstance(value, int | float):
            if value != 0:
                return True
        elif isinstance(value, str) and value.strip().lower() in ("1", "true", "yes"):
            return True

    return False


def parse_streaming_records(
    data: list[dict[str, Any]],
    *,
    require_names: bool = False,
) -> tuple[list[StreamingRecord], int]:
    """Normalize Spotify export rows and count skipped or invalid records."""
    records: list[StreamingRecord] = []
    skipped_count = 0

    for item in data:
        if is_skipped_item(item):
            skipped_count += 1
            continue

        timestamp = item.get("ts") or item.get("endTime") or item.get("timestamp")
        if not timestamp:
            skipped_count += 1
            continue

        try:
            played_at = parse_spotify_date(timestamp)
        except ValueError:
            skipped_count += 1
            continue

        track_name = (
            item.get("master_metadata_track_name")
            or item.get("trackName")
            or item.get("track_name")
        )
        artist_name = (
            item.get("master_metadata_album_artist_name")
            or item.get("artistName")
            or item.get("artist_name")
        )

        if require_names and (not track_name or not artist_name):
            skipped_count += 1
            continue

        album_name = (
            item.get("master_metadata_album_album_name")
            or item.get("albumName")
            or item.get("album_name")
        )
        duration_ms = item.get("ms_played") or item.get("msPlayed") or item.get("duration_ms") or 0

        track_uri = item.get("spotify_track_uri", "")
        track_id = track_uri.replace("spotify:track:", "") if track_uri else ""

        artist_uri = item.get("spotify_artist_uri", "")
        artist_id = artist_uri.replace("spotify:artist:", "") if artist_uri else ""

        records.append(
            StreamingRecord(
                track_name=track_name or "Unknown",
                artist_name=artist_name or "Unknown",
                album_name=album_name,
                played_at=played_at,
                duration_ms=duration_ms,
                track_id=track_id,
                artist_id=artist_id,
            )
        )

    return records, skipped_count


def load_existing_streams(
    db: Session,
    date_min: datetime,
    date_max: datetime,
    *,
    fuzzy_window: timedelta = FUZZY_WINDOW,
) -> ExistingStreamIndex:
    """Load existing streams in a date range, indexed for duplicate checks."""
    rows = db.execute(
        select(Stream.track_id, Stream.track_name, Stream.artist_name, Stream.played_at).where(
            Stream.played_at >= date_min - fuzzy_window,
            Stream.played_at <= date_max + fuzzy_window,
        )
    ).all()

    by_track_id: dict[str, list[datetime]] = {}
    by_name: set[tuple[str, str, datetime]] = set()

    for row in rows:
        if row.track_id:
            by_track_id.setdefault(row.track_id, []).append(row.played_at)
        by_name.add((row.track_name, row.artist_name, row.played_at))

    for times in by_track_id.values():
        times.sort()

    return ExistingStreamIndex(by_track_id=by_track_id, by_name=by_name)


def has_fuzzy_match(
    track_id: str,
    played_at: datetime,
    index: ExistingStreamIndex,
    *,
    fuzzy_window: timedelta = FUZZY_WINDOW,
) -> bool:
    """Check if an existing stream has the same track ID within the fuzzy window."""
    times = index.by_track_id.get(track_id)
    if not times:
        return False

    return any(
        abs((played_at - time).total_seconds()) <= fuzzy_window.total_seconds() for time in times
    )


def classify_new_records(
    records: list[StreamingRecord],
    index: ExistingStreamIndex,
    *,
    fuzzy: bool = True,
    fuzzy_window: timedelta = FUZZY_WINDOW,
) -> ImportClassification:
    """Split parsed records into insertable rows and duplicate counts."""
    to_add: list[StreamingRecord] = []
    dup_exact = 0
    dup_fuzzy = 0
    dup_name = 0

    for record in records:
        if record.track_id:
            times = index.by_track_id.get(record.track_id, [])
            if record.played_at in times:
                dup_exact += 1
                continue
            if fuzzy and has_fuzzy_match(
                record.track_id,
                record.played_at,
                index,
                fuzzy_window=fuzzy_window,
            ):
                dup_fuzzy += 1
                continue
        elif (record.track_name, record.artist_name, record.played_at) in index.by_name:
            dup_name += 1
            continue

        to_add.append(record)

    return ImportClassification(
        to_add=to_add,
        dup_exact=dup_exact,
        dup_fuzzy=dup_fuzzy,
        dup_name=dup_name,
    )


def stream_from_record(record: StreamingRecord) -> Stream:
    return Stream(
        track_name=record.track_name,
        artist_name=record.artist_name,
        album_name=record.album_name,
        played_at=record.played_at,
        duration_ms=record.duration_ms,
        track_id=record.track_id,
        artist_id=record.artist_id,
    )
