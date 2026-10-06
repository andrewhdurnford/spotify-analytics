from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Artist, DailyListeningStats, Stream, Track
from app.services.backup_merge import merge_missing_streams

SINCE = datetime(2025, 1, 1)
DURATION_MS = 200_000


@pytest.fixture()
def backup_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def seed_catalog(session, track_id="track-1", artist_id="artist-1"):
    session.add(Track(spotify_id=track_id, name=f"Song {track_id}", duration_ms=DURATION_MS))
    session.add(Artist(spotify_id=artist_id, name=f"Artist {artist_id}", genres=["indie"]))
    session.flush()


def add_stream(session, played_at, track_id="track-1", artist_id="artist-1"):
    session.add(
        Stream(
            track_name=f"Song {track_id}",
            artist_name=f"Artist {artist_id}",
            album_name="Album",
            played_at=played_at,
            duration_ms=DURATION_MS,
            track_id=track_id,
            artist_id=artist_id,
        )
    )
    session.flush()


def local_played_at(db):
    return sorted(db.execute(select(Stream.played_at)).scalars())


def test_skips_plays_both_sides_recorded_seconds_apart(db, backup_db):
    seed_catalog(db)
    seed_catalog(backup_db)
    add_stream(db, datetime(2025, 1, 2, 12, 0, 0))
    add_stream(backup_db, datetime(2025, 1, 2, 12, 0, 40))

    stats = merge_missing_streams(db, backup_db, SINCE)

    assert stats.matched == 1
    assert stats.inserted_streams == 0
    assert local_played_at(db) == [datetime(2025, 1, 2, 12, 0, 0)]


def test_copies_plays_missing_locally_with_their_catalog_rows(db, backup_db):
    seed_catalog(backup_db, "track-2", "artist-2")
    add_stream(backup_db, datetime(2025, 1, 2, 12, 0, 0), "track-2", "artist-2")

    stats = merge_missing_streams(db, backup_db, SINCE)
    db.commit()

    assert stats.inserted_streams == 1
    assert stats.inserted_tracks == 1
    assert stats.inserted_artists == 1
    assert db.execute(select(Track.name)).scalar_one() == "Song track-2"
    assert db.execute(select(Artist.genres)).scalar_one() == ["indie"]
    # The Stream insert hook keeps rollups in step with merged plays.
    assert db.execute(select(func.sum(DailyListeningStats.play_count))).scalar_one() == 1


def test_back_to_back_repeats_are_not_collapsed(db, backup_db):
    """Two real plays of one track are a full duration apart; each side's
    copy of each play matches only its own counterpart."""
    seed_catalog(db)
    seed_catalog(backup_db)
    first = datetime(2025, 1, 2, 12, 0, 0)
    second = first + timedelta(milliseconds=DURATION_MS)
    add_stream(db, first)
    add_stream(backup_db, first + timedelta(seconds=30))
    add_stream(backup_db, second + timedelta(seconds=30))

    stats = merge_missing_streams(db, backup_db, SINCE)

    assert stats.matched == 1
    assert stats.inserted_streams == 1
    assert local_played_at(db) == [first, second + timedelta(seconds=30)]


def test_ignores_backup_plays_before_since(db, backup_db):
    seed_catalog(backup_db)
    add_stream(backup_db, SINCE - timedelta(days=1))

    stats = merge_missing_streams(db, backup_db, SINCE)

    assert stats.backup_streams == 0
    assert local_played_at(db) == []
