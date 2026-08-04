import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.routes import detail, top
from .config import Settings, settings
from .database import SessionLocal, get_db
from .dependencies import require_cron_rate_limit, require_cron_route_rate_limit
from .schemas import (
    CronSyncResponse,
    ErrorResponse,
    HealthResponse,
    MessageResponse,
    SyncStatusResponse,
)
from .services.cron_auth import cron_token_is_valid
from .services.related_tracks_rollup import (
    get_last_rebuilt_at,
    rebuild_related_tracks_rollup,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def local_playback_sync_loop() -> None:
    """Run playback sync in-process for local/dev environments."""
    from .tasks.sync_recent_tracks import fetch_and_store_recent_tracks

    interval_seconds = max(5, settings.local_sync_interval_seconds)
    logger.info("Starting local playback sync loop every %ss", interval_seconds)

    while True:
        try:
            await fetch_and_store_recent_tracks(max_wait_seconds=settings.sync_max_wait_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Local playback sync failed")

        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sync_task = None
    if settings.enable_local_sync:
        sync_task = asyncio.create_task(local_playback_sync_loop())

    yield

    if sync_task:
        sync_task.cancel()
        with suppress(asyncio.CancelledError):
            await sync_task


# --- App configuration (pure functions, unit-testable without booting a
# live app instance) ----------------------------------------------------


def docs_urls_for(environment: str) -> tuple[str | None, str | None, str | None]:
    """Return (docs_url, redoc_url, openapi_url) for the given environment.

    Production doesn't expose interactive API docs or the raw OpenAPI
    schema -- no reason to hand out a map of the API surface publicly.
    """
    if environment == "production":
        return None, None, None
    return "/docs", "/redoc", "/openapi.json"


DEV_CORS_ORIGINS = ["http://localhost:3000"]
PRODUCTION_CORS_ORIGINS = [
    "https://spotify-andrewdurnford.netlify.app",
    "https://spotify-frontend-phi.vercel.app",
    "https://spotify.andrewdurnford.com",
]
CORS_ALLOW_METHODS = ["GET", "POST"]
CORS_ALLOW_HEADERS = ["Content-Type", "Authorization"]


def cors_origins_for(app_settings: Settings) -> list[str]:
    """Explicit allow-list of origins allowed to call this API.

    No wildcard, in any environment: the frontend's origin is always known
    (either the configured FRONTEND_URL, or localhost:3000 for local dev),
    so there's no reason to accept requests from arbitrary origins. The API
    doesn't use cookies (allow_credentials=False), so this isn't relied on
    as the only defense, but it still cuts down on unwanted cross-origin
    browser access.
    """
    base = PRODUCTION_CORS_ORIGINS if app_settings.environment == "production" else DEV_CORS_ORIGINS
    if app_settings.frontend_url in base:
        return list(base)
    return [*base, app_settings.frontend_url]


async def _run_with_timeout(call_next_coro, timeout_seconds: float):
    """Run `call_next_coro()` under a deadline, returning a structured 504
    JSONResponse if it doesn't complete in time.

    This bounds how long a *client* waits, not how long the server works:
    sync `def` handlers run in the threadpool, and cancelling the awaiting
    coroutine doesn't stop that thread. The DB `statement_timeout`
    (`database.py`) is what actually sheds slow query load; this middleware
    is the client-facing complement.

    Extracted as a standalone helper (rather than inlined in the
    middleware) so the timeout branch is directly unit-testable without
    needing a live request/response cycle.
    """
    try:
        return await asyncio.wait_for(call_next_coro(), timeout=timeout_seconds)
    except TimeoutError:
        return JSONResponse(
            status_code=504,
            content={
                "detail": {
                    "error": {
                        "code": "REQUEST_TIMEOUT",
                        "message": "The request took too long to process.",
                    }
                }
            },
        )


docs_url, redoc_url, openapi_url = docs_urls_for(settings.environment)

app = FastAPI(
    title="Spotify Analytics API",
    lifespan=lifespan,
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)


# Global exception handler for unhandled errors
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred.",
            }
        },
    )


# Rejects requests whose Host header isn't recognized. Disabled by default
# (ALLOWED_HOSTS="*"); production deployments should set ALLOWED_HOSTS to
# the real backend hostname(s).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list())

# CORS middleware. allow_credentials=False because this API doesn't use
# cookies -- callers authenticate (where needed, e.g. cron) via a bearer
# token header instead.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins_for(settings),
    allow_credentials=False,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
)


@app.middleware("http")
async def enforce_request_timeout(request: Request, call_next):
    return await _run_with_timeout(lambda: call_next(request), settings.request_timeout_seconds)


@app.middleware("http")
async def log_request_timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(f"{request.method} {request.url.path} completed in {elapsed_ms:.1f}ms")
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
    return response


# Include routers
app.include_router(top.router, prefix="/api/top", tags=["top"])
app.include_router(detail.router, prefix="/api", tags=["detail"])


def _unauthorized_cron() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "error": {
                "code": "UNAUTHORIZED",
                "message": "Invalid or missing authorization token.",
            }
        },
    )


@app.post(
    "/api/cron/sync-tracks",
    response_model=CronSyncResponse,
    dependencies=[Depends(require_cron_rate_limit)],
)
async def cron_sync_tracks(authorization: str = Header(None)):
    """
    Endpoint triggered by external cron service (cron-job.org).
    Protected by secret token to prevent unauthorized access.
    """
    if not cron_token_is_valid(authorization):
        logger.warning("Unauthorized cron attempt")
        raise _unauthorized_cron()

    from .tasks.sync_recent_tracks import fetch_and_store_recent_tracks

    try:
        logger.info("Starting cron sync...")
        await fetch_and_store_recent_tracks(max_wait_seconds=settings.sync_max_wait_seconds)
        logger.info("Cron sync completed successfully")
        return CronSyncResponse(status="success", message="Tracks synced successfully")
    except Exception as e:
        logger.error(f"Cron sync failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": "SYNC_FAILED",
                    "message": "Failed to sync tracks.",
                }
            },
        ) from e


# A rebuild is a full scan of `streams`; the external schedule is hourly, so
# anything arriving sooner than this is a double-fire or a misconfiguration.
MIN_RELATED_REBUILD_INTERVAL = timedelta(minutes=55)


def run_related_tracks_rebuild() -> None:
    """Rebuild the related-tracks rollup on its own DB session.

    Opens its own `SessionLocal` rather than taking one from `Depends(get_db)`:
    that session is closed as soon as the request completes (see
    `database.get_db`), which is *before* this background task runs.
    """
    db = SessionLocal()
    try:
        stats = rebuild_related_tracks_rollup(db)
        if stats.skipped_locked:
            db.rollback()
            logger.info("Related-tracks rebuild skipped; another rebuild holds the lock")
            return
        db.commit()
        logger.info(
            "Related-tracks rollup rebuilt: %d streams, %d sessions, %d tracks, %d pairs",
            stats.streams_processed,
            stats.sessions,
            stats.track_rows,
            stats.pair_rows,
        )
    except Exception:
        db.rollback()
        logger.exception("Related-tracks rollup rebuild failed")
    finally:
        db.close()


@app.post(
    "/api/cron/rebuild-related-tracks",
    response_model=CronSyncResponse,
    status_code=202,
    dependencies=[Depends(require_cron_route_rate_limit)],
)
def cron_rebuild_related_tracks(
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """Recompute the related-tracks co-occurrence rollup (external cron, hourly).

    A plain `def` (not `async def`) so FastAPI runs it in the threadpool -- the
    throttle check below touches the database, and an async handler would block
    the event loop for every other request while it did.

    Always answers 202 immediately and does the actual rebuild in a background
    task, because a rebuild scans every `streams` row and would otherwise sit
    well past the request timeout. `status` distinguishes the two outcomes:
    "accepted" (rebuild enqueued) or "skipped" (ran too recently).
    """
    if not cron_token_is_valid(authorization):
        logger.warning("Unauthorized related-tracks rebuild attempt")
        raise _unauthorized_cron()

    last_rebuilt_at = get_last_rebuilt_at(db)
    now = datetime.now(UTC).replace(tzinfo=None)
    if last_rebuilt_at and now - last_rebuilt_at < MIN_RELATED_REBUILD_INTERVAL:
        # First line of defense against an over-eager schedule; the builder's
        # `pg_try_advisory_xact_lock` is the second.
        logger.info("Related-tracks rebuild skipped; last completed %s", last_rebuilt_at)
        return CronSyncResponse(
            status="skipped",
            message=f"Rollup was rebuilt at {last_rebuilt_at.isoformat()}Z; skipping.",
        )

    background_tasks.add_task(run_related_tracks_rebuild)
    return CronSyncResponse(status="accepted", message="Related-tracks rebuild started.")


@app.get("/", response_model=MessageResponse)
def read_root():
    return MessageResponse(message="Spotify Analytics API")


@app.get("/health", response_model=HealthResponse)
def health_check():
    return HealthResponse(status="healthy", message="API is running")


@app.get("/api/sync-status", response_model=SyncStatusResponse)
def sync_status(db: Session = Depends(get_db)):
    """Check the last sync status.

    `related_tracks_rollup_last_rebuilt_at` is exposed so a broken or
    forgotten rebuild schedule degrades visibly: without it, the only symptom
    of a dead cron job is related-track results quietly going stale (or never
    appearing at all, if the post-migration backfill was skipped).

    Deliberately omits the cron endpoint paths -- no reason to publish those
    to unauthenticated callers.
    """
    return SyncStatusResponse(
        status="Syncing via external cron service",
        interval="Every minute",
        related_tracks_rollup_last_rebuilt_at=get_last_rebuilt_at(db),
    )
