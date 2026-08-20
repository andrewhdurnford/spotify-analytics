import asyncio
import contextlib
import logging

import httpx

from ..database import SessionLocal
from ..dependencies import get_spotify_client
from ..services.ingest_errors import IngestIssue
from ..services.recent_plays_sync import (
    RecentPlaysSyncResult,
    cursor_epoch_ms,
    process_recently_played_items,
    seed_cursor_to_now,
)

logger = logging.getLogger(__name__)

RECENTLY_PLAYED_URL = "https://api.spotify.com/v1/me/player/recently-played"
RECENTLY_PLAYED_PAGE_LIMIT = 50
# Bounds how far a single run pages forward when catching up after downtime
# -- 5 pages * 50 items covers a long gap without an unbounded loop against
# a possibly-misbehaving upstream.
MAX_PAGES_PER_RUN = 5
MAX_FETCH_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0

_sync_lock = asyncio.Lock()


def _log_ingest_issue(issue: IngestIssue) -> None:
    logger.warning(issue.message, extra=issue.log_extra())


async def _fetch_recently_played_page(
    client: httpx.AsyncClient, access_token: str, after_ms: int | None
) -> dict | None:
    """Fetch one page, retrying on 429/5xx.

    Unlike the old live-progress poller (which could silently drop a poll
    and rely on the next tick catching the same live moment), this is now
    the only writer of completed plays -- a dropped fetch risks a real gap,
    not just a slightly-later capture, so transient failures get a bounded
    retry instead of being swallowed.
    """
    params: dict[str, int] = {"limit": RECENTLY_PLAYED_PAGE_LIMIT}
    if after_ms is not None:
        params["after"] = after_ms

    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        response = await client.get(
            RECENTLY_PLAYED_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        if response.status_code == 200:
            return response.json()

        retryable = response.status_code == 429 or response.status_code >= 500
        if retryable and attempt < MAX_FETCH_ATTEMPTS:
            delay = RETRY_BACKOFF_SECONDS
            if response.status_code == 429:
                with contextlib.suppress(ValueError):
                    delay = float(response.headers.get("Retry-After", RETRY_BACKOFF_SECONDS))
            logger.warning(
                "Recently-played fetch failed (%s), retrying in %.1fs (attempt %d/%d)",
                response.status_code,
                delay,
                attempt,
                MAX_FETCH_ATTEMPTS,
            )
            await asyncio.sleep(delay)
            continue

        logger.error("Error fetching recently-played: %s - %s", response.status_code, response.text)
        return None

    return None


async def _fetch_all_pages(
    client: httpx.AsyncClient, access_token: str, after_ms: int
) -> list[dict]:
    """Page forward from `after_ms` until caught up or MAX_PAGES_PER_RUN hit."""
    items: list[dict] = []
    cursor = after_ms

    for _ in range(MAX_PAGES_PER_RUN):
        page = await _fetch_recently_played_page(client, access_token, cursor)
        if page is None:
            break

        page_items = page.get("items", [])
        items.extend(page_items)

        if len(page_items) < RECENTLY_PLAYED_PAGE_LIMIT:
            break

        next_cursor = page.get("cursors", {}).get("after")
        if not next_cursor:
            break
        cursor = int(next_cursor)

    return items


async def sync_recent_plays() -> RecentPlaysSyncResult | None:
    """Reconcile completed plays since the last run.

    Idempotent (dedup against existing `streams` rows via
    app.services.recent_plays_sync), so it's safe to call from both the
    external cron endpoint and the local dev loop without cross-process
    coordination -- a redundant concurrent run just re-derives "nothing
    new" instead of racing to write. The lock below only avoids wasted
    duplicate work within a single process.
    """
    if _sync_lock.locked():
        logger.info("Recent plays sync already running")
        return None

    async with _sync_lock:
        return await _sync_recent_plays()


async def _sync_recent_plays() -> RecentPlaysSyncResult | None:
    db = SessionLocal()
    logger.info("🔄 Starting recent plays sync...")

    try:
        after_ms = cursor_epoch_ms(db)
        if after_ms is None:
            # Cold start: don't reach back into Spotify's history, which may
            # overlap what historical import already covered -- establish a
            # baseline and pick up from here on the next run.
            seed_cursor_to_now(db)
            logger.info("Recent plays sync cursor seeded; nothing to process yet")
            return None

        spotify_client = await get_spotify_client()

        async with httpx.AsyncClient(timeout=30.0) as client:
            items = await _fetch_all_pages(client, spotify_client.access_token, after_ms)
            if not items:
                logger.info("No new recently-played items")
                return None

            async def fetch_artist_details(artist_id: str) -> dict | None:
                artist_response = await client.get(
                    f"https://api.spotify.com/v1/artists/{artist_id}",
                    headers={"Authorization": f"Bearer {spotify_client.access_token}"},
                )
                if artist_response.status_code != 200:
                    return None
                return artist_response.json()

            result = await process_recently_played_items(
                db,
                items,
                fetch_artist_details=fetch_artist_details,
                report_issue=_log_ingest_issue,
            )

            logger.info(
                "✅ Recent plays sync: %d stored, %d rejected (below ratio), %d duplicate",
                result.inserted,
                result.rejected_ratio,
                result.duplicates,
            )
            return result

    except Exception as e:
        logger.error(f"Error syncing recent plays: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
