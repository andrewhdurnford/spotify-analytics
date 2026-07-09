"""add daily_listening_stats and daily_track_stats rollup tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-08

Creates the two rollup tables only; does not populate them. After upgrading,
run the backfill script to derive rollup rows from existing `streams`:

    python -m app.scripts.backfill_rollups --commit

(inside the backend container, or against a local .venv per CLAUDE.md).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create daily_listening_stats and daily_track_stats rollup tables."""
    op.create_table(
        "daily_listening_stats",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("play_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("listening_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_daily_listening_stats_id", "daily_listening_stats", ["id"])
    op.create_index("ix_daily_listening_stats_date", "daily_listening_stats", ["date"])
    op.create_unique_constraint("uix_daily_listening_stats_date", "daily_listening_stats", ["date"])

    op.create_table(
        "daily_track_stats",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("track_key", sa.String(600), nullable=False),
        sa.Column("artist_key", sa.String(300), nullable=False),
        sa.Column("album_key", sa.String(600), nullable=False, server_default=""),
        sa.Column("track_id", sa.String(100), nullable=True),
        sa.Column("artist_id", sa.String(100), nullable=True),
        sa.Column("track_name", sa.String(255), nullable=False),
        sa.Column("artist_name", sa.String(255), nullable=False),
        sa.Column("album_name", sa.String(255), nullable=True),
        sa.Column("play_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("listening_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_daily_track_stats_id", "daily_track_stats", ["id"])
    op.create_index("ix_daily_track_stats_date", "daily_track_stats", ["date"])
    op.create_index("ix_daily_track_stats_track_key", "daily_track_stats", ["track_key"])
    op.create_index("ix_daily_track_stats_artist_key", "daily_track_stats", ["artist_key"])
    op.create_index("ix_daily_track_stats_track_id", "daily_track_stats", ["track_id"])
    op.create_index("ix_daily_track_stats_artist_id", "daily_track_stats", ["artist_id"])
    op.create_unique_constraint(
        "uix_daily_track_stats_key",
        "daily_track_stats",
        ["date", "track_key", "artist_key", "album_key"],
    )
    op.create_index(
        "ix_daily_track_stats_date_track_key", "daily_track_stats", ["date", "track_key"]
    )
    op.create_index(
        "ix_daily_track_stats_date_artist_key", "daily_track_stats", ["date", "artist_key"]
    )
    op.create_index(
        "ix_daily_track_stats_date_album",
        "daily_track_stats",
        ["date", "album_key", "artist_name"],
    )


def downgrade() -> None:
    """Drop the rollup tables."""
    op.drop_table("daily_track_stats")
    op.drop_table("daily_listening_stats")
