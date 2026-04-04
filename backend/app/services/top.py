"""Service layer for top-items queries.

Keeps SQLAlchemy query logic out of route handlers so routes only deal with
request parsing and response serialisation.
"""

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..models import Artist, Stream, Track
from ..schemas import (
    AvailablePeriods,
    RecentlyPlayedItem,
    RecentlyPlayedResponse,
    TopAlbum,
    TopArtist,
    TopSong,
)

logger = logging.getLogger(__name__)


def _to_iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def get_date_range(timeframe: str, base_date: datetime) -> tuple[datetime | None, datetime | None]:
    """Return (start, end) for a given timeframe, or (None, None) for 'all'."""
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
        end = base_date.replace(month=12, day=31, hour=23, minute=59, second=59)
    else:
        return None, None

    return start, end


def get_top_songs(
    db: Session,
    timeframe: str,
    base_date: datetime,
    limit: int,
) -> list[TopSong]:
    # Aggregate by track_id (indexed) in a subquery, then join for metadata.
    # Falls back to grouping by name strings for streams without a track_id.
    group_key = func.coalesce(Stream.track_id, Stream.track_name).label("group_key")
    counts = select(
        group_key,
        func.count(Stream.id).label("play_count"),
    )

    start, end = get_date_range(timeframe, base_date)
    if start and end:
        counts = counts.where(Stream.played_at.between(start, end))

    counts = counts.group_by("group_key").order_by(desc("play_count")).limit(limit).subquery()

    # Join back to streams + tracks to get display names and album art.
    query = (
        select(
            Stream.track_name,
            Stream.artist_name,
            Stream.track_id,
            Stream.artist_id,
            Track.album_image,
            counts.c.play_count,
        )
        .join(counts, func.coalesce(Stream.track_id, Stream.track_name) == counts.c.group_key)
        .outerjoin(Track, Stream.track_id == Track.spotify_id)
        .group_by(
            Stream.track_name,
            Stream.artist_name,
            Stream.track_id,
            Stream.artist_id,
            Track.album_image,
            counts.c.play_count,
        )
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
            play_count=r.play_count,
        )
        for r in results
    ]


def get_top_artists(
    db: Session,
    timeframe: str,
    base_date: datetime,
    limit: int,
) -> list[TopArtist]:
    # Aggregate by artist_id (indexed) in a subquery, then join for metadata.
    group_key = func.coalesce(Stream.artist_id, Stream.artist_name).label("group_key")
    counts = select(
        group_key,
        func.count(Stream.id).label("play_count"),
    )

    start, end = get_date_range(timeframe, base_date)
    if start and end:
        counts = counts.where(Stream.played_at.between(start, end))

    counts = counts.group_by("group_key").order_by(desc("play_count")).limit(limit).subquery()

    query = (
        select(
            Stream.artist_name,
            Stream.artist_id,
            Artist.image_url,
            counts.c.play_count,
        )
        .join(
            counts,
            func.coalesce(Stream.artist_id, Stream.artist_name) == counts.c.group_key,
        )
        .outerjoin(Artist, Stream.artist_id == Artist.spotify_id)
        .group_by(Stream.artist_name, Stream.artist_id, Artist.image_url, counts.c.play_count)
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
            play_count=r.play_count,
        )
        for r in results
    ]


def get_top_albums(
    db: Session,
    timeframe: str,
    base_date: datetime,
    limit: int,
) -> list[TopAlbum]:
    # Albums don't have their own ID, so group by album_name + artist_name.
    # Still benefits from the played_at index for the date filter.
    stmt = select(
        Stream.album_name,
        Stream.artist_name,
        Track.album_image,
        func.count(Stream.id).label("play_count"),
    ).outerjoin(Track, Stream.track_id == Track.spotify_id)

    start, end = get_date_range(timeframe, base_date)
    if start and end:
        stmt = stmt.where(Stream.played_at.between(start, end))

    query = (
        stmt.group_by(Stream.album_name, Stream.artist_name, Track.album_image)
        .order_by(desc("play_count"))
        .limit(limit)
    )

    t0 = time.perf_counter()
    results = db.execute(query).all()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"get_top_albums query: {elapsed_ms:.1f}ms ({timeframe}, limit={limit})")

    return [
        TopAlbum(
            album_name=r.album_name,
            artist_name=r.artist_name,
            album_image=r.album_image,
            play_count=r.play_count,
        )
        for r in results
    ]


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
        end_of_day = start_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        stmt = stmt.where(Stream.played_at <= end_of_day)

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

    start_year = result.earliest.year
    end_year = result.latest.year
    years = list(range(start_year, end_year + 1))

    return AvailablePeriods(
        years=years,
        earliest_date=result.earliest.isoformat(),
        latest_date=result.latest.isoformat(),
    )
