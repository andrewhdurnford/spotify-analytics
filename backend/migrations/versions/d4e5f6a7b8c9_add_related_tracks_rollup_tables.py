"""add track_session_counts / track_cooccurrences rollup tables

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-28

DEPLOY ORDER — a post-migration backfill is REQUIRED, not optional:

Unlike the daily-rollup migration, this one creates the tables EMPTY. Session
co-occurrence is not reasonably expressible as a single SQL statement, so
there is no in-migration backfill.

That means there is a real, user-visible degraded window between this
migration and the backfill: `GET /api/tracks/{id}/related` returns HTTP 200
with `{"related": []}` for every track — an empty "Related Tracks" panel, not
a visible error, so nothing will page you if this step is skipped. Close the
window immediately after the new code is serving:

    python -m app.scripts.backfill_related_tracks --commit

Thereafter the tables are refreshed by the hourly
`POST /api/cron/rebuild-related-tracks` schedule (see README). There is no
incremental maintenance hook for these tables — see
`app.services.related_tracks_rollup` for why.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the related-tracks rollup tables (left empty; see docstring)."""
    op.create_table(
        "track_session_counts",
        sa.Column("track_id", sa.String(100), primary_key=True),
        sa.Column("session_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "track_cooccurrences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("track_id", sa.String(100), nullable=False),
        sa.Column("other_track_id", sa.String(100), nullable=False),
        sa.Column("cooc_sessions", sa.Integer(), nullable=False, server_default="0"),
    )
    # No `ix_track_cooccurrences_id` counterpart to the older tables'
    # `ix_*_id`: the primary key is already indexed, and this is the largest
    # table in the schema after `streams`.
    #
    # No standalone index on `track_id` or `other_track_id` either: this unique
    # constraint's own index already serves the leading-column lookup the read
    # path issues, and `other_track_id` is only ever joined to
    # track_session_counts' primary key.
    op.create_unique_constraint(
        "uix_track_cooccurrence_pair",
        "track_cooccurrences",
        ["track_id", "other_track_id"],
    )

    op.create_table(
        "related_tracks_rollup_meta",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("last_rebuilt_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Drop the related-tracks rollup tables."""
    op.drop_table("related_tracks_rollup_meta")
    op.drop_table("track_cooccurrences")
    op.drop_table("track_session_counts")
