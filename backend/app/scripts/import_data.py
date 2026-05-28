import json
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.orm import Session

sys.path.append("/app")

from app.database import SessionLocal, engine
from app.models import Base, Stream
from app.services.ingest import (
    classify_new_records,
    load_existing_streams,
    parse_streaming_records,
    stream_from_record,
)
from app.services.spotify_metadata import (
    get_missing_artist_ids,
    get_missing_track_ids,
    get_track_ids_missing_stream_artist,
    upsert_artists_from_spotify,
    upsert_tracks_from_spotify,
)
from app.utils.spotify import fetch_spotify_token_from_client_credentials

# load .env from project root
load_dotenv()


def import_spotify_file(file_path: str, db: Session):
    """Import a single Spotify streaming history JSON file"""

    print(f"\nProcessing: {file_path}")

    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            print(f"Skipping {file_path} - not a list of records")
            return 0

        print(f"  Found {len(data)} records")

        imported = 0
        records, skipped = parse_streaming_records(data)

        if records:
            date_min = min(record.played_at for record in records)
            date_max = max(record.played_at for record in records)
            existing_streams = load_existing_streams(db, date_min, date_max)
            classified = classify_new_records(records, existing_streams, fuzzy=False)
            skipped += classified.dup_exact + classified.dup_name

            for record in classified.to_add:
                db.add(stream_from_record(record))

                # Commit each record individually to avoid batch failures
                try:
                    db.commit()
                    imported += 1
                except Exception as commit_error:
                    db.rollback()
                    skipped += 1
                    if skipped <= 5:
                        print(f"  Error committing record: {commit_error}")
                    continue

        print(f"  ✓ Imported {imported} records ({skipped} skipped)")
        return imported

    except Exception as e:
        print(f"  ✗ Error processing file: {e}")
        db.rollback()
        return 0


def import_all_spotify_data(data_dir: str = "/data"):
    """Import all JSON files from the data directory"""

    # Create tables
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    try:
        # Find all JSON files
        json_files = [f for f in os.listdir(data_dir) if f.endswith(".json")]

        if not json_files:
            print(f"No JSON files found in {data_dir}")
            return

        print(f"Found {len(json_files)} JSON file(s) to import:")
        for f in json_files:
            print(f"  - {f}")

        total_imported = 0

        for json_file in json_files:
            file_path = os.path.join(data_dir, json_file)
            imported = import_spotify_file(file_path, db)
            total_imported += imported

        print(f"\n{'=' * 60}")
        print("IMPORT COMPLETE")
        print(f"{'=' * 60}")
        print(f"Total records imported: {total_imported}")

        # Update tracks table with Spotify metadata
        spotify_token = os.getenv("SPOTIFY_TOKEN") or fetch_spotify_token_from_client_credentials()
        if spotify_token:
            to_fetch = get_missing_track_ids(db)
            print(f"Fetching metadata for {len(to_fetch)} tracks from Spotify...")
            upserted = upsert_tracks_from_spotify(
                db,
                spotify_token,
                to_fetch,
                warn=lambda message: print(f"  Warning: {message}"),
            )
            print(f"  Upserted/updated {upserted} tracks")

            to_fetch_artists = get_missing_artist_ids(db)
            print(f"Fetching metadata for {len(to_fetch_artists)} artists from Spotify...")
            artists_upserted = upsert_artists_from_spotify(
                db,
                spotify_token,
                to_fetch_artists,
                warn=lambda message: print(f"  Warning: {message}"),
            )
            print(f"  Upserted/updated {artists_upserted} artists")
        else:
            print(
                "No SPOTIFY_TOKEN and no client credentials available — skipping Spotify metadata enrichment"
            )

        db.commit()

        # Show some stats
        total = db.scalar(select(func.count()).select_from(Stream))
        print(f"Total records in database: {total}")

        if total > 0:
            earliest = db.execute(
                select(Stream).order_by(Stream.played_at.asc())
            ).scalar_one_or_none()
            latest = db.execute(
                select(Stream).order_by(Stream.played_at.desc())
            ).scalar_one_or_none()
            unique_tracks = db.scalar(select(func.count(Stream.track_name.distinct())))
            unique_artists = db.scalar(select(func.count(Stream.artist_name.distinct())))

            print("\nDatabase Stats:")
            print(f"  Date range: {earliest.played_at} to {latest.played_at}")
            print(f"  Unique tracks: {unique_tracks}")
            print(f"  Unique artists: {unique_artists}")

            print("Backfilling missing artist IDs from Spotify API...")
            track_ids_to_fetch = get_track_ids_missing_stream_artist(db)

            if track_ids_to_fetch:
                # This will update both tracks and streams tables
                upsert_tracks_from_spotify(
                    db,
                    spotify_token,
                    track_ids_to_fetch,
                    warn=lambda message: print(f"  Warning: {message}"),
                )
                print(f"Backfilled artist IDs from {len(track_ids_to_fetch)} tracks")

    except Exception as e:
        print(f"Error during import: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    print("Starting Spotify data import...")
    print("Looking for JSON files in /data")
    import_all_spotify_data()
