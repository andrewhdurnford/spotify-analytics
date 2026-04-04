import os
import sys

import httpx
from dotenv import load_dotenv
from sqlalchemy import func, select

sys.path.append("/app")

from app.database import SessionLocal
from app.models import Artist, Stream
from app.utils.spotify import fetch_spotify_token_from_client_credentials

load_dotenv()


def backfill_artists():
    """Backfill artists table from unique artist_ids in streams table"""

    db = SessionLocal()

    try:
        # Get Spotify token
        spotify_token = os.getenv("SPOTIFY_TOKEN") or fetch_spotify_token_from_client_credentials()
        if not spotify_token:
            print("Error: No Spotify token available")
            return

        print("Spotify token obtained successfully")

        # Get all unique artist_ids from streams table
        print("Fetching unique artist IDs from streams table...")
        unique_artist_ids = (
            db.execute(
                select(Stream.artist_id)
                .where(Stream.artist_id.is_not(None), Stream.artist_id != "")
                .distinct()
            )
            .scalars()
            .all()
        )

        unique_artist_ids = list(unique_artist_ids)
        print(f"Found {len(unique_artist_ids)} unique artist IDs in streams table")

        if not unique_artist_ids:
            print("No artist IDs found in streams table!")
            return

        # Get existing artist_ids in artists table to avoid duplicates
        print("Checking which artists already exist in database...")
        existing_artist_ids = set(
            db.execute(select(Artist.spotify_id).where(Artist.spotify_id.in_(unique_artist_ids)))
            .scalars()
            .all()
        )

        print(f"Found {len(existing_artist_ids)} artists already in database")

        # Filter out artists that already exist
        artists_to_fetch = [aid for aid in unique_artist_ids if aid not in existing_artist_ids]

        print(f"Need to fetch {len(artists_to_fetch)} new artists from Spotify API")

        if not artists_to_fetch:
            print("All artists already exist in database!")
            return

        # Fetch in batches of 50 (Spotify API limit)
        headers = {"Authorization": f"Bearer {spotify_token}"}
        BATCH_SIZE = 50

        artists_added = 0
        artists_failed = 0

        for i in range(0, len(artists_to_fetch), BATCH_SIZE):
            batch = artists_to_fetch[i : i + BATCH_SIZE]
            ids_param = ",".join(batch)

            batch_num = i // BATCH_SIZE + 1
            total_batches = (len(artists_to_fetch) - 1) // BATCH_SIZE + 1
            print(f"\nFetching batch {batch_num}/{total_batches} ({len(batch)} artists)...")

            try:
                resp = httpx.get(
                    "https://api.spotify.com/v1/artists",
                    params={"ids": ids_param},
                    headers=headers,
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()

                for artist in data.get("artists", []):
                    if not artist:
                        artists_failed += 1
                        continue

                    artist_id = artist.get("id")
                    if not artist_id:
                        artists_failed += 1
                        continue

                    name = artist.get("name", "")
                    genres = artist.get("genres", [])
                    popularity = artist.get("popularity")
                    images = artist.get("images", [])
                    image_url = images[0]["url"] if images else None

                    # Check if artist already exists (double-check to be safe)
                    existing = db.execute(
                        select(Artist).where(Artist.spotify_id == artist_id)
                    ).scalar_one_or_none()

                    if existing:
                        # Update existing artist
                        existing.name = name or existing.name
                        existing.genres = genres or existing.genres
                        existing.popularity = (
                            popularity if popularity is not None else existing.popularity
                        )
                        existing.image_url = image_url or existing.image_url
                        print(f"  Updated: {name} ({artist_id})")
                    else:
                        # Create new artist
                        new_artist = Artist(
                            spotify_id=artist_id,
                            name=name,
                            genres=genres,
                            popularity=popularity,
                            image_url=image_url,
                        )
                        db.add(new_artist)
                        print(
                            f"  Added: {name} ({artist_id}) - Genres: {', '.join(genres[:3]) if genres else 'None'}"
                        )
                        artists_added += 1

                # Commit after each batch
                db.commit()
                print(f"  Batch {batch_num} committed successfully")

            except httpx.HTTPStatusError as e:
                print(f"  HTTP Error fetching batch: {e}")
                if e.response.status_code == 429:
                    print("  Rate limited! Waiting 60 seconds...")
                    import time

                    time.sleep(60)
                db.rollback()
                artists_failed += len(batch)
                continue
            except Exception as e:
                print(f"  Error fetching batch: {e}")
                db.rollback()
                artists_failed += len(batch)
                continue

        print(f"\n{'=' * 60}")
        print(f"BACKFILL COMPLETE")
        print(f"{'=' * 60}")
        print(f"Artists added: {artists_added}")
        print(f"Artists failed: {artists_failed}")
        total_artists = db.scalar(select(func.count()).select_from(Artist))
        print(f"Total artists in database: {total_artists}")

        # Show some stats
        print(f"\n{'=' * 60}")
        print(f"DATABASE STATS")
        print(f"{'=' * 60}")

        print(f"Total artists: {total_artists}")

        if total_artists > 0:
            # Top 10 most popular artists
            top_artists = (
                db.execute(
                    select(Artist)
                    .where(Artist.popularity.is_not(None))
                    .order_by(Artist.popularity.desc())
                    .limit(10)
                )
                .scalars()
                .all()
            )

            if top_artists:
                print(f"\nTop 10 most popular artists:")
                for idx, artist in enumerate(top_artists, 1):
                    genres_str = ", ".join(artist.genres[:2]) if artist.genres else "No genres"
                    print(
                        f"  {idx}. {artist.name} (Popularity: {artist.popularity}) - {genres_str}"
                    )

            # Genre distribution
            all_artists_with_genres = (
                db.execute(select(Artist).where(Artist.genres.is_not(None))).scalars().all()
            )
            genre_count = {}
            for artist in all_artists_with_genres:
                for genre in artist.genres or []:
                    genre_count[genre] = genre_count.get(genre, 0) + 1

            if genre_count:
                print(f"\nTop 10 genres:")
                sorted_genres = sorted(genre_count.items(), key=lambda x: x[1], reverse=True)[:10]
                for idx, (genre, count) in enumerate(sorted_genres, 1):
                    print(f"  {idx}. {genre}: {count} artists")

    except Exception as e:
        print(f"Error during backfill: {e}")
        import traceback

        traceback.print_exc()
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 60)
    print("ARTIST TABLE BACKFILL SCRIPT")
    print("=" * 60)
    print()
    backfill_artists()
