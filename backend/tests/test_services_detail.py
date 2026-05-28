"""Focused tests for app.services.detail query behavior."""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Artist, Stream, Track
from app.services.detail import (
    get_artist_detail,
    get_track_detail,
    get_track_detail_by_name,
)


def _seed_detail_data(db: Session):
    artist = Artist(
        spotify_id="artist-1",
        name="Artist One",
        genres=["indie"],
        popularity=70,
        image_url="https://img/artist-1.jpg",
    )
    tracks = [
        Track(
            spotify_id="track-1",
            name="Song One",
            artist_name="Artist One",
            album_name="Album One",
            album_image="https://img/track-1.jpg",
            duration_ms=200_000,
        ),
        Track(
            spotify_id="track-2",
            name="Song Two",
            artist_name="Artist One",
            album_name="Album Two",
            album_image="https://img/track-2.jpg",
            duration_ms=180_000,
        ),
        Track(
            spotify_id="track-empty",
            name="Never Played",
            artist_name="Artist One",
            album_name="Quiet Album",
            album_image="https://img/empty.jpg",
            duration_ms=210_000,
        ),
    ]
    streams = [
        Stream(
            track_name="Song One",
            artist_name="Artist One",
            album_name="Album One",
            played_at=datetime(2025, 1, 5, 9, 0),
            duration_ms=200_000,
            track_id="track-1",
            artist_id="artist-1",
        ),
        Stream(
            track_name="Song One",
            artist_name="Artist One",
            album_name="Album One",
            played_at=datetime(2025, 1, 6, 9, 0),
            duration_ms=200_000,
            track_id="track-1",
            artist_id="artist-1",
        ),
        Stream(
            track_name="Song Two",
            artist_name="Artist One",
            album_name="Album Two",
            played_at=datetime(2025, 2, 1, 9, 0),
            duration_ms=180_000,
            track_id="track-2",
            artist_id="artist-1",
        ),
        Stream(
            track_name="Loose Song",
            artist_name="Loose Artist",
            album_name="Loose Album",
            played_at=datetime(2025, 1, 7, 9, 0),
            duration_ms=150_000,
        ),
        Stream(
            track_name="Loose Song",
            artist_name="Different Artist",
            album_name="Other Album",
            played_at=datetime(2025, 1, 8, 9, 0),
            duration_ms=150_000,
        ),
    ]

    db.add_all([artist, *tracks, *streams])
    db.commit()


class TestGetArtistDetail:
    def test_month_filter_limits_counts_timeline_and_top_items(self, db: Session):
        _seed_detail_data(db)

        result = get_artist_detail(
            db,
            artist_id="artist-1",
            timeframe="month",
            base_date=datetime(2025, 1, 15),
            limit=10,
        )

        assert result is not None
        assert result.artist.spotify_id == "artist-1"
        assert result.artist.total_play_count == 2
        assert result.granularity == "daily"
        assert [song.track_name for song in result.top_songs] == ["Song One"]
        assert [album.album_name for album in result.top_albums] == ["Album One"]
        assert [point.model_dump() for point in result.timeline] == [
            {"period": "2025-01-05", "play_count": 1},
            {"period": "2025-01-06", "play_count": 1},
        ]

    def test_missing_artist_returns_none(self, db: Session):
        _seed_detail_data(db)

        result = get_artist_detail(
            db,
            artist_id="missing",
            timeframe="all",
            base_date=datetime(2025, 1, 1),
            limit=10,
        )

        assert result is None


class TestGetTrackDetail:
    def test_track_with_metadata_but_no_streams_returns_empty_counts(self, db: Session):
        _seed_detail_data(db)

        result = get_track_detail(
            db,
            track_id="track-empty",
            timeframe="all",
            base_date=datetime(2025, 1, 1),
        )

        assert result is not None
        assert result.track.spotify_id == "track-empty"
        assert result.track.total_play_count == 0
        assert result.track.artist_id is None
        assert result.timeline == []

    def test_month_filter_uses_daily_timeline(self, db: Session):
        _seed_detail_data(db)

        result = get_track_detail(
            db,
            track_id="track-1",
            timeframe="month",
            base_date=datetime(2025, 1, 1),
        )

        assert result is not None
        assert result.track.total_play_count == 2
        assert result.granularity == "daily"
        assert [point.model_dump() for point in result.timeline] == [
            {"period": "2025-01-05", "play_count": 1},
            {"period": "2025-01-06", "play_count": 1},
        ]

    def test_missing_track_returns_none(self, db: Session):
        result = get_track_detail(
            db,
            track_id="missing",
            timeframe="all",
            base_date=datetime(2025, 1, 1),
        )

        assert result is None


class TestGetTrackDetailByName:
    def test_name_fallback_distinguishes_artist_and_applies_timeframe(self, db: Session):
        _seed_detail_data(db)

        result = get_track_detail_by_name(
            db,
            track_name="Loose Song",
            artist_name="Loose Artist",
            timeframe="month",
            base_date=datetime(2025, 1, 1),
        )

        assert result is not None
        assert result.track.spotify_id is None
        assert result.track.name == "Loose Song"
        assert result.track.artist_name == "Loose Artist"
        assert result.track.total_play_count == 1
        assert result.granularity == "daily"
        assert [point.model_dump() for point in result.timeline] == [
            {"period": "2025-01-07", "play_count": 1},
        ]

    def test_name_fallback_returns_zero_for_existing_track_outside_timeframe(self, db: Session):
        _seed_detail_data(db)

        result = get_track_detail_by_name(
            db,
            track_name="Loose Song",
            artist_name="Loose Artist",
            timeframe="month",
            base_date=datetime(2025, 2, 1),
        )

        assert result is not None
        assert result.track.total_play_count == 0
        assert result.timeline == []

    def test_missing_name_fallback_returns_none(self, db: Session):
        _seed_detail_data(db)

        result = get_track_detail_by_name(
            db,
            track_name="Missing",
            artist_name="Loose Artist",
            timeframe="all",
            base_date=datetime(2025, 1, 1),
        )

        assert result is None
