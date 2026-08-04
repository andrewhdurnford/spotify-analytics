"""Service layer for top-items queries.

Keeps SQLAlchemy query logic out of route handlers so routes only deal with
request parsing and response serialisation.
"""

import logging
import time
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import desc, extract, func, select
from sqlalchemy.orm import Session

from ..models import Artist, DailyListeningStats, DailyTrackStats, Stream, Track
from ..schemas import (
    AvailablePeriods,
    ListeningStats,
    PlayCountOverTime,
    RecentlyPlayedItem,
    RecentlyPlayedResponse,
    TimelineResponse,
    TopAlbum,
    TopArtist,
    TopSong,
)
from .rollups import NO_ALBUM_SENTINEL
from .timezone import display_naive_to_utc, utc_naive_to_display

logger = logging.getLogger(__name__)


def _to_iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def get_date_range(timeframe: str, base_date: datetime) -> tuple[datetime | None, datetime | None]:
    """Return UTC storage boundaries for a Toronto calendar timeframe."""
    if timeframe == "all":
        return None, None

    if timeframe == "day":
        start = base_date.replace(hour=0, minute=0, second=0)
        end = start + timedelta(days=1)
    elif timeframe == "week":
        start = base_date - timedelta(days=base_date.weekday())
        start = start.replace(hour=0, minute=0, second=0)
        end = start + timedelta(days=7)
    elif timeframe == "month":
        start = base_date.replace(day=1, hour=0, minute=0, second=0)
        next_month = start.replace(day=28) + timedelta(days=4)
        end = next_month.replace(day=1)
    elif timeframe == "quarter":
        quarter = (base_date.month - 1) // 3
        start_month = quarter * 3 + 1
        start = base_date.replace(month=start_month, day=1, hour=0, minute=0, second=0)
        end_month = start_month + 3
        if end_month > 12:
            end = start.replace(year=start.year + 1, month=end_month - 12, day=1)
        else:
            end = start.replace(month=end_month, day=1)
    elif timeframe == "year":
        start = base_date.replace(month=1, day=1, hour=0, minute=0, second=0)
        end = start.replace(year=start.year + 1)
    else:
        return None, None

    return display_naive_to_utc(start), display_naive_to_utc(end)


def fill_timeline_gaps(
    counts: dict[str, int],
    granularity: str,
    start_date: date | None,
    end_date: date | None,
) -> list[PlayCountOverTime]:
    """Expand sparse period->count rows into a contiguous timeline.

    The rollup tables backing timelines only have rows for periods with at
    least one play, so `counts` has gaps wherever a day/month had none. When
    the full [start_date, end_date) range is known, fill those gaps with
    zero-count entries so charts don't skip empty days/months. When the
    range is unbounded (timeframe "all"), there's no way to know the full
    span, so just return what data exists.
    """
    if start_date is None or end_date is None:
        return [PlayCountOverTime(period=p, play_count=c) for p, c in counts.items()]

    periods: list[str] = []
    if granularity == "daily":
        day = start_date
        while day < end_date:
            periods.append(str(day))
            day += timedelta(days=1)
    else:
        year, month = start_date.year, start_date.month
        while (year, month) < (end_date.year, end_date.month):
            periods.append(f"{year}-{month:02d}")
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    return [PlayCountOverTime(period=p, play_count=counts.get(p, 0)) for p in periods]


def get_local_date_range(timeframe: str, base_date: datetime) -> tuple[date | None, date | None]:
    """Return Toronto-local calendar date boundaries [start, end) for a timeframe.

    Rollup tables store a plain local calendar `date`, so rollup-backed
    queries filter on this instead of the UTC storage boundaries `streams`
    queries use. Derived from get_date_range's UTC boundaries so the two stay
    in lockstep by construction.
    """
    start, end = get_date_range(timeframe, base_date)
    if start is None or end is None:
        return None, None
    return utc_naive_to_display(start).date(), utc_naive_to_display(end).date()


def get_top_songs(
    db: Session,
    timeframe: str,
    base_date: datetime,
    limit: int,
) -> list[TopSong]:
    """Rollup-backed: sums daily_track_stats grouped by track_key instead of
    scanning every raw stream row."""
    start_date, end_date = get_local_date_range(timeframe, base_date)

    counts = select(
        DailyTrackStats.track_key,
        func.max(DailyTrackStats.track_name).label("track_name"),
        func.max(DailyTrackStats.artist_name).label("artist_name"),
        func.max(DailyTrackStats.track_id).label("track_id"),
        func.max(DailyTrackStats.artist_id).label("artist_id"),
        func.sum(DailyTrackStats.play_count).label("play_count"),
    )
    if start_date and end_date:
        counts = counts.where(DailyTrackStats.date >= start_date, DailyTrackStats.date < end_date)
    counts = (
        counts.group_by(DailyTrackStats.track_key)
        .order_by(desc("play_count"))
        .limit(limit)
        .subquery()
    )

    # Album art stays a live join against tracks (not stored in the rollup)
    # so it reflects the latest known metadata.
    query = (
        select(
            counts.c.track_name,
            counts.c.artist_name,
            counts.c.track_id,
            counts.c.artist_id,
            Track.album_image,
            counts.c.play_count,
        )
        .outerjoin(Track, counts.c.track_id == Track.spotify_id)
        .order_by(desc(counts.c.play_count))
    )

    t0 = time.perf_counter()
    results = db.execute(query).all()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"get_top_songs query: {elapsed_ms:.1f}ms ({timeframe}, limit={limit})")

    return [
        TopSong(
            track_name=r.track_name,
            artist_name=r.artist_name,
            track_id=r.track_id,
            artist_id=r.artist_id,
            album_image=r.album_image,
            play_count=int(r.play_count),
        )
        for r in results
    ]


def get_top_artists(
    db: Session,
    timeframe: str,
    base_date: datetime,
    limit: int,
) -> list[TopArtist]:
    """Rollup-backed: sums daily_track_stats grouped by artist_key."""
    start_date, end_date = get_local_date_range(timeframe, base_date)

    counts = select(
        DailyTrackStats.artist_key,
        func.max(DailyTrackStats.artist_name).label("artist_name"),
        func.max(DailyTrackStats.artist_id).label("artist_id"),
        func.sum(DailyTrackStats.play_count).label("play_count"),
    )
    if start_date and end_date:
        counts = counts.where(DailyTrackStats.date >= start_date, DailyTrackStats.date < end_date)
    counts = (
        counts.group_by(DailyTrackStats.artist_key)
        .order_by(desc("play_count"))
        .limit(limit)
        .subquery()
    )

    query = (
        select(
            counts.c.artist_name,
            counts.c.artist_id,
            Artist.image_url,
            counts.c.play_count,
        )
        .outerjoin(Artist, counts.c.artist_id == Artist.spotify_id)
        .order_by(desc(counts.c.play_count))
    )

    t0 = time.perf_counter()
    results = db.execute(query).all()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"get_top_artists query: {elapsed_ms:.1f}ms ({timeframe}, limit={limit})")

    return [
        TopArtist(
            artist_name=r.artist_name,
            spotify_id=r.artist_id,
            image_url=r.image_url,
            play_count=int(r.play_count),
        )
        for r in results
    ]


def get_top_albums(
    db: Session,
    timeframe: str,
    base_date: datetime,
    limit: int,
) -> list[TopAlbum]:
    """Rollup-backed: sums daily_track_stats grouped by (album, artist_name)."""
    start_date, end_date = get_local_date_range(timeframe, base_date)

    stmt = select(
        DailyTrackStats.album_key,
        DailyTrackStats.artist_name,
        func.max(DailyTrackStats.artist_id).label("artist_id"),
        func.max(DailyTrackStats.track_id).label("track_id"),
        func.sum(DailyTrackStats.play_count).label("play_count"),
    )
    if start_date and end_date:
        stmt = stmt.where(DailyTrackStats.date >= start_date, DailyTrackStats.date < end_date)

    counts = (
        stmt.group_by(DailyTrackStats.album_key, DailyTrackStats.artist_name)
        .order_by(desc("play_count"))
        .limit(limit)
        .subquery()
    )

    # Representative track_id (any track on the album) for the live
    # album_image join, mirroring the old query's live Track join.
    query = (
        select(
            counts.c.album_key,
            counts.c.artist_name,
            counts.c.artist_id,
            Track.album_image,
            counts.c.play_count,
        )
        .outerjoin(Track, counts.c.track_id == Track.spotify_id)
        .order_by(desc(counts.c.play_count))
    )

    t0 = time.perf_counter()
    results = db.execute(query).all()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"get_top_albums query: {elapsed_ms:.1f}ms ({timeframe}, limit={limit})")

    return [
        TopAlbum(
            album_name=r.album_key if r.album_key != NO_ALBUM_SENTINEL else None,
            artist_name=r.artist_name,
            artist_id=r.artist_id,
            album_image=r.album_image,
            play_count=int(r.play_count),
        )
        for r in results
    ]


def _daily_listening_totals(db: Session, start_date: date | None, end_date: date | None):
    """Sum/count over daily_listening_stats for a date range.

    One tiny aggregate query (one row per calendar day) replaces the old
    multi-DISTINCT scan over every raw stream for total_play_count,
    total_listening_ms, and active_days.
    """
    stmt = select(
        func.coalesce(func.sum(DailyListeningStats.play_count), 0).label("total_play_count"),
        func.coalesce(func.sum(DailyListeningStats.listening_ms), 0).label("total_listening_ms"),
        func.count().label("active_days"),
    )
    if start_date and end_date:
        stmt = stmt.where(
            DailyListeningStats.date >= start_date, DailyListeningStats.date < end_date
        )
    return db.execute(stmt).one()


def get_listening_stats(
    db: Session,
    timeframe: str,
    base_date: datetime,
) -> ListeningStats:
    """Return aggregate listening stats for the selected period.

    Rollup-backed. Replaces the old single query with 4 DISTINCT aggregates
    over raw `streams` (the disk-spill culprit) with: one small SUM/COUNT(*)
    query over daily_listening_stats (also reused by get_timeline's daily
    branch — see `_daily_listening_totals`), plus up to 3 small DISTINCT
    counts over daily_track_stats, which has far fewer rows than `streams`
    (one row per day an item was played, not one per play).
    """
    start_date, end_date = get_local_date_range(timeframe, base_date)

    t0 = time.perf_counter()

    totals = _daily_listening_totals(db, start_date, end_date)

    range_filter = ()
    if start_date and end_date:
        range_filter = (DailyTrackStats.date >= start_date, DailyTrackStats.date < end_date)

    unique_songs = db.execute(
        select(func.count(func.distinct(DailyTrackStats.track_key))).where(*range_filter)
    ).scalar_one()
    unique_artists = db.execute(
        select(func.count(func.distinct(DailyTrackStats.artist_key))).where(*range_filter)
    ).scalar_one()

    albums_subquery = (
        select(DailyTrackStats.album_key, DailyTrackStats.artist_name)
        .where(DailyTrackStats.album_key != NO_ALBUM_SENTINEL, *range_filter)
        .distinct()
        .subquery()
    )
    unique_albums = db.execute(select(func.count()).select_from(albums_subquery)).scalar_one()

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"get_listening_stats query: {elapsed_ms:.1f}ms ({timeframe})")

    return ListeningStats(
        total_play_count=int(totals.total_play_count or 0),
        total_listening_ms=int(totals.total_listening_ms or 0),
        unique_songs=int(unique_songs or 0),
        unique_artists=int(unique_artists or 0),
        unique_albums=int(unique_albums or 0),
        active_days=int(totals.active_days or 0),
    )


def get_timeline(
    db: Session,
    timeframe: str,
    base_date: datetime,
) -> TimelineResponse:
    """Return overall play-count timeline grouped by day (month) or month (year/all).

    Rollup-backed: daily_listening_stats already has one row per calendar
    day, so the "daily" branch is a direct range select with no grouping at
    all; the "monthly" branch groups that same small table by year/month
    instead of scanning every raw stream.
    """
    start_date, end_date = get_local_date_range(timeframe, base_date)

    if timeframe == "month":
        stmt = select(DailyListeningStats.date, DailyListeningStats.play_count)
        if start_date and end_date:
            stmt = stmt.where(
                DailyListeningStats.date >= start_date, DailyListeningStats.date < end_date
            )
        stmt = stmt.order_by(DailyListeningStats.date)
        granularity = "daily"

        t0 = time.perf_counter()
        results = db.execute(stmt).all()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"get_timeline query: {elapsed_ms:.1f}ms ({timeframe})")

        counts = {str(r.date): int(r.play_count) for r in results}
        timeline = fill_timeline_gaps(counts, granularity, start_date, end_date)
    else:
        year_col = extract("year", DailyListeningStats.date).label("yr")
        month_col = extract("month", DailyListeningStats.date).label("mo")
        stmt = select(
            year_col, month_col, func.sum(DailyListeningStats.play_count).label("play_count")
        )
        if start_date and end_date:
            stmt = stmt.where(
                DailyListeningStats.date >= start_date, DailyListeningStats.date < end_date
            )
        stmt = stmt.group_by(year_col, month_col).order_by(year_col, month_col)
        granularity = "monthly"

        t0 = time.perf_counter()
        results = db.execute(stmt).all()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"get_timeline query: {elapsed_ms:.1f}ms ({timeframe})")

        counts = {f"{int(r.yr)}-{int(r.mo):02d}": int(r.play_count) for r in results}
        timeline = fill_timeline_gaps(counts, granularity, start_date, end_date)

    return TimelineResponse(timeline=timeline, granularity=granularity)


def get_recently_played(
    db: Session,
    start_date: datetime | None,
    page: int,
    page_size: int,
) -> RecentlyPlayedResponse:
    stmt = (
        select(
            Stream.track_name,
            Stream.artist_name,
            Stream.track_id,
            Stream.artist_id,
            Track.album_image,
            Stream.played_at,
        )
        .outerjoin(Track, Stream.track_id == Track.spotify_id)
        .order_by(Stream.played_at.desc(), Stream.id.desc())
    )

    if start_date:
        _, next_day = get_date_range("day", start_date)
        stmt = stmt.where(Stream.played_at < next_day)

    offset = (page - 1) * page_size
    query = stmt.offset(offset).limit(page_size + 1)

    t0 = time.perf_counter()
    results = db.execute(query).all()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "get_recently_played query: %.1fms (start_date=%s, page=%s, page_size=%s)",
        elapsed_ms,
        start_date.isoformat() if start_date else "none",
        page,
        page_size,
    )

    has_next = len(results) > page_size
    page_results = results[:page_size]

    return RecentlyPlayedResponse(
        items=[
            RecentlyPlayedItem(
                track_name=row.track_name,
                artist_name=row.artist_name,
                track_id=row.track_id,
                artist_id=row.artist_id,
                album_image=row.album_image,
                played_at=_to_iso_utc(row.played_at),
            )
            for row in page_results
        ],
        page=page,
        page_size=page_size,
        has_next=has_next,
    )


def get_available_periods(db: Session) -> AvailablePeriods:
    start = time.perf_counter()
    result = db.execute(
        select(
            func.min(Stream.played_at).label("earliest"),
            func.max(Stream.played_at).label("latest"),
        )
    ).first()
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(f"get_available_periods query: {elapsed_ms:.1f}ms")

    if not result or not result.earliest:
        return AvailablePeriods(years=[], earliest_date=None, latest_date=None)

    earliest = utc_naive_to_display(result.earliest)
    latest = utc_naive_to_display(result.latest)
    start_year = earliest.year
    end_year = latest.year
    years = list(range(start_year, end_year + 1))

    return AvailablePeriods(
        years=years,
        earliest_date=earliest.isoformat(),
        latest_date=latest.isoformat(),
    )
