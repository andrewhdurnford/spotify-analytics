from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Artist, DailyListeningStats, DailyTrackStats, Stream, Track
from app.services import rollups as rollups_service
from app.services.playback_sync import store_recently_played


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


def recent_item(track, played_at):
    return {"track": track, "played_at": played_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"}


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
async def test_store_recently_played_creates_stream_track_and_artist(db, playback_track):
    played_at = datetime(2025, 1, 1, 12, 0, 0)

    result = await store_recently_played(
        db,
        [recent_item(playback_track, played_at)],
        fetch_artist_details=fetch_artist_details,
    )

    assert result.inserted == 1
    assert result.new_tracks == 1
    assert result.new_artists == 1
    assert result.duplicates == 0

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
async def test_store_recently_played_skips_play_already_stored_by_old_poller(db, playback_track):
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

    result = await store_recently_played(
        db,
        [recent_item(playback_track, played_at)],
        fetch_artist_details=fetch_artist_details,
    )

    assert result.inserted == 0
    assert result.duplicates == 1
    assert db.execute(select(Stream)).scalars().all()[0].track_name == "Song"
    assert len(db.execute(select(Stream)).scalars().all()) == 1
    assert db.execute(select(Track)).scalars().all() == []
    assert db.execute(select(Artist)).scalars().all() == []


@pytest.mark.asyncio
async def test_store_recently_played_reuses_existing_track_and_artist(db, playback_track):
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

    result = await store_recently_played(
        db,
        [recent_item(playback_track, played_at)],
        fetch_artist_details=unused_fetcher,
    )

    assert result.inserted == 1
    assert result.new_tracks == 0
    assert result.new_artists == 0
    assert len(db.execute(select(Stream)).scalars().all()) == 1
    assert len(db.execute(select(Track)).scalars().all()) == 1
    assert len(db.execute(select(Artist)).scalars().all()) == 1


@pytest.mark.asyncio
async def test_store_recently_played_continues_when_artist_fetch_fails(db, playback_track):
    issues = []

    async def failing_fetcher(artist_id: str):
        raise RuntimeError("artist API failed")

    result = await store_recently_played(
        db,
        [recent_item(playback_track, datetime(2025, 1, 1, 12, 0, 0))],
        fetch_artist_details=failing_fetcher,
        report_issue=issues.append,
    )

    assert result.inserted == 1
    assert result.new_tracks == 1
    assert result.new_artists == 0
    assert len(issues) == 1
    assert issues[0].message == "Error fetching artist artist-1: artist API failed"
    assert issues[0].operation == "playback_artist_metadata"
    assert issues[0].identifiers == ("artist-1",)
    assert len(db.execute(select(Stream)).scalars().all()) == 1
    assert db.execute(select(Artist)).scalars().all() == []


@pytest.mark.asyncio
async def test_store_recently_played_handles_tracks_without_artists(db):
    track = {
        "id": "track-1",
        "name": "Song",
        "artists": [],
        "album": {"name": "Album", "images": []},
        "duration_ms": 210000,
    }

    result = await store_recently_played(
        db,
        [recent_item(track, datetime(2025, 1, 1, 12, 0, 0))],
        fetch_artist_details=fetch_artist_details,
    )

    assert result.inserted == 1
    assert result.new_tracks == 1
    assert result.new_artists == 0

    stream = db.execute(select(Stream)).scalar_one()
    assert stream.artist_name == "Unknown"
    assert stream.artist_id is None

    stored_track = db.execute(select(Track)).scalar_one()
    assert stored_track.artist_name is None


# ---------------------------------------------------------------------------
# Atomic raw + rollup write path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_recently_played_writes_rollups_in_same_transaction(db, playback_track):
    """A stored play updates streams AND daily_listening_stats /
    daily_track_stats together (both committed by the same db.commit())."""
    played_at = datetime(2025, 1, 1, 12, 0, 0)

    result = await store_recently_played(
        db,
        [recent_item(playback_track, played_at)],
        fetch_artist_details=fetch_artist_details,
    )

    assert result.inserted == 1

    daily = db.execute(select(DailyListeningStats)).scalar_one()
    assert daily.date.isoformat() == "2025-01-01"
    assert daily.play_count == 1
    assert daily.listening_ms == 210000

    track_row = db.execute(select(DailyTrackStats)).scalar_one()
    assert track_row.track_id == "track-1"
    assert track_row.artist_id == "artist-1"
    assert track_row.play_count == 1
    assert track_row.listening_ms == 210000


@pytest.mark.asyncio
async def test_store_recently_played_rolls_back_stream_when_rollup_write_fails(
    db, playback_track, monkeypatch
):
    """If rollup maintenance raises during the flush, nothing from this call
    — not the Stream, not the rollup rows — should be persisted."""

    def _boom(session, stream, **kwargs):
        raise RuntimeError("simulated rollup failure")

    monkeypatch.setattr(rollups_service, "apply_stream_to_rollups", _boom)

    with pytest.raises(RuntimeError, match="simulated rollup failure"):
        await store_recently_played(
            db,
            [recent_item(playback_track, datetime(2025, 1, 1, 12, 0, 0))],
            fetch_artist_details=fetch_artist_details,
        )

    # The failed transaction was rolled back — nothing from this call landed,
    # including the Stream row that had already been added to the session.
    assert db.execute(select(Stream)).scalars().all() == []
    assert db.execute(select(DailyListeningStats)).scalars().all() == []
    assert db.execute(select(DailyTrackStats)).scalars().all() == []

    # The session is usable again afterward (rollback() cleared the failed
    # transaction state rather than leaving it stuck).
    monkeypatch.undo()
    result = await store_recently_played(
        db,
        [recent_item(playback_track, datetime(2025, 1, 1, 12, 0, 0))],
        fetch_artist_details=fetch_artist_details,
    )
    assert result.inserted == 1
    assert len(db.execute(select(Stream)).scalars().all()) == 1


@pytest.mark.asyncio
async def test_store_recently_played_is_idempotent(db, playback_track):
    items = [
        recent_item(playback_track, datetime(2025, 1, 1, 12, 0, 0)),
        recent_item(playback_track, datetime(2025, 1, 1, 12, 3, 30)),
    ]

    first = await store_recently_played(db, items, fetch_artist_details=fetch_artist_details)
    second = await store_recently_played(db, items, fetch_artist_details=fetch_artist_details)

    assert (first.inserted, first.duplicates) == (2, 0)
    assert (second.inserted, second.duplicates) == (0, 2)
    assert len(db.execute(select(Stream)).scalars().all()) == 2


@pytest.mark.asyncio
async def test_store_recently_played_stores_played_at_verbatim(db, playback_track):
    await store_recently_played(
        db,
        [{"track": playback_track, "played_at": "2026-10-06T22:20:47.401Z"}],
        fetch_artist_details=fetch_artist_details,
    )

    stream = db.execute(select(Stream)).scalar_one()
    assert stream.played_at == datetime(2026, 10, 6, 22, 20, 47, 401000)


@pytest.mark.asyncio
async def test_store_recently_played_ignores_non_tracks(db):
    episode = {"id": "ep-1", "type": "episode", "name": "Episode", "duration_ms": 1000}

    result = await store_recently_played(
        db,
        [recent_item(episode, datetime(2025, 1, 1, 12, 0, 0)), {"track": None, "played_at": ""}],
        fetch_artist_details=fetch_artist_details,
    )

    assert result.inserted == 0
    assert db.execute(select(Stream)).scalars().all() == []
