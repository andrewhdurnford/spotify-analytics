"""Smoke tests for root endpoints and cron authentication."""

import os
from unittest.mock import AsyncMock, patch


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
        assert "endpoint" in body


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
        with patch("app.main.settings", Settings(cron_secret="test-secret-123")):
            with patch(
                "app.tasks.sync_recent_tracks.fetch_and_store_recent_tracks",
                new_callable=AsyncMock,
            ) as mock_sync:
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

        with patch("app.main.settings", Settings(cron_secret="test-secret-123")):
            with patch(
                "app.tasks.sync_recent_tracks.fetch_and_store_recent_tracks",
                new_callable=AsyncMock,
            ) as mock_sync:
                mock_sync.side_effect = Exception("boom")
                resp = client.post(
                    "/api/cron/sync-tracks",
                    headers={"Authorization": "Bearer test-secret-123"},
                )
                assert resp.status_code == 500
