"""Service layer for artist and track detail pages.

Provides queries for individual artist/track metadata, play counts,
top songs/albums per artist, and monthly listening timelines.
"""

import logging
import time
from datetime import datetime

from sqlalchemy import desc, extract, func, select
from sqlalchemy.orm import Session

from ..models import Artist, Stream, Track
from ..schemas import (
    ArtistDetail,
    ArtistDetailResponse,
    PlayCountOverTime,
    TopAlbum,
    TopSong,
    TrackDetail,
    TrackDetailResponse,
)
from .top import get_date_range

logger = logging.getLogger(__name__)


def _build_monthly_timeline_query(
    filter_col,
    filter_val: str,
    start: datetime | None,
    end: datetime | None,
):
    """Build a monthly play-count timeline query for a given filter."""
    year_col = extract("year", Stream.played_at).label("yr")
    month_col = extract("month", Stream.played_at).label("mo")

    stmt = select(
        year_col,
        month_col,
        func.count(Stream.id).label("play_count"),
    ).where(filter_col == filter_val)

    if start and end:
        stmt = stmt.where(Stream.played_at.between(start, end))

    return stmt.group_by(year_col, month_col).order_by(year_col, month_col)


def _build_daily_timeline_query(
    filter_col,
    filter_val: str,
    start: datetime | None,
    end: datetime | None,
):
    """Build a daily play-count timeline query for a given filter."""
    date_col = func.date(Stream.played_at).label("day")

    stmt = select(
        date_col,
        func.count(Stream.id).label("play_count"),
    ).where(filter_col == filter_val)

    if start and end:
        stmt = stmt.where(Stream.played_at.between(start, end))

    return stmt.group_by(date_col).order_by(date_col)


def _build_timeline(filter_col, filter_val: str, timeframe: str, start, end):
    """Build timeline data and return (timeline_list, granularity)."""
    if timeframe == "month":
        query = _build_daily_timeline_query(filter_col, filter_val, start, end)
        return query, "daily"
    else:
        query = _build_monthly_timeline_query(filter_col, filter_val, start, end)
        return query, "monthly"


def _format_timeline_results(results, granularity: str) -> list[PlayCountOverTime]:
    """Format timeline query results into PlayCountOverTime objects."""
    if granularity == "daily":
        return [
            PlayCountOverTime(
                period=str(r.day),
                play_count=r.play_count,
            )
            for r in results
        ]
    else:
        return [
            PlayCountOverTime(
                period=f"{int(r.yr)}-{int(r.mo):02d}",
                play_count=r.play_count,
            )
            for r in results
        ]


def get_artist_detail(
    db: Session,
    artist_id: str,
    timeframe: str,
    base_date: datetime,
    limit: int,
) -> ArtistDetailResponse | None:
    """Get full detail for a single artist by Spotify ID."""
    t0 = time.perf_counter()

    # Look up artist metadata
    artist = db.execute(select(Artist).where(Artist.spotify_id == artist_id)).scalar_one_or_none()

    if not artist:
        return None

    start, end = get_date_range(timeframe, base_date)

    # Total play count (filtered by timeframe)
    count_stmt = select(func.count(Stream.id)).where(Stream.artist_id == artist_id)
    if start and end:
        count_stmt = count_stmt.where(Stream.played_at.between(start, end))
    total_play_count = db.execute(count_stmt).scalar() or 0

    # Top songs for this artist
    song_group_key = func.coalesce(Stream.track_id, Stream.track_name).label("group_key")
    song_counts = select(
        song_group_key,
        func.count(Stream.id).label("play_count"),
    ).where(Stream.artist_id == artist_id)

    if start and end:
        song_counts = song_counts.where(Stream.played_at.between(start, end))

    song_counts = (
        song_counts.group_by("group_key").order_by(desc("play_count")).limit(limit).subquery()
    )

    songs_query = (
        select(
            Stream.track_name,
            Stream.artist_name,
            Stream.track_id,
            Stream.artist_id,
            Track.album_image,
            song_counts.c.play_count,
        )
        .join(
            song_counts,
            func.coalesce(Stream.track_id, Stream.track_name) == song_counts.c.group_key,
        )
        .outerjoin(Track, Stream.track_id == Track.spotify_id)
        .where(Stream.artist_id == artist_id)
        .group_by(
            Stream.track_name,
            Stream.artist_name,
            Stream.track_id,
            Stream.artist_id,
            Track.album_image,
            song_counts.c.play_count,
        )
        .order_by(desc(song_counts.c.play_count))
    )

    song_results = db.execute(songs_query).all()
    top_songs = [
        TopSong(
            track_name=r.track_name,
            artist_name=r.artist_name,
            track_id=r.track_id,
            artist_id=r.artist_id,
            album_image=r.album_image,
            play_count=r.play_count,
        )
        for r in song_results
    ]

    # Top albums for this artist
    album_stmt = (
        select(
            Stream.album_name,
            Stream.artist_name,
            Track.album_image,
            func.count(Stream.id).label("play_count"),
        )
        .outerjoin(Track, Stream.track_id == Track.spotify_id)
        .where(Stream.artist_id == artist_id)
    )

    if start and end:
        album_stmt = album_stmt.where(Stream.played_at.between(start, end))

    album_query = (
        album_stmt.group_by(Stream.album_name, Stream.artist_name, Track.album_image)
        .order_by(desc("play_count"))
        .limit(limit)
    )

    album_results = db.execute(album_query).all()
    top_albums = [
        TopAlbum(
            album_name=r.album_name,
            artist_name=r.artist_name,
            album_image=r.album_image,
            play_count=r.play_count,
        )
        for r in album_results
    ]

    # Listening timeline (daily for month, monthly otherwise)
    timeline_query, granularity = _build_timeline(
        Stream.artist_id, artist_id, timeframe, start, end
    )
    timeline_results = db.execute(timeline_query).all()
    timeline = _format_timeline_results(timeline_results, granularity)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"get_artist_detail query: {elapsed_ms:.1f}ms (artist_id={artist_id})")

    return ArtistDetailResponse(
        artist=ArtistDetail(
            spotify_id=artist.spotify_id,
            name=artist.name,
            image_url=artist.image_url,
            genres=artist.genres or [],
            popularity=artist.popularity,
            total_play_count=total_play_count,
        ),
        top_songs=top_songs,
        top_albums=top_albums,
        timeline=timeline,
        granularity=granularity,
    )


def get_track_detail(
    db: Session,
    track_id: str,
    timeframe: str,
    base_date: datetime,
) -> TrackDetailResponse | None:
    """Get full detail for a single track by Spotify ID."""
    t0 = time.perf_counter()

    # Look up track metadata
    track = db.execute(select(Track).where(Track.spotify_id == track_id)).scalar_one_or_none()

    if not track:
        return None

    start, end = get_date_range(timeframe, base_date)

    # Play count (filtered by timeframe)
    count_stmt = select(func.count(Stream.id)).where(Stream.track_id == track_id)
    if start and end:
        count_stmt = count_stmt.where(Stream.played_at.between(start, end))
    total_play_count = db.execute(count_stmt).scalar() or 0

    # Get artist_id from streams for this track
    artist_id_result = db.execute(
        select(Stream.artist_id)
        .where(Stream.track_id == track_id)
        .where(Stream.artist_id.isnot(None))
        .limit(1)
    ).scalar_one_or_none()

    # Listening timeline (daily for month, monthly otherwise)
    timeline_query, granularity = _build_timeline(Stream.track_id, track_id, timeframe, start, end)
    timeline_results = db.execute(timeline_query).all()
    timeline = _format_timeline_results(timeline_results, granularity)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"get_track_detail query: {elapsed_ms:.1f}ms (track_id={track_id})")

    return TrackDetailResponse(
        track=TrackDetail(
            spotify_id=track.spotify_id,
            name=track.name,
            artist_name=track.artist_name or "",
            artist_id=artist_id_result,
            album_name=track.album_name,
            album_image=track.album_image,
            duration_ms=track.duration_ms,
            total_play_count=total_play_count,
        ),
        timeline=timeline,
        granularity=granularity,
    )


def get_track_detail_by_name(
    db: Session,
    track_name: str,
    artist_name: str,
    timeframe: str,
    base_date: datetime,
) -> TrackDetailResponse | None:
    """Get detail for a track by name + artist (fallback when no Spotify ID)."""
    t0 = time.perf_counter()

    start, end = get_date_range(timeframe, base_date)

    # Check the track exists in streams
    exists_stmt = (
        select(Stream.id)
        .where(Stream.track_name == track_name, Stream.artist_name == artist_name)
        .limit(1)
    )
    if not db.execute(exists_stmt).first():
        return None

    # Play count
    count_stmt = select(func.count(Stream.id)).where(
        Stream.track_name == track_name,
        Stream.artist_name == artist_name,
    )
    if start and end:
        count_stmt = count_stmt.where(Stream.played_at.between(start, end))
    total_play_count = db.execute(count_stmt).scalar() or 0

    # Try to get album image from tracks table
    album_info = db.execute(
        select(Track.album_image, Track.album_name, Track.duration_ms, Track.spotify_id)
        .join(Stream, Stream.track_id == Track.spotify_id)
        .where(Stream.track_name == track_name, Stream.artist_name == artist_name)
        .limit(1)
    ).first()

    # Get artist_id from streams
    artist_id_result = db.execute(
        select(Stream.artist_id)
        .where(
            Stream.track_name == track_name,
            Stream.artist_name == artist_name,
            Stream.artist_id.isnot(None),
        )
        .limit(1)
    ).scalar_one_or_none()

    # Listening timeline (daily for month, monthly otherwise)
    granularity = "daily" if timeframe == "month" else "monthly"
    if granularity == "daily":
        date_col = func.date(Stream.played_at).label("day")
        timeline_stmt = select(date_col, func.count(Stream.id).label("play_count")).where(
            Stream.track_name == track_name, Stream.artist_name == artist_name
        )
        if start and end:
            timeline_stmt = timeline_stmt.where(Stream.played_at.between(start, end))
        timeline_query = timeline_stmt.group_by(date_col).order_by(date_col)
        timeline_results = db.execute(timeline_query).all()
        timeline = [
            PlayCountOverTime(
                period=str(r.day),
                play_count=r.play_count,
            )
            for r in timeline_results
        ]
    else:
        year_col = extract("year", Stream.played_at).label("yr")
        month_col = extract("month", Stream.played_at).label("mo")
        timeline_stmt = select(
            year_col, month_col, func.count(Stream.id).label("play_count")
        ).where(Stream.track_name == track_name, Stream.artist_name == artist_name)
        if start and end:
            timeline_stmt = timeline_stmt.where(Stream.played_at.between(start, end))
        timeline_query = timeline_stmt.group_by(year_col, month_col).order_by(year_col, month_col)
        timeline_results = db.execute(timeline_query).all()
        timeline = [
            PlayCountOverTime(
                period=f"{int(r.yr)}-{int(r.mo):02d}",
                play_count=r.play_count,
            )
            for r in timeline_results
        ]

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        f"get_track_detail_by_name query: {elapsed_ms:.1f}ms "
        f"(track={track_name}, artist={artist_name})"
    )

    return TrackDetailResponse(
        track=TrackDetail(
            spotify_id=album_info.spotify_id if album_info else None,
            name=track_name,
            artist_name=artist_name,
            artist_id=artist_id_result,
            album_name=album_info.album_name if album_info else None,
            album_image=album_info.album_image if album_info else None,
            duration_ms=album_info.duration_ms if album_info else None,
            total_play_count=total_play_count,
        ),
        timeline=timeline,
        granularity=granularity,
    )
