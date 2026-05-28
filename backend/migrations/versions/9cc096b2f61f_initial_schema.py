"""initial schema

Revision ID: 9cc096b2f61f
Revises:
Create Date: 2026-02-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9cc096b2f61f'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create initial tables: streams, tracks, artists."""
    op.create_table(
        'streams',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('track_name', sa.String(255), nullable=False),
        sa.Column('artist_name', sa.String(255), nullable=False),
        sa.Column('album_name', sa.String(255), nullable=True),
        sa.Column('played_at', sa.DateTime(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('track_id', sa.String(100), nullable=True),
        sa.Column('artist_id', sa.String(100), nullable=True),
    )
    op.create_index('ix_streams_id', 'streams', ['id'])
    op.create_index('ix_streams_track_name', 'streams', ['track_name'])
    op.create_index('ix_streams_artist_name', 'streams', ['artist_name'])
    op.create_index('ix_streams_played_at', 'streams', ['played_at'])
    op.create_unique_constraint('uix_trackid_playedat', 'streams', ['track_id', 'played_at'])
    op.create_unique_constraint('uix_name_artist_playedat', 'streams', ['track_name', 'artist_name', 'played_at'])

    op.create_table(
        'tracks',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('spotify_id', sa.String(100), nullable=False),
        sa.Column('name', sa.String(512), nullable=False),
        sa.Column('artist_name', sa.String(255), nullable=True),
        sa.Column('album_name', sa.String(512), nullable=True),
        sa.Column('album_image', sa.String(1000), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('popularity', sa.Integer(), nullable=True),
        sa.Column('first_played_at', sa.DateTime(), nullable=True),
        sa.Column('last_updated', sa.DateTime(), server_default=sa.func.now(), nullable=True),
    )
    op.create_index('ix_tracks_id', 'tracks', ['id'])
    op.create_index('ix_tracks_spotify_id', 'tracks', ['spotify_id'], unique=True)

    op.create_table(
        'artists',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('spotify_id', sa.String(100), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('genres', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('popularity', sa.Integer(), nullable=True),
        sa.Column('image_url', sa.String(1000), nullable=True),
    )
    op.create_index('ix_artists_id', 'artists', ['id'])
    op.create_index('ix_artists_spotify_id', 'artists', ['spotify_id'], unique=True)
    op.create_index('ix_artists_name', 'artists', ['name'])


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table('artists')
    op.drop_table('tracks')
    op.drop_table('streams')
