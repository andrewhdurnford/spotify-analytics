"""Equivalence tests: rollup-backed query functions vs. the original raw
`streams`-scanning implementations they replaced.

The `_raw_*` functions below are frozen copies of the pre-rewrite
implementations from `app.services.top` / `app.services.detail` (the exact
code that existed before this change — see git history), kept here only to
prove the new rollup-backed functions return identical results for the same
fixture data across a range of timeframes, including empty ranges. They are
intentionally NOT exported/reused elsewhere; this file is the "keep the old
behavior as a reference oracle" test the task asked for, without leaving a
second copy of the query logic live in `services/`.
"""

from collections import Counter
from datetime import datetime

from sqlalchemy import desc, extract, func, select
from sqlalchemy.orm import Session

from app.models import Artist, Stream, Track
from app.schemas import (
    ListeningStats,
    PlayCountOverTime,
    TimelineResponse,
    TopAlbum,
    TopArtist,
    TopSong,
)
from app.services import detail as detail_service
from app.services import top as top_service
from app.services.timezone import display_timestamp

# ---------------------------------------------------------------------------
# Frozen raw-`streams` implementations (pre-rewrite behavior)
# ---------------------------------------------------------------------------


def _raw_get_top_songs(db: Session, timeframe: str, base_date: datetime, limit: int):
    group_key = func.coalesce(Stream.track_id, Stream.track_name).label("group_key")
    counts = select(group_key, func.count(Stream.id).label("play_count"))
    start, end = top_service.get_date_range(timeframe, base_date)
    if start and end:
        counts = counts.where(Stream.played_at >= start, Stream.played_at < end)
    counts = counts.group_by("group_key").order_by(desc("play_count")).limit(limit).subquery()

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
    results = db.execute(query).all()
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


def _raw_get_top_artists(db: Session, timeframe: str, base_date: datetime, limit: int):
    group_key = func.coalesce(Stream.artist_id, Stream.artist_name).label("group_key")
    counts = select(group_key, func.count(Stream.id).label("play_count"))
    start, end = top_service.get_date_range(timeframe, base_date)
    if start and end:
        counts = counts.where(Stream.played_at >= start, Stream.played_at < end)
    counts = counts.group_by("group_key").order_by(desc("play_count")).limit(limit).subquery()

    query = (
        select(Stream.artist_name, Stream.artist_id, Artist.image_url, counts.c.play_count)
        .join(counts, func.coalesce(Stream.artist_id, Stream.artist_name) == counts.c.group_key)
        .outerjoin(Artist, Stream.artist_id == Artist.spotify_id)
        .group_by(Stream.artist_name, Stream.artist_id, Artist.image_url, counts.c.play_count)
        .order_by(desc(counts.c.play_count))
    )
    results = db.execute(query).all()
    return [
        TopArtist(
            artist_name=r.artist_name,
            spotify_id=r.artist_id,
            image_url=r.image_url,
            play_count=r.play_count,
        )
        for r in results
    ]


def _raw_get_top_albums(db: Session, timeframe: str, base_date: datetime, limit: int):
    stmt = select(
        Stream.album_name,
        Stream.artist_name,
        func.max(Stream.artist_id).label("artist_id"),
        Track.album_image,
        func.count(Stream.id).label("play_count"),
    ).outerjoin(Track, Stream.track_id == Track.spotify_id)
    start, end = top_service.get_date_range(timeframe, base_date)
    if start and end:
        stmt = stmt.where(Stream.played_at >= start, Stream.played_at < end)
    query = (
        stmt.group_by(Stream.album_name, Stream.artist_name, Track.album_image)
        .order_by(desc("play_count"))
        .limit(limit)
    )
    results = db.execute(query).all()
    return [
        TopAlbum(
            album_name=r.album_name,
            artist_name=r.artist_name,
            artist_id=r.artist_id,
            album_image=r.album_image,
            play_count=r.play_count,
        )
        for r in results
    ]


def _raw_get_listening_stats(db: Session, timeframe: str, base_date: datetime) -> ListeningStats:
    start, end = top_service.get_date_range(timeframe, base_date)
    song_key = func.coalesce(Stream.track_id, Stream.track_name)
    artist_key = func.coalesce(Stream.artist_id, Stream.artist_name)

    stmt = (
        select(
            func.count(Stream.id).label("total_play_count"),
            func.coalesce(func.sum(Track.duration_ms), 0).label("total_listening_ms"),
            func.count(func.distinct(song_key)).label("unique_songs"),
            func.count(func.distinct(artist_key)).label("unique_artists"),
            func.count(func.distinct(func.date(display_timestamp(Stream.played_at)))).label(
                "active_days"
            ),
        )
        .select_from(Stream)
        .outerjoin(Track, Track.spotify_id == Stream.track_id)
    )
    albums_stmt = select(Stream.album_name, Stream.artist_name).where(
        Stream.album_name.is_not(None)
    )
    if start and end:
        stmt = stmt.where(Stream.played_at >= start, Stream.played_at < end)
        albums_stmt = albums_stmt.where(Stream.played_at >= start, Stream.played_at < end)

    albums_subquery = albums_stmt.distinct().subquery()
    albums_count_stmt = select(func.count()).select_from(albums_subquery)

    result = db.execute(stmt).one()
    unique_albums = db.execute(albums_count_stmt).scalar_one()

    return ListeningStats(
        total_play_count=int(result.total_play_count or 0),
        total_listening_ms=int(result.total_listening_ms or 0),
        unique_songs=int(result.unique_songs or 0),
        unique_artists=int(result.unique_artists or 0),
        unique_albums=int(unique_albums or 0),
        active_days=int(result.active_days or 0),
    )


def _raw_get_timeline(db: Session, timeframe: str, base_date: datetime) -> TimelineResponse:
    start, end = top_service.get_date_range(timeframe, base_date)
    if timeframe == "month":
        date_col = func.date(display_timestamp(Stream.played_at)).label("day")
        stmt = select(date_col, func.count(Stream.id).label("play_count"))
        if start and end:
            stmt = stmt.where(Stream.played_at >= start, Stream.played_at < end)
        stmt = stmt.group_by(date_col).order_by(date_col)
        results = db.execute(stmt).all()
        timeline = [PlayCountOverTime(period=str(r.day), play_count=r.play_count) for r in results]
        granularity = "daily"
    else:
        local_played_at = display_timestamp(Stream.played_at)
        year_col = extract("year", local_played_at).label("yr")
        month_col = extract("month", local_played_at).label("mo")
        stmt = select(year_col, month_col, func.count(Stream.id).label("play_count"))
        if start and end:
            stmt = stmt.where(Stream.played_at >= start, Stream.played_at < end)
        stmt = stmt.group_by(year_col, month_col).order_by(year_col, month_col)
        results = db.execute(stmt).all()
        timeline = [
            PlayCountOverTime(period=f"{int(r.yr)}-{int(r.mo):02d}", play_count=r.play_count)
            for r in results
        ]
        granularity = "monthly"
    return TimelineResponse(timeline=timeline, granularity=granularity)


# ---------------------------------------------------------------------------
# Fixture data (mirrors app.services.top's own test fixture, plus a few
# extra streams to exercise loose/no-track-id/no-album cases)
# ---------------------------------------------------------------------------


def _seed(db: Session):
    track = Track(
        spotify_id="track1",
        name="Song A",
        artist_name="Artist X",
        album_name="Album 1",
        album_image="https://img/album1.jpg",
        duration_ms=200_000,
    )
    track2 = Track(
        spotify_id="track2",
        name="Song B",
        artist_name="Artist Y",
        album_name="Album 2",
        album_image="https://img/album2.jpg",
        duration_ms=180_000,
    )
    artist = Artist(
        spotify_id="artist1",
        name="Artist X",
        genres=["pop"],
        popularity=80,
        image_url="https://img/artistx.jpg",
    )
    db.add_all([track, track2, artist])
    db.flush()

    streams = [
        Stream(
            track_name="Song A",
            artist_name="Artist X",
            album_name="Album 1",
            played_at=datetime(2025, 1, 10, 12, 0),
            duration_ms=200_000,
            track_id="track1",
            artist_id="artist1",
        ),
        Stream(
            track_name="Song A",
            artist_name="Artist X",
            album_name="Album 1",
            played_at=datetime(2025, 1, 11, 12, 0),
            duration_ms=200_000,
            track_id="track1",
            artist_id="artist1",
        ),
        Stream(
            track_name="Song A",
            artist_name="Artist X",
            album_name="Album 1",
            played_at=datetime(2025, 1, 12, 12, 0),
            duration_ms=200_000,
            track_id="track1",
            artist_id="artist1",
        ),
        Stream(
            track_name="Song B",
            artist_name="Artist Y",
            album_name="Album 2",
            played_at=datetime(2025, 2, 5, 12, 0),
            duration_ms=180_000,
            track_id="track2",
            artist_id=None,
        ),
        # No track_id/artist_id/album — exercises name-fallback grouping.
        Stream(
            track_name="Loose Song",
            artist_name="Unknown Artist",
            played_at=datetime(2025, 2, 6, 9, 0),
            duration_ms=100_000,
        ),
        # Late-UTC play landing on the previous Toronto calendar day.
        Stream(
            track_name="Night Owl",
            artist_name="Artist Z",
            played_at=datetime(2026, 7, 9, 2, 5),
        ),
    ]
    db.add_all(streams)
    db.commit()


TIMEFRAMES = [
    ("all", datetime(2025, 6, 1)),
    ("month", datetime(2025, 1, 15)),
    ("week", datetime(2025, 1, 10)),
    ("day", datetime(2025, 1, 10)),
    ("month", datetime(2025, 3, 15)),  # empty range
    ("year", datetime(2025, 1, 1)),
]


def _as_multiset(models) -> Counter:
    """Order-insensitive comparison helper.

    Neither the old nor the new top-N queries declare a tiebreaker for equal
    play_counts, so which of two tied items sorts first is incidental to the
    query planner, not a documented contract. Comparing as a multiset of
    field-tuples verifies "same items, same counts" without asserting a
    specific (unspecified) tie order.
    """
    return Counter(tuple(sorted(m.model_dump().items())) for m in models)


class TestTopSongsEquivalence:
    def test_matches_raw_query_across_timeframes(self, db: Session):
        _seed(db)
        for timeframe, base_date in TIMEFRAMES:
            new_results = top_service.get_top_songs(db, timeframe, base_date, limit=10)
            raw_results = _raw_get_top_songs(db, timeframe, base_date, limit=10)
            assert _as_multiset(new_results) == _as_multiset(
                raw_results
            ), f"mismatch for timeframe={timeframe}"


class TestTopArtistsEquivalence:
    def test_matches_raw_query_across_timeframes(self, db: Session):
        _seed(db)
        for timeframe, base_date in TIMEFRAMES:
            new_results = top_service.get_top_artists(db, timeframe, base_date, limit=10)
            raw_results = _raw_get_top_artists(db, timeframe, base_date, limit=10)
            assert _as_multiset(new_results) == _as_multiset(
                raw_results
            ), f"mismatch for timeframe={timeframe}"


class TestTopAlbumsEquivalence:
    def test_matches_raw_query_across_timeframes(self, db: Session):
        _seed(db)
        for timeframe, base_date in TIMEFRAMES:
            new_results = top_service.get_top_albums(db, timeframe, base_date, limit=10)
            raw_results = _raw_get_top_albums(db, timeframe, base_date, limit=10)
            assert _as_multiset(new_results) == _as_multiset(
                raw_results
            ), f"mismatch for timeframe={timeframe}"


class TestListeningStatsEquivalence:
    def test_matches_raw_query_across_timeframes(self, db: Session):
        _seed(db)
        for timeframe, base_date in TIMEFRAMES:
            new_result = top_service.get_listening_stats(db, timeframe, base_date)
            raw_result = _raw_get_listening_stats(db, timeframe, base_date)
            assert (
                new_result.model_dump() == raw_result.model_dump()
            ), f"mismatch for timeframe={timeframe}"


class TestTimelineEquivalence:
    def test_matches_raw_query_across_timeframes(self, db: Session):
        _seed(db)
        for timeframe, base_date in TIMEFRAMES:
            new_result = top_service.get_timeline(db, timeframe, base_date)
            raw_result = _raw_get_timeline(db, timeframe, base_date)
            assert (
                new_result.model_dump() == raw_result.model_dump()
            ), f"mismatch for timeframe={timeframe}"


# ---------------------------------------------------------------------------
# Detail-page equivalence (artist detail totals/rankings/timeline)
# ---------------------------------------------------------------------------


def _raw_get_artist_detail_totals(db: Session, artist_id: str, timeframe: str, base_date: datetime):
    """Frozen pre-rewrite total_play_count + top_songs + top_albums for an artist."""
    start, end = top_service.get_date_range(timeframe, base_date)

    count_stmt = select(func.count(Stream.id)).where(Stream.artist_id == artist_id)
    if start and end:
        count_stmt = count_stmt.where(Stream.played_at >= start, Stream.played_at < end)
    total_play_count = db.execute(count_stmt).scalar() or 0

    song_group_key = func.coalesce(Stream.track_id, Stream.track_name).label("group_key")
    song_counts = select(song_group_key, func.count(Stream.id).label("play_count")).where(
        Stream.artist_id == artist_id
    )
    if start and end:
        song_counts = song_counts.where(Stream.played_at >= start, Stream.played_at < end)
    song_counts = (
        song_counts.group_by("group_key").order_by(desc("play_count")).limit(10).subquery()
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
    top_songs = [
        TopSong(
            track_name=r.track_name,
            artist_name=r.artist_name,
            track_id=r.track_id,
            artist_id=r.artist_id,
            album_image=r.album_image,
            play_count=r.play_count,
        )
        for r in db.execute(songs_query).all()
    ]

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
        .limit(10)
    )
    top_albums = [
        TopAlbum(
            album_name=r.album_name,
            artist_name=r.artist_name,
            artist_id=artist_id,
            album_image=r.album_image,
            play_count=r.play_count,
        )
        for r in db.execute(album_query).all()
    ]

    return total_play_count, top_songs, top_albums


class TestArtistDetailEquivalence:
    def test_totals_and_rankings_match_raw_query(self, db: Session):
        _seed(db)
        for timeframe, base_date in TIMEFRAMES:
            response = detail_service.get_artist_detail(
                db, "artist1", timeframe, base_date, limit=10
            )
            raw_total, raw_songs, raw_albums = _raw_get_artist_detail_totals(
                db, "artist1", timeframe, base_date
            )
            assert response.artist.total_play_count == raw_total, f"timeframe={timeframe}"
            # New implementation always reports the artist's own name/id
            # (guaranteed correct since it filters on artist_id); compare
            # play_count/track_id ordering & values which is what matters.
            assert [s.play_count for s in response.top_songs] == [
                s.play_count for s in raw_songs
            ], f"timeframe={timeframe}"
            assert [s.track_id for s in response.top_songs] == [
                s.track_id for s in raw_songs
            ], f"timeframe={timeframe}"
            assert [a.play_count for a in response.top_albums] == [
                a.play_count for a in raw_albums
            ], f"timeframe={timeframe}"
