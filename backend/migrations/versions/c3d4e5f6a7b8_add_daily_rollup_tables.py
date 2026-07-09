"""add daily_listening_stats and daily_track_stats rollup tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-08

Creates and backfills both rollup tables in the migration transaction so the
application never switches its reads to empty tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLLUP_ADVISORY_LOCK_ID = 7_315_902_024


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

    # Prevent a deploy-time Stream write from landing between the backfill
    # snapshot and transaction commit. The advisory lock matches the runtime
    # rollup lock; the table lock also protects against the pre-rollup app
    # version, which does not know about that advisory lock yet.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text(f"SELECT pg_advisory_xact_lock({ROLLUP_ADVISORY_LOCK_ID})"))
        op.execute(sa.text("LOCK TABLE streams IN SHARE MODE"))

    local_date = (
        "(((streams.played_at AT TIME ZONE 'UTC') " "AT TIME ZONE 'America/Toronto')::date)"
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO daily_listening_stats (date, play_count, listening_ms)
            SELECT
                {local_date},
                count(streams.id),
                coalesce(sum(tracks.duration_ms), 0)
            FROM streams
            LEFT JOIN tracks ON tracks.spotify_id = streams.track_id
            GROUP BY {local_date}
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            WITH inputs AS (
                SELECT
                    streams.*,
                    {local_date} AS local_date,
                    CASE
                        WHEN streams.track_id IS NULL OR streams.track_id = ''
                            THEN 'name:' || streams.track_name
                        ELSE streams.track_id
                    END AS computed_track_key,
                    CASE
                        WHEN streams.artist_id IS NULL OR streams.artist_id = ''
                            THEN 'name:' || streams.artist_name
                        ELSE streams.artist_id
                    END AS computed_artist_key,
                    coalesce(nullif(streams.album_name, ''), '') AS computed_album_key,
                    coalesce(tracks.duration_ms, 0) AS track_duration_ms
                FROM streams
                LEFT JOIN tracks ON tracks.spotify_id = streams.track_id
            )
            INSERT INTO daily_track_stats (
                date,
                track_key,
                artist_key,
                album_key,
                track_id,
                artist_id,
                track_name,
                artist_name,
                album_name,
                play_count,
                listening_ms
            )
            SELECT
                local_date,
                computed_track_key,
                computed_artist_key,
                computed_album_key,
                max(nullif(track_id, '')),
                max(nullif(artist_id, '')),
                (array_agg(track_name ORDER BY id DESC))[1],
                (array_agg(artist_name ORDER BY id DESC))[1],
                (array_agg(album_name ORDER BY id DESC))[1],
                count(id),
                sum(track_duration_ms)
            FROM inputs
            GROUP BY
                local_date,
                computed_track_key,
                computed_artist_key,
                computed_album_key
            """
        )
    )


def downgrade() -> None:
    """Drop the rollup tables."""
    op.drop_table("daily_track_stats")
    op.drop_table("daily_listening_stats")
