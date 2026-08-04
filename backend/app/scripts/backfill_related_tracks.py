"""Rebuild track_session_counts / track_cooccurrences from existing `streams`.

Deterministic full rebuild: deletes all related-tracks rollup rows and
recomputes them by scanning `streams` in session order (see
app.services.related_tracks_rollup.rebuild_related_tracks_rollup). Safe to
rerun any time — reruns against unchanged data produce identical rows.

Run this with --commit:

- immediately after deploying migration d4e5f6a7b8c9, which creates the
  tables EMPTY. Until this runs, `/api/tracks/{id}/related` returns an empty
  list (HTTP 200) for every track;
- whenever the hourly `POST /api/cron/rebuild-related-tracks` schedule has
  been broken or paused long enough for the rollup to go stale — these tables
  have no incremental maintenance hook, so a missed schedule is the only way
  they drift.

Usage (inside the backend container or a local .venv per CLAUDE.md):

    # Preview counts without writing anything
    python -m app.scripts.backfill_related_tracks

    # Actually rebuild the rollup tables
    python -m app.scripts.backfill_related_tracks --commit
"""

import argparse
import sys

sys.path.append("/app")

from app.database import SessionLocal
from app.services.related_tracks_rollup import rebuild_related_tracks_rollup


def run_backfill(commit: bool) -> None:
    db = SessionLocal()
    try:
        stats = rebuild_related_tracks_rollup(db)
        if stats.skipped_locked:
            db.rollback()
            print("Another rebuild is already running — nothing to do.")
            return

        print(f"Streams processed:          {stats.streams_processed}")
        print(f"Listening sessions:         {stats.sessions}")
        print(f"track_session_counts rows:  {stats.track_rows}")
        print(f"track_cooccurrences rows:   {stats.pair_rows}")
        if stats.oversized_sessions_skipped:
            print(
                f"Oversized sessions skipped: {stats.oversized_sessions_skipped} "
                "(counted toward session totals, not toward pairs)"
            )

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
        description="Rebuild related-tracks co-occurrence rollups from existing streams"
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually persist the rebuilt rollups (default is dry-run preview)",
    )
    args = parser.parse_args()

    run_backfill(commit=args.commit)
