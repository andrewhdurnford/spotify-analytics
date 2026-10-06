#!/usr/bin/env bash
# One-time setup: copy Supabase's schema and data into this app's (empty)
# database in the shared Postgres instance.
set -euo pipefail
cd "$(dirname "$0")/../.."
compose() { docker compose -f docker-compose.home.yml --env-file .env.home "$@"; }
tools() { compose run --rm -T pgtools sh -c "$1"; }

existing=$(tools 'psql "$DATABASE_URL" -Atc "select count(*) from pg_tables where schemaname = '\''public'\''"')
if [ "$existing" != "0" ]; then
  echo "Home database already has $existing tables; refusing to restore over it." >&2
  exit 1
fi

echo "Copying Supabase -> home (this takes a minute)..."
# --table rather than --schema: selecting the schema also emits CREATE SCHEMA
# public, which always exists here. Supabase-only roles (anon, authenticated)
# don't exist here either, so an RLS policy may error; the data still restores.
tools 'pg_dump "$BACKUP_DATABASE_URL" --table='\''public.*'\'' --no-owner --no-privileges -Fc \
         | pg_restore -d "$DATABASE_URL" --no-owner --no-privileges' \
  || echo "pg_restore reported errors (see above); check the counts below match."

home=$(tools 'psql "$DATABASE_URL" -Atc "select count(*) from streams"')
supabase=$(tools 'psql "$BACKUP_DATABASE_URL" -Atc "select count(*) from streams"')
echo "streams: home=$home supabase=$supabase"
