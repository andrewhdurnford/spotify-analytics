"""Shared request parameter helpers for API routes."""

from datetime import datetime
from typing import Literal

from fastapi import HTTPException

Timeframe = Literal["all", "year", "quarter", "month", "week", "day"]


def parse_date_param(date: str | None) -> datetime:
    """Parse an ISO date query parameter into a datetime."""
    if not date:
        return datetime.now()

    try:
        return datetime.fromisoformat(date)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_DATE",
                    "message": (
                        f"Invalid date format: '{date}'. "
                        "Expected ISO 8601 format (e.g. 2025-01-01)."
                    ),
                }
            },
        ) from exc
