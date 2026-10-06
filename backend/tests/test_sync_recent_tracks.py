"""Tests for the sync_recent_tracks task.

Since this task talks to the Spotify API and manages its own DB session,
we test the core logic by mocking external dependencies.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select

from app.models import Artist, Stream, Track


@pytest.fixture()
def mock_recently_played_response():
    """A realistic Spotify recently-played API response (completed plays only)."""
    return {
        "items": [
            {
                "played_at": "2026-10-06T22:20:47.401Z",
                "track": {
                    "id": "sp_track_1",
                    "name": "New Song",
                    "type": "track",
                    "artists": [{"id": "sp_artist_1", "name": "New Artist"}],
                    "album": {
                        "name": "New Album",
                        "images": [{"url": "https://img/new.jpg"}],
                    },
                    "duration_ms": 165_428,
                    "popularity": 75,
                },
            }
        ]
    }


@pytest.fixture()
def mock_artist_response():
    """A realistic Spotify artist API response."""
    return {
        "id": "sp_artist_1",
        "name": "New Artist",
        "genres": ["indie"],
        "popularity": 70,
        "images": [{"url": "https://img/artist_new.jpg"}],
    }


def make_response(status_code, body=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    response.text = str(body)
    return response


async def run_sync(db, responses):
    mock_spotify_client = AsyncMock()
    mock_spotify_client.access_token = "fake-token"

    mock_http_client = AsyncMock()
    mock_http_client.get = AsyncMock(side_effect=responses)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "app.tasks.sync_recent_tracks.get_spotify_client",
            new_callable=AsyncMock,
            return_value=mock_spotify_client,
        ),
        patch(
            "app.tasks.sync_recent_tracks.SessionLocal",
            return_value=db,
        ),
        patch("httpx.AsyncClient", return_value=mock_http_client),
    ):
        from app.tasks.sync_recent_tracks import fetch_and_store_recent_tracks

        await fetch_and_store_recent_tracks()

    return mock_http_client


class TestFetchAndStoreRecentTracks:
    @pytest.mark.asyncio
    async def test_inserts_new_stream(
        self, db, mock_recently_played_response, mock_artist_response
    ):
        """New track, artist, and stream are all created."""
        client = await run_sync(
            db,
            [
                make_response(200, mock_recently_played_response),
                make_response(200, mock_artist_response),
            ],
        )

        url = client.get.call_args_list[0].args[0]
        assert url == "https://api.spotify.com/v1/me/player/recently-played"
        assert client.get.call_args_list[0].kwargs["params"] == {"limit": 50}

        stream = db.execute(select(Stream)).scalar_one()
        assert stream.track_name == "New Song"
        assert stream.played_at == datetime(2026, 10, 6, 22, 20, 47, 401000)

        assert db.execute(select(Track)).scalar_one().spotify_id == "sp_track_1"
        assert db.execute(select(Artist)).scalar_one().name == "New Artist"

    @pytest.mark.asyncio
    async def test_rerun_does_not_duplicate(
        self, db, mock_recently_played_response, mock_artist_response
    ):
        await run_sync(
            db,
            [
                make_response(200, mock_recently_played_response),
                make_response(200, mock_artist_response),
            ],
        )
        await run_sync(db, [make_response(200, mock_recently_played_response)])

        assert db.scalar(select(func.count()).select_from(Stream)) == 1

    @pytest.mark.asyncio
    async def test_handles_api_error(self, db):
        """Gracefully handles a non-200 response from Spotify."""
        await run_sync(db, [make_response(500, "Internal Server Error")])

        assert db.scalar(select(func.count()).select_from(Stream)) == 0
