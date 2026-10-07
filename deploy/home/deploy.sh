#!/usr/bin/env bash
# Deploy a commit to the home server: build, migrate, swap, health check.
# Run by the self-hosted runner after CI passes on main; safe to run by hand.
#
#   deploy/home/deploy.sh [<sha>]   # default: origin/main
#
# The swap takes a few seconds; meanwhile Caddy returns 502 and the frontend
# fails over to Render. If the new code doesn't come up healthy, the previous
# commit is redeployed. Migrations are not rolled back -- keep them backward
# compatible with the previous release.
set -euo pipefail
cd "$(dirname "$0")/../.."
compose() { docker compose -f docker-compose.home.yml --env-file .env.home "$@"; }
host=$(sed -n 's/^ALLOWED_HOSTS=\([^,]*\).*/\1/p' .env.home)

# /health doesn't touch the database; /api/sync-status does.
health() {
  for _ in $(seq 30); do
    if curl -sf -o /dev/null -H "Host: $host" http://localhost:8080/spotify/health \
      && curl -sf -o /dev/null -H "Host: $host" http://localhost:8080/spotify/api/sync-status; then
      return 0
    fi
    sleep 2
  done
  return 1
}

git fetch -q origin
target=$(git rev-parse "${1:-origin/main}")
previous=$(git rev-parse HEAD)
echo "== $(date -u '+%Y-%m-%d %H:%M:%S') UTC: deploying ${target:0:7} (was ${previous:0:7})"

git checkout -q -B main "$target"
# Build first: the old container keeps serving until `up` swaps it.
compose build -q backend
compose up -d backend

# Migrate in the new container (a `compose run` container would also join
# `proxy` as spotify-api, and Caddy could route requests to it).
if compose exec -T backend alembic upgrade head && health; then
  echo "Deployed ${target:0:7}."
  exit 0
fi

echo "Health check failed; rolling back to ${previous:0:7}." >&2
compose logs --tail 50 backend >&2
git checkout -q -B main "$previous"
compose build -q backend
compose up -d backend
health && echo "Rolled back to ${previous:0:7}." >&2 || echo "Rollback is unhealthy too!" >&2
exit 1
