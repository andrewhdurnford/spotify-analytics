"""Shared request parameter helpers for API routes."""

from datetime import datetime, timedelta
from typing import Literal

from fastapi import HTTPException

Timeframe = Literal["all", "year", "quarter", "month", "week", "day"]

# Bounds for the `date` query parameter. Spotify launched in 2008 and this
# app only ever ingests historical/near-real-time listening data, so dates
# outside this range are always the result of a bad/malicious client rather
# than real data.
MIN_ALLOWED_DATE = datetime(2000, 1, 1)
FUTURE_DATE_GRACE = timedelta(days=1)

# Server-side bounds shared across route parameter declarations.
MAX_LIMIT = 50
MIN_LIMIT = 1
MAX_SPOTIFY_ID_LENGTH = 100
MAX_NAME_LENGTH = 255
MAX_ALBUM_NAME_LENGTH = 512


def _invalid_date(date: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "error": {
                "code": "INVALID_DATE",
                "message": message,
            }
        },
    )


def parse_date_param(date: str | None, *, now: datetime | None = None) -> datetime:
    """Parse an ISO date query parameter into a datetime.

    Rejects dates that don't parse, are unreasonably old, or are in the
    future (beyond a small grace window for client/server clock skew).
    """
    if not date:
        return now or datetime.now()

    try:
        parsed = datetime.fromisoformat(date)
    except (ValueError, TypeError) as exc:
        raise _invalid_date(
            date,
            f"Invalid date format: '{date}'. Expected ISO 8601 format (e.g. 2025-01-01).",
        ) from exc

    reference_now = now or datetime.now()
    if parsed < MIN_ALLOWED_DATE:
        raise _invalid_date(date, f"Date '{date}' is too far in the past to be valid.")
    if parsed > reference_now + FUTURE_DATE_GRACE:
        raise _invalid_date(date, f"Date '{date}' is in the future.")

    return parsed
