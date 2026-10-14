"""add foreign key constraints to streams table

Revision ID: a1b2c3d4e5f6
Revises: 9cc096b2f61f
Create Date: 2026-02-19

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "9cc096b2f61f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add foreign key constraints from streams to tracks and artists."""
    # Clean up orphaned references before adding FK constraints.
    # Set track_id to NULL where it doesn't match any track in the tracks table.
    op.execute(
        "UPDATE streams SET track_id = NULL "
        "WHERE track_id IS NOT NULL "
        "AND track_id NOT IN (SELECT spotify_id FROM tracks)"
    )
    # Set artist_id to NULL where it doesn't match any artist in the artists table.
    op.execute(
        "UPDATE streams SET artist_id = NULL "
        "WHERE artist_id IS NOT NULL "
        "AND artist_id NOT IN (SELECT spotify_id FROM artists)"
    )

    op.create_foreign_key(
        "fk_streams_track_id",
        "streams",
        "tracks",
        ["track_id"],
        ["spotify_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_streams_artist_id",
        "streams",
        "artists",
        ["artist_id"],
        ["spotify_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Remove foreign key constraints."""
    op.drop_constraint("fk_streams_artist_id", "streams", type_="foreignkey")
    op.drop_constraint("fk_streams_track_id", "streams", type_="foreignkey")
