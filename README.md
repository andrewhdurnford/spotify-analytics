# Spotify Analytics

Full-stack Spotify analytics app in a single monorepo.

## Stack

- Frontend: Next.js (App Router) + TypeScript
- Backend: FastAPI + SQLAlchemy
- Local dev: Docker Compose

## Repository Layout

- `frontend/` - Next.js app (port `3000`)
- `backend/` - FastAPI API (port `8000`)
- `data/` - local data/import files (not committed)
- `docker-compose.yml` - full-stack local orchestration

## Prerequisites

- Docker Desktop with Docker Compose plugin
- Access to an external Postgres database

## Quick Start (Canonical)

1. Create your local environment file from the template:

```bash
cp .env.example .env
```

2. Update `.env` and set `DATABASE_URL` to your external Postgres connection string.

3. Start backend + frontend:

```bash
docker compose up --build -d
```

4. Verify DB connectivity from the backend container:

```bash
docker compose exec backend sh -c 'pg_isready -d "$DATABASE_URL"'
```

5. Apply migrations:

```bash
docker compose exec backend alembic upgrade head
```

6. Rebuild the analytics rollups once the new backend is serving:

```bash
docker compose exec backend python -m app.scripts.backfill_rollups --commit
```

## Analytics Rollups

Dashboard totals, rankings, and timelines read from `daily_listening_stats`
and `daily_track_stats` rather than scanning `streams`. They are maintained
incrementally in the same transaction as each new `Stream` insert, so normal
operation needs nothing extra.

Rerun the backfill (idempotent, safe any time) when:

- **Deploying migration `c3d4e5f6a7b8`.** Its in-migration backfill only sees
  the streams that exist at migration time; the still-running old backend
  keeps writing plays without the rollup hook until the new code is live.
  Those plays are only picked up by a post-deploy backfill.
- **`streams` rows were deleted or edited in place** (duplicate cleanup,
  export reconciliation). Rollup maintenance is insert-only; the backend logs
  a warning naming this script when it sees such a flush.
- **A metadata enrichment run reported a failed rollup recompute.**

```bash
# Preview counts, write nothing
docker compose exec backend python -m app.scripts.backfill_rollups
# Persist
docker compose exec backend python -m app.scripts.backfill_rollups --commit
```

### Related-Tracks Rollup

`GET /api/tracks/{id}/related` reads two separate rollup tables,
`track_session_counts` and `track_cooccurrences`, which record how often
tracks are played in the same listening session. Unlike the daily rollups
these have **no incremental maintenance hook** — min-support filtering is
incompatible with incremental counting (see
`backend/app/services/related_tracks_rollup.py`), so they are rebuilt
wholesale on a schedule.

That schedule is a **second cron-job.org job**, separate from the once-a-minute
playback sync. Hourly is plenty; the endpoint self-throttles anything arriving
under 55 minutes apart and answers `202` immediately, doing the rebuild in the
background:

```bash
curl -X POST https://<backend-host>/api/cron/rebuild-related-tracks -H "Authorization: Bearer $CRON_SECRET"
```

Run the backfill manually (idempotent, safe any time) when:

- **Deploying migration `d4e5f6a7b8c9`.** It creates both tables empty. Until
  the backfill runs, every track's related list is empty — an HTTP 200 with
  `{"related": []}`, so nothing errors to tell you it was skipped.
- **The hourly schedule has been paused or broken.** Check
  `related_tracks_rollup_last_rebuilt_at` on `GET /api/sync-status`; `null`
  means the rollup has never been built.

```bash
# Preview counts, write nothing
docker compose exec backend python -m app.scripts.backfill_related_tracks
# Persist
docker compose exec backend python -m app.scripts.backfill_related_tracks --commit
```

## Verification Commands

Run these to confirm your local setup is healthy.

```bash
curl http://localhost:8000/health
curl http://localhost:3000
docker compose exec backend ruff check .
docker compose exec backend pytest
docker compose exec frontend npm run lint
docker compose exec frontend npm test -- --run
docker compose exec backend alembic current
```

## Optional Local Playback Sync

The hosted app can still be pinged by an external cron every minute, but local
development can also run an in-process playback sync loop. Set this in `.env`:

```bash
ENABLE_LOCAL_SYNC=true
LOCAL_SYNC_INTERVAL_SECONDS=30
SYNC_MAX_WAIT_SECONDS=25
```

The local loop uses the same completion-aware sync as the cron endpoint.

## Production Backend Deployment

The API rate-limits per client IP, which requires the ASGI server to resolve the
real client address. Behind a reverse proxy or load balancer, set:

```bash
FORWARDED_ALLOW_IPS=<proxy IPs, or * if only the platform proxy can reach the container>
ALLOWED_HOSTS=<backend hostname(s), comma-separated>
CRON_SECRET=<non-empty random secret>
ENVIRONMENT=production
```

Without `FORWARDED_ALLOW_IPS`, uvicorn ignores `X-Forwarded-For` and every
caller shares one client key, so per-IP limits become a single shared bucket any
one client can exhaust for everyone. `CRON_SECRET` and a non-empty
`ALLOWED_HOSTS` are enforced at startup when `ENVIRONMENT=production`.

## Backend Response Cache

Dashboard, detail, and history reads use a bounded in-process TTL cache to
reduce repeated database egress. The local playback sync loop clears the cache
after recording a completed play in that backend process. Historical import
scripts and multi-worker deployments do not share in-process invalidation, so
those writes become visible after the endpoint TTL expires.

Related-tracks reads are deliberately uncached: they are already two indexed
lookups against the rollup above, so a cache entry would only occupy a slot in
the shared pool that a genuinely expensive dashboard aggregate needs.

## Frontend Environment

For local frontend development, create `frontend/.env.local`:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
API_URL=http://localhost:8000
```

Use the deployed backend URL for both values in hosted frontend environments.

## CI Checks

- GitHub Actions workflow: `.github/workflows/ci.yml`
- Triggered on pull requests and pushes to `main`
- Runs backend checks (`ruff check .`, `pytest`) and frontend checks (`npm run lint`, `npm test -- --run`, `npm run build`)

## Troubleshooting External Postgres

- `DATABASE_URL` must include `sslmode=require`
