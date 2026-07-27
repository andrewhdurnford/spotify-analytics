from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import (
    get_spotify_client,
    require_currently_playing_rate_limit,
    require_general_rate_limit,
)
from ...schemas import (
    AvailablePeriods,
    CurrentlyPlaying,
    ListeningStats,
    RecentlyPlayedResponse,
    TimelineResponse,
    TopAlbum,
    TopArtist,
    TopSong,
)
from ...services import top as top_service
from ...services.dashboard_cache import (
    ALL_TIME_TTL_SECONDS,
    CURRENTLY_PLAYING_TTL_SECONDS,
    RECENTLY_PLAYED_TTL_SECONDS,
    cache_window_key,
    dashboard_cache,
    dashboard_ttl,
)
from ..params import MAX_LIMIT, MIN_LIMIT, Timeframe, parse_date_param

router = APIRouter()


@router.get(
    "/songs",
    response_model=list[TopSong],
    dependencies=[Depends(require_general_rate_limit)],
)
def get_top_songs(
    timeframe: Timeframe = "all",
    date: str = Query(None),
    limit: int = Query(10, ge=MIN_LIMIT, le=MAX_LIMIT),
    db: Session = Depends(get_db),
):
    base_date = parse_date_param(date)
    key = ("songs", timeframe, cache_window_key(timeframe, base_date, date), limit)
    return dashboard_cache.get_or_set(
        key,
        dashboard_ttl(timeframe),
        lambda: top_service.get_top_songs(db, timeframe, base_date, limit),
    )


@router.get(
    "/artists",
    response_model=list[TopArtist],
    dependencies=[Depends(require_general_rate_limit)],
)
def get_top_artists(
    timeframe: Timeframe = "all",
    date: str = Query(None),
    limit: int = Query(10, ge=MIN_LIMIT, le=MAX_LIMIT),
    db: Session = Depends(get_db),
):
    base_date = parse_date_param(date)
    key = ("artists", timeframe, cache_window_key(timeframe, base_date, date), limit)
    return dashboard_cache.get_or_set(
        key,
        dashboard_ttl(timeframe),
        lambda: top_service.get_top_artists(db, timeframe, base_date, limit),
    )


@router.get(
    "/albums",
    response_model=list[TopAlbum],
    dependencies=[Depends(require_general_rate_limit)],
)
def get_top_albums(
    timeframe: Timeframe = "all",
    date: str = Query(None),
    limit: int = Query(10, ge=MIN_LIMIT, le=MAX_LIMIT),
    db: Session = Depends(get_db),
):
    base_date = parse_date_param(date)
    key = ("albums", timeframe, cache_window_key(timeframe, base_date, date), limit)
    return dashboard_cache.get_or_set(
        key,
        dashboard_ttl(timeframe),
        lambda: top_service.get_top_albums(db, timeframe, base_date, limit),
    )


@router.get(
    "/stats",
    response_model=ListeningStats,
    dependencies=[Depends(require_general_rate_limit)],
)
def get_listening_stats(
    timeframe: Timeframe = "all",
    date: str = Query(None),
    db: Session = Depends(get_db),
):
    base_date = parse_date_param(date)
    key = ("stats", timeframe, cache_window_key(timeframe, base_date, date))
    return dashboard_cache.get_or_set(
        key,
        dashboard_ttl(timeframe),
        lambda: top_service.get_listening_stats(db, timeframe, base_date),
    )


@router.get(
    "/timeline",
    response_model=TimelineResponse,
    dependencies=[Depends(require_general_rate_limit)],
)
def get_timeline(
    timeframe: Timeframe = "all",
    date: str = Query(None),
    db: Session = Depends(get_db),
):
    base_date = parse_date_param(date)
    key = ("timeline", timeframe, cache_window_key(timeframe, base_date, date))
    return dashboard_cache.get_or_set(
        key,
        dashboard_ttl(timeframe),
        lambda: top_service.get_timeline(db, timeframe, base_date),
    )


@router.get(
    "/available-periods",
    response_model=AvailablePeriods,
    dependencies=[Depends(require_general_rate_limit)],
)
def get_available_periods(db: Session = Depends(get_db)):
    """Get available years and date range for the dataset."""
    return dashboard_cache.get_or_set(
        ("available-periods",),
        ALL_TIME_TTL_SECONDS,
        lambda: top_service.get_available_periods(db),
    )


@router.get(
    "/recently-played",
    response_model=RecentlyPlayedResponse,
    dependencies=[Depends(require_general_rate_limit)],
)
def get_recently_played(
    start_date: str = Query(None),
    page: int = Query(1, ge=1, le=1_000),
    page_size: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
):
    parsed_start_date = parse_date_param(start_date) if start_date else None
    key = (
        "recently-played",
        parsed_start_date.isoformat() if parsed_start_date else None,
        page,
        page_size,
    )
    return dashboard_cache.get_or_set(
        key,
        RECENTLY_PLAYED_TTL_SECONDS,
        lambda: top_service.get_recently_played(db, parsed_start_date, page, page_size),
    )


@router.get(
    "/currently-playing",
    response_model=CurrentlyPlaying,
    dependencies=[Depends(require_currently_playing_rate_limit)],
)
async def get_currently_playing(
    spotify_service=Depends(get_spotify_client),
):
    async def _load():
        currently_playing = await spotify_service.get_currently_playing()
        if currently_playing:
            return (
                currently_playing
                if isinstance(currently_playing, dict)
                else currently_playing.model_dump()
            )
        return {"is_playing": False}

    return await dashboard_cache.get_or_set_async(
        ("currently-playing",),
        CURRENTLY_PLAYING_TTL_SECONDS,
        _load,
    )
