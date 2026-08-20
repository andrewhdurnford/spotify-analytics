from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Artist, RecentPlaysSyncMeta, Stream, Track
from app.services.recent_plays_sync import (
    cursor_epoch_ms,
    get_last_synced_at,
    process_recently_played_items,
    seed_cursor_to_now,
)


def _track(
    track_id: str = "track-1",
    name: str = "Song",
    duration_ms: int = 200_000,
    artist_id: str = "artist-1",
    artist_name: str = "Artist",
) -> dict:
    return {
        "id": track_id,
        "name": name,
        "type": "track",
        "artists": [{"id": artist_id, "name": artist_name}],
        "album": {"name": "Album", "images": [{"url": "https://img/album.jpg"}]},
        "duration_ms": duration_ms,
        "popularity": 75,
    }


def _item(played_at: datetime, track: dict | None = None) -> dict:
    return {
        "played_at": played_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "track": track or _track(),
    }


async def fetch_artist_details(artist_id: str) -> dict:
    return {
        "id": artist_id,
        "name": "Artist",
        "genres": ["indie"],
        "popularity": 70,
        "images": [{"url": "https://img/artist.jpg"}],
    }


@pytest.mark.asyncio
async def test_accepts_fully_played_track_using_cursor_as_predecessor(db):
    cursor = datetime(2025, 1, 1, 12, 0, 0)
    db.add(RecentPlaysSyncMeta(id=1, cursor_played_at=cursor))
    db.commit()

    track = _track(duration_ms=200_000)
    played_at = cursor + timedelta(milliseconds=200_000)

    result = await process_recently_played_items(
        db, [_item(played_at, track)], fetch_artist_details=fetch_artist_details
    )

    assert result.inserted == 1
    assert result.rejected_ratio == 0
    assert result.duplicates == 0

    stream = db.execute(select(Stream)).scalar_one()
    assert stream.track_id == "track-1"
    assert stream.played_at == played_at
    assert stream.played_ratio == pytest.approx(1.0)

    meta = db.get(RecentPlaysSyncMeta, 1)
    assert meta.cursor_played_at == played_at
    assert meta.last_synced_at is not None


@pytest.mark.asyncio
async def test_rejects_track_played_below_threshold(db):
    cursor = datetime(2025, 1, 1, 12, 0, 0)
    db.add(RecentPlaysSyncMeta(id=1, cursor_played_at=cursor))
    db.commit()

    track = _track(duration_ms=200_000)
    # Only ~10% of the track's duration elapsed before the next item logged.
    played_at = cursor + timedelta(milliseconds=20_000)

    result = await process_recently_played_items(
        db, [_item(played_at, track)], fetch_artist_details=fetch_artist_details
    )

    assert result.inserted == 0
    assert result.rejected_ratio == 1
    assert db.execute(select(Stream)).scalars().all() == []
    # A resolved-but-rejected item still advances the cursor -- it's been
    # accounted for, just not stored.
    assert db.get(RecentPlaysSyncMeta, 1).cursor_played_at == played_at


@pytest.mark.asyncio
async def test_chains_ratio_across_consecutive_items(db):
    cursor = datetime(2025, 1, 1, 12, 0, 0)
    db.add(RecentPlaysSyncMeta(id=1, cursor_played_at=cursor))
    db.commit()

    track_a = _track(track_id="track-a", duration_ms=200_000)
    track_b = _track(
        track_id="track-b", artist_id="artist-2", artist_name="Other Artist", duration_ms=100_000
    )

    played_a = cursor + timedelta(milliseconds=200_000)  # fully played
    played_b = played_a + timedelta(milliseconds=10_000)  # skipped quickly

    result = await process_recently_played_items(
        db,
        [_item(played_a, track_a), _item(played_b, track_b)],
        fetch_artist_details=fetch_artist_details,
    )

    assert result.inserted == 1
    assert result.rejected_ratio == 1
    stream = db.execute(select(Stream)).scalar_one()
    assert stream.track_id == "track-a"


@pytest.mark.asyncio
async def test_skips_ratio_calc_for_first_item_with_no_cursor(db):
    db.add(RecentPlaysSyncMeta(id=1, cursor_played_at=None))
    db.commit()

    track_a = _track(track_id="track-a", duration_ms=200_000)
    track_b = _track(track_id="track-b", artist_id="artist-2", duration_ms=100_000)

    played_a = datetime(2025, 1, 1, 12, 0, 0)
    played_b = played_a + timedelta(milliseconds=100_000)  # fully played, relative to track_a

    result = await process_recently_played_items(
        db,
        [_item(played_a, track_a), _item(played_b, track_b)],
        fetch_artist_details=fetch_artist_details,
    )

    # track_a has no known predecessor -- not credited either way, just
    # becomes the predecessor for track_b.
    assert result.inserted == 1
    assert result.rejected_ratio == 0
    stream = db.execute(select(Stream)).scalar_one()
    assert stream.track_id == "track-b"


@pytest.mark.asyncio
async def test_filters_non_track_items_but_keeps_them_as_predecessor(db):
    cursor = datetime(2025, 1, 1, 12, 0, 0)
    db.add(RecentPlaysSyncMeta(id=1, cursor_played_at=cursor))
    db.commit()

    episode_played_at = cursor + timedelta(milliseconds=50_000)
    episode_item = {
        "played_at": episode_played_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "track": {"id": "ep-1", "name": "Episode", "type": "episode", "duration_ms": 1_800_000},
    }

    track = _track(duration_ms=100_000)
    track_played_at = episode_played_at + timedelta(milliseconds=100_000)

    result = await process_recently_played_items(
        db,
        [episode_item, _item(track_played_at, track)],
        fetch_artist_details=fetch_artist_details,
    )

    assert result.inserted == 1
    stream = db.execute(select(Stream)).scalar_one()
    assert stream.track_id == "track-1"
    assert stream.played_ratio == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_dedups_against_existing_stream(db):
    cursor = datetime(2025, 1, 1, 12, 0, 0)
    played_at = cursor + timedelta(milliseconds=200_000)
    db.add(RecentPlaysSyncMeta(id=1, cursor_played_at=cursor))
    db.add(
        Stream(
            track_name="Song",
            artist_name="Artist",
            played_at=played_at - timedelta(seconds=5),
            duration_ms=200_000,
            track_id="track-1",
            artist_id="artist-1",
        )
    )
    db.commit()

    track = _track(duration_ms=200_000)

    result = await process_recently_played_items(
        db, [_item(played_at, track)], fetch_artist_details=fetch_artist_details
    )

    assert result.inserted == 0
    assert result.duplicates == 1
    assert len(db.execute(select(Stream)).scalars().all()) == 1


@pytest.mark.asyncio
async def test_creates_track_and_artist_for_new_stream(db):
    cursor = datetime(2025, 1, 1, 12, 0, 0)
    db.add(RecentPlaysSyncMeta(id=1, cursor_played_at=cursor))
    db.commit()

    track = _track(duration_ms=200_000)
    played_at = cursor + timedelta(milliseconds=200_000)

    await process_recently_played_items(
        db, [_item(played_at, track)], fetch_artist_details=fetch_artist_details
    )

    assert db.execute(select(Track)).scalar_one().spotify_id == "track-1"
    assert db.execute(select(Artist)).scalar_one().spotify_id == "artist-1"


def test_cursor_epoch_ms_none_before_first_run(db):
    assert cursor_epoch_ms(db) is None


def test_seed_cursor_to_now_establishes_baseline(db):
    seed_cursor_to_now(db)

    assert cursor_epoch_ms(db) is not None
    # Seeding a baseline isn't itself a completed sync run.
    assert get_last_synced_at(db) is None
