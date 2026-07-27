"""Tests for app.services.rollups — daily_listening_stats / daily_track_stats.

Covers: incremental maintenance (via the before_flush hook, exercised simply
by adding Stream rows), deterministic bulk backfill from existing streams,
the scoped rebuild used after metadata enrichment, '' vs NULL normalization
of display columns, warnings for Stream mutations the hook cannot track,
idempotent reruns, timezone boundaries, partial-day data, and empty ranges.
"""

import logging
from datetime import date, datetime

from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from app.models import Artist, DailyListeningStats, DailyTrackStats, Stream, Track
from app.services.rollups import (
    NO_ALBUM_SENTINEL,
    RollupRebuildStats,
    album_key_for,
    artist_key_for,
    local_date_for,
    optional_text,
    rebuild_all_rollups,
    rebuild_rollups_for_tracks,
    track_key_for,
)

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


class TestKeyHelpers:
    def test_track_key_prefers_id(self):
        assert track_key_for("abc123", "Song") == "abc123"

    def test_track_key_falls_back_to_name(self):
        assert track_key_for(None, "Song") == "name:Song"

    def test_artist_key_prefers_id(self):
        assert artist_key_for("artist1", "Artist") == "artist1"

    def test_artist_key_falls_back_to_name(self):
        assert artist_key_for(None, "Artist") == "name:Artist"

    def test_album_key_uses_sentinel_for_missing_album(self):
        assert album_key_for(None) == NO_ALBUM_SENTINEL
        assert album_key_for("") == NO_ALBUM_SENTINEL

    def test_album_key_passthrough(self):
        assert album_key_for("Album 1") == "Album 1"

    def test_local_date_for_late_utc_play_lands_on_previous_toronto_day(self):
        # 2026-07-09 02:05 UTC is 2026-07-08 22:05 in Toronto (EDT, UTC-4).
        assert local_date_for(datetime(2026, 7, 9, 2, 5)) == date(2026, 7, 8)

    def test_local_date_for_daytime_play_same_day(self):
        assert local_date_for(datetime(2025, 1, 10, 18, 0)) == date(2025, 1, 10)


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------


def _seed(db: Session) -> None:
    track = Track(
        spotify_id="track1",
        name="Song A",
        artist_name="Artist X",
        album_name="Album 1",
        album_image="https://img/album1.jpg",
        duration_ms=200_000,
    )
    track2 = Track(
        spotify_id="track2",
        name="Song B",
        artist_name="Artist Y",
        album_name="Album 2",
        album_image="https://img/album2.jpg",
        duration_ms=180_000,
    )
    artist = Artist(spotify_id="artist1", name="Artist X", genres=["pop"], popularity=80)
    db.add_all([track, track2, artist])
    db.flush()

    streams = [
        # Two plays of Song A on 2025-01-10 (same local day).
        Stream(
            track_name="Song A",
            artist_name="Artist X",
            album_name="Album 1",
            played_at=datetime(2025, 1, 10, 12, 0),
            duration_ms=200_000,
            track_id="track1",
            artist_id="artist1",
        ),
        Stream(
            track_name="Song A",
            artist_name="Artist X",
            album_name="Album 1",
            played_at=datetime(2025, 1, 10, 15, 0),
            duration_ms=200_000,
            track_id="track1",
            artist_id="artist1",
        ),
        # One play the next day.
        Stream(
            track_name="Song A",
            artist_name="Artist X",
            album_name="Album 1",
            played_at=datetime(2025, 1, 11, 12, 0),
            duration_ms=200_000,
            track_id="track1",
            artist_id="artist1",
        ),
        # Song B: has a track_id but no artist_id on the stream row.
        Stream(
            track_name="Song B",
            artist_name="Artist Y",
            album_name="Album 2",
            played_at=datetime(2025, 2, 5, 12, 0),
            duration_ms=180_000,
            track_id="track2",
            artist_id=None,
        ),
        # No track_id/artist_id/album — exercises name-fallback keys + the
        # "no album" sentinel. Also has no matching Track row, so it should
        # contribute 0 to listening_ms (mirrors historical raw-query
        # semantics: no fallback to Stream.duration_ms).
        Stream(
            track_name="Loose Song",
            artist_name="Unknown Artist",
            played_at=datetime(2025, 2, 5, 13, 0),
            duration_ms=150_000,
        ),
        # Late-UTC play landing on the previous Toronto calendar day.
        Stream(
            track_name="Night Owl",
            artist_name="Artist Z",
            played_at=datetime(2026, 7, 9, 2, 5),
        ),
    ]
    db.add_all(streams)
    db.commit()


def _daily_rows(db: Session) -> dict[date, DailyListeningStats]:
    rows = db.execute(select(DailyListeningStats)).scalars().all()
    return {r.date: r for r in rows}


def _track_rows(db: Session) -> list[DailyTrackStats]:
    return db.execute(select(DailyTrackStats)).scalars().all()


# ---------------------------------------------------------------------------
# Incremental maintenance (before_flush hook, exercised via plain db.add)
# ---------------------------------------------------------------------------


class TestIncrementalMaintenance:
    def test_daily_listening_stats_matches_hand_computed_aggregates(self, db: Session):
        _seed(db)
        daily = _daily_rows(db)

        assert daily[date(2025, 1, 10)].play_count == 2
        assert daily[date(2025, 1, 10)].listening_ms == 400_000
        assert daily[date(2025, 1, 11)].play_count == 1
        assert daily[date(2025, 1, 11)].listening_ms == 200_000
        # Song B has a known Track (180_000ms); Loose Song has no Track row
        # so contributes 0 — total for the day is 180_000, not 330_000.
        assert daily[date(2025, 2, 5)].play_count == 2
        assert daily[date(2025, 2, 5)].listening_ms == 180_000
        # Late-UTC play recorded under the Toronto-local date.
        assert daily[date(2026, 7, 8)].play_count == 1
        assert date(2026, 7, 9) not in daily

    def test_daily_track_stats_matches_hand_computed_aggregates(self, db: Session):
        _seed(db)
        rows = {(r.date, r.track_key): r for r in _track_rows(db)}

        song_a = rows[(date(2025, 1, 10), "track1")]
        assert song_a.play_count == 2
        assert song_a.listening_ms == 400_000
        assert song_a.artist_key == "artist1"
        assert song_a.album_key == "Album 1"
        assert song_a.track_name == "Song A"
        assert song_a.artist_name == "Artist X"

        song_b = rows[(date(2025, 2, 5), "track2")]
        assert song_b.play_count == 1
        assert song_b.listening_ms == 180_000
        # No artist_id on the stream -> falls back to a name-based artist_key.
        assert song_b.artist_key == "name:Artist Y"
        assert song_b.artist_id is None

        loose = rows[(date(2025, 2, 5), "name:Loose Song")]
        assert loose.play_count == 1
        assert loose.listening_ms == 0
        assert loose.artist_key == "name:Unknown Artist"
        assert loose.album_key == NO_ALBUM_SENTINEL
        assert loose.album_name is None
        assert loose.track_id is None
        assert loose.artist_id is None

    def test_repeat_plays_same_flush_batch_accumulate_correctly(self, db: Session):
        """Regression test: multiple new Stream rows mapping to the same
        rollup key within a single flush must accumulate onto one row, not
        each attempt a competing INSERT (which would violate the unique
        constraint on daily_listening_stats.date)."""
        db.add_all(
            [
                Stream(
                    track_name=f"Song {i}",
                    artist_name="Artist",
                    played_at=datetime(2025, 5, 1, 10, i),
                )
                for i in range(5)
            ]
        )
        db.commit()

        daily = _daily_rows(db)
        assert daily[date(2025, 5, 1)].play_count == 5
        assert len(_track_rows(db)) == 5  # 5 distinct name-fallback track_keys

    def test_repeated_track_in_one_flush_looks_up_duration_once(self, db: Session):
        db.add(Track(spotify_id="repeat", name="Repeat", duration_ms=123_000))
        db.commit()

        duration_selects = []

        def record_duration_select(_conn, _cursor, statement, _parameters, _context, _many):
            if "SELECT tracks.duration_ms" in statement:
                duration_selects.append(statement)

        event.listen(db.get_bind(), "before_cursor_execute", record_duration_select)
        try:
            db.add_all(
                [
                    Stream(
                        track_name="Repeat",
                        artist_name="Artist",
                        played_at=datetime(2025, 5, 2, 10, minute),
                        track_id="repeat",
                    )
                    for minute in range(3)
                ]
            )
            db.commit()
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", record_duration_select)

        assert len(duration_selects) == 1


# ---------------------------------------------------------------------------
# Bulk backfill / rebuild
# ---------------------------------------------------------------------------


class TestRebuildAllRollups:
    def test_rebuild_matches_incremental_rollups(self, db: Session):
        """Backfill correctness: rebuilding from scratch reproduces exactly
        what incremental maintenance already computed for the same streams."""
        _seed(db)

        before_daily = sorted(
            (r.date, r.play_count, r.listening_ms)
            for r in db.execute(select(DailyListeningStats)).scalars()
        )
        before_track = sorted(
            (r.date, r.track_key, r.artist_key, r.album_key, r.play_count, r.listening_ms)
            for r in db.execute(select(DailyTrackStats)).scalars()
        )

        stats = rebuild_all_rollups(db)
        db.commit()

        assert stats.streams_processed == 6
        assert stats.daily_rows == len(before_daily)
        assert stats.track_rows == len(before_track)

        after_daily = sorted(
            (r.date, r.play_count, r.listening_ms)
            for r in db.execute(select(DailyListeningStats)).scalars()
        )
        after_track = sorted(
            (r.date, r.track_key, r.artist_key, r.album_key, r.play_count, r.listening_ms)
            for r in db.execute(select(DailyTrackStats)).scalars()
        )

        assert after_daily == before_daily
        assert after_track == before_track

    def test_rebuild_is_idempotent(self, db: Session):
        _seed(db)
        rebuild_all_rollups(db)
        db.commit()

        first_daily = sorted(
            (r.date, r.play_count, r.listening_ms)
            for r in db.execute(select(DailyListeningStats)).scalars()
        )
        first_track = sorted(
            (r.date, r.track_key, r.play_count, r.listening_ms)
            for r in db.execute(select(DailyTrackStats)).scalars()
        )

        rebuild_all_rollups(db)
        db.commit()

        second_daily = sorted(
            (r.date, r.play_count, r.listening_ms)
            for r in db.execute(select(DailyListeningStats)).scalars()
        )
        second_track = sorted(
            (r.date, r.track_key, r.play_count, r.listening_ms)
            for r in db.execute(select(DailyTrackStats)).scalars()
        )

        assert first_daily == second_daily
        assert first_track == second_track

    def test_rebuild_empty_database_produces_no_rows(self, db: Session):
        stats = rebuild_all_rollups(db)
        db.commit()

        assert stats.streams_processed == 0
        assert stats.daily_rows == 0
        assert stats.track_rows == 0
        assert _daily_rows(db) == {}
        assert _track_rows(db) == []

    def test_rebuild_partial_day_single_play(self, db: Session):
        db.add(
            Stream(
                track_name="Solo",
                artist_name="Solo Artist",
                played_at=datetime(2025, 3, 3, 8, 0),
                duration_ms=210_000,
            )
        )
        db.commit()

        # rebuild_all_rollups deletes+recomputes both tables in one call, so
        # this also proves the bulk path alone (not just incremental
        # maintenance) correctly reconstructs a single partial day.
        stats = rebuild_all_rollups(db)
        db.commit()

        assert stats.streams_processed == 1
        assert stats.daily_rows == 1
        assert stats.track_rows == 1

        daily = _daily_rows(db)
        assert daily[date(2025, 3, 3)].play_count == 1

    def test_rebuild_timezone_boundary(self, db: Session):
        db.add(
            Stream(
                track_name="Night Owl",
                artist_name="Artist Z",
                played_at=datetime(2026, 7, 9, 2, 5),
            )
        )
        db.commit()

        rebuild_all_rollups(db)
        db.commit()

        daily = _daily_rows(db)
        assert date(2026, 7, 8) in daily
        assert date(2026, 7, 9) not in daily

    def test_rebuild_clears_stale_rows_not_backed_by_any_stream(self, db: Session):
        """A rollup row with no matching streams (e.g. left over from a
        deleted/corrected import) must not survive a rebuild."""
        db.add(DailyListeningStats(date=date(2020, 1, 1), play_count=99, listening_ms=1))
        db.commit()

        stats = rebuild_all_rollups(db)
        db.commit()

        assert stats.daily_rows == 0
        assert date(2020, 1, 1) not in _daily_rows(db)


# ---------------------------------------------------------------------------
# Scoped rebuild (metadata enrichment path)
# ---------------------------------------------------------------------------


def _rollup_snapshot(db: Session) -> tuple[list, list]:
    """Comparable, order-independent dump of both rollup tables."""

    def _sort_key(row: tuple) -> tuple:
        return tuple(str(value) for value in row)

    daily = sorted(
        ((r.date, r.play_count, r.listening_ms) for r in _daily_rows(db).values()),
        key=_sort_key,
    )
    tracks = sorted(
        (
            (
                r.date,
                r.track_key,
                r.artist_key,
                r.album_key,
                r.track_id,
                r.artist_id,
                r.track_name,
                r.artist_name,
                r.album_name,
                r.play_count,
                r.listening_ms,
            )
            for r in _track_rows(db)
        ),
        key=_sort_key,
    )
    return daily, tracks


class TestScopedRebuild:
    """rebuild_rollups_for_tracks must agree with a full rebuild.

    It is the cheap path used after Spotify metadata enrichment, which can
    change Track.duration_ms (feeds listening_ms) and fill in
    Stream.artist_id (changes artist_key), but never adds or removes plays.
    """

    def test_matches_full_rebuild_after_metadata_enrichment(self, db: Session):
        _seed(db)

        # Simulate an enrichment pass over track2: duration corrected and the
        # previously-unknown artist_id backfilled onto its streams.
        track2 = db.execute(select(Track).where(Track.spotify_id == "track2")).scalar_one()
        track2.duration_ms = 240_000
        db.execute(update(Stream).where(Stream.track_id == "track2").values(artist_id="artistY"))
        db.commit()

        stats = rebuild_rollups_for_tracks(db, ["track2"])
        db.commit()
        scoped = _rollup_snapshot(db)

        assert stats.streams_processed == 1
        assert stats.track_rows == 1

        rebuild_all_rollups(db)
        db.commit()
        assert scoped == _rollup_snapshot(db)

    def test_regroups_affected_rows_and_adjusts_daily_totals(self, db: Session):
        _seed(db)
        before = _daily_rows(db)[date(2025, 2, 5)]
        assert before.play_count == 2
        assert before.listening_ms == 180_000

        track2 = db.execute(select(Track).where(Track.spotify_id == "track2")).scalar_one()
        track2.duration_ms = 240_000
        db.execute(update(Stream).where(Stream.track_id == "track2").values(artist_id="artistY"))
        db.commit()

        rebuild_rollups_for_tracks(db, ["track2"])
        db.commit()

        daily = _daily_rows(db)[date(2025, 2, 5)]
        # Play count is untouched by enrichment; only listening_ms moves.
        assert daily.play_count == 2
        assert daily.listening_ms == 240_000

        rows = {(r.date, r.track_key): r for r in _track_rows(db)}
        song_b = rows[(date(2025, 2, 5), "track2")]
        assert song_b.artist_key == "artistY"
        assert song_b.artist_id == "artistY"
        assert song_b.listening_ms == 240_000
        # The name-keyed row sharing that date is left alone.
        assert rows[(date(2025, 2, 5), "name:Loose Song")].listening_ms == 0

    def test_leaves_other_tracks_and_dates_untouched(self, db: Session):
        _seed(db)
        untouched_before = {
            (r.date, r.track_key): (r.play_count, r.listening_ms)
            for r in _track_rows(db)
            if r.track_key != "track2"
        }
        daily_before = {
            d: (r.play_count, r.listening_ms)
            for d, r in _daily_rows(db).items()
            if d != date(2025, 2, 5)
        }

        track2 = db.execute(select(Track).where(Track.spotify_id == "track2")).scalar_one()
        track2.duration_ms = 240_000
        db.commit()

        rebuild_rollups_for_tracks(db, ["track2"])
        db.commit()

        assert {
            (r.date, r.track_key): (r.play_count, r.listening_ms)
            for r in _track_rows(db)
            if r.track_key != "track2"
        } == untouched_before
        assert {
            d: (r.play_count, r.listening_ms)
            for d, r in _daily_rows(db).items()
            if d != date(2025, 2, 5)
        } == daily_before

    def test_empty_and_blank_ids_are_a_noop(self, db: Session):
        _seed(db)
        before = _rollup_snapshot(db)

        stats = rebuild_rollups_for_tracks(db, ["", None])  # type: ignore[list-item]
        db.commit()

        assert stats == RollupRebuildStats(daily_rows=0, track_rows=0, streams_processed=0)
        assert _rollup_snapshot(db) == before

    def test_is_idempotent(self, db: Session):
        _seed(db)
        rebuild_rollups_for_tracks(db, ["track1"])
        db.commit()
        once = _rollup_snapshot(db)

        rebuild_rollups_for_tracks(db, ["track1"])
        db.commit()

        assert _rollup_snapshot(db) == once


# ---------------------------------------------------------------------------
# Display-column normalization ('' vs NULL)
# ---------------------------------------------------------------------------


class TestOptionalTextNormalization:
    def test_optional_text_maps_blank_to_none(self):
        assert optional_text("") is None
        assert optional_text(None) is None
        assert optional_text("abc") == "abc"

    def test_blank_ids_and_album_store_as_null(self, db: Session):
        db.add(
            Stream(
                track_name="Blank Fields",
                artist_name="Blank Artist",
                album_name="",
                played_at=datetime(2025, 4, 1, 12, 0),
                track_id="",
                artist_id="",
            )
        )
        db.commit()

        row = _track_rows(db)[0]
        assert row.track_id is None
        assert row.artist_id is None
        assert row.album_name is None
        assert row.track_key == "name:Blank Fields"
        assert row.album_key == NO_ALBUM_SENTINEL

    def test_rebuild_agrees_with_incremental_on_blank_fields(self, db: Session):
        db.add(
            Stream(
                track_name="Blank Fields",
                artist_name="Blank Artist",
                album_name="",
                played_at=datetime(2025, 4, 1, 12, 0),
                track_id="",
                artist_id="",
            )
        )
        db.commit()
        incremental = _rollup_snapshot(db)

        rebuild_all_rollups(db)
        db.commit()

        assert _rollup_snapshot(db) == incremental


# ---------------------------------------------------------------------------
# Mutations the incremental hook cannot track
# ---------------------------------------------------------------------------


class TestUnrollupableStreamChanges:
    def test_stream_delete_warns_and_points_at_the_backfill_script(self, db: Session, caplog):
        _seed(db)
        stream = db.execute(select(Stream)).scalars().first()

        with caplog.at_level(logging.WARNING, logger="app.models"):
            db.delete(stream)
            db.commit()

        assert "backfill_rollups" in caplog.text
        assert "deletes 1" in caplog.text

    def test_stream_edit_of_rollup_column_warns(self, db: Session, caplog):
        _seed(db)
        stream = db.execute(select(Stream)).scalars().first()

        with caplog.at_level(logging.WARNING, logger="app.models"):
            stream.played_at = datetime(2030, 1, 1, 12, 0)
            db.commit()

        assert "edits 1" in caplog.text

    def test_edit_of_non_rollup_column_is_silent(self, db: Session, caplog):
        _seed(db)
        stream = db.execute(select(Stream)).scalars().first()

        with caplog.at_level(logging.WARNING, logger="app.models"):
            stream.duration_ms = 12345
            db.commit()

        assert caplog.text == ""

    def test_plain_insert_does_not_warn(self, db: Session, caplog):
        with caplog.at_level(logging.WARNING, logger="app.models"):
            db.add(
                Stream(
                    track_name="Fresh",
                    artist_name="Fresh Artist",
                    played_at=datetime(2025, 5, 1, 12, 0),
                )
            )
            db.commit()

        assert caplog.text == ""
