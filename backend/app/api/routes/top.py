from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_spotify_client
from ...schemas import (
    AvailablePeriods,
    CurrentlyPlaying,
    ListeningStats,
    RecentlyPlayedResponse,
    TopAlbum,
    TopArtist,
    TopSong,
)
from ...services import top as top_service
from ..params import Timeframe, parse_date_param

router = APIRouter()


@router.get("/songs", response_model=list[TopSong])
def get_top_songs(
    timeframe: Timeframe = "all",
    date: str = Query(None),
    limit: int = 10,
    db: Session = Depends(get_db),
):
    base_date = parse_date_param(date)
    return top_service.get_top_songs(db, timeframe, base_date, limit)


@router.get("/artists", response_model=list[TopArtist])
def get_top_artists(
    timeframe: Timeframe = "all",
    date: str = Query(None),
    limit: int = 10,
    db: Session = Depends(get_db),
):
    base_date = parse_date_param(date)
    return top_service.get_top_artists(db, timeframe, base_date, limit)


@router.get("/albums", response_model=list[TopAlbum])
def get_top_albums(
    timeframe: Timeframe = "all",
    date: str = Query(None),
    limit: int = 10,
    db: Session = Depends(get_db),
):
    base_date = parse_date_param(date)
    return top_service.get_top_albums(db, timeframe, base_date, limit)


@router.get("/stats", response_model=ListeningStats)
def get_listening_stats(
    timeframe: Timeframe = "all",
    date: str = Query(None),
    db: Session = Depends(get_db),
):
    base_date = parse_date_param(date)
    return top_service.get_listening_stats(db, timeframe, base_date)


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
    parsed_start_date = parse_date_param(start_date) if start_date else None
    return top_service.get_recently_played(db, parsed_start_date, page, page_size)


@router.get("/currently-playing", response_model=CurrentlyPlaying)
async def get_currently_playing(
    spotify_service=Depends(get_spotify_client),
):
    currently_playing = await spotify_service.get_currently_playing()
    if currently_playing:
        return currently_playing if isinstance(currently_playing, dict) else currently_playing.model_dump()
    return {"is_playing": False}
