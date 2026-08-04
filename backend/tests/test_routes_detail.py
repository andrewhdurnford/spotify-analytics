"""Integration tests for artist and track detail routes."""

import calendar
from datetime import datetime

from app.models import Artist, Stream, Track


def _full_month(year: int, month: int, counts: dict[str, int]) -> list[dict]:
    """Expected daily timeline for a whole month, zero-filled except `counts`."""
    days_in_month = calendar.monthrange(year, month)[1]
    return [
        {
            "period": (period := f"{year}-{month:02d}-{day:02d}"),
            "play_count": counts.get(period, 0),
        }
        for day in range(1, days_in_month + 1)
    ]


def _seed(db):
    artist = Artist(
        spotify_id="a1",
        name="Art 1",
        genres=["rock", "indie"],
        popularity=90,
        image_url="https://img/a1.jpg",
    )
    tracks = [
        Track(
            spotify_id="t1",
            name="Track 1",
            artist_name="Art 1",
            album_name="Alb 1",
            album_image="https://img/t1.jpg",
            duration_ms=200_000,
        ),
        Track(
            spotify_id="t2",
            name="Track 2",
            artist_name="Art 1",
            album_name="Alb 2",
            album_image="https://img/t2.jpg",
            duration_ms=180_000,
        ),
    ]
    streams = [
        Stream(
            track_name="Track 1",
            artist_name="Art 1",
            album_name="Alb 1",
            played_at=datetime(2025, 1, 15, 10, 0),
            duration_ms=200_000,
            track_id="t1",
            artist_id="a1",
        ),
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
            track_name="Track 2",
            artist_name="Art 1",
            album_name="Alb 2",
            played_at=datetime(2025, 2, 1, 10, 0),
            duration_ms=180_000,
            track_id="t2",
            artist_id="a1",
        ),
        Stream(
            track_name="Loose Track",
            artist_name="Loose Artist",
            album_name="Loose Album",
            played_at=datetime(2025, 1, 20, 10, 0),
            duration_ms=150_000,
        ),
    ]
    db.add_all([artist, *tracks, *streams])
    db.commit()


class TestArtistDetailRoute:
    def test_artist_detail_default_params(self, client, db):
        _seed(db)

        resp = client.get("/api/artists/a1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["artist"]["spotify_id"] == "a1"
        assert body["artist"]["name"] == "Art 1"
        assert body["artist"]["total_play_count"] == 3
        assert body["top_songs"][0]["track_name"] == "Track 1"
        assert body["top_songs"][0]["play_count"] == 2
        assert body["top_albums"][0]["album_name"] == "Alb 1"
        assert body["granularity"] == "monthly"
        assert {"period": "2025-01", "play_count": 2} in body["timeline"]
        assert {"period": "2025-02", "play_count": 1} in body["timeline"]

    def test_artist_detail_month_timeframe_uses_daily_timeline(self, client, db):
        _seed(db)

        resp = client.get("/api/artists/a1?timeframe=month&date=2025-01-15")

        assert resp.status_code == 200
        body = resp.json()
        assert body["artist"]["total_play_count"] == 2
        assert body["granularity"] == "daily"
        assert body["timeline"] == _full_month(2025, 1, {"2025-01-15": 1, "2025-01-16": 1})

    def test_artist_detail_not_found(self, client):
        resp = client.get("/api/artists/missing")

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "ARTIST_NOT_FOUND"

    def test_artist_detail_invalid_date(self, client):
        resp = client.get("/api/artists/a1?date=bad-date")

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_DATE"

    def test_artist_detail_rejects_unbounded_limit(self, client):
        resp = client.get("/api/artists/a1?limit=500")

        assert resp.status_code == 422


class TestTrackDetailRoute:
    def test_track_detail_by_id(self, client, db):
        _seed(db)

        resp = client.get("/api/tracks/t1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["track"]["spotify_id"] == "t1"
        assert body["track"]["name"] == "Track 1"
        assert body["track"]["artist_id"] == "a1"
        assert body["track"]["album_image"] == "https://img/t1.jpg"
        assert body["track"]["duration_ms"] == 200_000
        assert body["track"]["total_play_count"] == 2
        assert body["timeline"] == [{"period": "2025-01", "play_count": 2}]

    def test_track_detail_by_id_month_timeframe(self, client, db):
        _seed(db)

        resp = client.get("/api/tracks/t1?timeframe=month&date=2025-01-01")

        assert resp.status_code == 200
        body = resp.json()
        assert body["granularity"] == "daily"
        assert body["timeline"] == _full_month(2025, 1, {"2025-01-15": 1, "2025-01-16": 1})

    def test_track_detail_by_name_fallback(self, client, db):
        _seed(db)

        resp = client.get(
            "/api/tracks/by-name",
            params={"track_name": "Loose Track", "artist_name": "Loose Artist"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["track"]["spotify_id"] is None
        assert body["track"]["name"] == "Loose Track"
        assert body["track"]["artist_name"] == "Loose Artist"
        assert body["track"]["total_play_count"] == 1
        assert body["timeline"] == [{"period": "2025-01", "play_count": 1}]

    def test_track_detail_by_name_not_found(self, client):
        resp = client.get(
            "/api/tracks/by-name",
            params={"track_name": "Nope", "artist_name": "Nobody"},
        )

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "TRACK_NOT_FOUND"

    def test_track_detail_by_id_not_found(self, client):
        resp = client.get("/api/tracks/missing")

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "TRACK_NOT_FOUND"


class TestAlbumDetailRoute:
    def test_album_detail_rejects_unbounded_limit(self, client):
        resp = client.get(
            "/api/albums/by-name",
            params={"artist_id": "a1", "album_name": "Alb 1", "limit": 500},
        )

        assert resp.status_code == 422
