from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...database import get_db
from ...schemas import (
    AlbumDetailResponse,
    ArtistDetailResponse,
    RelatedTracksResponse,
    TrackDetailResponse,
)
from ...services import detail as detail_service
from ...services.dashboard_cache import (
    RELATED_TRACKS_TTL_SECONDS,
    cache_window_key,
    dashboard_cache,
    detail_ttl,
)
from ..params import Timeframe, parse_date_param

router = APIRouter()


@router.get("/artists/{artist_id}", response_model=ArtistDetailResponse)
def get_artist_detail(
    artist_id: str,
    timeframe: Timeframe = "all",
    date: str = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Get detailed information for a single artist."""
    base_date = parse_date_param(date)
    key = (
        "artist-detail",
        artist_id,
        timeframe,
        cache_window_key(timeframe, base_date, date),
        limit,
    )
    result = dashboard_cache.get_or_set(
        key,
        detail_ttl(timeframe),
        lambda: detail_service.get_artist_detail(
            db,
            artist_id,
            timeframe,
            base_date,
            limit,
        ),
        cache_none=False,
    )
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


@router.get("/albums/by-name", response_model=AlbumDetailResponse)
def get_album_detail_by_name(
    artist_id: str = Query(...),
    album_name: str = Query(...),
    timeframe: Timeframe = "all",
    date: str = Query(None),
    limit: int = Query(25, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Get detailed information for an album, keyed by (artist_id, album_name)."""
    base_date = parse_date_param(date)
    key = (
        "album-detail",
        artist_id,
        album_name,
        timeframe,
        cache_window_key(timeframe, base_date, date),
        limit,
    )
    result = dashboard_cache.get_or_set(
        key,
        detail_ttl(timeframe),
        lambda: detail_service.get_album_detail(
            db, artist_id, album_name, timeframe, base_date, limit
        ),
        cache_none=False,
    )
    if not result:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "ALBUM_NOT_FOUND",
                    "message": f"Album '{album_name}' for artist '{artist_id}' not found.",
                }
            },
        )
    return result


@router.get("/tracks/by-name", response_model=TrackDetailResponse)
def get_track_detail_by_name(
    track_name: str = Query(...),
    artist_name: str = Query(...),
    timeframe: Timeframe = "all",
    date: str = Query(None),
    db: Session = Depends(get_db),
):
    """Get detailed information for a track by name + artist (fallback for tracks without Spotify ID)."""
    base_date = parse_date_param(date)
    key = (
        "track-detail-by-name",
        track_name,
        artist_name,
        timeframe,
        cache_window_key(timeframe, base_date, date),
    )
    result = dashboard_cache.get_or_set(
        key,
        detail_ttl(timeframe),
        lambda: detail_service.get_track_detail_by_name(
            db, track_name, artist_name, timeframe, base_date
        ),
        cache_none=False,
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
    timeframe: Timeframe = "all",
    date: str = Query(None),
    db: Session = Depends(get_db),
):
    """Get detailed information for a single track."""
    base_date = parse_date_param(date)
    key = (
        "track-detail",
        track_id,
        timeframe,
        cache_window_key(timeframe, base_date, date),
    )
    result = dashboard_cache.get_or_set(
        key,
        detail_ttl(timeframe),
        lambda: detail_service.get_track_detail(db, track_id, timeframe, base_date),
        cache_none=False,
    )
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


@router.get("/tracks/{track_id}/related", response_model=RelatedTracksResponse)
def get_related_tracks(
    track_id: str,
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Get tracks frequently played in the same listening session as this one.

    Uses session-based co-occurrence over all-time history. Sessions are
    contiguous runs of plays with no gap > 30 min. Results are scored with
    cosine similarity over session-membership and require at least 3
    co-occurring sessions to surface.
    """
    key = ("related-tracks", track_id, limit)
    result = dashboard_cache.get_or_set(
        key,
        RELATED_TRACKS_TTL_SECONDS,
        lambda: detail_service.get_related_tracks(db, track_id, limit=limit),
        cache_none=False,
    )
    if result is None:
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
