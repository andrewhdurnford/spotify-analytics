# CLAUDE.md

Lean guidance for working in this repo.

## Shape

- Monorepo: `frontend/` is Next.js App Router + TypeScript; `backend/` is FastAPI + SQLAlchemy.
- Preferred dev stack: `docker compose up` from the repo root.
- Backend runs on `8000`; frontend runs on `3000`.
- Historical Spotify exports live in `data/`.

## Commands

- Full stack: `docker compose up --build`
- Backend tests: `cd backend && pytest`
- Backend lint/format: `cd backend && ruff check . && ruff format .`
- Frontend lint: `cd frontend && npm run lint`
- Frontend tests: `cd frontend && npm test -- --run`
- Frontend build: `cd frontend && npm run build`
- DB migrations: `docker compose exec backend alembic upgrade head`

## Backend Conventions

- Keep routes in `backend/app/api/routes/` thin.
- Put query/business logic in `backend/app/services/`.
- Keep response models in `backend/app/schemas.py`.
- Use `get_db` from `backend/app/database.py` for request-scoped DB sessions.
- Use SQLAlchemy 2.0 style: `select(...)`, `db.execute(...)`; avoid `db.query()`.
- Raise `HTTPException` with structured payloads:
  `{"error": {"code": "CODE", "message": "Human message"}}`.
- Spotify/current playback work belongs in `backend/app/tasks/` or `backend/app/utils/spotify.py`.

## Frontend Conventions

- Server components fetch initial data in `page.tsx`.
- Client components use SWR with `fallbackData`.
- Use `app/lib/fetcher.ts` for client API calls.
- Preserve URL-driven state for `timeframe` and `date`.
- Shared API contracts live in `frontend/app/types.ts`.
- Prefer function declarations for components.
- Use `import type` for type-only imports.

## Tracking Semantics

- Dashboard play stats are based on rows in `streams`.
- Historical imports should ignore skipped/non-completed export rows.
- Cron/local sync should only count current playback after it is near completion.
- Existing DB rows do not store `skipped` or `reason_end`, so historical correction requires reimporting/export reconciliation.

## Safety

- Do not commit `.env`, Spotify tokens, database URLs, or credentials.
- Avoid broad reformatting.
- Do not change endpoint paths without checking frontend consumers.
- Before touching sync/import code, run backend tests and inspect duplicate handling.
