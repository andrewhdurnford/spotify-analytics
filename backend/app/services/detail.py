"""Service layer for artist and track detail pages.

Provides queries for individual artist/track metadata, play counts,
top songs/albums per artist, and monthly listening timelines.

Totals, rankings (top songs/albums/tracks), and timelines are rollup-backed
(read from `daily_track_stats`) rather than scanning raw `streams`. Simple
point lookups (existence checks, "grab one album image/artist name") stay
Stream-based since they're already cheap single-row lookups, not scans.
`get_related_tracks` needs individual play-event ordering for session
detection and is intentionally left Stream-based (rollups have no per-event
grain).
"""

import logging
import math
import time
from collections import defaultdict
from datetime import date as date_type
from datetime import datetime

from sqlalchemy import desc, extract, func, select
from sqlalchemy.orm import Session

from ..models import Artist, DailyTrackStats, Stream, Track
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
from .rollups import NO_ALBUM_SENTINEL
from .timezone import utc_naive_to_display
from .top import get_local_date_range

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


def _daily_track_stats_range_filter(start_date: date_type | None, end_date: date_type | None):
    if start_date and end_date:
        return (DailyTrackStats.date >= start_date, DailyTrackStats.date < end_date)
    return ()


def _build_rollup_timeline_query(filters: tuple, timeframe: str):
    """Build a daily (month timeframe) or monthly (otherwise) timeline query
    against daily_track_stats for the given filter clauses, and return
    (query, granularity)."""
    if timeframe == "month":
        stmt = (
            select(DailyTrackStats.date, func.sum(DailyTrackStats.play_count).label("play_count"))
            .where(*filters)
            .group_by(DailyTrackStats.date)
            .order_by(DailyTrackStats.date)
        )
        return stmt, "daily"
    else:
        year_col = extract("year", DailyTrackStats.date).label("yr")
        month_col = extract("month", DailyTrackStats.date).label("mo")
        stmt = (
            select(year_col, month_col, func.sum(DailyTrackStats.play_count).label("play_count"))
            .where(*filters)
            .group_by(year_col, month_col)
            .order_by(year_col, month_col)
        )
        return stmt, "monthly"


def _format_timeline_results(results, granularity: str) -> list[PlayCountOverTime]:
    """Format timeline query results into PlayCountOverTime objects."""
    if granularity == "daily":
        return [
            PlayCountOverTime(
                period=str(r.date),
                play_count=int(r.play_count),
            )
            for r in results
        ]
    else:
        return [
            PlayCountOverTime(
                period=f"{int(r.yr)}-{int(r.mo):02d}",
                play_count=int(r.play_count),
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

    start_date, end_date = get_local_date_range(timeframe, base_date)
    range_filter = _daily_track_stats_range_filter(start_date, end_date)
    artist_filter = (DailyTrackStats.artist_id == artist_id, *range_filter)

    # Total play count (filtered by timeframe)
    total_play_count = db.execute(
        select(func.coalesce(func.sum(DailyTrackStats.play_count), 0)).where(*artist_filter)
    ).scalar_one()

    # Top songs for this artist
    song_counts_with_id = (
        select(
            DailyTrackStats.track_key,
            func.max(DailyTrackStats.track_name).label("track_name"),
            func.max(DailyTrackStats.track_id).label("track_id"),
            func.sum(DailyTrackStats.play_count).label("play_count"),
        )
        .where(*artist_filter)
        .group_by(DailyTrackStats.track_key)
        .order_by(desc("play_count"))
        .limit(limit)
        .subquery()
    )
    songs_query = (
        select(
            song_counts_with_id.c.track_name,
            song_counts_with_id.c.track_id,
            Track.album_image,
            song_counts_with_id.c.play_count,
        )
        .outerjoin(Track, song_counts_with_id.c.track_id == Track.spotify_id)
        .order_by(desc(song_counts_with_id.c.play_count))
    )

    song_results = db.execute(songs_query).all()
    top_songs = [
        TopSong(
            track_name=r.track_name,
            artist_name=artist.name,
            track_id=r.track_id,
            artist_id=artist_id,
            album_image=r.album_image,
            play_count=int(r.play_count),
        )
        for r in song_results
    ]

    # Top albums for this artist
    album_counts = (
        select(
            DailyTrackStats.album_key,
            func.max(DailyTrackStats.track_id).label("track_id"),
            func.sum(DailyTrackStats.play_count).label("play_count"),
        )
        .where(*artist_filter)
        .group_by(DailyTrackStats.album_key)
        .order_by(desc("play_count"))
        .limit(limit)
        .subquery()
    )
    album_query = (
        select(
            album_counts.c.album_key,
            Track.album_image,
            album_counts.c.play_count,
        )
        .outerjoin(Track, album_counts.c.track_id == Track.spotify_id)
        .order_by(desc(album_counts.c.play_count))
    )

    album_results = db.execute(album_query).all()
    top_albums = [
        TopAlbum(
            album_name=r.album_key if r.album_key != NO_ALBUM_SENTINEL else None,
            artist_name=artist.name,
            artist_id=artist_id,
            album_image=r.album_image,
            play_count=int(r.play_count),
        )
        for r in album_results
    ]

    # Listening timeline (daily for month, monthly otherwise)
    timeline_query, granularity = _build_rollup_timeline_query(artist_filter, timeframe)
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
            total_play_count=int(total_play_count),
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

    artist_name = (
        db.execute(select(Stream.artist_name).where(*base_filter).limit(1)).scalar_one_or_none()
        or ""
    )

    album_image = db.execute(
        select(Track.album_image)
        .join(Stream, Stream.track_id == Track.spotify_id)
        .where(*base_filter, Track.album_image.isnot(None))
        .limit(1)
    ).scalar_one_or_none()

    start_date, end_date = get_local_date_range(timeframe, base_date)
    range_filter = _daily_track_stats_range_filter(start_date, end_date)
    album_filter = (
        DailyTrackStats.artist_id == artist_id,
        DailyTrackStats.album_key == album_name,
        *range_filter,
    )

    total_play_count = db.execute(
        select(func.coalesce(func.sum(DailyTrackStats.play_count), 0)).where(*album_filter)
    ).scalar_one()

    unique_tracks_played = db.execute(
        select(func.count(func.distinct(DailyTrackStats.track_key))).where(*album_filter)
    ).scalar_one()

    # Top tracks on this album
    track_counts = (
        select(
            DailyTrackStats.track_key,
            func.max(DailyTrackStats.track_name).label("track_name"),
            func.max(DailyTrackStats.track_id).label("track_id"),
            func.sum(DailyTrackStats.play_count).label("play_count"),
        )
        .where(*album_filter)
        .group_by(DailyTrackStats.track_key)
        .order_by(desc("play_count"))
        .limit(limit)
        .subquery()
    )
    tracks_query = (
        select(
            track_counts.c.track_name,
            track_counts.c.track_id,
            Track.album_image,
            track_counts.c.play_count,
        )
        .outerjoin(Track, track_counts.c.track_id == Track.spotify_id)
        .order_by(desc(track_counts.c.play_count))
    )

    track_results = db.execute(tracks_query).all()
    top_tracks = [
        TopSong(
            track_name=r.track_name,
            artist_name=artist_name,
            track_id=r.track_id,
            artist_id=artist_id,
            album_image=r.album_image,
            play_count=int(r.play_count),
        )
        for r in track_results
    ]

    # Timeline (filter on both artist_id and album_name)
    timeline_query, granularity = _build_rollup_timeline_query(album_filter, timeframe)
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
            total_play_count=int(total_play_count),
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

    start_date, end_date = get_local_date_range(timeframe, base_date)
    range_filter = _daily_track_stats_range_filter(start_date, end_date)
    track_filter = (DailyTrackStats.track_id == track_id, *range_filter)

    # Play count (filtered by timeframe)
    total_play_count = db.execute(
        select(func.coalesce(func.sum(DailyTrackStats.play_count), 0)).where(*track_filter)
    ).scalar_one()

    # Get artist_id from streams for this track
    artist_id_result = db.execute(
        select(Stream.artist_id)
        .where(Stream.track_id == track_id)
        .where(Stream.artist_id.isnot(None))
        .limit(1)
    ).scalar_one_or_none()

    # Listening timeline (daily for month, monthly otherwise)
    timeline_query, granularity = _build_rollup_timeline_query(track_filter, timeframe)
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
            total_play_count=int(total_play_count),
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

    start_date, end_date = get_local_date_range(timeframe, base_date)
    range_filter = _daily_track_stats_range_filter(start_date, end_date)

    # Check the track exists (all-time, not date-filtered — a brand-new page
    # for an old track should still resolve even if the current timeframe
    # has no plays).
    exists_stmt = (
        select(DailyTrackStats.id)
        .where(DailyTrackStats.track_name == track_name, DailyTrackStats.artist_name == artist_name)
        .limit(1)
    )
    if not db.execute(exists_stmt).first():
        return None

    name_filter = (
        DailyTrackStats.track_name == track_name,
        DailyTrackStats.artist_name == artist_name,
        *range_filter,
    )

    total_play_count = db.execute(
        select(func.coalesce(func.sum(DailyTrackStats.play_count), 0)).where(*name_filter)
    ).scalar_one()

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
    timeline_query, granularity = _build_rollup_timeline_query(name_filter, timeframe)
    timeline_results = db.execute(timeline_query).all()
    timeline = _format_timeline_results(timeline_results, granularity)

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
            total_play_count=int(total_play_count),
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

    Deliberately not rollup-backed: session detection needs individual play
    timestamps, which daily_track_stats doesn't retain.
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
