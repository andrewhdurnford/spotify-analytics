from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...database import get_db
from ...schemas import ArtistDetailResponse, TrackDetailResponse
from ...services import detail as detail_service

router = APIRouter()


def _parse_date(date: str | None) -> datetime:
    """Parse a date string, raising HTTPException 400 on invalid input."""
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


@router.get("/artists/{artist_id}", response_model=ArtistDetailResponse)
def get_artist_detail(
    artist_id: str,
    timeframe: Literal["all", "year", "quarter", "month", "week", "day"] = "all",
    date: str = Query(None),
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """Get detailed information for a single artist."""
    base_date = _parse_date(date)
    result = detail_service.get_artist_detail(db, artist_id, timeframe, base_date, limit)
    if not result:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "ARTIST_NOT_FOUND",
                    "message": f"Artist with ID '{artist_id}' not found.",
                }
            },
        )
    return result


@router.get("/tracks/by-name", response_model=TrackDetailResponse)
def get_track_detail_by_name(
    track_name: str = Query(...),
    artist_name: str = Query(...),
    timeframe: Literal["all", "year", "quarter", "month", "week", "day"] = "all",
    date: str = Query(None),
    db: Session = Depends(get_db),
):
    """Get detailed information for a track by name + artist (fallback for tracks without Spotify ID)."""
    base_date = _parse_date(date)
    result = detail_service.get_track_detail_by_name(
        db, track_name, artist_name, timeframe, base_date
    )
    if not result:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "TRACK_NOT_FOUND",
                    "message": f"Track '{track_name}' by '{artist_name}' not found.",
                }
            },
        )
    return result


@router.get("/tracks/{track_id}", response_model=TrackDetailResponse)
def get_track_detail(
    track_id: str,
    timeframe: Literal["all", "year", "quarter", "month", "week", "day"] = "all",
    date: str = Query(None),
    db: Session = Depends(get_db),
):
    """Get detailed information for a single track."""
    base_date = _parse_date(date)
    result = detail_service.get_track_detail(db, track_id, timeframe, base_date)
    if not result:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "TRACK_NOT_FOUND",
                    "message": f"Track with ID '{track_id}' not found.",
                }
            },
        )
    return result
