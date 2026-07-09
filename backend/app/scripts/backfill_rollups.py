"""Rebuild daily_listening_stats / daily_track_stats from existing `streams`.

Deterministic full rebuild: deletes all rollup rows and recomputes them from
scratch by scanning `streams` (see app.services.rollups.rebuild_all_rollups).
Safe to rerun any time — reruns against unchanged data produce identical
rollup rows (idempotent).

Usage (inside the backend container or a local .venv per CLAUDE.md):

    # Preview counts without writing anything
    python -m app.scripts.backfill_rollups

    # Actually rebuild the rollup tables
    python -m app.scripts.backfill_rollups --commit
"""

import argparse
import sys

sys.path.append("/app")

from app.database import SessionLocal
from app.services.rollups import rebuild_all_rollups


def run_backfill(commit: bool) -> None:
    db = SessionLocal()
    try:
        stats = rebuild_all_rollups(db)
        print(f"Streams processed:        {stats.streams_processed}")
        print(f"daily_listening_stats rows: {stats.daily_rows}")
        print(f"daily_track_stats rows:     {stats.track_rows}")

        if commit:
            db.commit()
            print("Committed.")
        else:
            db.rollback()
            print("Dry run — no changes committed. Use --commit to persist.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Rebuild daily rollup tables from existing streams"
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually persist the rebuilt rollups (default is dry-run preview)",
    )
    args = parser.parse_args()

    run_backfill(commit=args.commit)
