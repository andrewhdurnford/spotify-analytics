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

## Troubleshooting External Postgres

- `DATABASE_URL` must include `sslmode=require`