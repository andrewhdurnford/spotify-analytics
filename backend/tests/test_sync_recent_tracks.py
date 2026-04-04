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
def mock_spotify_response():
    """A realistic Spotify recently-played API response."""
    return {
        "items": [
            {
                "track": {
                    "id": "sp_track_1",
                    "name": "New Song",
                    "artists": [{"id": "sp_artist_1", "name": "New Artist"}],
                    "album": {
                        "name": "New Album",
                        "images": [{"url": "https://img/new.jpg"}],
                    },
                    "duration_ms": 210_000,
                    "popularity": 75,
                },
                "played_at": "2025-03-01T12:00:00Z",
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


class TestFetchAndStoreRecentTracks:
    @pytest.mark.asyncio
    async def test_inserts_new_stream(self, db, mock_spotify_response, mock_artist_response):
        """New track, artist, and stream are all created."""
        mock_spotify_client = AsyncMock()
        mock_spotify_client.access_token = "fake-token"

        # Mock the httpx responses
        recently_played_resp = MagicMock()
        recently_played_resp.status_code = 200
        recently_played_resp.json.return_value = mock_spotify_response

        artist_resp = MagicMock()
        artist_resp.status_code = 200
        artist_resp.json.return_value = mock_artist_response

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(side_effect=[recently_played_resp, artist_resp])
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

        # Verify records were created
        streams = db.execute(select(Stream)).scalars().all()
        assert len(streams) == 1
        assert streams[0].track_name == "New Song"

        tracks = db.execute(select(Track)).scalars().all()
        assert len(tracks) == 1
        assert tracks[0].spotify_id == "sp_track_1"

        artists = db.execute(select(Artist)).scalars().all()
        assert len(artists) == 1
        assert artists[0].name == "New Artist"

    @pytest.mark.asyncio
    async def test_skips_duplicate_stream(self, db, mock_spotify_response, mock_artist_response):
        """If a stream already exists for the same track_id + played_at, it's skipped."""
        # Pre-insert the stream
        db.add(
            Stream(
                track_name="New Song",
                artist_name="New Artist",
                album_name="New Album",
                played_at=datetime(2025, 3, 1, 12, 0, 0),
                duration_ms=210_000,
                track_id="sp_track_1",
                artist_id="sp_artist_1",
            )
        )
        db.commit()

        mock_spotify_client = AsyncMock()
        mock_spotify_client.access_token = "fake-token"

        recently_played_resp = MagicMock()
        recently_played_resp.status_code = 200
        recently_played_resp.json.return_value = mock_spotify_response

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=recently_played_resp)
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

        # Still only the original stream
        streams = db.execute(select(Stream)).scalars().all()
        assert len(streams) == 1

    @pytest.mark.asyncio
    async def test_handles_api_error(self, db):
        """Gracefully handles a non-200 response from Spotify."""
        mock_spotify_client = AsyncMock()
        mock_spotify_client.access_token = "fake-token"

        error_resp = MagicMock()
        error_resp.status_code = 500
        error_resp.text = "Internal Server Error"

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=error_resp)
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

            # Should not raise
            await fetch_and_store_recent_tracks()

        # No streams inserted
        assert db.scalar(select(func.count()).select_from(Stream)) == 0
