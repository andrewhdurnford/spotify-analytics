import logging
import os
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

# Store token and expiry
_access_token = None
_token_expiry = None

async def get_spotify_client():
    global _access_token, _token_expiry

    # Refresh token if expired
    if not _access_token or datetime.now() >= _token_expiry:
        refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN")
        if not refresh_token:
            logger.error("❌ No refresh token configured")
            raise Exception("No refresh token configured")

        logger.info("🔑 Refreshing Spotify access token...")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://accounts.spotify.com/api/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": os.getenv("SPOTIFY_CLIENT_ID"),
                    "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
                },
            )

            if response.status_code != 200:
                logger.error(f"❌ Token refresh failed: {response.status_code} - {response.text}")
                response.raise_for_status()

            data = response.json()
            _access_token = data["access_token"]
            _token_expiry = datetime.now() + timedelta(seconds=data["expires_in"])
            logger.info(f"✅ Got new access token, expires in {data['expires_in']}s")

    return SpotifyClient(_access_token)

class SpotifyClient:
    def __init__(self, access_token: str):
        self.access_token = access_token

    async def get_currently_playing(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.spotify.com/v1/me/player/currently-playing",
                headers={"Authorization": f"Bearer {self.access_token}"},
            )

            logger.debug(f"Spotify API response: {response.status_code}")

            if response.status_code == 204:
                logger.info("🎵 No track currently playing")
                return {"is_playing": False}

            if response.status_code == 200:
                data = response.json()
                track_name = data.get('item', {}).get('name', 'Unknown')
                artist_name = ", ".join([artist["name"] for artist in data.get('item', {}).get("artists", [])])
                logger.info(f"🎵 Currently playing: {track_name} by {artist_name}")

                # Format the response
                if data.get("is_playing") and data.get("item"):
                    item = data["item"]
                    artists = item.get("artists", [])
                    return {
                        "is_playing": True,
                        "track_id": item.get("id"),
                        "track_name": item.get("name"),
                        "artist_id": artists[0].get("id") if artists else None,
                        "artist_name": ", ".join(
                            [artist["name"] for artist in artists]
                        ),
                        "album_name": item.get("album", {}).get("name"),
                        "album_image": (
                            item.get("album", {}).get("images", [{}])[0].get("url")
                        ),
                        "progress_ms": data.get("progress_ms"),
                        "duration_ms": item.get("duration_ms"),
                    }
                else:
                    return {"is_playing": False}

            logger.error(f"❌ Unexpected Spotify API response: {response.status_code}")
            response.raise_for_status()
            return {"is_playing": False}
