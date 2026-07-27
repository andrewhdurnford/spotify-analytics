from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import require_general_rate_limit, require_related_tracks_rate_limit
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
from ..params import (
    MAX_ALBUM_NAME_LENGTH,
    MAX_LIMIT,
    MAX_NAME_LENGTH,
    MAX_SPOTIFY_ID_LENGTH,
    MIN_LIMIT,
    Timeframe,
    parse_date_param,
)

router = APIRouter()


def _spotify_id_path() -> Any:
    """A fresh Path(...) bound per parameter -- FastAPI mutates the FieldInfo
    it's given to bind it to a specific parameter name, so a single shared
    instance can't be reused across different path parameters.
    """
    return Path(..., min_length=1, max_length=MAX_SPOTIFY_ID_LENGTH)


@router.get(
    "/artists/{artist_id}",
    response_model=ArtistDetailResponse,
    dependencies=[Depends(require_general_rate_limit)],
)
def get_artist_detail(
    artist_id: str = _spotify_id_path(),
    timeframe: Timeframe = "all",
    date: str = Query(None),
    limit: int = Query(10, ge=MIN_LIMIT, le=MAX_LIMIT),
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


@router.get(
    "/albums/by-name",
    response_model=AlbumDetailResponse,
    dependencies=[Depends(require_general_rate_limit)],
)
def get_album_detail_by_name(
    artist_id: str = Query(..., min_length=1, max_length=MAX_SPOTIFY_ID_LENGTH),
    album_name: str = Query(..., min_length=1, max_length=MAX_ALBUM_NAME_LENGTH),
    timeframe: Timeframe = "all",
    date: str = Query(None),
    limit: int = Query(25, ge=MIN_LIMIT, le=MAX_LIMIT),
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


@router.get(
    "/tracks/by-name",
    response_model=TrackDetailResponse,
    dependencies=[Depends(require_general_rate_limit)],
)
def get_track_detail_by_name(
    track_name: str = Query(..., min_length=1, max_length=MAX_NAME_LENGTH),
    artist_name: str = Query(..., min_length=1, max_length=MAX_NAME_LENGTH),
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


@router.get(
    "/tracks/{track_id}",
    response_model=TrackDetailResponse,
    dependencies=[Depends(require_general_rate_limit)],
)
def get_track_detail(
    track_id: str = _spotify_id_path(),
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


@router.get(
    "/tracks/{track_id}/related",
    response_model=RelatedTracksResponse,
    dependencies=[Depends(require_related_tracks_rate_limit)],
)
def get_related_tracks(
    track_id: str = _spotify_id_path(),
    limit: int = Query(20, ge=MIN_LIMIT, le=MAX_LIMIT),
    db: Session = Depends(get_db),
):
    """Get tracks frequently played in the same listening session as this one.

    Uses session-based co-occurrence over all-time history. Sessions are
    contiguous runs of plays with no gap > 30 min. Results are scored with
    cosine similarity over session-membership and require at least 3
    co-occurring sessions to surface.

    Results are cached (keyed by track_id + limit) for
    `RELATED_TRACKS_TTL_SECONDS` since the underlying computation walks the
    full session history on every call. Misses (unknown track_id -> 404)
    aren't cached, so bogus IDs can't occupy slots in the shared pool.
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
