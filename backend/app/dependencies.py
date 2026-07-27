import logging
import os
from datetime import datetime, timedelta

import httpx
from fastapi import Header, HTTPException, Request

from .services.cron_auth import cron_token_is_valid
from .services.rate_limit import (
    RateLimiter,
    cron_authenticated_limiter,
    cron_limiter,
    currently_playing_limiter,
    general_limiter,
    related_tracks_limiter,
)

logger = logging.getLogger(__name__)

# Outbound HTTP calls to Spotify get an explicit timeout so a slow/hanging
# upstream can't tie up a worker indefinitely.
SPOTIFY_HTTP_TIMEOUT_SECONDS = 10.0

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

        async with httpx.AsyncClient(timeout=SPOTIFY_HTTP_TIMEOUT_SECONDS) as client:
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
        async with httpx.AsyncClient(timeout=SPOTIFY_HTTP_TIMEOUT_SECONDS) as client:
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
                track_name = data.get("item", {}).get("name", "Unknown")
                artist_name = ", ".join(
                    [artist["name"] for artist in data.get("item", {}).get("artists", [])]
                )
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
                        "artist_name": ", ".join([artist["name"] for artist in artists]),
                        "album_name": item.get("album", {}).get("name"),
                        "album_image": (item.get("album", {}).get("images", [{}])[0].get("url")),
                        "progress_ms": data.get("progress_ms"),
                        "duration_ms": item.get("duration_ms"),
                    }
                else:
                    return {"is_playing": False}

            logger.error(f"❌ Unexpected Spotify API response: {response.status_code}")
            response.raise_for_status()
            return {"is_playing": False}


# --- Rate limiting -----------------------------------------------------
#
# Thin FastAPI dependencies wrapping the RateLimiter instances in
# `app.services.rate_limit`. Each route opts into exactly the limiter(s)
# that describe it (general analytics / currently-playing / related-tracks
# / cron), so requests are only ever counted against the buckets that
# actually apply to that endpoint.


def _client_key(request: Request) -> str:
    """Return the client identity established by the ASGI server.

    Proxy headers must be interpreted only by a server configured with a
    trusted-proxy allow-list (see FORWARDED_ALLOW_IPS in `.env.example`).
    Reading X-Forwarded-For here would let direct clients mint arbitrary
    limiter identities.
    """
    return request.client.host if request.client else "unknown"


def _route_key(request: Request) -> str:
    """Return the matched route's template, e.g. "/api/artists/{artist_id}".

    Keying on the concrete `request.url.path` instead would give every
    distinct path parameter value its own fresh budget, so a client rotating
    IDs would face no effective limit on exactly the per-entity endpoints
    that hit the DB -- and the resulting key churn would evict other
    clients' (and its own throttled) buckets from the limiter's bounded
    table.
    """
    path_format = getattr(request.scope.get("route"), "path_format", None)
    return path_format if isinstance(path_format, str) else request.url.path


def _enforce_rate_limit(limiter: RateLimiter, key: str) -> None:
    if not limiter.allow(key):
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Too many requests. Please slow down and try again shortly.",
                }
            },
        )


def require_general_rate_limit(request: Request) -> None:
    # The documented 60/minute threshold is per analytics route. Scoping the
    # key by route also prevents the dashboard aggregator's parallel calls to
    # six different endpoints from consuming one shared allowance.
    _enforce_rate_limit(general_limiter, f"{_client_key(request)}:{_route_key(request)}")


def require_currently_playing_rate_limit(request: Request) -> None:
    _enforce_rate_limit(currently_playing_limiter, _client_key(request))


def require_related_tracks_rate_limit(request: Request) -> None:
    _enforce_rate_limit(related_tracks_limiter, _client_key(request))


def require_cron_rate_limit(request: Request, authorization: str = Header(None)) -> None:
    """Throttle cron attempts, with a separate budget once authenticated.

    The limiter necessarily runs before the endpoint's own auth check, so a
    single tiny budget would let anyone starve the real cron job -- more so
    behind a proxy, where every caller can collapse onto one client key.
    Requests carrying a valid CRON_SECRET are counted against their own
    (larger) bucket, so unauthenticated floods can't consume the legitimate
    cron's allowance.
    """
    if cron_token_is_valid(authorization):
        _enforce_rate_limit(cron_authenticated_limiter, _client_key(request))
        return
    _enforce_rate_limit(cron_limiter, _client_key(request))
