"""add recent_plays_sync_meta table and streams.played_ratio

Revision ID: f6a7b8c9dae0
Revises: e5f6a7b8c9da
Create Date: 2026-08-17

Supports the switch from live-progress polling (catching a track in its
last 2%/5s) to reconciling completed plays from Spotify's recently-played
history. `recent_plays_sync_meta` holds the single-row cursor the new sync
job resumes from; `streams.played_ratio` records the estimated played
fraction for rows the new job inserts (null for every existing row and for
historical-import rows going forward -- see app.services.recent_plays_sync).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9dae0"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recent_plays_sync_meta",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("cursor_played_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
    )
    op.add_column("streams", sa.Column("played_ratio", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("streams", "played_ratio")
    op.drop_table("recent_plays_sync_meta")
