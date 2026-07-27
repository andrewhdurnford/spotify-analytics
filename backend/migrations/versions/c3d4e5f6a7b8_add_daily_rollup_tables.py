"""add daily_listening_stats and daily_track_stats rollup tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-08

Creates and backfills both rollup tables in the migration transaction so the
application never switches its reads to empty tables.

DEPLOY ORDER — a post-migration backfill is required:

The backfill below covers every `streams` row that exists when the migration
commits. The rollups are then kept current by the `before_flush` hook in
`app.models`, which only the *new* application code has. Any play the
still-running old code writes between `alembic upgrade head` and the new code
going live is therefore missing from the rollups. After the new code is
serving, run:

    python -m app.scripts.backfill_rollups --commit

It is idempotent, so running it unconditionally on every deploy of this
revision is the safe default.
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
    # No standalone index on `date`: the unique constraint below is backed by
    # its own index, which already serves equality and range lookups.
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
    op.create_index("ix_daily_track_stats_track_key", "daily_track_stats", ["track_key"])
    op.create_index("ix_daily_track_stats_artist_key", "daily_track_stats", ["artist_key"])
    op.create_index("ix_daily_track_stats_track_id", "daily_track_stats", ["track_id"])
    op.create_index("ix_daily_track_stats_artist_id", "daily_track_stats", ["artist_id"])
    # Leading columns (date) and (date, track_key) are covered by this unique
    # constraint's index, so neither gets a separate index of its own.
    op.create_unique_constraint(
        "uix_daily_track_stats_key",
        "daily_track_stats",
        ["date", "track_key", "artist_key", "album_key"],
    )
    op.create_index(
        "ix_daily_track_stats_date_artist_key", "daily_track_stats", ["date", "artist_key"]
    )
    op.create_index(
        "ix_daily_track_stats_date_album",
        "daily_track_stats",
        ["date", "album_key", "artist_name"],
    )

    # The backfill below is PostgreSQL-only SQL (AT TIME ZONE, array_agg, ||,
    # ::date). On any other dialect, create the tables empty and leave the
    # data to the script, which computes the same rollups in Python.
    if op.get_bind().dialect.name != "postgresql":
        return

    # Prevent a deploy-time Stream write from landing between the backfill
    # snapshot and transaction commit. The advisory lock matches the runtime
    # rollup lock; the table lock also protects against the pre-rollup app
    # version, which does not know about that advisory lock yet.
    op.execute(sa.text(f"SELECT pg_advisory_xact_lock({ROLLUP_ADVISORY_LOCK_ID})"))
    op.execute(sa.text("LOCK TABLE streams IN SHARE MODE"))

    # Bucketing here uses the *database server's* tzdata, while the runtime
    # path (app.services.rollups.local_date_for) uses the app image's Python
    # zoneinfo. They agree unless the two images carry tzdata releases that
    # disagree about America/Toronto, which would bucket plays near a DST
    # boundary differently. `backfill_rollups --commit` recomputes everything
    # through the Python path and is the tiebreaker if that ever happens.
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
            -- Display-column resolution must match the Python path
            -- (app.services.rollups) exactly, or a later
            -- `rebuild_all_rollups` would rewrite rows this backfill wrote:
            --   * ids/album_name: the group's single non-empty value, with
            --     '' normalized to NULL (rollups.optional_text). The grouping
            --     keys already force one distinct value per group, but a
            --     group can mix NULL and '', hence nullif().
            --   * names: last-seen-wins, i.e. highest streams.id.
            SELECT
                local_date,
                computed_track_key,
                computed_artist_key,
                computed_album_key,
                max(nullif(track_id, '')),
                max(nullif(artist_id, '')),
                (array_agg(track_name ORDER BY id DESC))[1],
                (array_agg(artist_name ORDER BY id DESC))[1],
                max(nullif(album_name, '')),
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
