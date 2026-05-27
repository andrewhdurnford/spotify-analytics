import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from ..database import SessionLocal
from ..dependencies import get_spotify_client
from ..models import Artist, Stream, Track

logger = logging.getLogger(__name__)

COMPLETION_RATIO = 0.98
COMPLETION_GRACE_MS = 5_000
DUPLICATE_GRACE = timedelta(seconds=30)
_sync_lock = asyncio.Lock()


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
        logger.error(
            f"Error fetching current playback: {response.status_code} - {response.text}"
        )
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
            duration_ms = track.get("duration_ms")

            played_at = datetime.now(UTC).replace(tzinfo=None)
            estimated_started_at = played_at - timedelta(milliseconds=progress_ms)
            duplicate_start = estimated_started_at - DUPLICATE_GRACE
            duplicate_end = played_at + DUPLICATE_GRACE

            # Check if this same observed play has already been stored.
            existing_stream = db.execute(
                select(Stream).where(
                    Stream.track_id == track["id"],
                    Stream.played_at >= duplicate_start,
                    Stream.played_at <= duplicate_end,
                )
            ).scalar_one_or_none()

            if existing_stream:
                logger.info("Current completed play already exists")
                return

            new_tracks = 0
            new_artists = 0

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
                    duration_ms=duration_ms,
                    popularity=track.get("popularity"),
                    first_played_at=played_at,
                )
                db.add(track_record)
                db.flush()
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
                                db.flush()
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
                duration_ms=duration_ms,
                track_id=track["id"],
                artist_id=track["artists"][0]["id"] if track.get("artists") else None,
            )
            db.add(stream)

            db.commit()
            logger.info(
                f"✅ Synced: 1 stream, {new_tracks} tracks, {new_artists} artists"
            )

    except Exception as e:
        logger.error(f"Error syncing recent tracks: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
