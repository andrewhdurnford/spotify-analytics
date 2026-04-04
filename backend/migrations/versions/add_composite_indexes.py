"""add composite indexes for top-items queries

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-23

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add composite indexes to speed up date-filtered aggregation queries."""
    op.create_index(
        "ix_streams_played_at_track_id",
        "streams",
        ["played_at", "track_id"],
    )
    op.create_index(
        "ix_streams_played_at_artist_id",
        "streams",
        ["played_at", "artist_id"],
    )


def downgrade() -> None:
    """Remove composite indexes."""
    op.drop_index("ix_streams_played_at_artist_id", table_name="streams")
    op.drop_index("ix_streams_played_at_track_id", table_name="streams")
