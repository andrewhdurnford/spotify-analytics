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

sys.path.append("/app")

from app.database import SessionLocal
from app.services.ingest import (
    FUZZY_WINDOW,
    classify_new_records,
    load_existing_streams,
    parse_streaming_records,
    stream_from_record,
)


def import_file(file_path: str, commit: bool = False):
    print(f"Processing: {file_path}")
    print(f"Mode: {'COMMIT (will write to DB)' if commit else 'DRY RUN (preview only)'}")
    print()

    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("Error: file does not contain a JSON array")
        return

    print(f"Total records in file: {len(data)}")

    records, skipped_count = parse_streaming_records(data, require_names=True)
    print(f"Valid records (after filtering skipped/invalid): {len(records)}")
    print(f"Filtered out: {skipped_count}")
    print()

    if not records:
        print("No valid records to import.")
        return

    # Determine date range and load existing streams
    date_min = min(record.played_at for record in records)
    date_max = max(record.played_at for record in records)
    print(f"Date range: {date_min} to {date_max}")

    db = SessionLocal()
    try:
        print("Loading existing streams from DB for fuzzy matching...")
        existing_streams = load_existing_streams(db, date_min, date_max)
        print(
            f"Loaded {sum(len(v) for v in existing_streams.by_track_id.values())} "
            f"streams by track_id, {len(existing_streams.by_name)} by name"
        )
        print()

        classified = classify_new_records(records, existing_streams)
        to_add = classified.to_add

        print("=== RESULTS ===")
        print(f"Exact duplicates (track_id + timestamp): {classified.dup_exact}")
        print(
            f"Fuzzy duplicates (track_id within "
            f"\u00b1{int(FUZZY_WINDOW.total_seconds())}s): {classified.dup_fuzzy}"
        )
        print(f"Name-based duplicates: {classified.dup_name}")
        print(f"Records to add: {len(to_add)}")
        print()

        if to_add:
            month_counts = Counter(record.played_at.strftime("%Y-%m") for record in to_add)
            print("Distribution by month:")
            for month in sorted(month_counts):
                print(f"  {month}: {month_counts[month]}")
            print()

            print("--- First 20 records to add ---")
            for i, record in enumerate(to_add[:20]):
                dur = round(record.duration_ms / 1000) if record.duration_ms else 0
                print(
                    f"  {i + 1:4d}. {record.played_at}  "
                    f"{record.artist_name[:30]:30s}  "
                    f"{record.track_name[:40]:40s}  "
                    f"{dur:>4d}s"
                )

            if len(to_add) > 30:
                print(f"  ... ({len(to_add) - 30} more) ...")

            if len(to_add) > 20:
                print("--- Last 10 records to add ---")
                for i, record in enumerate(to_add[-10:]):
                    dur = round(record.duration_ms / 1000) if record.duration_ms else 0
                    print(
                        f"  {len(to_add) - 9 + i:4d}. {record.played_at}  "
                        f"{record.artist_name[:30]:30s}  "
                        f"{record.track_name[:40]:40s}  "
                        f"{dur:>4d}s"
                    )
            print()

        if commit and to_add:
            print("Inserting records...")
            inserted = 0
            errors = 0
            for record in to_add:
                db.add(stream_from_record(record))
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
