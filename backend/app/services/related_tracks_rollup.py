"""Periodic full rebuild of the related-tracks co-occurrence rollup.

`get_related_tracks` used to walk every `streams` row (ordered by
`played_at`) in Python on each cache miss, grouping plays into sessions and
counting co-occurrence. That is an unbounded full table scan on a request
path. This module precomputes the same numbers into two small tables —
`track_session_counts` (sessions per track) and `track_cooccurrences`
(sessions containing both tracks of a pair) — so the request path becomes a
couple of indexed lookups.

WHY A FULL PERIODIC REBUILD, NOT AN INCREMENTAL HOOK

Not because session detection is sequential: appending plays at the tail is
purely additive (a new play either extends the last session or starts a new
one; earlier sessions and their pairs are never revised), so a tail-only
incremental path would be no harder to write than the `before_flush` hook in
`app.services.rollups`.

The actual blocker is `DEFAULT_MIN_SUPPORT`. A pair only earns a row once it
has co-occurred in 3 sessions, which means an incremental maintainer has to
carry that pair through counts 1 and 2 first — i.e. persist *every* pair
ever observed, several times the row count this design exists to avoid.
Filtering is only cheap when the whole history is in memory at once, which
is exactly what a full rebuild does. Please don't "optimize" this into an
incremental path without solving that first.

`DEFAULT_MIN_SUPPORT` (and `MAX_RELATED_PER_TRACK`) are build-time constants,
not query parameters: they decide which rows exist on disk. Changing either
requires a rebuild, not just a redeploy.
"""

import logging
import math
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.orm import Session

from ..models import RelatedTracksRollupMeta, Stream, TrackCooccurrence, TrackSessionCount

logger = logging.getLogger(__name__)

# Session = consecutive plays with no gap > this threshold.
SESSION_GAP_SECONDS = 30 * 60
# Minimum number of co-occurring sessions for a pair to be persisted at all.
DEFAULT_MIN_SUPPORT = 3
# Hard cap on persisted rows per track_id. The route's MAX_LIMIT is 50, so
# this leaves ample headroom while bounding the table even if a huge number
# of pairs clear min-support.
MAX_RELATED_PER_TRACK = 100
# A session with more distinct tracks than this contributes to per-track
# session counts but is skipped for pairwise counting: `combinations` is
# quadratic, so one pathological session (a day-long shuffle with no 30-min
# break) could otherwise generate millions of pairs in the accumulator.
# Sized well above a plausible real session (~8h of 3-min tracks is ~160).
MAX_SESSION_TRACKS = 300

# Server-side batch size for streaming `streams` during a rebuild.
_STREAM_FETCH_SIZE = 1000
# Rows per bulk INSERT — keeps the bound-parameter count per statement sane.
_INSERT_CHUNK_SIZE = 5000
# Serializes related-tracks rebuilds in PostgreSQL. Distinct from
# `rollups.ROLLUP_ADVISORY_LOCK_ID`: the two rebuilds touch different tables
# and must not block each other.
RELATED_ROLLUP_ADVISORY_LOCK_ID = 7_315_902_025
# The delete/insert phase rewrites the whole rollup, which is well past the
# 10s connection-level `statement_timeout` (see config.db_statement_timeout_ms).
REBUILD_STATEMENT_TIMEOUT_MS = 15 * 60 * 1000
# The single row of `related_tracks_rollup_meta`.
_META_ROW_ID = 1


@dataclass(frozen=True)
class RelatedRollupStats:
    streams_processed: int
    sessions: int
    track_rows: int
    pair_rows: int
    oversized_sessions_skipped: int
    # True when another rebuild already held the advisory lock and this call
    # did nothing.
    skipped_locked: bool = False


def _is_postgres(session: Session) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def _try_acquire_rebuild_lock(session: Session) -> bool:
    """Take the rebuild lock if it is free, otherwise report failure.

    Deliberately the *non*-blocking `pg_try_advisory_xact_lock`, unlike
    `rollups._acquire_rollup_write_lock`: a rebuild is a full scan, and
    queueing a second one behind the first would double the scan cost to
    produce the same result. SQLite tests are single-connection and need no
    database-level lock.
    """
    if not _is_postgres(session):
        return True
    return bool(
        session.execute(
            select(func.pg_try_advisory_xact_lock(RELATED_ROLLUP_ADVISORY_LOCK_ID))
        ).scalar()
    )


def _relax_statement_timeout(session: Session) -> None:
    """Raise `statement_timeout` for this transaction only (`SET LOCAL`)."""
    if _is_postgres(session):
        session.execute(text(f"SET LOCAL statement_timeout = {REBUILD_STATEMENT_TIMEOUT_MS}"))


def iter_sessions(session: Session) -> Iterator[tuple[frozenset[str], int]]:
    """Yield `(deduped track_ids, play count)` per listening session, in time order.

    A session is a contiguous run of plays with no gap larger than
    `SESSION_GAP_SECONDS`, exactly as the old in-request implementation
    defined it.

    Rows with a missing or empty `track_id` are excluded from the scan
    entirely: they can't be linked from a related-tracks result, and an empty
    string is a junk id this data really does contain (see the
    `nullif(track_id, '')` handling in the daily-rollup migration).

    Streamed with `yield_per` so only the accumulators, not every `streams`
    row, have to fit in memory.
    """
    stmt = (
        select(Stream.track_id, Stream.played_at)
        .where(Stream.track_id.isnot(None), Stream.track_id != "")
        .order_by(Stream.played_at)
        .execution_options(yield_per=_STREAM_FETCH_SIZE)
    )

    current: set[str] = set()
    plays = 0
    prev_played_at: datetime | None = None
    for track_id, played_at in session.execute(stmt):
        if (
            prev_played_at is not None
            and (played_at - prev_played_at).total_seconds() > SESSION_GAP_SECONDS
        ):
            yield frozenset(current), plays
            current = set()
            plays = 0
        current.add(track_id)
        plays += 1
        prev_played_at = played_at

    if current:
        yield frozenset(current), plays


def _accumulate(
    session: Session,
    session_counts: dict[str, int],
    pair_counts: dict[tuple[str, str], int],
) -> tuple[int, int, int]:
    """Fold every session into the accumulators.

    Pairs are keyed canonically (lexicographically smaller id first) so each
    unordered pair is counted once; both directions are materialized later,
    at persist time.

    Returns (streams_processed, sessions, oversized_sessions_skipped).
    """
    streams_processed = 0
    sessions = 0
    oversized = 0

    for track_ids, plays in iter_sessions(session):
        sessions += 1
        streams_processed += plays
        for track_id in track_ids:
            session_counts[track_id] = session_counts.get(track_id, 0) + 1

        if len(track_ids) > MAX_SESSION_TRACKS:
            oversized += 1
            logger.warning(
                "Skipping pairwise counting for session #%d with %d distinct tracks "
                "(cap is %d); its plays still count toward per-track session totals.",
                sessions,
                len(track_ids),
                MAX_SESSION_TRACKS,
            )
            continue

        for a, b in combinations(sorted(track_ids), 2):
            pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1

    return streams_processed, sessions, oversized


def _persisted_pair_rows(
    session_counts: dict[str, int],
    pair_counts: dict[tuple[str, str], int],
) -> list[dict]:
    """Filter to min-support, cap per track, and expand to bidirectional rows.

    The per-track cap keeps the highest-*scoring* candidates, using the same
    `(score desc, cooc_sessions desc, track_id asc)` ordering the read path
    applies — ranking by raw co-occurrence instead would sometimes truncate a
    pair the read would have surfaced, since the score divides by the other
    track's own session count.
    """
    candidates: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (a, b), count in pair_counts.items():
        if count < DEFAULT_MIN_SUPPORT:
            continue
        candidates[a].append((b, count))
        candidates[b].append((a, count))

    rows: list[dict] = []
    for track_id, pairs in candidates.items():
        target_count = session_counts[track_id]
        pairs.sort(
            key=lambda pair: (
                -pair[1] / math.sqrt(target_count * session_counts[pair[0]]),
                -pair[1],
                pair[0],
            )
        )
        rows.extend(
            {"track_id": track_id, "other_track_id": other_id, "cooc_sessions": count}
            for other_id, count in pairs[:MAX_RELATED_PER_TRACK]
        )
    return rows


def _bulk_insert(session: Session, model, rows: Sequence[dict]) -> None:
    """Insert `rows` in chunks with a Core executemany.

    Not `add_all`: this table is an order of magnitude larger than the daily
    rollups, and every ORM flush re-runs the `before_flush` hook registered in
    `app.models`.
    """
    for start in range(0, len(rows), _INSERT_CHUNK_SIZE):
        session.execute(insert(model), rows[start : start + _INSERT_CHUNK_SIZE])


def get_last_rebuilt_at(session: Session) -> datetime | None:
    """Completion time (naive UTC) of the last successful rebuild, if any."""
    return session.execute(
        select(RelatedTracksRollupMeta.last_rebuilt_at).where(
            RelatedTracksRollupMeta.id == _META_ROW_ID
        )
    ).scalar_one_or_none()


def _record_rebuilt_at(session: Session, when: datetime) -> None:
    meta = session.get(RelatedTracksRollupMeta, _META_ROW_ID)
    if meta is None:
        session.add(RelatedTracksRollupMeta(id=_META_ROW_ID, last_rebuilt_at=when))
    else:
        meta.last_rebuilt_at = when


def rebuild_related_tracks_rollup(session: Session) -> RelatedRollupStats:
    """Rebuild both related-tracks rollup tables from `streams`.

    Wholesale delete + recompute in one transaction (the caller commits).
    Idempotent: rerunning against unchanged `streams` produces identical
    rows. No-ops when another rebuild already holds the advisory lock.
    """
    if not _try_acquire_rebuild_lock(session):
        logger.info("Related-tracks rebuild already in progress; skipping this run.")
        return RelatedRollupStats(
            streams_processed=0,
            sessions=0,
            track_rows=0,
            pair_rows=0,
            oversized_sessions_skipped=0,
            skipped_locked=True,
        )

    _relax_statement_timeout(session)

    session_counts: dict[str, int] = {}
    pair_counts: dict[tuple[str, str], int] = {}
    streams_processed, sessions, oversized = _accumulate(session, session_counts, pair_counts)

    pair_rows = _persisted_pair_rows(session_counts, pair_counts)
    count_rows = [
        {"track_id": track_id, "session_count": count}
        for track_id, count in sorted(session_counts.items())
    ]

    session.execute(delete(TrackCooccurrence))
    session.execute(delete(TrackSessionCount))
    _bulk_insert(session, TrackSessionCount, count_rows)
    _bulk_insert(session, TrackCooccurrence, pair_rows)
    # Naive UTC, matching `streams.played_at` and the rest of the schema.
    _record_rebuilt_at(session, datetime.now(UTC).replace(tzinfo=None))

    return RelatedRollupStats(
        streams_processed=streams_processed,
        sessions=sessions,
        track_rows=len(count_rows),
        pair_rows=len(pair_rows),
        oversized_sessions_skipped=oversized,
    )
