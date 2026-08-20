"""Smoke tests for root endpoints and cron authentication."""

import asyncio
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings
from app.main import (
    DEV_CORS_ORIGINS,
    PRODUCTION_CORS_ORIGINS,
    _run_with_timeout,
    cors_origins_for,
    docs_urls_for,
)
from app.models import Stream, Track
from app.services.related_tracks_rollup import (
    get_last_rebuilt_at,
    rebuild_related_tracks_rollup,
)

from .conftest import TestingSessionLocal

VALID_DB_URL = "postgresql://test:test@localhost/test?sslmode=require"


class TestRootEndpoints:
    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Spotify Analytics API"

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"

    def test_sync_status(self, client):
        resp = client.get("/api/sync-status")
        assert resp.status_code == 200
        body = resp.json()
        assert "interval" in body

    def test_sync_status_does_not_leak_cron_endpoint_path(self, client):
        """The cron endpoint path shouldn't be published to unauthenticated callers."""
        resp = client.get("/api/sync-status")
        assert resp.status_code == 200
        body = resp.json()
        assert "endpoint" not in body
        assert "/api/cron/sync-tracks" not in resp.text


class TestCronSyncTracks:
    def test_rejects_missing_auth(self, client):
        resp = client.post("/api/cron/sync-tracks")
        assert resp.status_code == 401

    def test_rejects_wrong_token(self, client):
        resp = client.post(
            "/api/cron/sync-tracks",
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert resp.status_code == 401

    @patch.dict(os.environ, {"CRON_SECRET": "test-secret-123"})
    def test_accepts_correct_token(self, client):
        from app.config import Settings

        # Reload settings to pick up the env override
        with (
            patch("app.services.cron_auth.settings", Settings(cron_secret="test-secret-123")),
            patch("app.main.settings", Settings(cron_secret="test-secret-123")),
            patch(
                "app.tasks.sync_recent_tracks.sync_recent_plays",
                new_callable=AsyncMock,
            ) as mock_sync,
        ):
            mock_sync.return_value = None
            resp = client.post(
                "/api/cron/sync-tracks",
                headers={"Authorization": "Bearer test-secret-123"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "success"

    @patch.dict(os.environ, {"CRON_SECRET": "test-secret-123"})
    def test_returns_500_when_sync_fails(self, client):
        from app.config import Settings

        with (
            patch("app.services.cron_auth.settings", Settings(cron_secret="test-secret-123")),
            patch("app.main.settings", Settings(cron_secret="test-secret-123")),
            patch(
                "app.tasks.sync_recent_tracks.sync_recent_plays",
                new_callable=AsyncMock,
            ) as mock_sync,
        ):
            mock_sync.side_effect = Exception("boom")
            resp = client.post(
                "/api/cron/sync-tracks",
                headers={"Authorization": "Bearer test-secret-123"},
            )
            assert resp.status_code == 500

    def test_empty_configured_secret_rejects_matching_blank_bearer_header(self, client):
        """Defense in depth: even if CRON_SECRET were ever blank at runtime,
        the endpoint must not authenticate a client sending the resulting
        'Bearer ' token (or no token at all).
        """
        with patch(
            "app.services.cron_auth.settings",
            Settings(database_url=VALID_DB_URL, cron_secret=""),
        ):
            resp = client.post(
                "/api/cron/sync-tracks",
                headers={"Authorization": "Bearer "},
            )
            assert resp.status_code == 401

    @patch.dict(os.environ, {"CRON_SECRET": "test-secret-123"})
    def test_uses_constant_time_comparison(self, client):
        """Guards against a future regression back to `==` comparison by
        asserting `secrets.compare_digest` is actually invoked.
        """
        with (
            patch("app.services.cron_auth.settings", Settings(cron_secret="test-secret-123")),
            patch(
                "app.services.cron_auth.secrets.compare_digest",
                wraps=__import__("secrets").compare_digest,
            ) as mock_compare,
        ):
            resp = client.post(
                "/api/cron/sync-tracks",
                headers={"Authorization": "Bearer wrong"},
            )
            assert resp.status_code == 401
            assert mock_compare.called

    def test_non_ascii_authorization_header_is_rejected_not_a_500(self, client):
        """`secrets.compare_digest` raises TypeError on non-ASCII `str`, and
        Starlette decodes headers as latin-1 -- comparing bytes keeps a header
        like `Bearer é` a 401 instead of an attacker-triggerable 500.
        """
        with patch(
            "app.services.cron_auth.settings",
            Settings(database_url=VALID_DB_URL, cron_secret="test-secret-123"),
        ):
            resp = client.post(
                "/api/cron/sync-tracks",
                headers={"Authorization": "Bearer é".encode("latin-1")},
            )
            assert resp.status_code == 401


class TestCronRebuildRelatedTracks:
    """The rollup rebuild trigger: auth, 202-then-background, self-throttle."""

    AUTH = {"Authorization": "Bearer test-secret-123"}

    @contextmanager
    def _authorized(self):
        """Valid CRON_SECRET, with the background task on the test database.

        `run_related_tracks_rebuild` deliberately opens its own session (a
        `Depends(get_db)` one is closed before the background task runs), so
        the test has to point that sessionmaker at the test engine.
        """
        with (
            patch("app.services.cron_auth.settings", Settings(cron_secret="test-secret-123")),
            patch("app.main.SessionLocal", TestingSessionLocal),
        ):
            yield

    def _seed_one_session(self, db):
        db.add(Track(spotify_id="r1", name="R1", artist_name="A"))
        db.add(
            Stream(
                track_name="R1",
                artist_name="A",
                played_at=datetime(2025, 5, 1, 12, 0),
                track_id="r1",
            )
        )
        db.commit()

    def test_rejects_missing_auth(self, client):
        resp = client.post("/api/cron/rebuild-related-tracks")
        assert resp.status_code == 401

    def test_rejects_wrong_token(self, client):
        with patch("app.services.cron_auth.settings", Settings(cron_secret="test-secret-123")):
            resp = client.post(
                "/api/cron/rebuild-related-tracks",
                headers={"Authorization": "Bearer wrong-secret"},
            )
        assert resp.status_code == 401

    def test_accepts_and_runs_the_rebuild_in_the_background(self, client, db):
        self._seed_one_session(db)

        with self._authorized():
            resp = client.post("/api/cron/rebuild-related-tracks", headers=self.AUTH)

        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"
        # TestClient runs background tasks before returning, so by now the
        # rebuild has committed.
        assert get_last_rebuilt_at(db) is not None

    def test_second_call_within_the_throttle_window_is_skipped(self, client, db):
        self._seed_one_session(db)

        with self._authorized():
            first = client.post("/api/cron/rebuild-related-tracks", headers=self.AUTH)
            with patch("app.main.run_related_tracks_rebuild") as mock_rebuild:
                second = client.post("/api/cron/rebuild-related-tracks", headers=self.AUTH)

        assert first.json()["status"] == "accepted"
        assert second.status_code == 202
        assert second.json()["status"] == "skipped"
        mock_rebuild.assert_not_called()

    def test_rebuild_runs_again_once_the_throttle_window_has_passed(self, client, db):
        self._seed_one_session(db)

        with self._authorized():
            client.post("/api/cron/rebuild-related-tracks", headers=self.AUTH)
            with patch("app.main.MIN_RELATED_REBUILD_INTERVAL", timedelta(0)):
                second = client.post("/api/cron/rebuild-related-tracks", headers=self.AUTH)

        assert second.json()["status"] == "accepted"

    def test_background_failure_does_not_surface_as_a_500(self, client, db):
        """The handler has already answered 202 by the time the rebuild runs,
        so a failing rebuild must be logged and swallowed, not raised into the
        response cycle.
        """
        self._seed_one_session(db)

        with (
            self._authorized(),
            patch(
                "app.main.rebuild_related_tracks_rollup",
                side_effect=RuntimeError("boom"),
            ),
        ):
            resp = client.post("/api/cron/rebuild-related-tracks", headers=self.AUTH)

        assert resp.status_code == 202
        assert get_last_rebuilt_at(db) is None

    def test_rate_limit_budget_is_separate_from_sync_tracks(self, client):
        """A misconfigured rebuild schedule must not starve the sync job.

        The unauthenticated cron limiter allows 2/minute. Exhausting it on the
        rebuild route leaves the sync route's own budget untouched.
        """
        for _ in range(2):
            assert client.post("/api/cron/rebuild-related-tracks").status_code == 401
        assert client.post("/api/cron/rebuild-related-tracks").status_code == 429

        # Still reaching the handler's auth check, not the limiter.
        assert client.post("/api/cron/sync-tracks").status_code == 401


class TestSyncStatusRollupVisibility:
    def test_reports_last_rebuilt_at_once_the_rollup_has_been_built(self, client, db):
        assert (
            client.get("/api/sync-status").json()["related_tracks_rollup_last_rebuilt_at"] is None
        )

        db.add(
            Stream(
                track_name="R1",
                artist_name="A",
                played_at=datetime(2025, 5, 1, 12, 0),
                track_id="r1",
            )
        )
        db.commit()
        rebuild_related_tracks_rollup(db)
        db.commit()

        body = client.get("/api/sync-status").json()
        assert body["related_tracks_rollup_last_rebuilt_at"] is not None


class TestDocsUrlsForEnvironment:
    def test_production_disables_docs_redoc_and_openapi(self):
        assert docs_urls_for("production") == (None, None, None)

    def test_non_production_exposes_docs_redoc_and_openapi(self):
        assert docs_urls_for("development") == ("/docs", "/redoc", "/openapi.json")

    def test_docs_are_reachable_in_the_current_test_environment(self, client):
        """The real `app` singleton is built once at import time under the
        test env (ENVIRONMENT=development by default), so /docs should be
        reachable -- confirming the dev experience isn't regressed.
        """
        resp = client.get("/docs")
        assert resp.status_code == 200
        resp = client.get("/openapi.json")
        assert resp.status_code == 200


class TestCorsOriginsForSettings:
    def test_development_uses_localhost_only(self):
        settings = Settings(database_url=VALID_DB_URL, environment="development")
        assert cors_origins_for(settings) == DEV_CORS_ORIGINS

    def test_production_uses_known_frontend_origins(self):
        settings = Settings(
            database_url=VALID_DB_URL,
            environment="production",
            frontend_url="https://spotify.andrewdurnford.com",
            cron_secret="secret",
        )
        origins = cors_origins_for(settings)
        assert set(PRODUCTION_CORS_ORIGINS).issubset(set(origins))
        assert "http://localhost:3000" not in origins

    def test_no_wildcard_origin_in_any_environment(self):
        for environment in ("development", "production"):
            settings = Settings(
                database_url=VALID_DB_URL, environment=environment, cron_secret="secret"
            )
            assert "*" not in cors_origins_for(settings)

    def test_custom_frontend_url_is_appended_once(self):
        settings = Settings(
            database_url=VALID_DB_URL,
            environment="production",
            frontend_url="https://custom.example.com",
            cron_secret="secret",
        )
        origins = cors_origins_for(settings)
        assert origins.count("https://custom.example.com") == 1

    def test_frontend_url_already_present_is_not_duplicated(self):
        settings = Settings(
            database_url=VALID_DB_URL,
            environment="development",
            frontend_url="http://localhost:3000",
        )
        origins = cors_origins_for(settings)
        assert origins.count("http://localhost:3000") == 1


class TestRequestTimeout:
    async def test_fast_call_returns_normally(self):
        async def fast():
            return "ok"

        result = await _run_with_timeout(fast, timeout_seconds=5)
        assert result == "ok"

    async def test_slow_call_returns_structured_504(self):
        async def slow():
            await asyncio.sleep(5)
            return "too-late"

        response = await _run_with_timeout(slow, timeout_seconds=0.01)
        assert response.status_code == 504
        import json

        body = json.loads(response.body)
        assert body["detail"]["error"]["code"] == "REQUEST_TIMEOUT"


class TestTrustedHostMiddlewareWiring:
    """Exercises TrustedHostMiddleware directly against a minimal app built
    with restrictive allowed_hosts, since the real `app` singleton is built
    once at import time with the permissive test-env default ("*").
    """

    def test_rejects_unrecognized_host_header(self):
        test_app = FastAPI()
        test_app.add_middleware(TrustedHostMiddleware, allowed_hosts=["expected.example.com"])

        @test_app.get("/ping")
        def ping():
            return {"ok": True}

        client = TestClient(test_app, base_url="http://not-expected.example.com")
        resp = client.get("/ping")
        assert resp.status_code == 400

    def test_allows_recognized_host_header(self):
        test_app = FastAPI()
        test_app.add_middleware(TrustedHostMiddleware, allowed_hosts=["expected.example.com"])

        @test_app.get("/ping")
        def ping():
            return {"ok": True}

        client = TestClient(test_app, base_url="http://expected.example.com")
        resp = client.get("/ping")
        assert resp.status_code == 200

    def test_wildcard_allows_any_host(self, client):
        """The real app's default (ALLOWED_HOSTS="*") shouldn't reject the
        TestClient's default Host header.
        """
        resp = client.get("/health")
        assert resp.status_code == 200


class TestCorsCredentialsAndHeaders:
    def test_preflight_reflects_restricted_methods_and_headers(self, client):
        resp = client.options(
            "/api/top/songs",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert resp.status_code == 200
        assert "Access-Control-Allow-Credentials" not in resp.headers
        assert resp.headers.get("access-control-allow-methods") is not None

    def test_disallowed_origin_is_not_reflected(self, client):
        resp = client.get(
            "/health",
            headers={"Origin": "https://evil.example.com"},
        )
        # The request itself isn't blocked (CORS is enforced by the
        # browser), but the disallowed origin must not be echoed back.
        assert resp.headers.get("access-control-allow-origin") != "https://evil.example.com"


class TestRateLimitedCronRequiresGeneralCoverage:
    """Sanity check that /api/cron/sync-tracks is covered by its own
    limiter, independent of the general-analytics endpoints (regression
    guard for the per-endpoint bucket wiring)."""

    def test_cron_endpoint_has_its_own_budget_separate_from_analytics(self, client, db):
        from datetime import datetime as dt

        from app.models import Artist, Stream, Track

        db.add_all(
            [
                Track(
                    spotify_id="t1",
                    name="Track 1",
                    artist_name="Art 1",
                    album_name="Alb 1",
                    album_image="https://img/1.jpg",
                    duration_ms=200_000,
                ),
                Artist(
                    spotify_id="a1",
                    name="Art 1",
                    genres=["rock"],
                    popularity=90,
                    image_url="https://img/a1.jpg",
                ),
            ]
        )
        db.flush()
        db.add(
            Stream(
                track_name="Track 1",
                artist_name="Art 1",
                album_name="Alb 1",
                played_at=dt(2025, 1, 15, 10, 0),
                duration_ms=200_000,
                track_id="t1",
                artist_id="a1",
            )
        )
        db.commit()

        # Exhaust the cron limiter (2/min) without affecting the general
        # analytics limiter (60/min).
        client.post("/api/cron/sync-tracks")
        client.post("/api/cron/sync-tracks")
        assert client.post("/api/cron/sync-tracks").status_code == 429

        # Analytics endpoints are unaffected.
        assert client.get("/api/top/songs").status_code == 200
