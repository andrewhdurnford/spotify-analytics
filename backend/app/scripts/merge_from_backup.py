"""Copy plays from the standby database (Supabase) that this database missed.

For the home-server deployment: while the home server is offline, the standby
(Render + Supabase) keeps recording plays. Run this once the home server is
back -- and hourly, so a brief network blip is caught up too -- to pull those
plays in. Matching tolerates the two sides stamping the same play a few
seconds apart (see app.services.backup_merge). Safe to rerun: plays already
present locally are skipped.

Requires BACKUP_DATABASE_URL (the Supabase session-pooler URL).

Usage (inside the backend container):

    # Preview what would be copied
    python -m app.scripts.merge_from_backup

    # Actually copy
    python -m app.scripts.merge_from_backup --commit

    # Look further back than the default 14 days (after a long outage)
    python -m app.scripts.merge_from_backup --lookback-days 60 --commit
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta

sys.path.append("/app")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import SessionLocal, build_connect_args
from app.services.backup_merge import merge_missing_streams


def run_merge(commit: bool, lookback_days: int) -> None:
    if not settings.backup_database_url:
        sys.exit("BACKUP_DATABASE_URL is not set")

    backup_engine = create_engine(
        settings.backup_database_url,
        pool_pre_ping=True,
        connect_args=build_connect_args(
            settings.backup_database_url,
            # Reading a couple of weeks of streams is quick, but leave room
            # for a slow pooler connection.
            60_000,
            settings.db_connect_timeout_seconds,
        ),
    )
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=lookback_days)

    local_db = SessionLocal()
    backup_db = sessionmaker(bind=backup_engine)()
    try:
        stats = merge_missing_streams(local_db, backup_db, since)
        print(f"Standby streams since {since:%Y-%m-%d %H:%M}: {stats.backup_streams}")
        print(f"Already present locally:  {stats.matched}")
        print(f"Missing streams:          {stats.inserted_streams}")
        print(f"Missing tracks:           {stats.inserted_tracks}")
        print(f"Missing artists:          {stats.inserted_artists}")

        if commit:
            local_db.commit()
            print("Committed.")
        else:
            local_db.rollback()
            print("Dry run — no changes committed. Use --commit to persist.")
    except Exception:
        local_db.rollback()
        raise
    finally:
        backup_db.close()
        local_db.close()
        backup_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Copy plays the standby database recorded")
    parser.add_argument("--commit", action="store_true", help="Persist the copied rows")
    parser.add_argument("--lookback-days", type=int, default=14)
    args = parser.parse_args()
    run_merge(commit=args.commit, lookback_days=args.lookback_days)
