# Spotify Analytics

A self-hosted dashboard for your Spotify listening history. Import historical
export data and/or sync live playback, then browse stats by track, artist,
album, and time range.

## Stack

- Frontend: Next.js (App Router) + TypeScript
- Backend: FastAPI + SQLAlchemy
- Database: Postgres
- Local dev: Docker Compose

## Repository Layout

- `frontend/` - Next.js app (port `3000`)
- `backend/` - FastAPI API (port `8000`)
- `data/` - local data/import files (not committed)
- `docker-compose.yml` - full-stack local orchestration

## Quick Start

Requires Docker Desktop and access to an external Postgres database
(`DATABASE_URL` must include `sslmode=require`).

```bash
cp .env.example .env
# set DATABASE_URL in .env

docker compose up --build -d
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.scripts.backfill_rollups --commit
```

For local frontend development, also create `frontend/.env.local`:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
API_URL=http://localhost:8000
```

## Core Commands

```bash
curl http://localhost:8000/health
curl http://localhost:3000

docker compose exec backend ruff check .
docker compose exec backend pytest
docker compose exec backend alembic upgrade head

docker compose exec frontend npm run lint
docker compose exec frontend npm test -- --run
```

See [`CLAUDE.md`](CLAUDE.md) for non-Docker local run instructions and
project conventions, and [`backend/MIGRATIONS.md`](backend/MIGRATIONS.md)
for the migration workflow.

## Notes

- Analytics rollups need a manual backfill after certain migrations or data
  edits -- see `app/scripts/backfill_rollups.py` and
  `app/scripts/backfill_related_tracks.py`.
- Related-tracks rollups rebuild on a schedule via
  `POST /api/cron/rebuild-related-tracks`, separate from playback sync.
- Plays are recorded from Spotify's recently-played history, which only lists
  completed plays. Local dev can run the sync in-process instead of an
  external cron: set `ENABLE_LOCAL_SYNC=true` in `.env` (every 60s).
- Behind a reverse proxy in production, set `FORWARDED_ALLOW_IPS` so per-IP
  rate limiting resolves the real client address; `CRON_SECRET` and
  `ALLOWED_HOSTS` are required when `ENVIRONMENT=production`.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on PRs and pushes to `main`:
backend (`ruff check .`, `pytest`) and frontend (`npm run lint`,
`npm test -- --run`, `npm run build`).
