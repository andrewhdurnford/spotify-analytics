import asyncio
import logging
import os
import sys
from datetime import datetime

import httpx
from dotenv import load_dotenv
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.config import settings
from app.database import SessionLocal
from app.models import Stream

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def get_spotify_access_token() -> str:
    """Get Spotify access token using refresh token"""
    refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN") or settings.spotify_refresh_token
    client_id = os.getenv("SPOTIFY_CLIENT_ID") or settings.spotify_client_id
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET") or settings.spotify_client_secret

    if not refresh_token or not client_id or not client_secret:
        raise Exception(
            "Missing Spotify credentials. Set SPOTIFY_REFRESH_TOKEN, SPOTIFY_CLIENT_ID, and SPOTIFY_CLIENT_SECRET"
        )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )

        if response.status_code != 200:
            raise Exception(f"Failed to get access token: {response.status_code} - {response.text}")

        data = response.json()
        return data["access_token"]


async def get_user_id(access_token: str) -> str:
    """Get the current user's Spotify user ID"""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.spotify.com/v1/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        if response.status_code != 200:
            raise Exception(f"Failed to get user info: {response.status_code} - {response.text}")

        data = response.json()
        return data["id"]


def get_top_songs_for_month(db: Session, year: int, month: int, limit: int = 100):
    """Get top songs for a specific month"""
    # Calculate start and end of month
    start = datetime(year, month, 1, 0, 0, 0)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0)

    stmt = (
        select(
            Stream.track_id,
            Stream.track_name,
            Stream.artist_name,
            func.count(Stream.id).label("play_count"),
        )
        .where(
            Stream.played_at >= start,
            Stream.played_at < end,
            Stream.track_id.isnot(None),
        )
        .group_by(Stream.track_id, Stream.track_name, Stream.artist_name)
        .order_by(desc("play_count"))
        .limit(limit)
    )

    results = db.execute(stmt).all()

    return [
        {
            "track_id": r.track_id,
            "track_name": r.track_name,
            "artist_name": r.artist_name,
            "play_count": r.play_count,
        }
        for r in results
    ]


async def create_playlist(
    access_token: str, user_id: str, name: str, description: str = None, public: bool = False
) -> str:
    """Create a playlist and return its ID"""
    async with httpx.AsyncClient() as client:
        payload = {
            "name": name,
            "public": public,
            "description": description or f"Top 100 songs for {name}",
        }

        response = await client.post(
            f"https://api.spotify.com/v1/users/{user_id}/playlists",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
        )

        if response.status_code != 201:
            raise Exception(f"Failed to create playlist: {response.status_code} - {response.text}")

        data = response.json()
        return data["id"]


async def add_tracks_to_playlist(access_token: str, playlist_id: str, track_ids: list[str]):
    """Add tracks to a playlist. Spotify API allows up to 100 tracks per request."""
    if not track_ids:
        return

    # Spotify API requires track URIs in format: spotify:track:TRACK_ID
    track_uris = [f"spotify:track:{track_id}" for track_id in track_ids if track_id]

    # Split into chunks of 100 (Spotify API limit)
    chunk_size = 100
    for i in range(0, len(track_uris), chunk_size):
        chunk = track_uris[i : i + chunk_size]

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"uris": chunk},
            )

            if response.status_code not in [200, 201]:
                raise Exception(
                    f"Failed to add tracks to playlist: {response.status_code} - {response.text}"
                )

        logger.info(f"  Added {len(chunk)} tracks to playlist")


async def generate_monthly_playlists():
    """Generate playlists for top songs for each month since 2024"""
    db = SessionLocal()

    try:
        # Get Spotify access token
        logger.info("🔑 Getting Spotify access token...")
        access_token = await get_spotify_access_token()

        # Get user ID
        logger.info("👤 Getting user ID...")
        user_id = await get_user_id(access_token)
        logger.info(f"✅ User ID: {user_id}")

        # Generate playlists for each month since 2024
        current_date = datetime.now()
        start_year = 2024
        start_month = 1

        # Calculate all months to process
        months_to_process = []
        year = start_year
        month = start_month

        while year < current_date.year or (
            year == current_date.year and month <= current_date.month
        ):
            months_to_process.append((year, month))
            month += 1
            if month > 12:
                month = 1
                year += 1

        logger.info(f"📅 Processing {len(months_to_process)} months...")

        for year, month in months_to_process:
            playlist_name = f"{month:02d}/{year}"
            month_name = datetime(year, month, 1).strftime("%B %Y")  # For logging and description

            logger.info(f"\n🎵 Processing {playlist_name} ({month_name})...")

            # Get top songs for this month
            top_songs = get_top_songs_for_month(db, year, month, limit=100)

            if not top_songs:
                logger.warning(f"  ⚠️  No songs found for {playlist_name}, skipping...")
                continue

            # Filter out songs without track_id
            valid_songs = [s for s in top_songs if s["track_id"]]

            if not valid_songs:
                logger.warning(f"  ⚠️  No valid track IDs found for {playlist_name}, skipping...")
                continue

            logger.info(f"  Found {len(valid_songs)} songs with valid track IDs")

            # Create playlist
            try:
                playlist_id = await create_playlist(
                    access_token,
                    user_id,
                    playlist_name,
                    description=f"Top 100 most played songs in {month_name}",
                    public=False,
                )
                logger.info(f"  ✅ Created playlist: {playlist_name} (ID: {playlist_id})")

                # Add tracks to playlist
                track_ids = [s["track_id"] for s in valid_songs]
                await add_tracks_to_playlist(access_token, playlist_id, track_ids)
                logger.info(f"  ✅ Added {len(track_ids)} tracks to playlist")

            except Exception as e:
                logger.error(f"  ❌ Error creating playlist for {playlist_name}: {e}")
                continue

        logger.info("\n✅ All playlists generated successfully!")

    except Exception as e:
        logger.error(f"❌ Error generating playlists: {e}")
        import traceback

        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(generate_monthly_playlists())
