#!/usr/bin/env bash
# Nightly: replace Supabase's data with an exact copy of the home database, so
# the standby serves current data whenever this server goes down.
#
# Refuses to run if home has fewer streams than Supabase -- that means the
# home database is empty or damaged, and pushing would wipe the backup.
# FORCE=1 overrides.
set -euo pipefail
cd "$(dirname "$0")/../.."
compose() { docker compose -f docker-compose.home.yml --env-file .env.home "$@"; }
# Run psql against either database from the pgtools container; extra args
# are passed through to psql.
home_sql() { compose run --rm -T pgtools sh -c 'psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -At "$@"' -- "$@"; }
supabase_sql() { compose run --rm -T pgtools sh -c 'psql "$BACKUP_DATABASE_URL" -v ON_ERROR_STOP=1 -At "$@"' -- "$@"; }

echo "== $(date -u '+%Y-%m-%d %H:%M:%S') UTC"

# Pull anything only the standby has first, so the overwrite can't drop it.
compose exec -T backend python -m app.scripts.merge_from_backup --commit

home_count=$(home_sql -c "select count(*) from streams")
supabase_count=$(supabase_sql -c "select count(*) from streams")
echo "streams: home=$home_count supabase=$supabase_count"
if [ "$home_count" -lt "$supabase_count" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "Home has fewer streams than Supabase; not pushing (FORCE=1 to override)." >&2
  exit 1
fi

tables=$(home_sql -c "select string_agg(format('public.%I', tablename), ', ')
  from pg_tables where schemaname = 'public' and tablename <> 'alembic_version'")

dump=$(mktemp)
trap 'rm -f "$dump"' EXIT
{
  echo "SET statement_timeout = 0;"
  echo "TRUNCATE $tables RESTART IDENTITY CASCADE;"
  compose run --rm -T pgtools sh -c 'pg_dump "$DATABASE_URL" --data-only --schema=public \
    --exclude-table=public.alembic_version --no-owner --no-privileges'
} > "$dump"

# One transaction: a failed upload leaves Supabase untouched. TRUNCATE locks
# the tables until the commit, so the standby's requests stall (or time out)
# for the minute or so this takes -- which is why it runs at night.
supabase_sql --single-transaction -q < "$dump" > /dev/null
echo "Pushed $(du -h "$dump" | cut -f1) to Supabase."
