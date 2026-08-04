"""Security/hardening-focused tests for /api/{artists,albums,tracks}* detail
routes: parameter bounds, caching, and rate limiting. Kept separate from
tests/test_routes_detail.py (behavioral coverage) to keep this slice's diff
self-contained.
"""

from datetime import datetime
from unittest.mock import patch

from app.models import Artist, Stream, Track
from app.services import detail as detail_service
from app.services.dashboard_cache import dashboard_cache

_real_get_related_tracks = detail_service.get_related_tracks


def _seed(db):
    artist = Artist(
        spotify_id="a1",
        name="Art 1",
        genres=["rock"],
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
            album_name="Alb 1",
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
            track_name="Track 2",
            artist_name="Art 1",
            album_name="Alb 1",
            played_at=datetime(2025, 1, 15, 10, 5),
            duration_ms=180_000,
            track_id="t2",
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
            album_name="Alb 1",
            played_at=datetime(2025, 1, 16, 10, 5),
            duration_ms=180_000,
            track_id="t2",
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
        Stream(
            track_name="Track 2",
            artist_name="Art 1",
            album_name="Alb 1",
            played_at=datetime(2025, 1, 17, 10, 5),
            duration_ms=180_000,
            track_id="t2",
            artist_id="a1",
        ),
    ]
    db.add_all([artist, *tracks, *streams])
    db.commit()


class TestArtistDetailBounds:
    def test_limit_above_max_returns_422(self, client, db):
        _seed(db)
        resp = client.get("/api/artists/a1?limit=51")
        assert resp.status_code == 422

    def test_limit_at_max_is_accepted(self, client, db):
        _seed(db)
        resp = client.get("/api/artists/a1?limit=50")
        assert resp.status_code == 200

    def test_artist_id_over_max_length_returns_422(self, client):
        resp = client.get(f"/api/artists/{'a' * 101}")
        assert resp.status_code == 422


class TestAlbumDetailBounds:
    def test_limit_above_max_returns_422(self, client, db):
        _seed(db)
        resp = client.get("/api/albums/by-name?artist_id=a1&album_name=Alb+1&limit=51")
        assert resp.status_code == 422

    def test_album_name_over_max_length_returns_422(self, client):
        resp = client.get(f"/api/albums/by-name?artist_id=a1&album_name={'x' * 513}")
        assert resp.status_code == 422

    def test_missing_album_name_returns_422(self, client):
        resp = client.get("/api/albums/by-name?artist_id=a1")
        assert resp.status_code == 422


class TestTrackByNameBounds:
    def test_track_name_over_max_length_returns_422(self, client):
        resp = client.get(f"/api/tracks/by-name?track_name={'x' * 256}&artist_name=Art+1")
        assert resp.status_code == 422

    def test_empty_track_name_returns_422(self, client):
        resp = client.get("/api/tracks/by-name?track_name=&artist_name=Art+1")
        assert resp.status_code == 422


class TestTrackDetailBounds:
    def test_track_id_over_max_length_returns_422(self, client):
        resp = client.get(f"/api/tracks/{'t' * 101}")
        assert resp.status_code == 422


class TestRelatedTracksBoundsAndCaching:
    def test_limit_above_max_returns_422(self, client, db):
        _seed(db)
        resp = client.get("/api/tracks/t1/related?limit=51")
        assert resp.status_code == 422

    def test_track_id_over_max_length_returns_422(self, client):
        resp = client.get(f"/api/tracks/{'t' * 101}/related")
        assert resp.status_code == 422

    def test_every_call_hits_the_service_and_never_the_cache(self, client, db):
        """This route is deliberately uncached (see the route's docstring).

        It reads a precomputed rollup, so a cache entry would only burn a slot
        in the shared bounded pool and add staleness. If a `get_or_set` wrapper
        is ever reintroduced here, this fails.
        """
        _seed(db)
        with patch(
            "app.services.detail.get_related_tracks",
            wraps=_real_get_related_tracks,
        ) as mock_get_related:
            first = client.get("/api/tracks/t1/related")
            second = client.get("/api/tracks/t1/related")

            assert first.status_code == 200
            assert second.status_code == 200
            assert first.json() == second.json()
            assert mock_get_related.call_count == 2
        assert len(dashboard_cache) == 0
