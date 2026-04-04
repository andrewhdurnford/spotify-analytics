"""Import a single Spotify streaming history JSON file with fuzzy dedup.

The standard import_data.py checks for exact (track_id, played_at) matches,
but cron-synced records have millisecond-precision timestamps while JSON
exports have second-precision timestamps, causing the same play event to
be treated as two separate records.

This script uses a fuzzy timestamp match: a JSON record is considered a
duplicate if a DB record exists with the same track_id within ±30 seconds.

Usage (inside Docker container):
    # Preview what would be imported (no changes)
    python -m app.scripts.import_file /data/Streaming_History_Audio_2025-2026_23.json

    # Actually import
    python -m app.scripts.import_file /data/Streaming_History_Audio_2025-2026_23.json --commit
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select

sys.path.append("/app")

from app.database import SessionLocal
from app.models import Stream

FUZZY_WINDOW = timedelta(seconds=30)


def parse_spotify_date(date_string: str) -> datetime:
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


def is_skipped_item(item: dict) -> bool:
    for k in ("skipped", "is_skipped", "skipped_flag", "skipped?"):
        if k not in item:
            continue
        v = item.get(k)
        if v is None:
            continue
        if isinstance(v, bool) and v:
            return True
        if isinstance(v, (int, float)) and v != 0:
            return True
        if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes"):
            return True
    return False


def load_existing_streams(db, date_min: datetime, date_max: datetime):
    """Load all existing streams in the date range, indexed by track_id."""
    rows = db.execute(
        select(Stream.track_id, Stream.track_name, Stream.artist_name, Stream.played_at).where(
            Stream.played_at >= date_min - FUZZY_WINDOW,
            Stream.played_at <= date_max + FUZZY_WINDOW,
        )
    ).all()

    by_track_id: dict[str, list[datetime]] = {}
    by_name: set[tuple[str, str, datetime]] = set()

    for row in rows:
        if row.track_id:
            by_track_id.setdefault(row.track_id, []).append(row.played_at)
        by_name.add((row.track_name, row.artist_name, row.played_at))

    # Sort each list for efficient searching
    for times in by_track_id.values():
        times.sort()

    return by_track_id, by_name


def has_fuzzy_match(track_id: str, played_at: datetime, by_track_id: dict) -> bool:
    """Check if any existing stream has the same track_id within ±FUZZY_WINDOW."""
    times = by_track_id.get(track_id)
    if not times:
        return False
    for t in times:
        if abs((played_at - t).total_seconds()) <= FUZZY_WINDOW.total_seconds():
            return True
    return False


def parse_records(data: list[dict]) -> tuple[list[dict], int]:
    """Parse JSON records into normalized dicts, filtering skipped/invalid ones."""
    records = []
    skipped_count = 0

    for item in data:
        if is_skipped_item(item):
            skipped_count += 1
            continue

        timestamp = item.get("ts") or item.get("endTime") or item.get("timestamp")
        if not timestamp:
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

        if not track_name or not artist_name:
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

        try:
            played_at = parse_spotify_date(timestamp)
        except ValueError:
            skipped_count += 1
            continue

        records.append(
            {
                "track_name": track_name,
                "artist_name": artist_name,
                "album_name": album_name,
                "played_at": played_at,
                "duration_ms": duration_ms,
                "track_id": track_id,
                "artist_id": artist_id,
            }
        )

    return records, skipped_count


def import_file(file_path: str, commit: bool = False):
    print(f"Processing: {file_path}")
    print(f"Mode: {'COMMIT (will write to DB)' if commit else 'DRY RUN (preview only)'}")
    print()

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("Error: file does not contain a JSON array")
        return

    print(f"Total records in file: {len(data)}")

    records, skipped_count = parse_records(data)
    print(f"Valid records (after filtering skipped/invalid): {len(records)}")
    print(f"Filtered out: {skipped_count}")
    print()

    if not records:
        print("No valid records to import.")
        return

    # Determine date range and load existing streams
    date_min = min(r["played_at"] for r in records)
    date_max = max(r["played_at"] for r in records)
    print(f"Date range: {date_min} to {date_max}")

    db = SessionLocal()
    try:
        print("Loading existing streams from DB for fuzzy matching...")
        by_track_id, by_name = load_existing_streams(db, date_min, date_max)
        print(
            f"Loaded {sum(len(v) for v in by_track_id.values())} streams by track_id, "
            f"{len(by_name)} by name"
        )
        print()

        # Classify records
        to_add = []
        dup_exact = 0
        dup_fuzzy = 0
        dup_name = 0

        for rec in records:
            if rec["track_id"]:
                # Check exact match first
                times = by_track_id.get(rec["track_id"], [])
                if rec["played_at"] in times:
                    dup_exact += 1
                    continue
                # Check fuzzy match
                if has_fuzzy_match(rec["track_id"], rec["played_at"], by_track_id):
                    dup_fuzzy += 1
                    continue
            else:
                # Fallback: exact name match
                if (rec["track_name"], rec["artist_name"], rec["played_at"]) in by_name:
                    dup_name += 1
                    continue

            to_add.append(rec)

        print("=== RESULTS ===")
        print(f"Exact duplicates (track_id + timestamp): {dup_exact}")
        print(
            f"Fuzzy duplicates (track_id within "
            f"\u00b1{int(FUZZY_WINDOW.total_seconds())}s): {dup_fuzzy}"
        )
        print(f"Name-based duplicates: {dup_name}")
        print(f"Records to add: {len(to_add)}")
        print()

        if to_add:
            month_counts = Counter(r["played_at"].strftime("%Y-%m") for r in to_add)
            print("Distribution by month:")
            for month in sorted(month_counts):
                print(f"  {month}: {month_counts[month]}")
            print()

            print("--- First 20 records to add ---")
            for i, r in enumerate(to_add[:20]):
                dur = round(r["duration_ms"] / 1000) if r["duration_ms"] else 0
                print(
                    f"  {i + 1:4d}. {r['played_at']}  "
                    f"{r['artist_name'][:30]:30s}  "
                    f"{r['track_name'][:40]:40s}  "
                    f"{dur:>4d}s"
                )

            if len(to_add) > 30:
                print(f"  ... ({len(to_add) - 30} more) ...")

            if len(to_add) > 20:
                print("--- Last 10 records to add ---")
                for i, r in enumerate(to_add[-10:]):
                    dur = round(r["duration_ms"] / 1000) if r["duration_ms"] else 0
                    print(
                        f"  {len(to_add) - 9 + i:4d}. {r['played_at']}  "
                        f"{r['artist_name'][:30]:30s}  "
                        f"{r['track_name'][:40]:40s}  "
                        f"{dur:>4d}s"
                    )
            print()

        if commit and to_add:
            print("Inserting records...")
            inserted = 0
            errors = 0
            for rec in to_add:
                stream = Stream(
                    track_name=rec["track_name"],
                    artist_name=rec["artist_name"],
                    album_name=rec["album_name"],
                    played_at=rec["played_at"],
                    duration_ms=rec["duration_ms"],
                    track_id=rec["track_id"],
                    artist_id=rec["artist_id"],
                )
                db.add(stream)
                try:
                    db.commit()
                    inserted += 1
                except Exception as e:
                    db.rollback()
                    errors += 1
                    if errors <= 5:
                        print(f"  Error inserting: {e}")

            print(f"Inserted: {inserted}, Errors: {errors}")
        elif not commit and to_add:
            print("Dry run complete. Use --commit to actually import these records.")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import a Spotify streaming history JSON file")
    parser.add_argument("file", help="Path to the JSON file")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually insert records (default is dry-run preview)",
    )
    args = parser.parse_args()

    import_file(args.file, commit=args.commit)
