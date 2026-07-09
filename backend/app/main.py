import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.routes import detail, top
from .config import Settings, settings
from .dependencies import require_cron_rate_limit
from .schemas import (
    CronSyncResponse,
    ErrorResponse,
    HealthResponse,
    MessageResponse,
    SyncStatusResponse,
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
    provided_token = authorization or ""
    expected_token = f"Bearer {settings.cron_secret}"
    # A blank CRON_SECRET must never authenticate anything, regardless of
    # what's sent -- otherwise a misconfigured (empty) secret would let an
    # empty/`Bearer ` token through. Comparison is constant-time to avoid
    # leaking the secret's value/length via response timing.
    token_is_valid = bool(settings.cron_secret) and secrets.compare_digest(
        provided_token, expected_token
    )
    if not token_is_valid:
        logger.warning("Unauthorized cron attempt")
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Invalid or missing authorization token.",
                }
            },
        )

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


@app.get("/", response_model=MessageResponse)
def read_root():
    return MessageResponse(message="Spotify Analytics API")


@app.get("/health", response_model=HealthResponse)
def health_check():
    return HealthResponse(status="healthy", message="API is running")


@app.get("/api/sync-status", response_model=SyncStatusResponse)
def sync_status():
    """Check the last sync status.

    Deliberately omits the cron endpoint path -- no reason to publish that
    to unauthenticated callers.
    """
    return SyncStatusResponse(
        status="Syncing via external cron service",
        interval="Every minute",
    )
