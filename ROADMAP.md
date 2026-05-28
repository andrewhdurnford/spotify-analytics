# Spotify Analytics Roadmap

This roadmap captures the current state of the project and the next useful work.
The guiding principle remains: keep delivery stable first, then evolve ingestion
and product insights with tests around the moving parts.

## Current State

- Last validated locally: 2026-05-28
- Repo shape: monorepo with `backend/` FastAPI + SQLAlchemy and `frontend/`
  Next.js App Router + TypeScript.
- Preferred local stack: `docker compose up --build` from the repo root.
- Backend port: `8000`; frontend port: `3000`.
- Historical Spotify exports live under `data/` and are not committed.
- Delivery stability work is present on `origin/main`; current ingest work has
  started by extracting historical import parsing and duplicate classification
  into a tested service module.

## Verified Checks

Validated on 2026-05-28:

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest
cd frontend && npm run lint
cd frontend && npm test -- --run
cd frontend && npm run build
```

Results:

- Backend Ruff: passing.
- Backend pytest: 83 tests passing.
- Frontend lint: passing with no warnings after cleanup.
- Frontend Vitest: 29 tests passing.
- Frontend build: passing.

## Implemented Product Surface

### Dashboard and Top Data

- Home dashboard fetches initial data in server components.
- Client components use SWR with `fallbackData`.
- URL-driven `timeframe` and `date` state is preserved.
- Top endpoints are implemented for songs, artists, albums, stats, available
  periods, recently played, and currently playing.
- Current playback is polled by the frontend and links into song detail when
  possible.

### Detail Pages and Endpoints

- Artist detail page exists at `frontend/app/artists/[id]`.
- Song detail page exists at `frontend/app/songs/[id]`.
- Backend detail endpoints are implemented:
  - `GET /api/artists/{id}`
  - `GET /api/tracks/{id}`
  - `GET /api/tracks/by-name`
- Detail responses include metadata, total play counts, timeline data, and
  daily/monthly granularity.
- Track detail supports a name + artist fallback for streams without Spotify IDs.
- Integration tests cover the three detail endpoints, not-found behavior,
  invalid dates, and monthly daily-timeline behavior.
- Focused service tests cover detail query behavior for timeframe filtering,
  missing rows, metadata without streams, and track name fallback.

### Ingestion and Sync

- Historical import code filters skipped or non-completed export rows.
- Cron/local sync records current playback only after it is near completion.
- Duplicate handling is covered by sync tests.
- Existing DB rows still do not store `skipped` or `reason_end`; historical
  correction requires reimporting or export reconciliation.
- Import/sync code exists, but the internals are still script-heavy and are the
  main area to refactor before adding more ingestion features.

### CI and Tooling

- GitHub Actions workflow exists at `.github/workflows/ci.yml`.
- CI triggers on pull requests and pushes to `main`.
- Backend CI installs `backend/requirements-dev.txt`, then runs:
  - `ruff check .`
  - `pytest`
- Frontend CI installs with `npm ci`, then runs:
  - `npm run lint`
  - `npm test -- --run`
  - `npm run build`
- Backend tests set a CI-safe `DATABASE_URL` and use in-memory SQLite fixtures.
- Top and detail routes share date parsing and timeframe typing through
  `backend/app/api/params.py`.
- Ruff ignores `B008` because FastAPI intentionally uses `Depends(...)` and
  `Query(...)` in default arguments.
- `pytest-asyncio` fixture loop scope is explicitly set to `function`.

## Status By Priority

### P0 - Delivery Stability

| ID | Title | Status | Notes |
| --- | --- | --- | --- |
| P0-1 | Deterministic local setup | Done | README documents canonical Docker Compose setup and verification commands. |
| P0-2 | CI pipeline for lint + tests | Pushed, remote status unverified | Workflow exists on `origin/main`; local parity checks pass. Confirm latest GitHub Actions run in the web UI. |
| P0-3 | Documentation alignment | Done for current scope | README, frontend README, backend migration docs, and roadmap match the current stack. |
| P0-4 | Basic detail endpoint smoke tests | Done | Added integration coverage for artist, track ID, and track by-name detail endpoints. |

### P1 - Maintainability and Confidence

| ID | Title | Status | Next action |
| --- | --- | --- | --- |
| P1-1 | Consolidate date/timeframe parsing | Done | Top and detail routes now use shared API parameter helpers. |
| P1-2 | Detail service test coverage | Done for current scope | Added focused service tests for `app/services/detail.py` query edge cases. |
| P1-3 | Refactor ingest/sync script internals | Started | Historical import parsing and duplicate classification now live in `app/services/ingest.py`; next split Spotify enrichment and sync persistence. |
| P1-4 | Structured sync/import error handling | Not started | Add clearer error categories and log fields for API, rate limit, and DB failures. |
| P1-5 | Backend format strategy | Not started | Decide whether CI should include `ruff format --check`; current code passes lint but not full format check. |

### P2 - Product Expansion

| ID | Title | Status | Next action |
| --- | --- | --- | --- |
| P2-1 | Timeframe strategy decision | Not started | Decide whether UI should expose day/week/quarter or backend should narrow to all/year/month. |
| P2-2 | Advanced history filtering | Not started | Add search, filtering, and sorting controls to history. |
| P2-3 | New insights surfaces | Not started | Consider repeats, streaks, sessions, trend comparisons, and listening-time summaries. |
| P2-4 | Query performance and caching pass | Not started | Profile aggregate/detail queries on realistic data and add caching or snapshots where needed. |

## Recommended Next Sequence

1. Commit and push the current roadmap/docs, detail service test, and route
   parameter helper changes.
2. Confirm the latest remote GitHub Actions run is green on `main` or a PR.
3. Decide whether to add `ruff format --check .` to CI or leave formatting as a
   manual maintenance command.
4. Continue refactoring ingestion/import code behind tests before changing data
   semantics; next extract Spotify enrichment and sync persistence.
5. Add structured sync/import error categories and log fields.
6. Decide the timeframe UX/API contract.
7. Add richer history and insight surfaces once the ingestion path is easier to
   change safely.

## Known Risks And Open Questions

- Remote CI has not been observed green from this local environment.
- `ruff format --check .` is not part of CI and currently would reformat several
  backend files. This is a style decision, not a failing delivery gate.
- Import and sync scripts still mix parsing, API access, duplicate detection,
  and persistence; changes there should stay test-first.
- Historical skipped-row correction cannot be inferred from existing DB rows
  alone.
- The backend supports more timeframe options than the current frontend exposes.
