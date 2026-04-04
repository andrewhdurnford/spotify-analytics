from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Literal
from datetime import datetime

from ...database import get_db
from ...dependencies import get_spotify_client
from ...schemas import (
    AvailablePeriods,
    CurrentlyPlaying,
    RecentlyPlayedResponse,
    TopAlbum,
    TopArtist,
    TopSong,
)
from ...services import top as top_service

router = APIRouter()


def _parse_date(date: str | None) -> datetime:
    """Parse a date string, raising HTTPException 400 on invalid input."""
    if not date:
        return datetime.now()
    try:
        return datetime.fromisoformat(date)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_DATE",
                    "message": f"Invalid date format: '{date}'. Expected ISO 8601 format (e.g. 2025-01-01).",
                }
            },
        )


@router.get("/songs", response_model=list[TopSong])
def get_top_songs(
    timeframe: Literal["all", "year", "quarter", "month", "week", "day"] = "all",
    date: str = Query(None),
    limit: int = 10,
    db: Session = Depends(get_db),
):
    base_date = _parse_date(date)
    return top_service.get_top_songs(db, timeframe, base_date, limit)


@router.get("/artists", response_model=list[TopArtist])
def get_top_artists(
    timeframe: Literal["all", "year", "quarter", "month", "week", "day"] = "all",
    date: str = Query(None),
    limit: int = 10,
    db: Session = Depends(get_db),
):
    base_date = _parse_date(date)
    return top_service.get_top_artists(db, timeframe, base_date, limit)


@router.get("/albums", response_model=list[TopAlbum])
def get_top_albums(
    timeframe: Literal["all", "year", "quarter", "month", "week", "day"] = "all",
    date: str = Query(None),
    limit: int = 10,
    db: Session = Depends(get_db),
):
    base_date = _parse_date(date)
    return top_service.get_top_albums(db, timeframe, base_date, limit)


@router.get("/available-periods", response_model=AvailablePeriods)
def get_available_periods(db: Session = Depends(get_db)):
    """Get available years and date range for the dataset."""
    return top_service.get_available_periods(db)


@router.get("/recently-played", response_model=RecentlyPlayedResponse)
def get_recently_played(
    start_date: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    parsed_start_date = _parse_date(start_date) if start_date else None
    return top_service.get_recently_played(db, parsed_start_date, page, page_size)


@router.get("/currently-playing", response_model=CurrentlyPlaying)
async def get_currently_playing(
    spotify_service=Depends(get_spotify_client),
):
    currently_playing = await spotify_service.get_currently_playing()
    if currently_playing:
        return currently_playing if isinstance(currently_playing, dict) else currently_playing.model_dump()
    return {"is_playing": False}
