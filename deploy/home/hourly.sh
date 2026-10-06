#!/usr/bin/env bash
# Hourly and at boot: pull plays the standby recorded while this server was
# offline, then refresh the related-tracks rollup (it has no incremental hook).
set -uo pipefail
cd "$(dirname "$0")/../.."
compose() { docker compose -f docker-compose.home.yml --env-file .env.home "$@"; }

echo "== $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
compose exec -T backend python -m app.scripts.merge_from_backup --commit \
  || echo "merge_from_backup failed (Supabase unreachable?); will retry next run"
compose exec -T backend python -m app.scripts.backfill_related_tracks --commit
