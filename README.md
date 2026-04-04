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

## Quick Start

1. Create a root `.env` file with backend variables (`DATABASE_URL`, Spotify secrets, `CRON_SECRET`).
2. Start everything:

```bash
docker compose up --build
```

3. Open:
   - Frontend: http://localhost:3000
   - Backend: http://localhost:8000
