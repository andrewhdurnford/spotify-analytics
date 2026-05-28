import json
import os
import sys
from datetime import datetime

import httpx
from dotenv import load_dotenv
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

sys.path.append("/app")

from app.database import SessionLocal, engine
from app.models import Artist, Base, Stream, Track
from app.utils.spotify import fetch_spotify_token_from_client_credentials

# load .env from project root
load_dotenv()


def parse_spotify_date(date_string):
    """Parse Spotify's date format"""
    # Try common formats
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


# --- added helper to detect skipped records ---
def is_skipped_item(item: dict) -> bool:
    """Return True if the JSON record indicates the track was not completed."""
    reason_end = item.get("reason_end")
    if reason_end and reason_end != "trackdone":
        return True

    keys_to_check = ("skipped", "is_skipped", "skipped_flag", "skipped?")
    for k in keys_to_check:
        if k not in item:
            continue
        v = item.get(k)
        if v is None:
            continue
        if isinstance(v, bool):
            if v:
                return True
        elif isinstance(v, int | float):
            if v != 0:
                return True
        elif isinstance(v, str) and v.strip().lower() in ("1", "true", "yes"):
            return True
    return False


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
        skipped = 0

        for item in data:
            try:
                # Skip records that indicate the track was skipped
                if is_skipped_item(item):
                    skipped += 1
                    continue

                # ... (your existing parsing code) ...

                timestamp = item.get("ts") or item.get("endTime") or item.get("timestamp")

                if not timestamp:
                    skipped += 1
                    continue

                played_at = parse_spotify_date(timestamp)

                track_name = (
                    item.get("master_metadata_track_name")
                    or item.get("trackName")
                    or item.get("track_name")
                    or "Unknown"
                )

                artist_name = (
                    item.get("master_metadata_album_artist_name")
                    or item.get("artistName")
                    or item.get("artist_name")
                    or "Unknown"
                )

                album_name = (
                    item.get("master_metadata_album_album_name")
                    or item.get("albumName")
                    or item.get("album_name")
                )

                duration_ms = (
                    item.get("ms_played") or item.get("msPlayed") or item.get("duration_ms") or 0
                )

                track_uri = item.get("spotify_track_uri", "")
                track_id = track_uri.replace("spotify:track:", "") if track_uri else ""

                artist_uri = item.get("spotify_artist_uri", "")
                artist_id = artist_uri.replace("spotify:artist:", "") if artist_uri else ""

                # --- DUPLICATE CHECK ---
                try:
                    existing = None
                    if track_id:
                        existing = db.execute(
                            select(Stream).where(
                                Stream.track_id == track_id,
                                Stream.played_at == played_at,
                            )
                        ).scalar_one_or_none()
                    else:
                        existing = db.execute(
                            select(Stream).where(
                                Stream.track_name == track_name,
                                Stream.artist_name == artist_name,
                                Stream.played_at == played_at,
                            )
                        ).scalar_one_or_none()

                    if existing:
                        skipped += 1
                        continue
                except Exception:
                    # If query fails due to invalid transaction, rollback and skip
                    db.rollback()
                    skipped += 1
                    continue
                # --- END DUPLICATE CHECK ---

                stream = Stream(
                    track_name=track_name,
                    artist_name=artist_name,
                    album_name=album_name,
                    played_at=played_at,
                    duration_ms=duration_ms,
                    track_id=track_id,
                    artist_id=artist_id,
                )

                db.add(stream)

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

            except Exception as e:
                db.rollback()  # Add rollback here too
                skipped += 1
                if skipped <= 5:
                    print(f"  Error importing record: {e}")
                continue

        print(f"  ✓ Imported {imported} records ({skipped} skipped)")
        return imported

    except Exception as e:
        print(f"  ✗ Error processing file: {e}")
        db.rollback()
        return 0


def upsert_tracks_from_spotify(db: Session, spotify_token: str, track_ids: list[str]):
    """Fetch track metadata in batches (max 50) and insert/update Tracks table."""
    if not track_ids:
        return 0

    headers = {"Authorization": f"Bearer {spotify_token}"}
    BATCH = 50
    upserted = 0

    for i in range(0, len(track_ids), BATCH):
        batch = track_ids[i : i + BATCH]
        ids_param = ",".join(batch)
        try:
            resp = httpx.get(
                "https://api.spotify.com/v1/tracks",
                params={"ids": ids_param},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            for t in data.get("tracks", []):
                if not t:
                    continue
                sid = t.get("id")
                if not sid:
                    continue

                album = t.get("album", {}) or {}
                album_images = album.get("images") or []
                cover = album_images[0]["url"] if album_images else None
                artists = t.get("artists") or []
                artist_name = artists[0]["name"] if artists else None

                # NEW: Get the first artist's ID
                artist_id = artists[0]["id"] if artists else None

                existing = db.execute(
                    select(Track).where(Track.spotify_id == sid)
                ).scalar_one_or_none()
                if existing:
                    existing.name = t.get("name") or existing.name
                    existing.artist_name = artist_name or existing.artist_name
                    existing.album_name = album.get("name") or existing.album_name
                    existing.album_image = cover or existing.album_image
                    existing.duration_ms = t.get("duration_ms") or existing.duration_ms
                    existing.popularity = t.get("popularity") or existing.popularity
                else:
                    new = Track(
                        spotify_id=sid,
                        name=t.get("name") or "",
                        artist_name=artist_name,
                        album_name=album.get("name"),
                        album_image=cover,
                        duration_ms=t.get("duration_ms"),
                        popularity=t.get("popularity"),
                    )
                    db.add(new)

                # NEW: Update streams table with artist_id where it's missing
                if artist_id:
                    db.execute(
                        update(Stream)
                        .where(
                            Stream.track_id == sid,
                            Stream.artist_id.is_(None) | (Stream.artist_id == ""),
                        )
                        .values(artist_id=artist_id)
                    )

                upserted += 1
            db.commit()
        except Exception as e:
            print(f"  Warning: spotify batch fetch failed: {e}")
            db.rollback()
            continue

    return upserted


# --- new: upsert artists helper ---
def upsert_artists_from_spotify(db: Session, spotify_token: str, artist_ids: list[str]):
    """Fetch artist metadata in batches (max 50) and insert/update Artists table."""
    if not artist_ids:
        return 0

    headers = {"Authorization": f"Bearer {spotify_token}"}
    BATCH = 50
    upserted = 0

    for i in range(0, len(artist_ids), BATCH):
        batch = artist_ids[i : i + BATCH]
        ids_param = ",".join(batch)
        try:
            resp = httpx.get(
                "https://api.spotify.com/v1/artists",
                params={"ids": ids_param},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            for a in data.get("artists", []):
                if not a:
                    continue
                sid = a.get("id")
                if not sid:
                    continue
                name = a.get("name")
                genres = a.get("genres") or []
                popularity = a.get("popularity")
                images = a.get("images") or []
                image_url = images[0]["url"] if images else None

                existing = db.execute(
                    select(Artist).where(Artist.spotify_id == sid)
                ).scalar_one_or_none()
                if existing:
                    existing.name = name or existing.name
                    existing.genres = genres or existing.genres
                    existing.popularity = popularity or existing.popularity
                    existing.image_url = image_url or existing.image_url
                else:
                    new = Artist(
                        spotify_id=sid,
                        name=name or "",
                        genres=genres,
                        popularity=popularity,
                        image_url=image_url,
                    )
                    db.add(new)
                upserted += 1
            db.commit()
        except Exception as e:
            print(f"  Warning: spotify artist batch fetch failed: {e}")
            db.rollback()
            continue

    return upserted


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
            # collect distinct track_ids from streams
            ids = (
                db.execute(
                    select(Stream.track_id)
                    .where(Stream.track_id.is_not(None), Stream.track_id != "")
                    .distinct()
                )
                .scalars()
                .all()
            )
            existing_ids = (
                set(
                    db.execute(select(Track.spotify_id).where(Track.spotify_id.in_(ids)))
                    .scalars()
                    .all()
                )
                if ids
                else set()
            )
            to_fetch = [i for i in ids if i not in existing_ids]
            print(f"Fetching metadata for {len(to_fetch)} tracks from Spotify...")
            upserted = upsert_tracks_from_spotify(db, spotify_token, to_fetch)
            print(f"  Upserted/updated {upserted} tracks")

            # --- NEW: populate artists table for any missing artist IDs referenced in streams ---
            artist_ids = (
                db.execute(
                    select(Stream.artist_id)
                    .where(Stream.artist_id.is_not(None), Stream.artist_id != "")
                    .distinct()
                )
                .scalars()
                .all()
            )
            if artist_ids:
                existing_artist_ids = (
                    set(
                        db.execute(
                            select(Artist.spotify_id).where(Artist.spotify_id.in_(artist_ids))
                        )
                        .scalars()
                        .all()
                    )
                    if artist_ids
                    else set()
                )
                to_fetch_artists = [i for i in artist_ids if i not in existing_artist_ids]
            else:
                to_fetch_artists = []

            print(f"Fetching metadata for {len(to_fetch_artists)} artists from Spotify...")
            artists_upserted = upsert_artists_from_spotify(db, spotify_token, to_fetch_artists)
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

        # After the artist fetch section, add:
        print("Backfilling missing artist IDs from Spotify API...")
        streams_missing_artist = (
            db.execute(
                select(Stream).where(
                    Stream.track_id.is_not(None),
                    Stream.track_id != "",
                    Stream.artist_id.is_(None) | (Stream.artist_id == ""),
                )
            )
            .scalars()
            .all()
        )

        if streams_missing_artist:
            track_ids_to_fetch = list(set([s.track_id for s in streams_missing_artist]))
            # This will update both tracks and streams tables
            upsert_tracks_from_spotify(db, spotify_token, track_ids_to_fetch)
            print(f"Backfilled artist IDs for {len(streams_missing_artist)} streams")

    except Exception as e:
        print(f"Error during import: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    print("Starting Spotify data import...")
    print("Looking for JSON files in /data")
    import_all_spotify_data()
