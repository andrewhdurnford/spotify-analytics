"""Focused tests for app.services.detail query behavior."""

from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.models import Artist, Stream, Track
from app.services.detail import (
    get_album_detail,
    get_artist_detail,
    get_related_tracks,
    get_track_detail,
    get_track_detail_by_name,
)
from app.services.related_tracks_rollup import rebuild_related_tracks_rollup


def _seed_detail_data(db: Session):
    artist = Artist(
        spotify_id="artist-1",
        name="Artist One",
        genres=["indie"],
        popularity=70,
        image_url="https://img/artist-1.jpg",
    )
    tracks = [
        Track(
            spotify_id="track-1",
            name="Song One",
            artist_name="Artist One",
            album_name="Album One",
            album_image="https://img/track-1.jpg",
            duration_ms=200_000,
        ),
        Track(
            spotify_id="track-2",
            name="Song Two",
            artist_name="Artist One",
            album_name="Album Two",
            album_image="https://img/track-2.jpg",
            duration_ms=180_000,
        ),
        Track(
            spotify_id="track-empty",
            name="Never Played",
            artist_name="Artist One",
            album_name="Quiet Album",
            album_image="https://img/empty.jpg",
            duration_ms=210_000,
        ),
    ]
    streams = [
        Stream(
            track_name="Song One",
            artist_name="Artist One",
            album_name="Album One",
            played_at=datetime(2025, 1, 5, 9, 0),
            duration_ms=200_000,
            track_id="track-1",
            artist_id="artist-1",
        ),
        Stream(
            track_name="Song One",
            artist_name="Artist One",
            album_name="Album One",
            played_at=datetime(2025, 1, 6, 9, 0),
            duration_ms=200_000,
            track_id="track-1",
            artist_id="artist-1",
        ),
        Stream(
            track_name="Song Two",
            artist_name="Artist One",
            album_name="Album Two",
            played_at=datetime(2025, 2, 1, 9, 0),
            duration_ms=180_000,
            track_id="track-2",
            artist_id="artist-1",
        ),
        Stream(
            track_name="Loose Song",
            artist_name="Loose Artist",
            album_name="Loose Album",
            played_at=datetime(2025, 1, 7, 9, 0),
            duration_ms=150_000,
        ),
        Stream(
            track_name="Loose Song",
            artist_name="Different Artist",
            album_name="Other Album",
            played_at=datetime(2025, 1, 8, 9, 0),
            duration_ms=150_000,
        ),
    ]

    db.add_all([artist, *tracks, *streams])
    db.commit()


class TestGetArtistDetail:
    def test_month_filter_limits_counts_timeline_and_top_items(self, db: Session):
        _seed_detail_data(db)

        result = get_artist_detail(
            db,
            artist_id="artist-1",
            timeframe="month",
            base_date=datetime(2025, 1, 15),
            limit=10,
        )

        assert result is not None
        assert result.artist.spotify_id == "artist-1"
        assert result.artist.total_play_count == 2
        assert result.granularity == "daily"
        assert [song.track_name for song in result.top_songs] == ["Song One"]
        assert [album.album_name for album in result.top_albums] == ["Album One"]
        assert [point.model_dump() for point in result.timeline] == [
            {"period": "2025-01-05", "play_count": 1},
            {"period": "2025-01-06", "play_count": 1},
        ]

    def test_missing_artist_returns_none(self, db: Session):
        _seed_detail_data(db)

        result = get_artist_detail(
            db,
            artist_id="missing",
            timeframe="all",
            base_date=datetime(2025, 1, 1),
            limit=10,
        )

        assert result is None


class TestGetAlbumDetail:
    def test_returns_top_tracks_and_timeline(self, db: Session):
        _seed_detail_data(db)

        # Add a second track on Album One so "top tracks" is non-trivial.
        db.add(
            Track(
                spotify_id="track-1b",
                name="Song One B",
                artist_name="Artist One",
                album_name="Album One",
                album_image="https://img/track-1b.jpg",
                duration_ms=210_000,
            )
        )
        db.add(
            Stream(
                track_name="Song One B",
                artist_name="Artist One",
                album_name="Album One",
                played_at=datetime(2025, 1, 7, 9, 0),
                duration_ms=210_000,
                track_id="track-1b",
                artist_id="artist-1",
            )
        )
        db.commit()

        result = get_album_detail(
            db,
            artist_id="artist-1",
            album_name="Album One",
            timeframe="all",
            base_date=datetime(2025, 6, 1),
            limit=10,
        )

        assert result is not None
        assert result.album.album_name == "Album One"
        assert result.album.artist_id == "artist-1"
        assert result.album.artist_name == "Artist One"
        assert result.album.album_image == "https://img/track-1.jpg"
        assert result.album.total_play_count == 3
        assert result.album.unique_tracks_played == 2
        assert [t.track_name for t in result.top_tracks] == ["Song One", "Song One B"]
        assert result.top_tracks[0].play_count == 2
        assert result.granularity == "monthly"
        assert [p.model_dump() for p in result.timeline] == [
            {"period": "2025-01", "play_count": 3},
        ]

    def test_month_filter_uses_daily_timeline(self, db: Session):
        _seed_detail_data(db)

        result = get_album_detail(
            db,
            artist_id="artist-1",
            album_name="Album One",
            timeframe="month",
            base_date=datetime(2025, 1, 15),
            limit=10,
        )

        assert result is not None
        assert result.granularity == "daily"
        assert result.album.total_play_count == 2
        assert [p.model_dump() for p in result.timeline] == [
            {"period": "2025-01-05", "play_count": 1},
            {"period": "2025-01-06", "play_count": 1},
        ]

    def test_missing_album_returns_none(self, db: Session):
        _seed_detail_data(db)

        result = get_album_detail(
            db,
            artist_id="artist-1",
            album_name="Does Not Exist",
            timeframe="all",
            base_date=datetime(2025, 1, 1),
            limit=10,
        )

        assert result is None


class TestGetTrackDetail:
    def test_track_with_metadata_but_no_streams_returns_empty_counts(self, db: Session):
        _seed_detail_data(db)

        result = get_track_detail(
            db,
            track_id="track-empty",
            timeframe="all",
            base_date=datetime(2025, 1, 1),
        )

        assert result is not None
        assert result.track.spotify_id == "track-empty"
        assert result.track.total_play_count == 0
        assert result.track.artist_id is None
        assert result.timeline == []

    def test_month_filter_uses_daily_timeline(self, db: Session):
        _seed_detail_data(db)

        result = get_track_detail(
            db,
            track_id="track-1",
            timeframe="month",
            base_date=datetime(2025, 1, 1),
        )

        assert result is not None
        assert result.track.total_play_count == 2
        assert result.granularity == "daily"
        assert [point.model_dump() for point in result.timeline] == [
            {"period": "2025-01-05", "play_count": 1},
            {"period": "2025-01-06", "play_count": 1},
        ]

    def test_daily_timeline_uses_toronto_calendar_date(self, db: Session):
        _seed_detail_data(db)
        db.add(
            Stream(
                track_name="Song One",
                artist_name="Artist One",
                album_name="Album One",
                played_at=datetime(2025, 1, 7, 2, 0),
                duration_ms=200_000,
                track_id="track-1",
                artist_id="artist-1",
            )
        )
        db.commit()

        result = get_track_detail(
            db,
            track_id="track-1",
            timeframe="month",
            base_date=datetime(2025, 1, 1),
        )

        assert result is not None
        assert [point.model_dump() for point in result.timeline] == [
            {"period": "2025-01-05", "play_count": 1},
            {"period": "2025-01-06", "play_count": 2},
        ]

    def test_missing_track_returns_none(self, db: Session):
        result = get_track_detail(
            db,
            track_id="missing",
            timeframe="all",
            base_date=datetime(2025, 1, 1),
        )

        assert result is None


class TestGetTrackDetailByName:
    def test_name_fallback_distinguishes_artist_and_applies_timeframe(self, db: Session):
        _seed_detail_data(db)

        result = get_track_detail_by_name(
            db,
            track_name="Loose Song",
            artist_name="Loose Artist",
            timeframe="month",
            base_date=datetime(2025, 1, 1),
        )

        assert result is not None
        assert result.track.spotify_id is None
        assert result.track.name == "Loose Song"
        assert result.track.artist_name == "Loose Artist"
        assert result.track.total_play_count == 1
        assert result.granularity == "daily"
        assert [point.model_dump() for point in result.timeline] == [
            {"period": "2025-01-07", "play_count": 1},
        ]

    def test_name_fallback_returns_zero_for_existing_track_outside_timeframe(self, db: Session):
        _seed_detail_data(db)

        result = get_track_detail_by_name(
            db,
            track_name="Loose Song",
            artist_name="Loose Artist",
            timeframe="month",
            base_date=datetime(2025, 2, 1),
        )

        assert result is not None
        assert result.track.total_play_count == 0
        assert result.timeline == []

    def test_missing_name_fallback_returns_none(self, db: Session):
        _seed_detail_data(db)

        result = get_track_detail_by_name(
            db,
            track_name="Missing",
            artist_name="Loose Artist",
            timeframe="all",
            base_date=datetime(2025, 1, 1),
        )

        assert result is None


def _seed_related_data(db: Session):
    """Seed several listening sessions for related-tracks tests.

    Layout (each session = contiguous plays, gap > 30 min ends a session):

      Session 0 (Jan 1):  target, near-A, near-B
      Session 1 (Jan 2):  target, near-A, near-B
      Session 2 (Jan 3):  target, near-A, near-B, same-artist
      Session 3 (Jan 4):  target, same-artist            (cooc=2 for same-artist)
      Session 4 (Jan 5):  far-track only                 (no cooc with target)

    Expected: near-A and near-B both have cooc=3 (every session with target).
    same-artist has cooc=2 — below the build-time DEFAULT_MIN_SUPPORT of 3,
    so it never earns a `track_cooccurrences` row.

    `get_related_tracks` reads the rollup rather than `streams`, so the
    rebuild below is part of the fixture: without it the seeded plays are
    invisible to the query.
    """
    artist_target = Artist(spotify_id="art-target", name="Target Artist", genres=[])
    artist_other = Artist(spotify_id="art-other", name="Other Artist", genres=[])
    artist_far = Artist(spotify_id="art-far", name="Far Artist", genres=[])
    tracks = [
        Track(spotify_id="t-target", name="Target Song", artist_name="Target Artist"),
        Track(spotify_id="t-nearA", name="Near A", artist_name="Other Artist"),
        Track(spotify_id="t-nearB", name="Near B", artist_name="Other Artist"),
        Track(spotify_id="t-same", name="Same Artist Song", artist_name="Target Artist"),
        Track(spotify_id="t-far", name="Far Song", artist_name="Far Artist"),
    ]

    def stream(tid: str, artist_spotify_id: str, name: str, at: datetime) -> Stream:
        return Stream(
            track_name=name,
            artist_name=name,  # not under test
            played_at=at,
            track_id=tid,
            artist_id=artist_spotify_id,
        )

    # Each session's plays are within a few minutes of each other; sessions
    # are a full day apart, well beyond the 30-min gap threshold.
    base = datetime(2025, 1, 1, 12, 0)
    day = timedelta(days=1)
    minute = timedelta(minutes=1)

    streams: list[Stream] = []
    # Sessions 0, 1, 2 — target + nearA + nearB
    for i in range(3):
        start = base + i * day
        streams.append(stream("t-target", "art-target", "Target Song", start))
        streams.append(stream("t-nearA", "art-other", "Near A", start + 5 * minute))
        streams.append(stream("t-nearB", "art-other", "Near B", start + 10 * minute))
    # Session 2 also has same-artist
    streams.append(stream("t-same", "art-target", "Same Artist Song", base + 2 * day + 15 * minute))
    # Session 3 — target + same-artist only
    s3 = base + 3 * day
    streams.append(stream("t-target", "art-target", "Target Song", s3))
    streams.append(stream("t-same", "art-target", "Same Artist Song", s3 + 5 * minute))
    # Session 4 — far track only, no target
    streams.append(stream("t-far", "art-far", "Far Song", base + 4 * day))

    db.add_all([artist_target, artist_other, artist_far, *tracks, *streams])
    db.commit()

    rebuild_related_tracks_rollup(db)
    db.commit()


class TestGetRelatedTracks:
    def test_returns_top_cooccurring_tracks_sorted_by_score(self, db: Session):
        _seed_related_data(db)

        result = get_related_tracks(db, track_id="t-target", limit=10)

        assert result is not None
        # same-artist has cooc=2 (below default min_support of 3), dropped.
        # far track never co-occurs with target, dropped.
        ids = [r.track_id for r in result.related]
        assert set(ids) == {"t-nearA", "t-nearB"}
        for r in result.related:
            assert r.cooc_sessions == 3
            # Target is in 4 sessions (0,1,2,3); near tracks appear in 3 of them.
            assert r.cooc_rate == 0.75
            assert r.artist_id == "art-other"
            assert r.artist_name  # populated from Track or Stream fallback
            assert 0.0 < r.score <= 1.0

    def test_target_track_not_returned_in_its_own_related_list(self, db: Session):
        # min_support lowered to 1 so every co-occurrence in the fixture is
        # persisted -- the target still must not appear among its own results.
        with patch("app.services.related_tracks_rollup.DEFAULT_MIN_SUPPORT", 1):
            _seed_related_data(db)

        result = get_related_tracks(db, track_id="t-target", limit=10)

        assert result is not None
        assert result.related  # sanity: the lowered floor did surface pairs
        assert all(r.track_id != "t-target" for r in result.related)

    def test_limit_caps_results(self, db: Session):
        _seed_related_data(db)

        result = get_related_tracks(db, track_id="t-target", limit=1)

        assert result is not None
        assert len(result.related) == 1

    def test_metadata_comes_from_most_recent_stream_per_track(self, db: Session):
        _seed_related_data(db)
        # t-nearA already has three streams on art-other. Add a newer one that
        # re-attributes it, plus an even newer row with no artist_id at all.
        # The rollup is deliberately not rebuilt afterwards: display metadata
        # is read straight from `streams`, independent of the co-occurrence
        # tables, and this asserts exactly that lookup.
        later = datetime(2025, 2, 1, 12, 0)
        db.add(
            Stream(
                track_name="Near A",
                artist_name="Reattributed",
                played_at=later,
                track_id="t-nearA",
                artist_id="art-far",
            )
        )
        db.add(
            Stream(
                track_name="Near A",
                artist_name="No Artist Id",
                played_at=later + timedelta(days=1),
                track_id="t-nearA",
                artist_id=None,
            )
        )
        db.commit()

        result = get_related_tracks(db, track_id="t-target", limit=10)

        assert result is not None
        near_a = [r for r in result.related if r.track_id == "t-nearA"]
        assert len(near_a) == 1  # one row per track_id, never a duplicate
        # Highest streams.id with a non-NULL artist_id wins; the newer NULL row
        # is filtered out rather than blanking the artist.
        assert near_a[0].artist_id == "art-far"

    def test_unknown_track_returns_none(self, db: Session):
        _seed_related_data(db)

        result = get_related_tracks(db, track_id="not-a-real-track", limit=10)

        assert result is None

    def test_known_track_with_no_streams_returns_empty(self, db: Session):
        # Track row exists but no stream rows reference it.
        db.add(Track(spotify_id="t-orphan", name="Orphan", artist_name="X"))
        db.commit()

        result = get_related_tracks(db, track_id="t-orphan", limit=10)

        assert result is not None
        assert result.related == []
