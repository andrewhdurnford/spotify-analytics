import logging
from datetime import datetime

import httpx
from sqlalchemy import select

from ..database import SessionLocal
from ..dependencies import get_spotify_client
from ..models import Artist, Stream, Track

logger = logging.getLogger(__name__)


async def fetch_and_store_recent_tracks():
    """Fetch recently played tracks and store new ones in the database"""
    db = SessionLocal()
    logger.info("🔄 Starting sync of recent tracks...")

    try:
        # Get Spotify client
        spotify_client = await get_spotify_client()

        # Get the most recent stream timestamp from database
        last_stream = (
            db.execute(select(Stream).order_by(Stream.played_at.desc()).limit(1))
            .scalars()
            .first()
        )
        after_timestamp = None

        if last_stream:
            # Convert to Unix timestamp in milliseconds
            after_timestamp = int(last_stream.played_at.timestamp() * 1000)

        # Fetch recently played tracks
        async with httpx.AsyncClient(timeout=30.0) as client:
            params = {"limit": 50}
            if after_timestamp:
                params["after"] = after_timestamp

            response = await client.get(
                "https://api.spotify.com/v1/me/player/recently-played",
                headers={"Authorization": f"Bearer {spotify_client.access_token}"},
                params=params,
            )

            if response.status_code != 200:
                logger.error(
                    f"Error fetching recent tracks: {response.status_code} - {response.text}"
                )
                return

            data = response.json()
            items = data.get("items", [])

            logger.info(f"Found {len(items)} recently played tracks")

            new_streams = 0
            new_tracks = 0
            new_artists = 0

            for item in items:
                track = item["track"]
                played_at = datetime.fromisoformat(item["played_at"].replace("Z", "+00:00"))

                # Check if stream already exists
                existing_stream = db.execute(
                    select(Stream).where(
                        Stream.track_id == track["id"],
                        Stream.played_at == played_at,
                    )
                ).scalar_one_or_none()

                if existing_stream:
                    continue

                # Get or create track
                track_record = db.execute(
                    select(Track).where(Track.spotify_id == track["id"])
                ).scalar_one_or_none()
                if not track_record:
                    album_image = None
                    if track.get("album") and track["album"].get("images"):
                        album_image = track["album"]["images"][0]["url"]

                    track_record = Track(
                        spotify_id=track["id"],
                        name=track["name"],
                        artist_name=track["artists"][0]["name"] if track.get("artists") else None,
                        album_name=track.get("album", {}).get("name"),
                        album_image=album_image,
                        duration_ms=track.get("duration_ms"),
                        popularity=track.get("popularity"),
                        first_played_at=played_at,
                    )
                    db.add(track_record)
                    db.flush()  # Flush to get the ID without committing
                    new_tracks += 1

                # Get or create artists
                if track.get("artists"):
                    for artist_data in track["artists"]:
                        artist_record = db.execute(
                            select(Artist).where(Artist.spotify_id == artist_data["id"])
                        ).scalar_one_or_none()

                        if not artist_record:
                            # Fetch full artist details
                            try:
                                artist_response = await client.get(
                                    f"https://api.spotify.com/v1/artists/{artist_data['id']}",
                                    headers={
                                        "Authorization": f"Bearer {spotify_client.access_token}"
                                    },
                                )

                                if artist_response.status_code == 200:
                                    artist_full = artist_response.json()
                                    image_url = None
                                    if artist_full.get("images"):
                                        image_url = artist_full["images"][0]["url"]

                                    artist_record = Artist(
                                        spotify_id=artist_data["id"],
                                        name=artist_data["name"],
                                        genres=artist_full.get("genres", []),
                                        popularity=artist_full.get("popularity"),
                                        image_url=image_url,
                                    )
                                    db.add(artist_record)
                                    db.flush()  # Flush to avoid duplicates
                                    new_artists += 1
                            except Exception as e:
                                logger.warning(f"Error fetching artist {artist_data['id']}: {e}")
                                continue

                # Create stream record
                stream = Stream(
                    track_name=track["name"],
                    artist_name=track["artists"][0]["name"] if track.get("artists") else "Unknown",
                    album_name=track.get("album", {}).get("name"),
                    played_at=played_at,
                    duration_ms=track.get("duration_ms"),
                    track_id=track["id"],
                    artist_id=track["artists"][0]["id"] if track.get("artists") else None,
                )
                db.add(stream)
                new_streams += 1

            db.commit()
            if new_streams > 0 or new_tracks > 0 or new_artists > 0:
                logger.info(
                    f"✅ Synced: {new_streams} streams, {new_tracks} tracks, {new_artists} artists"
                )
            else:
                logger.info("✅ Sync completed - no new data")

    except Exception as e:
        logger.error(f"Error syncing recent tracks: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
