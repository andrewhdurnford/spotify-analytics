# AGENTS.md

Practical instructions for coding agents working in this repository.

## Repo Overview

- Monorepo with `frontend/` (Next.js App Router + TypeScript) and `backend/` (FastAPI + SQLAlchemy).
- Preferred full-stack dev run from repo root: `docker compose up`.
- Backend container: port `8000`, Frontend container: port `3000`.
- Backend uses Python 3.11 (`backend/Dockerfile`).
- Frontend uses Node 20 (`frontend/Dockerfile`).

## Commands

### Root (repo top level)

- Start full stack (dev): `docker compose up --build`
- Start in background: `docker compose up -d`
- Stop stack: `docker compose down`
- Tail logs: `docker compose logs -f`
- Backend shell: `docker compose exec backend sh`
- Frontend shell: `docker compose exec frontend sh`

### Backend (`backend/`)

- Install dev deps locally: `pip install -r requirements-dev.txt`
- Run API locally: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
- Lint: `ruff check .`
- Format: `ruff format .`
- Run tests: `pytest`
- Run a single test file: `pytest tests/test_services_top.py`
- Run one test case: `pytest tests/test_services_top.py::TestGetTopSongs::test_all_time`
- Run tests by expression: `pytest -k currently_playing`
- Migrate DB (docker): `docker compose exec backend alembic upgrade head`
- Current migration version: `docker compose exec backend alembic current`
- New migration: `docker compose exec backend alembic revision --autogenerate -m "describe change"`

### Frontend (`frontend/`)

- Install deps: `npm install`
- Dev server: `npm run dev`
- Production build: `npm run build`
- Start built app: `npm run start`
- Lint: `npm run lint`
- Run tests (non-watch): `npm test -- --run`
- Run a single test file: `npm test -- --run app/lib/__tests__/fetcher.test.ts`
- Run a single test by name: `npm test -- --run app/lib/__tests__/fetcher.test.ts -t "returns parsed JSON on success"`
- Default test command (`npm test`) runs Vitest and may enter watch mode.

## Architecture Expectations

### Backend architecture

- Keep route handlers thin in `app/api/routes/`.
- Put query/business logic in `app/services/`.
- Keep response models in `app/schemas.py` and use them in `response_model=`.
- Use `get_db` dependency from `app/database.py` for request-scoped DB sessions.
- Use async functions for external IO (Spotify/httpx); sync functions for DB-only routes.

### Frontend architecture

- Server components fetch initial data in `page.tsx` files.
- Client components consume SWR with `fallbackData` for hydration-safe updates.
- Reuse `app/lib/fetcher.ts` for client-side API calls.
- Preserve URL-driven state (`timeframe`, `date`) in query params.
- Keep shared data contracts in `app/types.ts`.

## Code Style Rules

### General

- Match existing file style; avoid broad reformatting unrelated lines.
- Keep functions focused and side effects explicit.
- Prefer clear names over short/cryptic names.
- Do not commit secrets (`.env`, tokens, credentials).

### Python (FastAPI/backend)

- Ruff is the source of truth (`backend/pyproject.toml`).
- Line length: 100.
- Import order/grouping is enforced (`I` rules from Ruff/isort).
- Use type hints on function signatures and meaningful return types.
- Use SQLAlchemy 2.0 query style (`select(...)`, `db.execute(...)`), not `db.query()`.
- Model classes are singular PascalCase (`Track`, `Artist`, `Stream`).
- Module/function/variable names are snake_case.
- Keep HTTP parsing/validation in routes; heavy query logic in services.
- Use `HTTPException` with structured error payloads.
- Error body shape should be:
  - `{"error": {"code": "SOME_CODE", "message": "Human readable message"}}`
- When mutating DB state in tasks/scripts, rollback on failure and log the exception.
- Prefer explicit transactions and avoid swallowing exceptions silently.

### TypeScript/React (Next.js/frontend)

- TypeScript is strict (`frontend/tsconfig.json`); keep typings explicit.
- Prefer `interface` for object shapes; use `type` for unions.
- Use `import type` for type-only imports.
- Use function declarations for components (not const arrow components).
- Component files use PascalCase names; utility modules use lowercase names.
- Keep path alias usage consistent: `@/*` maps to project root.
- Follow existing quote/semicolon style in the touched file.
- Prefer small helper functions for formatting/parsing logic.
- Handle loading/error/empty states in UI components.

## Imports and Naming

- Backend imports: stdlib, third-party, then local modules, with blank lines between groups.
- Frontend imports: framework/libs first, local modules next, type imports separated when practical.
- Avoid wildcard imports.
- Keep endpoint naming and existing route shape stable:
  - `/api/top/songs`, `/api/top/artists`, `/api/top/albums`
  - `/api/top/available-periods`, `/api/top/currently-playing`
  - `/api/artists/{artist_id}`, `/api/tracks/{track_id}`, `/api/tracks/by-name`

## Error Handling Conventions

- Backend:
  - Validate request inputs early (for example, date parsing helpers in routes).
  - Raise `HTTPException` with structured `detail` payloads.
  - Global unhandled exceptions are converted to `500` with `INTERNAL_ERROR`.
- Frontend:
  - `fetcher` should throw useful `Error` messages from `error.message` or `detail`.
  - Pages/components should render graceful fallback UI on fetch failure.

## Testing Conventions

- Backend tests live in `backend/tests/` and use pytest.
- Backend test DB is in-memory SQLite via fixtures in `backend/tests/conftest.py`.
- Use `TestClient` for route tests and direct session fixture for service tests.
- Async tests use `@pytest.mark.asyncio`.
- Frontend tests use Vitest + Testing Library + jsdom.
- Frontend test files follow `*.test.ts` / `*.test.tsx` patterns.
- Prefer behavior-focused assertions over implementation details.

## Data and Scripts

- Historical Spotify exports live in `data/`.
- Backend helper scripts live in `backend/app/scripts/`.
- Typical script runs (from backend context):
  - `python -m app.scripts.import_data`
  - `python -m app.scripts.import_file /data/<file>.json --commit`
  - `python -m app.scripts.backfill_artists`
  - `python -m app.scripts.generate_playlists`

## Environment Variables

- Backend expects: `DATABASE_URL`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN`, `CRON_SECRET`.
- Frontend expects: `NEXT_PUBLIC_API_URL` (client-side) and may use `API_URL` for server-side fetches.
- Default API fallback used in frontend is `http://localhost:8000`.