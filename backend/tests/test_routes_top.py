"""Integration tests for /api/top/* routes."""

from datetime import datetime

from app.models import Artist, Stream, Track


def _seed(db):
    """Minimal seed data for route tests."""
    track = Track(
        spotify_id="t1",
        name="Track 1",
        artist_name="Art 1",
        album_name="Alb 1",
        album_image="https://img/1.jpg",
        duration_ms=200_000,
    )
    artist = Artist(
        spotify_id="a1",
        name="Art 1",
        genres=["rock"],
        popularity=90,
        image_url="https://img/a1.jpg",
    )
    db.add_all([track, artist])
    db.flush()

    db.add(
        Stream(
            track_name="Track 1",
            artist_name="Art 1",
            album_name="Alb 1",
            played_at=datetime(2025, 1, 15, 10, 0),
            duration_ms=200_000,
            track_id="t1",
            artist_id="a1",
        )
    )
    db.commit()


class TestSongsRoute:
    def test_default_params(self, client, db):
        _seed(db)
        resp = client.get("/api/top/songs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["track_name"] == "Track 1"
        assert "play_count" in data[0]

    def test_with_timeframe_and_date(self, client, db):
        _seed(db)
        resp = client.get("/api/top/songs?timeframe=month&date=2025-01-15")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    def test_empty_result_for_wrong_month(self, client, db):
        _seed(db)
        resp = client.get("/api/top/songs?timeframe=month&date=2025-03-01")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_invalid_date_returns_400(self, client):
        resp = client.get("/api/top/songs?date=not-a-date")
        assert resp.status_code == 400


class TestArtistsRoute:
    def test_default_params(self, client, db):
        _seed(db)
        resp = client.get("/api/top/artists")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["artist_name"] == "Art 1"

    def test_invalid_timeframe_returns_422(self, client, db):
        _seed(db)
        resp = client.get("/api/top/artists?timeframe=bogus")
        assert resp.status_code == 422


class TestAlbumsRoute:
    def test_default_params(self, client, db):
        _seed(db)
        resp = client.get("/api/top/albums")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["album_name"] == "Alb 1"


class TestAvailablePeriodsRoute:
    def test_with_data(self, client, db):
        _seed(db)
        resp = client.get("/api/top/available-periods")
        assert resp.status_code == 200
        body = resp.json()
        assert "years" in body
        assert 2025 in body["years"]

    def test_empty_database(self, client):
        resp = client.get("/api/top/available-periods")
        assert resp.status_code == 200
        body = resp.json()
        assert body["years"] == []


class TestRecentlyPlayedRoute:
    def test_default_params(self, client, db):
        _seed(db)

        # Add more rows for pagination checks
        db.add_all(
            [
                Stream(
                    track_name="Track 1",
                    artist_name="Art 1",
                    album_name="Alb 1",
                    played_at=datetime(2025, 1, 16, 10, 0),
                    duration_ms=200_000,
                    track_id="t1",
                    artist_id="a1",
                ),
                Stream(
                    track_name="Track 1",
                    artist_name="Art 1",
                    album_name="Alb 1",
                    played_at=datetime(2025, 1, 17, 10, 0),
                    duration_ms=200_000,
                    track_id="t1",
                    artist_id="a1",
                ),
            ]
        )
        db.commit()

        resp = client.get("/api/top/recently-played?page=1&page_size=2")
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 1
        assert body["page_size"] == 2
        assert body["has_next"] is True
        assert len(body["items"]) == 2

    def test_start_date_filter(self, client, db):
        _seed(db)
        resp = client.get("/api/top/recently-played?start_date=2025-01-14&page_size=10")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 0

    def test_invalid_date_returns_400(self, client):
        resp = client.get("/api/top/recently-played?start_date=bad-date")
        assert resp.status_code == 400


class TestCurrentlyPlayingRoute:
    def test_not_playing(self, client):
        """Mock the Spotify dependency to return not-playing."""
        from unittest.mock import AsyncMock

        from app.dependencies import get_spotify_client
        from app.main import app

        mock_client = AsyncMock()
        mock_client.get_currently_playing.return_value = {"is_playing": False}

        app.dependency_overrides[get_spotify_client] = lambda: mock_client
        resp = client.get("/api/top/currently-playing")
        assert resp.status_code == 200
        assert resp.json()["is_playing"] is False
        app.dependency_overrides.pop(get_spotify_client, None)

    def test_playing(self, client):
        from unittest.mock import AsyncMock

        from app.dependencies import get_spotify_client
        from app.main import app

        mock_client = AsyncMock()
        mock_client.get_currently_playing.return_value = {
            "is_playing": True,
            "track_name": "Test Track",
            "artist_name": "Test Artist",
            "album_name": "Test Album",
            "album_image": "https://img/test.jpg",
            "progress_ms": 30_000,
            "duration_ms": 200_000,
        }

        app.dependency_overrides[get_spotify_client] = lambda: mock_client
        resp = client.get("/api/top/currently-playing")
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_playing"] is True
        assert body["track_name"] == "Test Track"
        app.dependency_overrides.pop(get_spotify_client, None)
