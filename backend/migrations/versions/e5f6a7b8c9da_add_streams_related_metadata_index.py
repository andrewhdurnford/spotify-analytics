"""add partial index serving the related-tracks metadata lookup

Revision ID: e5f6a7b8c9da
Revises: d4e5f6a7b8c9
Create Date: 2026-07-30

Supports the display-metadata subquery in `services/detail.py`
get_related_tracks:

    SELECT max(id) FROM streams
    WHERE track_id IN (...) AND artist_id IS NOT NULL
    GROUP BY track_id

Before this index the predicate was served by `uix_trackid_playedat`
(track_id, played_at), which does not carry `id` — so the planner bitmap-
scanned that index and then heap-fetched every matching row just to read
`id`. Carrying `id` here makes the aggregate an index-only scan.

Measured on a 171k-row `streams` (50 track_ids, ~57 streams each), which
matches the production shape in the pg_stat_statements sample:

    without: HashAggregate + Bitmap Heap Scan  241 buffers (84 heap)  1.66ms
    with:    GroupAggregate + Index Only Scan  154 buffers (0 heap)   1.05ms

Note what this does NOT do: Postgres has no per-group max() short-circuit, so
it still reads one index tuple per stream (2,750 in the measurement). The win
is eliminating heap I/O, not making the scan sublinear.

The index-only scan also depends on a current visibility map. Measured on the
same table before VACUUM, the identical plan did 2,750 heap fetches and 2,903
buffers — worse than the bitmap plan it replaces. `streams` is append-mostly
so autovacuum should keep the map current; if this endpoint regresses after a
bulk import, check `Heap Fetches` in EXPLAIN (ANALYZE, BUFFERS) first.

Partial on `artist_id IS NOT NULL` because the query always applies that
filter; it keeps loose streams (no resolved artist) out of the index.

LOCKING: plain CREATE INDEX takes ACCESS EXCLUSIVE on `streams` for the
duration, blocking the sync writer. That is seconds at this table's size and
matches how the existing stream indexes were added (see b2c3d4e5f6a7). On a
substantially larger `streams`, build it out-of-band instead:

    CREATE INDEX CONCURRENTLY ix_streams_track_id_id_artist_not_null
        ON streams (track_id, id) WHERE artist_id IS NOT NULL;

then stamp this revision without running it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9da"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the partial (track_id, id) index on streams."""
    op.create_index(
        "ix_streams_track_id_id_artist_not_null",
        "streams",
        ["track_id", "id"],
        postgresql_where=sa.text("artist_id IS NOT NULL"),
        sqlite_where=sa.text("artist_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove the partial index."""
    op.drop_index("ix_streams_track_id_id_artist_not_null", table_name="streams")
