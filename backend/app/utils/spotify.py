import os
import base64
import httpx
import logging

logger = logging.getLogger(__name__)


def fetch_spotify_token_from_client_credentials() -> str | None:
    """Fetch an app access token via Client Credentials flow (expires ~1h).

    Uses *httpx* (sync mode) so the project doesn't need *requests* at all.
    """
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        resp = httpx.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception:
        logger.exception("Failed to fetch Spotify client-credentials token")
        return None
