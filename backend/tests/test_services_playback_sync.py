from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Artist, Stream, Track
from app.services.playback_sync import store_completed_play


@pytest.fixture()
def playback_track():
    return {
        "id": "track-1",
        "name": "Song",
        "artists": [{"id": "artist-1", "name": "Artist"}],
        "album": {
            "name": "Album",
            "images": [{"url": "https://img/album.jpg"}],
        },
        "duration_ms": 210000,
        "popularity": 75,
    }


async def fetch_artist_details(artist_id: str):
    assert artist_id == "artist-1"
    return {
        "id": artist_id,
        "name": "Artist",
        "genres": ["indie"],
        "popularity": 70,
        "images": [{"url": "https://img/artist.jpg"}],
    }


@pytest.mark.asyncio
async def test_store_completed_play_creates_stream_track_and_artist(db, playback_track):
    played_at = datetime(2025, 1, 1, 12, 0, 0)

    result = await store_completed_play(
        db,
        playback_track,
        progress_ms=206000,
        played_at=played_at,
        fetch_artist_details=fetch_artist_details,
    )

    assert result.stored is True
    assert result.new_tracks == 1
    assert result.new_artists == 1
    assert result.duplicate is False

    stream = db.execute(select(Stream)).scalar_one()
    assert stream.track_name == "Song"
    assert stream.artist_name == "Artist"
    assert stream.album_name == "Album"
    assert stream.played_at == played_at
    assert stream.duration_ms == 210000
    assert stream.track_id == "track-1"
    assert stream.artist_id == "artist-1"

    track = db.execute(select(Track)).scalar_one()
    assert track.spotify_id == "track-1"
    assert track.name == "Song"
    assert track.artist_name == "Artist"
    assert track.album_name == "Album"
    assert track.album_image == "https://img/album.jpg"
    assert track.duration_ms == 210000
    assert track.popularity == 75
    assert track.first_played_at == played_at

    artist = db.execute(select(Artist)).scalar_one()
    assert artist.spotify_id == "artist-1"
    assert artist.name == "Artist"
    assert artist.genres == ["indie"]
    assert artist.popularity == 70
    assert artist.image_url == "https://img/artist.jpg"


@pytest.mark.asyncio
async def test_store_completed_play_skips_duplicate_observed_play(db, playback_track):
    played_at = datetime(2025, 1, 1, 12, 0, 0)
    db.add(
        Stream(
            track_name="Song",
            artist_name="Artist",
            played_at=played_at - timedelta(seconds=30),
            duration_ms=210000,
            track_id="track-1",
            artist_id="artist-1",
        )
    )
    db.commit()

    result = await store_completed_play(
        db,
        playback_track,
        progress_ms=206000,
        played_at=played_at,
        fetch_artist_details=fetch_artist_details,
    )

    assert result.stored is False
    assert result.duplicate is True
    assert db.execute(select(Stream)).scalars().all()[0].track_name == "Song"
    assert len(db.execute(select(Stream)).scalars().all()) == 1
    assert db.execute(select(Track)).scalars().all() == []
    assert db.execute(select(Artist)).scalars().all() == []


@pytest.mark.asyncio
async def test_store_completed_play_reuses_existing_track_and_artist(db, playback_track):
    played_at = datetime(2025, 1, 1, 12, 0, 0)
    db.add_all(
        [
            Track(spotify_id="track-1", name="Existing Track"),
            Artist(spotify_id="artist-1", name="Existing Artist"),
        ]
    )
    db.commit()

    async def unused_fetcher(artist_id: str):
        raise AssertionError("existing artist should not be fetched")

    result = await store_completed_play(
        db,
        playback_track,
        progress_ms=206000,
        played_at=played_at,
        fetch_artist_details=unused_fetcher,
    )

    assert result.stored is True
    assert result.new_tracks == 0
    assert result.new_artists == 0
    assert len(db.execute(select(Stream)).scalars().all()) == 1
    assert len(db.execute(select(Track)).scalars().all()) == 1
    assert len(db.execute(select(Artist)).scalars().all()) == 1


@pytest.mark.asyncio
async def test_store_completed_play_continues_when_artist_fetch_fails(db, playback_track):
    warnings = []
    issues = []

    async def failing_fetcher(artist_id: str):
        raise RuntimeError("artist API failed")

    result = await store_completed_play(
        db,
        playback_track,
        progress_ms=206000,
        played_at=datetime(2025, 1, 1, 12, 0, 0),
        fetch_artist_details=failing_fetcher,
        warn=warnings.append,
        report_issue=issues.append,
    )

    assert result.stored is True
    assert result.new_tracks == 1
    assert result.new_artists == 0
    assert warnings == ["Error fetching artist artist-1: artist API failed"]
    assert len(issues) == 1
    assert issues[0].operation == "playback_artist_metadata"
    assert issues[0].identifiers == ("artist-1",)
    assert len(db.execute(select(Stream)).scalars().all()) == 1
    assert db.execute(select(Artist)).scalars().all() == []


@pytest.mark.asyncio
async def test_store_completed_play_handles_tracks_without_artists(db):
    track = {
        "id": "track-1",
        "name": "Song",
        "artists": [],
        "album": {"name": "Album", "images": []},
        "duration_ms": 210000,
    }

    result = await store_completed_play(
        db,
        track,
        progress_ms=206000,
        played_at=datetime(2025, 1, 1, 12, 0, 0),
        fetch_artist_details=fetch_artist_details,
    )

    assert result.stored is True
    assert result.new_tracks == 1
    assert result.new_artists == 0

    stream = db.execute(select(Stream)).scalar_one()
    assert stream.artist_name == "Unknown"
    assert stream.artist_id is None

    stored_track = db.execute(select(Track)).scalar_one()
    assert stored_track.artist_name is None
