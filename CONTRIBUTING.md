# Contributing to qarunner

## Development environment setup

```bash
# Clone and install
git clone <repo-url> && cd qarunner
uv sync --all-extras

# Required secrets (app refuses to start without them)
export QARUNNER_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(64))")
export QARUNNER_ADMIN_PASSWORD='dev-password'

# Backend dev server (hot reload)
uv run uvicorn qarunner.api.app:app --reload

# Frontend dev server (separate terminal)
cd frontend && npm install && npm run dev
```

### Docker hot-reload (recommended)

```bash
docker compose -f docker-compose.dev.yml up
```

Frontend at `http://localhost:5173`, backend at `:8000`. Vite proxies API calls
so the SameSite=Strict auth cookie works on a single origin.

## Code style

### Python
- `uv run ruff check .` — linting (zero errors required)
- `uv run ruff check --fix .` — auto-fix where possible
- Line length: 99 chars

### Frontend (TypeScript/React)
- `cd frontend && npx tsc --noEmit` — type checking (zero errors required)
- Components use `useDashboard()` from `hooks/DashboardContext` for shared state
- New `data-testid` attributes required for all interactive elements

## Quality gates (must pass before commit)

```bash
# Backend
uv run ruff check .
uv run pytest                # 100% coverage gate + --cov-fail-under=100

# Frontend
cd frontend && npx tsc --noEmit && npx vitest run
```

Pre-commit hook available: `pre-commit install`

## Pull request process

1. Create a feature branch from `master`
2. Make changes with tests where applicable
3. Ensure all quality gates pass locally
4. Open a PR — CI runs the same gates in GitHub Actions
5. At least one review required before merge

## Project structure

```
src/qarunner/
  api/          FastAPI routes, deps, schemas, app
  core/         Business logic (orchestrator, runners, auth, cron, etc.)
  adapters/     Real implementations of ports
  ports/        Interfaces (Store, ProcessRunner, Clock, etc.)
  models.py     Domain models
  config.py     Settings from environment
  errors.py     Domain exceptions

frontend/src/
  components/   React components (Header, RunsTable, modals, etc.)
  hooks/        Custom hooks (useApi, useAuth, useRuns, DashboardContext, etc.)
  i18n.ts       Translations (en/zh)
  types.ts      Shared TypeScript types

tests/
  unit/         Python unit tests (adapters, api, core)
  e2e/          End-to-end smoke tests
  fakes/        Test doubles for ports

frontend/tests-e2e/
  *.spec.ts     Playwright E2E tests
```
