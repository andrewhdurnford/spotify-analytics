"""Tests for the related-tracks co-occurrence rollup builder."""

from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Stream, Track, TrackCooccurrence, TrackSessionCount
from app.services.related_tracks_rollup import (
    get_last_rebuilt_at,
    rebuild_related_tracks_rollup,
)

MINUTE = timedelta(minutes=1)
DAY = timedelta(days=1)
BASE = datetime(2025, 1, 1, 12, 0)


def _stream(track_id: str | None, at: datetime) -> Stream:
    return Stream(
        track_name=track_id or "unknown",
        artist_name="Artist",
        played_at=at,
        track_id=track_id,
    )


def _seed(db: Session, plays: list[tuple[str | None, datetime]]) -> None:
    seen = {tid for tid, _ in plays if tid}
    db.add_all([Track(spotify_id=tid, name=tid, artist_name="Artist") for tid in sorted(seen)])
    db.add_all([_stream(tid, at) for tid, at in plays])
    db.commit()


def _rebuild(db: Session):
    stats = rebuild_related_tracks_rollup(db)
    db.commit()
    return stats


def _session_counts(db: Session) -> dict[str, int]:
    return {
        row.track_id: row.session_count
        for row in db.execute(select(TrackSessionCount.track_id, TrackSessionCount.session_count))
    }


def _pairs(db: Session) -> dict[tuple[str, str], int]:
    return {
        (row.track_id, row.other_track_id): row.cooc_sessions
        for row in db.execute(
            select(
                TrackCooccurrence.track_id,
                TrackCooccurrence.other_track_id,
                TrackCooccurrence.cooc_sessions,
            )
        )
    }


class TestSessionGrouping:
    def test_gap_over_threshold_starts_a_new_session(self, db: Session):
        # Two plays 31 minutes apart -> two sessions, so no co-occurrence.
        _seed(db, [("a", BASE), ("b", BASE + 31 * MINUTE)])

        stats = _rebuild(db)

        assert stats.sessions == 2
        assert _session_counts(db) == {"a": 1, "b": 1}
        assert _pairs(db) == {}

    def test_gap_at_threshold_stays_in_one_session(self, db: Session):
        # Exactly 30 minutes is *not* greater than the gap, so same session.
        _seed(db, [("a", BASE), ("b", BASE + 30 * MINUTE)])

        stats = _rebuild(db)

        assert stats.sessions == 1
        assert _session_counts(db) == {"a": 1, "b": 1}

    def test_repeated_play_counts_the_session_once(self, db: Session):
        # `a` plays three times in one session; its session_count is 1, not 3.
        _seed(db, [("a", BASE), ("b", BASE + MINUTE), ("a", BASE + 2 * MINUTE)])

        stats = _rebuild(db)

        assert stats.streams_processed == 3
        assert _session_counts(db) == {"a": 1, "b": 1}

    def test_streams_processed_counts_plays_not_tracks(self, db: Session):
        _seed(db, [("a", BASE), ("a", BASE + MINUTE), ("a", BASE + 2 * MINUTE)])

        stats = _rebuild(db)

        assert stats.streams_processed == 3
        assert stats.sessions == 1
        assert stats.track_rows == 1


class TestMinSupport:
    def _seed_three_and_two(self, db: Session) -> None:
        """`a`+`b` share 3 sessions; `a`+`c` share only 2."""
        plays: list[tuple[str, datetime]] = []
        for day in range(3):
            start = BASE + day * DAY
            plays += [("a", start), ("b", start + MINUTE)]
        for day in range(3, 5):
            start = BASE + day * DAY
            plays += [("a", start), ("c", start + MINUTE)]
        _seed(db, plays)

    def test_pairs_below_min_support_are_not_persisted(self, db: Session):
        self._seed_three_and_two(db)

        _rebuild(db)

        pairs = _pairs(db)
        assert pairs[("a", "b")] == 3
        assert ("a", "c") not in pairs
        assert ("c", "a") not in pairs

    def test_lowering_min_support_persists_more_pairs(self, db: Session):
        self._seed_three_and_two(db)

        with patch("app.services.related_tracks_rollup.DEFAULT_MIN_SUPPORT", 2):
            _rebuild(db)

        pairs = _pairs(db)
        assert pairs[("a", "c")] == 2
        assert pairs[("c", "a")] == 2


class TestBidirectionalStorage:
    def test_each_qualifying_pair_is_stored_in_both_directions(self, db: Session):
        plays: list[tuple[str, datetime]] = []
        for day in range(3):
            start = BASE + day * DAY
            plays += [("a", start), ("b", start + MINUTE)]
        _seed(db, plays)

        stats = _rebuild(db)

        assert _pairs(db) == {("a", "b"): 3, ("b", "a"): 3}
        assert stats.pair_rows == 2


class TestPerTrackCap:
    def test_rows_per_track_are_capped(self, db: Session):
        # One track co-occurring with 5 others across 3 sessions each; the cap
        # keeps only the 2 highest-scoring.
        others = ["b", "c", "d", "e", "f"]
        plays: list[tuple[str, datetime]] = []
        for day in range(3):
            start = BASE + day * DAY
            plays.append(("a", start))
            plays += [(other, start + (i + 1) * MINUTE) for i, other in enumerate(others)]
        _seed(db, plays)

        with patch("app.services.related_tracks_rollup.MAX_RELATED_PER_TRACK", 2):
            _rebuild(db)

        kept = [other for (track_id, other) in _pairs(db) if track_id == "a"]
        assert len(kept) == 2
        # All five candidates tie on score here, so the documented tie-break
        # (cooc desc, then track_id asc) decides.
        assert kept == ["b", "c"]

    def test_cap_keeps_highest_scoring_not_highest_count(self, db: Session):
        """A rarer track with the same overlap outranks a ubiquitous one.

        `noise` co-occurs with `a` more often in absolute terms, but it plays
        in every session ever, so its cosine score is lower than `focused`,
        which only ever plays alongside `a`.
        """
        plays: list[tuple[str, datetime]] = []
        for day in range(6):
            start = BASE + day * DAY
            plays += [("a", start), ("noise", start + MINUTE)]
            if day < 4:
                plays.append(("focused", start + 2 * MINUTE))
        # Extra `noise`-only sessions inflate its own session count.
        for day in range(6, 30):
            plays.append(("noise", BASE + day * DAY))
        _seed(db, plays)

        with patch("app.services.related_tracks_rollup.MAX_RELATED_PER_TRACK", 1):
            _rebuild(db)

        pairs = _pairs(db)
        kept = [other for (track_id, other) in pairs if track_id == "a"]
        assert kept == ["focused"]


class TestOversizedSessions:
    def test_huge_session_skips_pairs_but_still_counts_sessions(self, db: Session):
        track_ids = [f"t{i:03d}" for i in range(6)]
        _seed(db, [(tid, BASE + i * MINUTE) for i, tid in enumerate(track_ids)])

        with patch("app.services.related_tracks_rollup.MAX_SESSION_TRACKS", 5):
            stats = _rebuild(db)

        assert stats.oversized_sessions_skipped == 1
        assert stats.pair_rows == 0
        assert _pairs(db) == {}
        # Session membership is still recorded for every track in it.
        assert _session_counts(db) == dict.fromkeys(track_ids, 1)


class TestTrackIdFiltering:
    def test_empty_and_null_track_ids_are_excluded(self, db: Session):
        plays: list[tuple[str | None, datetime]] = []
        for day in range(3):
            start = BASE + day * DAY
            plays += [
                ("a", start),
                ("", start + MINUTE),
                (None, start + 2 * MINUTE),
                ("b", start + 3 * MINUTE),
            ]
        _seed(db, plays)

        stats = _rebuild(db)

        counts = _session_counts(db)
        assert counts == {"a": 3, "b": 3}
        assert "" not in counts
        assert stats.streams_processed == 6  # the 6 rows with a real track_id
        assert _pairs(db) == {("a", "b"): 3, ("b", "a"): 3}


class TestIdempotence:
    def test_rerunning_produces_identical_rows(self, db: Session):
        plays: list[tuple[str, datetime]] = []
        for day in range(3):
            start = BASE + day * DAY
            plays += [("a", start), ("b", start + MINUTE), ("c", start + 2 * MINUTE)]
        _seed(db, plays)

        first_stats = _rebuild(db)
        first_pairs, first_counts = _pairs(db), _session_counts(db)
        second_stats = _rebuild(db)

        assert _pairs(db) == first_pairs
        assert _session_counts(db) == first_counts
        assert second_stats.pair_rows == first_stats.pair_rows
        assert second_stats.track_rows == first_stats.track_rows

    def test_rebuild_drops_rows_for_deleted_streams(self, db: Session):
        plays: list[tuple[str, datetime]] = []
        for day in range(3):
            start = BASE + day * DAY
            plays += [("a", start), ("b", start + MINUTE)]
        _seed(db, plays)
        _rebuild(db)
        assert _pairs(db)

        db.execute(Stream.__table__.delete())
        db.commit()
        _rebuild(db)

        assert _pairs(db) == {}
        assert _session_counts(db) == {}


class TestRebuildMetadata:
    def test_last_rebuilt_at_starts_unset_and_advances(self, db: Session):
        _seed(db, [("a", BASE)])
        assert get_last_rebuilt_at(db) is None

        _rebuild(db)
        first = get_last_rebuilt_at(db)
        assert first is not None

        _rebuild(db)
        assert get_last_rebuilt_at(db) >= first

    def test_lock_contention_is_a_no_op(self, db: Session):
        _seed(db, [("a", BASE), ("b", BASE + MINUTE)])

        with patch(
            "app.services.related_tracks_rollup._try_acquire_rebuild_lock",
            return_value=False,
        ):
            stats = rebuild_related_tracks_rollup(db)

        assert stats.skipped_locked is True
        assert stats.streams_processed == 0
        assert _session_counts(db) == {}
        assert get_last_rebuilt_at(db) is None
