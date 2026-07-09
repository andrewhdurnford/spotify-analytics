"""Service layer for artist and track detail pages.

Provides queries for individual artist/track metadata, play counts,
top songs/albums per artist, and monthly listening timelines.
"""

import logging
import math
import time
from collections import defaultdict
from datetime import datetime

from sqlalchemy import desc, extract, func, select
from sqlalchemy.orm import Session

from ..models import Artist, Stream, Track
from ..schemas import (
    AlbumDetail,
    AlbumDetailResponse,
    ArtistDetail,
    ArtistDetailResponse,
    AvailablePeriods,
    PlayCountOverTime,
    RelatedTrack,
    RelatedTracksResponse,
    TopAlbum,
    TopSong,
    TrackDetail,
    TrackDetailResponse,
)
from .timezone import display_timestamp, utc_naive_to_display
from .top import get_date_range

logger = logging.getLogger(__name__)

# Session = consecutive plays with no gap > this threshold.
SESSION_GAP_SECONDS = 30 * 60
# Minimum number of co-occurring sessions for a related track to surface.
DEFAULT_MIN_SUPPORT = 3


def _get_available_periods(db: Session, *filters) -> AvailablePeriods:
    """Min/max played_at across streams matching the given filters."""
    stmt = select(
        func.min(Stream.played_at).label("earliest"),
        func.max(Stream.played_at).label("latest"),
    ).where(*filters)
    row = db.execute(stmt).first()
    if not row or not row.earliest:
        return AvailablePeriods(years=[], earliest_date=None, latest_date=None)
    earliest = utc_naive_to_display(row.earliest)
    latest = utc_naive_to_display(row.latest)
    years = list(range(earliest.year, latest.year + 1))
    return AvailablePeriods(
        years=years,
        earliest_date=earliest.isoformat(),
        latest_date=latest.isoformat(),
    )


def _build_monthly_timeline_query(
    filter_col,
    filter_val: str,
    start: datetime | None,
    end: datetime | None,
):
    """Build a monthly play-count timeline query for a given filter."""
    local_played_at = display_timestamp(Stream.played_at)
    year_col = extract("year", local_played_at).label("yr")
    month_col = extract("month", local_played_at).label("mo")

    stmt = select(
        year_col,
        month_col,
        func.count(Stream.id).label("play_count"),
    ).where(filter_col == filter_val)

    if start and end:
        stmt = stmt.where(Stream.played_at >= start, Stream.played_at < end)

    return stmt.group_by(year_col, month_col).order_by(year_col, month_col)


def _build_daily_timeline_query(
    filter_col,
    filter_val: str,
    start: datetime | None,
    end: datetime | None,
):
    """Build a daily play-count timeline query for a given filter."""
    date_col = func.date(display_timestamp(Stream.played_at)).label("day")

    stmt = select(
        date_col,
        func.count(Stream.id).label("play_count"),
    ).where(filter_col == filter_val)

    if start and end:
        stmt = stmt.where(Stream.played_at >= start, Stream.played_at < end)

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
        count_stmt = count_stmt.where(Stream.played_at >= start, Stream.played_at < end)
    total_play_count = db.execute(count_stmt).scalar() or 0

    # Top songs for this artist
    song_group_key = func.coalesce(Stream.track_id, Stream.track_name).label("group_key")
    song_counts = select(
        song_group_key,
        func.count(Stream.id).label("play_count"),
    ).where(Stream.artist_id == artist_id)

    if start and end:
        song_counts = song_counts.where(Stream.played_at >= start, Stream.played_at < end)

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
        album_stmt = album_stmt.where(Stream.played_at >= start, Stream.played_at < end)

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
            artist_id=artist_id,
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

    available_periods = _get_available_periods(db, Stream.artist_id == artist_id)

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
        available_periods=available_periods,
    )


def get_album_detail(
    db: Session,
    artist_id: str,
    album_name: str,
    timeframe: str,
    base_date: datetime,
    limit: int,
) -> AlbumDetailResponse | None:
    """Get full detail for a single album, keyed by (artist_id, album_name)."""
    t0 = time.perf_counter()

    base_filter = [Stream.artist_id == artist_id, Stream.album_name == album_name]

    exists_stmt = select(Stream.id).where(*base_filter).limit(1)
    if not db.execute(exists_stmt).first():
        return None

    artist_name = db.execute(
        select(Stream.artist_name).where(*base_filter).limit(1)
    ).scalar_one_or_none() or ""

    album_image = db.execute(
        select(Track.album_image)
        .join(Stream, Stream.track_id == Track.spotify_id)
        .where(*base_filter, Track.album_image.isnot(None))
        .limit(1)
    ).scalar_one_or_none()

    start, end = get_date_range(timeframe, base_date)

    count_stmt = select(func.count(Stream.id)).where(*base_filter)
    if start and end:
        count_stmt = count_stmt.where(Stream.played_at >= start, Stream.played_at < end)
    total_play_count = db.execute(count_stmt).scalar() or 0

    unique_tracks_stmt = select(
        func.count(func.distinct(func.coalesce(Stream.track_id, Stream.track_name)))
    ).where(*base_filter)
    if start and end:
        unique_tracks_stmt = unique_tracks_stmt.where(
            Stream.played_at >= start, Stream.played_at < end
        )
    unique_tracks_played = db.execute(unique_tracks_stmt).scalar() or 0

    # Top tracks on this album
    track_group_key = func.coalesce(Stream.track_id, Stream.track_name).label("group_key")
    track_counts_stmt = select(
        track_group_key,
        func.count(Stream.id).label("play_count"),
    ).where(*base_filter)
    if start and end:
        track_counts_stmt = track_counts_stmt.where(
            Stream.played_at >= start, Stream.played_at < end
        )
    track_counts = (
        track_counts_stmt.group_by("group_key")
        .order_by(desc("play_count"))
        .limit(limit)
        .subquery()
    )

    tracks_query = (
        select(
            Stream.track_name,
            Stream.artist_name,
            Stream.track_id,
            Stream.artist_id,
            Track.album_image,
            track_counts.c.play_count,
        )
        .join(
            track_counts,
            func.coalesce(Stream.track_id, Stream.track_name) == track_counts.c.group_key,
        )
        .outerjoin(Track, Stream.track_id == Track.spotify_id)
        .where(*base_filter)
        .group_by(
            Stream.track_name,
            Stream.artist_name,
            Stream.track_id,
            Stream.artist_id,
            Track.album_image,
            track_counts.c.play_count,
        )
        .order_by(desc(track_counts.c.play_count))
    )

    track_results = db.execute(tracks_query).all()
    top_tracks = [
        TopSong(
            track_name=r.track_name,
            artist_name=r.artist_name,
            track_id=r.track_id,
            artist_id=r.artist_id,
            album_image=r.album_image,
            play_count=r.play_count,
        )
        for r in track_results
    ]

    # Timeline (filter on both artist_id and album_name)
    if timeframe == "month":
        date_col = func.date(display_timestamp(Stream.played_at)).label("day")
        timeline_stmt = select(date_col, func.count(Stream.id).label("play_count")).where(
            *base_filter
        )
        if start and end:
            timeline_stmt = timeline_stmt.where(Stream.played_at >= start, Stream.played_at < end)
        timeline_query = timeline_stmt.group_by(date_col).order_by(date_col)
        granularity = "daily"
    else:
        local_played_at = display_timestamp(Stream.played_at)
        year_col = extract("year", local_played_at).label("yr")
        month_col = extract("month", local_played_at).label("mo")
        timeline_stmt = select(
            year_col, month_col, func.count(Stream.id).label("play_count")
        ).where(*base_filter)
        if start and end:
            timeline_stmt = timeline_stmt.where(Stream.played_at >= start, Stream.played_at < end)
        timeline_query = timeline_stmt.group_by(year_col, month_col).order_by(year_col, month_col)
        granularity = "monthly"

    timeline_results = db.execute(timeline_query).all()
    timeline = _format_timeline_results(timeline_results, granularity)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        f"get_album_detail query: {elapsed_ms:.1f}ms "
        f"(artist_id={artist_id}, album={album_name})"
    )

    available_periods = _get_available_periods(db, *base_filter)

    return AlbumDetailResponse(
        album=AlbumDetail(
            album_name=album_name,
            artist_name=artist_name,
            artist_id=artist_id,
            album_image=album_image,
            total_play_count=total_play_count,
            unique_tracks_played=int(unique_tracks_played),
        ),
        top_tracks=top_tracks,
        timeline=timeline,
        granularity=granularity,
        available_periods=available_periods,
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
        count_stmt = count_stmt.where(Stream.played_at >= start, Stream.played_at < end)
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

    available_periods = _get_available_periods(db, Stream.track_id == track_id)

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
        available_periods=available_periods,
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
        count_stmt = count_stmt.where(Stream.played_at >= start, Stream.played_at < end)
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
        date_col = func.date(display_timestamp(Stream.played_at)).label("day")
        timeline_stmt = select(date_col, func.count(Stream.id).label("play_count")).where(
            Stream.track_name == track_name, Stream.artist_name == artist_name
        )
        if start and end:
            timeline_stmt = timeline_stmt.where(Stream.played_at >= start, Stream.played_at < end)
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
        local_played_at = display_timestamp(Stream.played_at)
        year_col = extract("year", local_played_at).label("yr")
        month_col = extract("month", local_played_at).label("mo")
        timeline_stmt = select(
            year_col, month_col, func.count(Stream.id).label("play_count")
        ).where(Stream.track_name == track_name, Stream.artist_name == artist_name)
        if start and end:
            timeline_stmt = timeline_stmt.where(Stream.played_at >= start, Stream.played_at < end)
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

    available_periods = _get_available_periods(
        db,
        Stream.track_name == track_name,
        Stream.artist_name == artist_name,
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
        available_periods=available_periods,
    )


def get_related_tracks(
    db: Session,
    track_id: str,
    limit: int = 20,
    min_support: int = DEFAULT_MIN_SUPPORT,
) -> RelatedTracksResponse | None:
    """Return tracks frequently played in the same listening session as the target.

    A "session" is a contiguous run of plays separated by gaps no larger than
    SESSION_GAP_SECONDS. Two tracks are co-occurring when they appear in the
    same session. Co-occurrence is counted per-session (not per-play), then
    scored with cosine similarity over session-membership:

        score(A, B) = |sessions(A) ∩ sessions(B)|
                      / sqrt(|sessions(A)| * |sessions(B)|)

    Tracks with fewer than `min_support` co-occurring sessions are dropped to
    avoid one-off noise.

    Returns None when the track is unknown (so the route can 404); returns an
    empty list when the track is known but has no qualifying related tracks.

    Note: Sessions are computed in Python over all streams on every call. This
    is fine for personal-scale libraries (sub-100ms for ~100k streams). If it
    becomes a hot path, materialize co-occurrence into a nightly job table.
    """
    t0 = time.perf_counter()

    track = db.execute(
        select(Track.spotify_id).where(Track.spotify_id == track_id)
    ).scalar_one_or_none()
    if not track:
        return None

    # Pull all stream events with a known track_id, ordered by time. Loose
    # streams (no track_id) can't be linked from related results anyway, and
    # excluding them avoids polluting session boundaries with noise.
    rows = db.execute(
        select(Stream.track_id, Stream.played_at)
        .where(Stream.track_id.isnot(None))
        .order_by(Stream.played_at)
    ).all()

    # Walk in time order, bumping the session id whenever the gap exceeds
    # SESSION_GAP_SECONDS, and record which sessions each track appears in.
    sessions_per_track: dict[str, set[int]] = defaultdict(set)
    session_id = 0
    prev_t: datetime | None = None
    for tid, played_at in rows:
        if prev_t is not None and (played_at - prev_t).total_seconds() > SESSION_GAP_SECONDS:
            session_id += 1
        sessions_per_track[tid].add(session_id)
        prev_t = played_at

    target_sessions = sessions_per_track.get(track_id, set())
    if not target_sessions:
        return RelatedTracksResponse(related=[])

    # Count co-occurring sessions and apply min_support filter.
    target_count = len(target_sessions)
    scored: list[tuple[str, int, float]] = []
    for other_tid, sids in sessions_per_track.items():
        if other_tid == track_id:
            continue
        cooc = len(sids & target_sessions)
        if cooc < min_support:
            continue
        score = cooc / math.sqrt(target_count * len(sids))
        scored.append((other_tid, cooc, score))

    if not scored:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            f"get_related_tracks query: {elapsed_ms:.1f}ms "
            f"(track_id={track_id}, n_streams={len(rows)}, results=0)"
        )
        return RelatedTracksResponse(related=[])

    scored.sort(key=lambda x: x[2], reverse=True)
    top = scored[:limit]
    top_ids = [tid for tid, _, _ in top]

    # Fetch display metadata from tracks table.
    meta_rows = db.execute(
        select(
            Track.spotify_id,
            Track.name,
            Track.artist_name,
            Track.album_image,
        ).where(Track.spotify_id.in_(top_ids))
    ).all()
    meta_by_id = {r.spotify_id: r for r in meta_rows}

    # Pull artist_id (and fallback name fields) from streams — Track has no
    # artist_id column.
    stream_rows = db.execute(
        select(
            Stream.track_id,
            Stream.artist_id,
            Stream.track_name,
            Stream.artist_name,
        )
        .where(Stream.track_id.in_(top_ids))
        .where(Stream.artist_id.isnot(None))
    ).all()
    stream_info: dict[str, tuple[str | None, str, str]] = {}
    for r in stream_rows:
        if r.track_id not in stream_info:
            stream_info[r.track_id] = (r.artist_id, r.track_name, r.artist_name)

    related: list[RelatedTrack] = []
    for tid, cooc, score in top:
        meta = meta_by_id.get(tid)
        sinfo = stream_info.get(tid)
        track_name = (meta.name if meta else None) or (sinfo[1] if sinfo else "Unknown")
        artist_name = (meta.artist_name if meta else None) or (sinfo[2] if sinfo else "")
        related.append(
            RelatedTrack(
                track_id=tid,
                track_name=track_name,
                artist_id=sinfo[0] if sinfo else None,
                artist_name=artist_name or "",
                album_image=meta.album_image if meta else None,
                cooc_sessions=cooc,
                cooc_rate=round(cooc / target_count, 4),
                score=round(score, 4),
            )
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        f"get_related_tracks query: {elapsed_ms:.1f}ms "
        f"(track_id={track_id}, n_streams={len(rows)}, results={len(related)})"
    )

    return RelatedTracksResponse(related=related)
