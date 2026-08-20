"""Tests for the sync_recent_tracks task orchestration: cold-start cursor
seeding, pagination, and retry/backoff around the Spotify fetch.

Ratio/dedup/storage logic is covered separately in
test_services_recent_plays_sync.py -- these tests mock
`process_recently_played_items` where they only care about fetch behavior.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import Artist, RecentPlaysSyncMeta, Stream, Track


def _recently_played_page(items: list[dict], after_cursor: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "items": items,
        "cursors": {"after": after_cursor} if after_cursor else {},
    }
    return resp


def _item(played_at: datetime, track: dict) -> dict:
    return {"played_at": played_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "track": track}


def _track(track_id: str, duration_ms: int = 200_000) -> dict:
    return {
        "id": track_id,
        "name": "Song",
        "type": "track",
        "artists": [{"id": "artist-1", "name": "Artist"}],
        "album": {"name": "Album", "images": [{"url": "https://img/album.jpg"}]},
        "duration_ms": duration_ms,
        "popularity": 75,
    }


class TestColdStart:
    @pytest.mark.asyncio
    async def test_seeds_cursor_and_does_not_call_spotify(self, db):
        mock_get_spotify_client = AsyncMock()

        with (
            patch("app.tasks.sync_recent_tracks.get_spotify_client", mock_get_spotify_client),
            patch("app.tasks.sync_recent_tracks.SessionLocal", return_value=db),
        ):
            from app.tasks.sync_recent_tracks import sync_recent_plays

            result = await sync_recent_plays()

        assert result is None
        mock_get_spotify_client.assert_not_called()
        meta = db.execute(select(RecentPlaysSyncMeta)).scalar_one()
        assert meta.cursor_played_at is not None


class TestFetchAndStore:
    @pytest.mark.asyncio
    async def test_stores_a_fully_played_track(self, db):
        cursor = datetime(2025, 1, 1, 12, 0, 0)
        db.add(RecentPlaysSyncMeta(id=1, cursor_played_at=cursor))
        db.commit()

        played_at = datetime(2025, 1, 1, 12, 3, 20)  # 200s later == full duration
        track = _track("track-1")

        page_resp = _recently_played_page([_item(played_at, track)])
        artist_resp = MagicMock()
        artist_resp.status_code = 200
        artist_resp.json.return_value = {
            "id": "artist-1",
            "name": "Artist",
            "genres": ["indie"],
            "popularity": 70,
            "images": [{"url": "https://img/artist.jpg"}],
        }

        mock_spotify_client = AsyncMock()
        mock_spotify_client.access_token = "fake-token"

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(side_effect=[page_resp, artist_resp])
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "app.tasks.sync_recent_tracks.get_spotify_client",
                new_callable=AsyncMock,
                return_value=mock_spotify_client,
            ),
            patch("app.tasks.sync_recent_tracks.SessionLocal", return_value=db),
            patch("httpx.AsyncClient", return_value=mock_http_client),
        ):
            from app.tasks.sync_recent_tracks import sync_recent_plays

            result = await sync_recent_plays()

        assert result.inserted == 1
        assert db.execute(select(Stream)).scalar_one().track_id == "track-1"
        assert db.execute(select(Track)).scalar_one().spotify_id == "track-1"
        assert db.execute(select(Artist)).scalar_one().spotify_id == "artist-1"

    @pytest.mark.asyncio
    async def test_no_new_items_is_a_no_op(self, db):
        cursor = datetime(2025, 1, 1, 12, 0, 0)
        db.add(RecentPlaysSyncMeta(id=1, cursor_played_at=cursor))
        db.commit()

        mock_spotify_client = AsyncMock()
        mock_spotify_client.access_token = "fake-token"

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=_recently_played_page([]))
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "app.tasks.sync_recent_tracks.get_spotify_client",
                new_callable=AsyncMock,
                return_value=mock_spotify_client,
            ),
            patch("app.tasks.sync_recent_tracks.SessionLocal", return_value=db),
            patch("httpx.AsyncClient", return_value=mock_http_client),
        ):
            from app.tasks.sync_recent_tracks import sync_recent_plays

            result = await sync_recent_plays()

        assert result is None
        assert db.execute(select(Stream)).scalars().all() == []


class TestPagination:
    @pytest.mark.asyncio
    async def test_pages_forward_when_a_page_is_full(self):
        from app.tasks.sync_recent_tracks import (
            RECENTLY_PLAYED_PAGE_LIMIT,
            _fetch_all_pages,
        )

        full_page_items = [
            _item(datetime(2025, 1, 1, 12, i, 0), _track(f"track-{i}"))
            for i in range(RECENTLY_PLAYED_PAGE_LIMIT)
        ]
        partial_page_items = [_item(datetime(2025, 1, 1, 13, 0, 0), _track("track-last"))]

        page1 = _recently_played_page(full_page_items, after_cursor="1700000000000")
        page2 = _recently_played_page(partial_page_items)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[page1, page2])

        items = await _fetch_all_pages(mock_client, "fake-token", after_ms=1_600_000_000_000)

        assert len(items) == RECENTLY_PLAYED_PAGE_LIMIT + 1
        assert mock_client.get.call_count == 2
        second_call_params = mock_client.get.call_args_list[1].kwargs["params"]
        assert second_call_params["after"] == 1700000000000

    @pytest.mark.asyncio
    async def test_stops_at_max_pages_even_if_every_page_is_full(self):
        from app.tasks.sync_recent_tracks import (
            MAX_PAGES_PER_RUN,
            RECENTLY_PLAYED_PAGE_LIMIT,
            _fetch_all_pages,
        )

        full_page_items = [
            _item(datetime(2025, 1, 1, 12, i, 0), _track(f"track-{i}"))
            for i in range(RECENTLY_PLAYED_PAGE_LIMIT)
        ]

        def full_page(*_args, **_kwargs):
            return _recently_played_page(full_page_items, after_cursor="1700000000000")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=lambda *a, **k: full_page())

        items = await _fetch_all_pages(mock_client, "fake-token", after_ms=1_600_000_000_000)

        assert mock_client.get.call_count == MAX_PAGES_PER_RUN
        assert len(items) == MAX_PAGES_PER_RUN * RECENTLY_PLAYED_PAGE_LIMIT


class TestRetry:
    @pytest.mark.asyncio
    async def test_retries_on_429_then_succeeds(self):
        from app.tasks.sync_recent_tracks import _fetch_recently_played_page

        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "0"}

        ok = _recently_played_page([])

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[rate_limited, ok])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            page = await _fetch_recently_played_page(mock_client, "fake-token", after_ms=1)

        assert page == {"items": [], "cursors": {}}
        assert mock_client.get.call_count == 2
        mock_sleep.assert_awaited_once_with(0.0)

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries_on_persistent_5xx(self):
        from app.tasks.sync_recent_tracks import MAX_FETCH_ATTEMPTS, _fetch_recently_played_page

        server_error = MagicMock()
        server_error.status_code = 500
        server_error.text = "Internal Server Error"
        server_error.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=server_error)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            page = await _fetch_recently_played_page(mock_client, "fake-token", after_ms=1)

        assert page is None
        assert mock_client.get.call_count == MAX_FETCH_ATTEMPTS

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retryable_error(self):
        from app.tasks.sync_recent_tracks import _fetch_recently_played_page

        unauthorized = MagicMock()
        unauthorized.status_code = 401
        unauthorized.text = "Unauthorized"
        unauthorized.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=unauthorized)

        page = await _fetch_recently_played_page(mock_client, "fake-token", after_ms=1)

        assert page is None
        assert mock_client.get.call_count == 1


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_skips_run_if_already_in_progress(self, db):
        import app.tasks.sync_recent_tracks as sync_module

        async with sync_module._sync_lock:
            result = await sync_module.sync_recent_plays()

        assert result is None
