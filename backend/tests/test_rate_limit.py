"""Unit tests for the RateLimiter primitive, plus integration tests that
exercise real routes to confirm the configured per-endpoint thresholds
actually return 429 once exceeded.
"""

from datetime import datetime

from app.models import Artist, Stream, Track
from app.services.rate_limit import RateLimiter

VALID_DB_URL = "postgresql://test:test@localhost/test?sslmode=require"


def _seed(db):
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


class TestRateLimiterUnit:
    def test_allows_requests_within_the_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        assert limiter.allow("ip-1") is True
        assert limiter.allow("ip-1") is True
        assert limiter.allow("ip-1") is True

    def test_blocks_requests_once_limit_is_exceeded(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            assert limiter.allow("ip-1") is True
        assert limiter.allow("ip-1") is False
        # Still blocked -- exceeding the limit doesn't reset anything.
        assert limiter.allow("ip-1") is False

    def test_keys_are_tracked_independently(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        assert limiter.allow("ip-1") is True
        assert limiter.allow("ip-1") is False
        assert limiter.allow("ip-2") is True

    def test_budget_resets_in_a_new_window(self):
        now = [0.0]
        limiter = RateLimiter(max_requests=2, window_seconds=60, clock=lambda: now[0])
        assert limiter.allow("ip-1") is True
        assert limiter.allow("ip-1") is True
        assert limiter.allow("ip-1") is False

        now[0] += 61
        assert limiter.allow("ip-1") is True

    def test_reset_clears_all_tracked_keys(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        assert limiter.allow("ip-1") is True
        assert limiter.allow("ip-1") is False
        limiter.reset()
        assert limiter.allow("ip-1") is True

    def test_tracked_keys_are_bounded_and_lru_evicted(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60, max_tracked_keys=2)
        limiter.allow("ip-1")
        limiter.allow("ip-2")
        limiter.allow("ip-3")  # evicts ip-1 (least recently used)

        # ip-1's budget was forgotten, so it's treated as fresh.
        assert limiter.allow("ip-1") is True

    def test_rejects_non_positive_max_requests(self):
        import pytest

        with pytest.raises(ValueError):
            RateLimiter(max_requests=0, window_seconds=60)

    def test_rejects_non_positive_window(self):
        import pytest

        with pytest.raises(ValueError):
            RateLimiter(max_requests=1, window_seconds=0)

    def test_rejects_non_positive_max_tracked_keys(self):
        import pytest

        with pytest.raises(ValueError):
            RateLimiter(max_requests=1, window_seconds=60, max_tracked_keys=0)


class TestGeneralAnalyticsRateLimit:
    def test_returns_429_after_60_requests_per_minute(self, client, db):
        _seed(db)
        for _ in range(60):
            resp = client.get("/api/top/songs")
            assert resp.status_code == 200

        blocked = client.get("/api/top/songs")
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["error"]["code"] == "RATE_LIMITED"

    def test_spoofed_forwarded_for_does_not_create_fresh_buckets(self, client, db):
        _seed(db)
        for index in range(60):
            resp = client.get("/api/top/songs", headers={"X-Forwarded-For": f"203.0.113.{index}"})
            assert resp.status_code == 200

        blocked = client.get(
            "/api/top/songs",
            headers={"X-Forwarded-For": "198.51.100.250"},
        )
        assert blocked.status_code == 429

    def test_general_budgets_are_scoped_per_route(self, client, db):
        _seed(db)
        for _ in range(60):
            assert client.get("/api/top/songs").status_code == 200

        assert client.get("/api/top/songs").status_code == 429
        assert client.get("/api/top/artists").status_code == 200

    def test_rotating_path_parameters_share_one_budget(self, client, db):
        """Keying on the concrete path would mint a fresh 60/min bucket per
        artist_id, leaving the per-entity DB queries effectively unlimited.
        """
        _seed(db)
        for index in range(60):
            resp = client.get(f"/api/artists/rotating-{index}")
            # 404s still count -- the DB work happens before the miss.
            assert resp.status_code == 404

        blocked = client.get("/api/artists/rotating-fresh-id")
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["error"]["code"] == "RATE_LIMITED"

    def test_rotating_path_parameters_cannot_flush_other_buckets(self, client, db):
        """Distinct paths must not insert distinct limiter keys, otherwise a
        client could LRU-evict its own throttled bucket (or other clients')
        by minting enough of them.
        """
        from app.services.rate_limit import general_limiter

        _seed(db)
        for index in range(30):
            client.get(f"/api/artists/rotating-{index}")

        assert len(general_limiter._buckets) == 1

    def test_stays_within_bounds_below_the_threshold(self, client, db):
        _seed(db)
        for _ in range(59):
            resp = client.get("/api/top/artists")
            assert resp.status_code == 200


class TestCurrentlyPlayingRateLimit:
    def test_returns_429_after_10_requests_per_minute(self, client):
        from unittest.mock import AsyncMock

        from app.dependencies import get_spotify_client
        from app.main import app

        mock_client = AsyncMock()
        mock_client.get_currently_playing.return_value = {"is_playing": False}
        app.dependency_overrides[get_spotify_client] = lambda: mock_client
        try:
            for _ in range(10):
                resp = client.get("/api/top/currently-playing")
                assert resp.status_code == 200
            blocked = client.get("/api/top/currently-playing")
            assert blocked.status_code == 429
        finally:
            app.dependency_overrides.pop(get_spotify_client, None)


class TestRelatedTracksRateLimit:
    def test_returns_429_after_5_requests_per_minute(self, client, db):
        _seed(db)
        for _ in range(5):
            resp = client.get("/api/tracks/t1/related")
            assert resp.status_code == 200
        blocked = client.get("/api/tracks/t1/related")
        assert blocked.status_code == 429


class TestCronSyncRateLimit:
    def test_returns_429_after_2_requests_per_minute(self, client):
        # Both requests are unauthorized (no secret configured in tests),
        # but the rate limiter dependency runs regardless of auth outcome.
        for _ in range(2):
            resp = client.post("/api/cron/sync-tracks")
            assert resp.status_code == 401
        blocked = client.post("/api/cron/sync-tracks")
        assert blocked.status_code == 429

    def test_unauthenticated_flood_does_not_starve_the_real_cron(self, client):
        """The limiter runs before auth, so an unauthenticated flood must not
        consume the legitimate cron job's allowance -- otherwise anyone
        (especially behind a proxy, where all callers share one client key)
        could silently break sync.
        """
        from unittest.mock import AsyncMock, patch

        from app.config import Settings

        for _ in range(5):
            client.post("/api/cron/sync-tracks")
        assert client.post("/api/cron/sync-tracks").status_code == 429

        settings = Settings(database_url=VALID_DB_URL, cron_secret="test-secret-123")
        with (
            patch("app.services.cron_auth.settings", settings),
            patch("app.main.settings", settings),
            patch(
                "app.tasks.sync_recent_tracks.sync_recent_plays",
                new_callable=AsyncMock,
            ),
        ):
            resp = client.post(
                "/api/cron/sync-tracks",
                headers={"Authorization": "Bearer test-secret-123"},
            )
            assert resp.status_code == 200

    def test_authenticated_requests_are_still_bounded(self, client):
        from unittest.mock import AsyncMock, patch

        from app.config import Settings

        settings = Settings(database_url=VALID_DB_URL, cron_secret="test-secret-123")
        headers = {"Authorization": "Bearer test-secret-123"}
        with (
            patch("app.services.cron_auth.settings", settings),
            patch("app.main.settings", settings),
            patch(
                "app.tasks.sync_recent_tracks.sync_recent_plays",
                new_callable=AsyncMock,
            ),
        ):
            for _ in range(10):
                assert client.post("/api/cron/sync-tracks", headers=headers).status_code == 200
            blocked = client.post("/api/cron/sync-tracks", headers=headers)
            assert blocked.status_code == 429
