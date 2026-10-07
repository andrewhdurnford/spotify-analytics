import asyncio
import logging

import httpx

from ..database import SessionLocal
from ..dependencies import get_spotify_client
from ..services.ingest_errors import IngestIssue
from ..services.playback_sync import store_recently_played

logger = logging.getLogger(__name__)

_sync_lock = asyncio.Lock()


def _log_ingest_issue(issue: IngestIssue) -> None:
    logger.warning(issue.message, extra=issue.log_extra())


async def fetch_and_store_recent_tracks():
    """Store completed plays from Spotify's recently-played history.

    The endpoint keeps the last 50 completed plays, so running every minute
    or so (cron, or the local loop) never falls behind; an outage only loses
    plays once it spans more than 50 of them.
    """
    if _sync_lock.locked():
        logger.info("Recently-played sync already running")
        return

    async with _sync_lock:
        await _fetch_and_store_recent_tracks()


async def _fetch_and_store_recent_tracks() -> None:
    db = SessionLocal()
    logger.info("🔄 Starting recently-played sync...")

    try:
        spotify_client = await get_spotify_client()
        headers = {"Authorization": f"Bearer {spotify_client.access_token}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://api.spotify.com/v1/me/player/recently-played",
                params={"limit": 50},
                headers=headers,
            )
            if response.status_code != 200:
                logger.error(
                    f"Error fetching recently played: {response.status_code} - {response.text}"
                )
                return

            async def fetch_artist_details(artist_id: str) -> dict | None:
                artist_response = await client.get(
                    f"https://api.spotify.com/v1/artists/{artist_id}",
                    headers=headers,
                )
                if artist_response.status_code != 200:
                    return None
                return artist_response.json()

            result = await store_recently_played(
                db,
                response.json().get("items") or [],
                fetch_artist_details=fetch_artist_details,
                report_issue=_log_ingest_issue,
            )

            logger.info(
                "✅ Synced: %s streams (%s already stored), %s tracks, %s artists",
                result.inserted,
                result.duplicates,
                result.new_tracks,
                result.new_artists,
            )

    except Exception as e:
        logger.error(f"Error syncing recent tracks: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
