from datetime import datetime

from sqlalchemy import select

from app.models import Artist, Stream, Track
from app.services.spotify_metadata import (
    get_missing_artist_ids,
    get_missing_track_ids,
    upsert_artists_from_spotify,
    upsert_tracks_from_spotify,
)


class FakeResponse:
    def __init__(self, payload: dict | None = None, error: Exception | None = None):
        self.payload = payload or {}
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


def test_get_missing_track_ids_returns_stream_tracks_not_in_tracks(db):
    db.add_all(
        [
            Stream(
                track_name="Stored",
                artist_name="Artist",
                played_at=datetime(2025, 1, 1),
                track_id="stored-track",
            ),
            Stream(
                track_name="Missing",
                artist_name="Artist",
                played_at=datetime(2025, 1, 2),
                track_id="missing-track",
            ),
            Track(spotify_id="stored-track", name="Stored"),
        ]
    )
    db.commit()

    assert get_missing_track_ids(db) == ["missing-track"]


def test_get_missing_artist_ids_returns_stream_artists_not_in_artists(db):
    db.add_all(
        [
            Stream(
                track_name="Stored",
                artist_name="Artist",
                played_at=datetime(2025, 1, 1),
                artist_id="stored-artist",
            ),
            Stream(
                track_name="Missing",
                artist_name="Artist",
                played_at=datetime(2025, 1, 2),
                artist_id="missing-artist",
            ),
            Artist(spotify_id="stored-artist", name="Stored Artist"),
        ]
    )
    db.commit()

    assert get_missing_artist_ids(db) == ["missing-artist"]


def test_upsert_tracks_inserts_metadata_and_backfills_stream_artist_id(db):
    db.add(
        Stream(
            track_name="Song",
            artist_name="Artist",
            played_at=datetime(2025, 1, 1),
            track_id="track-1",
        )
    )
    db.commit()

    def fake_get(url, *, params, headers, timeout):
        assert url == "https://api.spotify.com/v1/tracks"
        assert params == {"ids": "track-1"}
        assert headers == {"Authorization": "Bearer token"}
        assert timeout == 10
        return FakeResponse(
            {
                "tracks": [
                    {
                        "id": "track-1",
                        "name": "Song",
                        "artists": [{"id": "artist-1", "name": "Artist"}],
                        "album": {
                            "name": "Album",
                            "images": [{"url": "https://img/album.jpg"}],
                        },
                        "duration_ms": 123000,
                        "popularity": 77,
                    }
                ]
            }
        )

    upserted = upsert_tracks_from_spotify(db, "token", ["track-1"], http_get=fake_get)

    assert upserted == 1
    track = db.execute(select(Track).where(Track.spotify_id == "track-1")).scalar_one()
    assert track.name == "Song"
    assert track.artist_name == "Artist"
    assert track.album_name == "Album"
    assert track.album_image == "https://img/album.jpg"
    assert track.duration_ms == 123000
    assert track.popularity == 77

    stream = db.execute(select(Stream)).scalar_one()
    assert stream.artist_id == "artist-1"


def test_upsert_tracks_updates_existing_metadata(db):
    db.add(
        Track(
            spotify_id="track-1",
            name="Old",
            artist_name="Old Artist",
            album_name="Old Album",
            album_image="old.jpg",
            duration_ms=1,
            popularity=1,
        )
    )
    db.commit()

    def fake_get(url, *, params, headers, timeout):
        return FakeResponse(
            {
                "tracks": [
                    {
                        "id": "track-1",
                        "name": "New",
                        "artists": [{"id": "artist-1", "name": "New Artist"}],
                        "album": {"name": "New Album", "images": []},
                        "duration_ms": 234000,
                        "popularity": 88,
                    }
                ]
            }
        )

    upserted = upsert_tracks_from_spotify(db, "token", ["track-1"], http_get=fake_get)

    assert upserted == 1
    track = db.execute(select(Track).where(Track.spotify_id == "track-1")).scalar_one()
    assert track.name == "New"
    assert track.artist_name == "New Artist"
    assert track.album_name == "New Album"
    assert track.album_image == "old.jpg"
    assert track.duration_ms == 234000
    assert track.popularity == 88


def test_upsert_artists_inserts_and_updates_metadata(db):
    db.add(
        Artist(
            spotify_id="artist-existing",
            name="Old",
            genres=["old"],
            popularity=1,
            image_url="old.jpg",
        )
    )
    db.commit()

    def fake_get(url, *, params, headers, timeout):
        assert url == "https://api.spotify.com/v1/artists"
        assert params == {"ids": "artist-existing,artist-new"}
        return FakeResponse(
            {
                "artists": [
                    {
                        "id": "artist-existing",
                        "name": "Updated",
                        "genres": ["indie"],
                        "popularity": 70,
                        "images": [{"url": "updated.jpg"}],
                    },
                    {
                        "id": "artist-new",
                        "name": "New",
                        "genres": ["pop"],
                        "popularity": 80,
                        "images": [{"url": "new.jpg"}],
                    },
                ]
            }
        )

    upserted = upsert_artists_from_spotify(
        db,
        "token",
        ["artist-existing", "artist-new"],
        http_get=fake_get,
    )

    assert upserted == 2
    artists = {artist.spotify_id: artist for artist in db.execute(select(Artist)).scalars().all()}
    assert artists["artist-existing"].name == "Updated"
    assert artists["artist-existing"].genres == ["indie"]
    assert artists["artist-existing"].popularity == 70
    assert artists["artist-existing"].image_url == "updated.jpg"
    assert artists["artist-new"].name == "New"


def test_upsert_batch_failure_rolls_back_and_reports_warning(db):
    warnings = []
    issues = []

    def fake_get(url, *, params, headers, timeout):
        return FakeResponse(error=RuntimeError("boom"))

    upserted = upsert_tracks_from_spotify(
        db,
        "token",
        ["track-1"],
        http_get=fake_get,
        warn=warnings.append,
        report_issue=issues.append,
    )

    assert upserted == 0
    assert warnings == ["spotify track batch fetch failed: boom"]
    assert len(issues) == 1
    assert issues[0].operation == "spotify_track_metadata"
    assert issues[0].identifiers == ("track-1",)
    assert db.execute(select(Track)).scalars().all() == []
