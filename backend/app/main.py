import logging
import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes import detail, top
from .config import settings
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

app = FastAPI(
    title="Spotify Analytics API",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
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


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url,
        "http://localhost:3000",
        "https://spotify-andrewdurnford.netlify.app",
        "https://spotify-frontend-phi.vercel.app",
        "https://spotify.andrewdurnford.com",
    ]
    if settings.environment == "production"
    else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.post("/api/cron/sync-tracks", response_model=CronSyncResponse)
async def cron_sync_tracks(authorization: str = Header(None)):
    """
    Endpoint triggered by external cron service (cron-job.org).
    Protected by secret token to prevent unauthorized access.
    """
    expected_token = f"Bearer {settings.cron_secret}"
    if authorization != expected_token:
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
        await fetch_and_store_recent_tracks()
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
        )


@app.get("/", response_model=MessageResponse)
def read_root():
    return MessageResponse(message="Spotify Analytics API")


@app.get("/health", response_model=HealthResponse)
def health_check():
    return HealthResponse(status="healthy", message="API is running")


@app.get("/api/sync-status", response_model=SyncStatusResponse)
def sync_status():
    """Check the last sync status."""
    return SyncStatusResponse(
        status="Syncing via external cron service",
        interval="Every minute",
        endpoint="/api/cron/sync-tracks",
    )
