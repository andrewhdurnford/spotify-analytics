import asyncio
import logging

import httpx

from ..database import SessionLocal
from ..dependencies import get_spotify_client
from ..services.ingest_errors import IngestIssue
from ..services.playback_sync import store_completed_play

logger = logging.getLogger(__name__)

COMPLETION_RATIO = 0.98
COMPLETION_GRACE_MS = 5_000
_sync_lock = asyncio.Lock()


def _log_ingest_issue(issue: IngestIssue) -> None:
    logger.warning(issue.message, extra=issue.log_extra())


def _is_complete_enough(progress_ms: int | None, duration_ms: int | None) -> bool:
    if not progress_ms or not duration_ms:
        return False
    if progress_ms >= duration_ms:
        return True
    return (
        progress_ms / duration_ms >= COMPLETION_RATIO
        or duration_ms - progress_ms <= COMPLETION_GRACE_MS
    )


def _ms_until_complete_enough(progress_ms: int | None, duration_ms: int | None) -> int | None:
    if progress_ms is None or duration_ms is None:
        return None
    ratio_threshold_ms = round(duration_ms * COMPLETION_RATIO)
    grace_threshold_ms = max(0, duration_ms - COMPLETION_GRACE_MS)
    threshold_ms = min(ratio_threshold_ms, grace_threshold_ms)
    return max(0, threshold_ms - progress_ms)


async def _fetch_current_playback(client: httpx.AsyncClient, access_token: str) -> dict | None:
    response = await client.get(
        "https://api.spotify.com/v1/me/player/currently-playing",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    if response.status_code == 204:
        logger.info("No active playback to sync")
        return None

    if response.status_code != 200:
        logger.error(f"Error fetching current playback: {response.status_code} - {response.text}")
        return None

    data = response.json()
    if not data.get("is_playing") or not data.get("item"):
        logger.info("Current playback is paused or empty")
        return None

    track = data["item"]
    if track.get("type") and track["type"] != "track":
        logger.info("Current playback is not a track")
        return None

    return data


async def _wait_for_completion_window(
    client: httpx.AsyncClient,
    access_token: str,
    data: dict,
    max_wait_seconds: int,
) -> dict | None:
    track = data["item"]
    progress_ms = data.get("progress_ms")
    duration_ms = track.get("duration_ms")
    wait_ms = _ms_until_complete_enough(progress_ms, duration_ms)

    if wait_ms is None:
        logger.info("Track is missing progress or duration")
        return None

    if wait_ms == 0:
        return data

    max_wait_ms = max_wait_seconds * 1000
    if wait_ms > max_wait_ms:
        logger.info(
            "Track is %sms away from completion threshold; waiting for a later tick",
            wait_ms,
        )
        return None

    logger.info("Waiting %.1fs to re-check completion threshold", wait_ms / 1000)
    await asyncio.sleep(wait_ms / 1000)

    updated = await _fetch_current_playback(client, access_token)
    if not updated:
        return None

    updated_track = updated["item"]
    if updated_track["id"] != track["id"]:
        logger.info("Track changed before completion threshold")
        return None

    if not _is_complete_enough(updated.get("progress_ms"), updated_track.get("duration_ms")):
        logger.info(
            "Track is still before completion threshold after re-check: %s/%sms",
            updated.get("progress_ms"),
            updated_track.get("duration_ms"),
        )
        return None

    return updated


async def fetch_and_store_recent_tracks(max_wait_seconds: int = 0):
    """Store the current track once playback is close enough to complete.

    Spotify's recently-played endpoint does not expose skipped/completed state.
    Polling currently-playing lets us avoid counting obvious skips in cron data.
    """
    if _sync_lock.locked():
        logger.info("Playback completion sync already running")
        return

    async with _sync_lock:
        await _fetch_and_store_recent_tracks(max_wait_seconds)


async def _fetch_and_store_recent_tracks(max_wait_seconds: int) -> None:
    db = SessionLocal()
    logger.info("🔄 Starting playback completion sync...")

    try:
        # Get Spotify client
        spotify_client = await get_spotify_client()

        async with httpx.AsyncClient(timeout=30.0) as client:
            data = await _fetch_current_playback(client, spotify_client.access_token)
            if not data:
                return

            data = await _wait_for_completion_window(
                client,
                spotify_client.access_token,
                data,
                max_wait_seconds,
            )
            if not data:
                return

            track = data["item"]
            progress_ms = data.get("progress_ms")

            async def fetch_artist_details(artist_id: str) -> dict | None:
                artist_response = await client.get(
                    f"https://api.spotify.com/v1/artists/{artist_id}",
                    headers={"Authorization": f"Bearer {spotify_client.access_token}"},
                )
                if artist_response.status_code != 200:
                    return None
                return artist_response.json()

            result = await store_completed_play(
                db,
                track,
                progress_ms=progress_ms,
                fetch_artist_details=fetch_artist_details,
                report_issue=_log_ingest_issue,
            )

            if result.duplicate:
                logger.info("Current completed play already exists")
                return

            logger.info(
                "✅ Synced: 1 stream, %s tracks, %s artists",
                result.new_tracks,
                result.new_artists,
            )

    except Exception as e:
        logger.error(f"Error syncing recent tracks: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
