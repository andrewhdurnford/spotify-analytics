from datetime import datetime

import pytest
from sqlalchemy import select

from app.models import Artist, Track
from app.services.playback_sync import ensure_artists, ensure_track


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


def test_ensure_track_creates_track(db, playback_track):
    played_at = datetime(2025, 1, 1, 12, 0, 0)

    created = ensure_track(db, playback_track, played_at)
    db.commit()

    assert created == 1
    track = db.execute(select(Track)).scalar_one()
    assert track.spotify_id == "track-1"
    assert track.name == "Song"
    assert track.artist_name == "Artist"
    assert track.album_name == "Album"
    assert track.album_image == "https://img/album.jpg"
    assert track.duration_ms == 210000
    assert track.popularity == 75
    assert track.first_played_at == played_at


def test_ensure_track_skips_when_already_exists(db, playback_track):
    db.add(Track(spotify_id="track-1", name="Existing Track"))
    db.commit()

    created = ensure_track(db, playback_track, datetime(2025, 1, 1))

    assert created == 0
    assert len(db.execute(select(Track)).scalars().all()) == 1


def test_ensure_track_handles_missing_artists(db):
    track = {
        "id": "track-1",
        "name": "Song",
        "artists": [],
        "album": {"name": "Album", "images": []},
        "duration_ms": 210000,
    }

    created = ensure_track(db, track, datetime(2025, 1, 1))
    db.commit()

    stored = db.execute(select(Track)).scalar_one()
    assert created == 1
    assert stored.artist_name is None


@pytest.mark.asyncio
async def test_ensure_artists_creates_artist(db, playback_track):
    created = await ensure_artists(db, playback_track, fetch_artist_details=fetch_artist_details)
    db.commit()

    assert created == 1
    artist = db.execute(select(Artist)).scalar_one()
    assert artist.spotify_id == "artist-1"
    assert artist.name == "Artist"
    assert artist.genres == ["indie"]
    assert artist.popularity == 70
    assert artist.image_url == "https://img/artist.jpg"


@pytest.mark.asyncio
async def test_ensure_artists_skips_when_already_exists(db, playback_track):
    db.add(Artist(spotify_id="artist-1", name="Existing Artist"))
    db.commit()

    async def unused_fetcher(artist_id: str):
        raise AssertionError("existing artist should not be fetched")

    created = await ensure_artists(db, playback_track, fetch_artist_details=unused_fetcher)

    assert created == 0
    assert len(db.execute(select(Artist)).scalars().all()) == 1


@pytest.mark.asyncio
async def test_ensure_artists_continues_when_fetch_fails(db, playback_track):
    warnings = []
    issues = []

    async def failing_fetcher(artist_id: str):
        raise RuntimeError("artist API failed")

    created = await ensure_artists(
        db,
        playback_track,
        fetch_artist_details=failing_fetcher,
        warn=warnings.append,
        report_issue=issues.append,
    )

    assert created == 0
    assert warnings == ["Error fetching artist artist-1: artist API failed"]
    assert len(issues) == 1
    assert issues[0].operation == "playback_artist_metadata"
    assert issues[0].identifiers == ("artist-1",)
    assert db.execute(select(Artist)).scalars().all() == []
