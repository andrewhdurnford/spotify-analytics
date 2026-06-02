"""Tests for app.services.top — date-range logic and top-items queries."""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Artist, Stream, Track
from app.services.top import (
    get_available_periods,
    get_date_range,
    get_listening_stats,
    get_recently_played,
    get_top_albums,
    get_top_artists,
    get_top_songs,
)

# ---------------------------------------------------------------------------
# get_date_range
# ---------------------------------------------------------------------------

class TestGetDateRange:
    def test_all_returns_none(self):
        start, end = get_date_range("all", datetime(2025, 6, 15))
        assert start is None
        assert end is None

    def test_day(self):
        start, end = get_date_range("day", datetime(2025, 3, 15, 14, 30))
        assert start == datetime(2025, 3, 15, 0, 0, 0)
        assert end == datetime(2025, 3, 16, 0, 0, 0)

    def test_week_monday_start(self):
        # 2025-03-12 is a Wednesday
        start, end = get_date_range("week", datetime(2025, 3, 12))
        assert start == datetime(2025, 3, 10, 0, 0, 0)  # Monday
        assert end == datetime(2025, 3, 17, 0, 0, 0)  # next Monday

    def test_week_on_monday(self):
        start, end = get_date_range("week", datetime(2025, 3, 10))
        assert start == datetime(2025, 3, 10, 0, 0, 0)
        assert end == datetime(2025, 3, 17, 0, 0, 0)

    def test_month(self):
        start, end = get_date_range("month", datetime(2025, 1, 20))
        assert start == datetime(2025, 1, 1, 0, 0, 0)
        assert end == datetime(2025, 2, 1)

    def test_month_december(self):
        start, end = get_date_range("month", datetime(2025, 12, 15))
        assert start == datetime(2025, 12, 1, 0, 0, 0)
        assert end == datetime(2026, 1, 1)

    def test_month_february_leap_year(self):
        start, end = get_date_range("month", datetime(2024, 2, 15))
        assert start == datetime(2024, 2, 1, 0, 0, 0)
        assert end == datetime(2024, 3, 1)

    def test_month_february_non_leap_year(self):
        start, end = get_date_range("month", datetime(2025, 2, 10))
        assert start == datetime(2025, 2, 1, 0, 0, 0)
        assert end == datetime(2025, 3, 1)

    def test_quarter_q1(self):
        start, end = get_date_range("quarter", datetime(2025, 2, 15))
        assert start == datetime(2025, 1, 1, 0, 0, 0)
        assert end == datetime(2025, 4, 1)

    def test_quarter_q4(self):
        start, end = get_date_range("quarter", datetime(2025, 11, 1))
        assert start == datetime(2025, 10, 1, 0, 0, 0)
        assert end == datetime(2026, 1, 1)

    def test_year(self):
        start, end = get_date_range("year", datetime(2025, 6, 15))
        assert start == datetime(2025, 1, 1, 0, 0, 0)
        assert end == datetime(2025, 12, 31, 23, 59, 59)

    def test_unknown_timeframe_returns_none(self):
        start, end = get_date_range("invalid", datetime(2025, 1, 1))
        assert start is None
        assert end is None


# ---------------------------------------------------------------------------
# Helper to seed test data
# ---------------------------------------------------------------------------

def _seed_data(db: Session):
    """Insert tracks, artists, and streams for query tests."""
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
        # 3 plays of Song A in Jan 2025
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
        # 1 play of Song B in Feb 2025
        Stream(
            track_name="Song B",
            artist_name="Artist Y",
            album_name="Album 2",
            played_at=datetime(2025, 2, 5, 12, 0),
            duration_ms=180_000,
            track_id="track2",
            artist_id=None,
        ),
    ]
    db.add_all(streams)
    db.commit()


# ---------------------------------------------------------------------------
# Top songs / artists / albums queries
# ---------------------------------------------------------------------------

class TestGetTopSongs:
    def test_all_time(self, db: Session):
        _seed_data(db)
        results = get_top_songs(db, "all", datetime(2025, 6, 1), limit=10)
        assert len(results) == 2
        assert results[0].track_name == "Song A"
        assert results[0].play_count == 3
        assert results[1].track_name == "Song B"
        assert results[1].play_count == 1

    def test_month_filter(self, db: Session):
        _seed_data(db)
        results = get_top_songs(db, "month", datetime(2025, 1, 15), limit=10)
        assert len(results) == 1
        assert results[0].track_name == "Song A"
        assert results[0].play_count == 3

    def test_limit(self, db: Session):
        _seed_data(db)
        results = get_top_songs(db, "all", datetime(2025, 6, 1), limit=1)
        assert len(results) == 1
        assert results[0].track_name == "Song A"

    def test_empty_timeframe(self, db: Session):
        _seed_data(db)
        # No data in March
        results = get_top_songs(db, "month", datetime(2025, 3, 15), limit=10)
        assert len(results) == 0

    def test_album_image_joined(self, db: Session):
        _seed_data(db)
        results = get_top_songs(db, "all", datetime(2025, 6, 1), limit=10)
        assert results[0].album_image == "https://img/album1.jpg"


class TestGetTopArtists:
    def test_all_time(self, db: Session):
        _seed_data(db)
        results = get_top_artists(db, "all", datetime(2025, 6, 1), limit=10)
        assert len(results) == 2
        assert results[0].artist_name == "Artist X"
        assert results[0].play_count == 3

    def test_image_url_joined(self, db: Session):
        _seed_data(db)
        results = get_top_artists(db, "all", datetime(2025, 6, 1), limit=10)
        assert results[0].image_url == "https://img/artistx.jpg"


class TestGetTopAlbums:
    def test_all_time(self, db: Session):
        _seed_data(db)
        results = get_top_albums(db, "all", datetime(2025, 6, 1), limit=10)
        assert len(results) == 2
        assert results[0].album_name == "Album 1"
        assert results[0].play_count == 3
        assert results[0].artist_id == "artist1"


class TestGetListeningStats:
    def test_all_time(self, db: Session):
        _seed_data(db)
        result = get_listening_stats(db, "all", datetime(2025, 6, 1))

        assert result.total_play_count == 4
        assert result.total_listening_ms == 780_000
        assert result.unique_songs == 2
        assert result.unique_artists == 2
        assert result.unique_albums == 2
        assert result.active_days == 4

    def test_month_filter(self, db: Session):
        _seed_data(db)
        result = get_listening_stats(db, "month", datetime(2025, 1, 15))

        assert result.total_play_count == 3
        assert result.total_listening_ms == 600_000
        assert result.unique_songs == 1
        assert result.unique_artists == 1
        assert result.unique_albums == 1

    def test_empty_timeframe(self, db: Session):
        _seed_data(db)
        result = get_listening_stats(db, "month", datetime(2025, 3, 15))

        assert result.total_play_count == 0
        assert result.total_listening_ms == 0
        assert result.unique_songs == 0
        assert result.unique_artists == 0
        assert result.unique_albums == 0
        assert result.active_days == 0


class TestGetAvailablePeriods:
    def test_with_data(self, db: Session):
        _seed_data(db)
        result = get_available_periods(db)
        assert result.years == [2025]
        assert result.earliest_date is not None
        assert result.latest_date is not None

    def test_empty_database(self, db: Session):
        result = get_available_periods(db)
        assert result.years == []
        assert result.earliest_date is None
        assert result.latest_date is None


class TestGetRecentlyPlayed:
    def test_returns_descending_rows_with_has_next(self, db: Session):
        _seed_data(db)
        result = get_recently_played(db, start_date=None, page=1, page_size=2)

        assert len(result.items) == 2
        assert result.has_next is True
        assert result.items[0].track_name == "Song B"
        assert result.items[1].track_name == "Song A"

    def test_second_page(self, db: Session):
        _seed_data(db)
        result = get_recently_played(db, start_date=None, page=2, page_size=2)

        assert len(result.items) == 2
        assert result.has_next is False
        assert result.items[0].played_at.startswith("2025-01-11")
        assert result.items[1].played_at.startswith("2025-01-10")

    def test_start_date_filters_from_day_and_backwards(self, db: Session):
        _seed_data(db)
        result = get_recently_played(
            db,
            start_date=datetime(2025, 1, 11),
            page=1,
            page_size=10,
        )

        assert len(result.items) == 2
        assert result.items[0].played_at.startswith("2025-01-11")
        assert result.items[1].played_at.startswith("2025-01-10")
