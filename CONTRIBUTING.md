# Contributing to qarunner

## Development environment setup

> [!IMPORTANT]
> All development, test and deployment commands run **inside Docker containers**.
> Do not run `uv`, `npm` or `pytest` directly on the host — the only exception is
> formatting/linting (`ruff format`, `ruff check`), which needs no runtime deps.

```bash
git clone <repo-url> && cd qarunner

# 1. Secrets. The app refuses to start if these are unset or left as placeholders.
cp .env.example .env
$EDITOR .env     # set QARUNNER_SECRET_KEY and QARUNNER_ADMIN_PASSWORD

# 2. Start the dev stack (backend + frontend, both hot-reloading)
docker compose -f docker-compose.dev.yml up -d

# 3. First run only: apply database migrations.
#    The backend fails closed until the schema matches — see docs/deployment.md
docker compose -f docker-compose.dev.yml run --rm \
  -e QARUNNER_MIGRATION_DATABASE_URL='postgresql://qarunner@postgres:5432/qarunner' \
  -e QARUNNER_MIGRATION_SCHEMA=public \
  backend uv run python -m qarunner.migrations upgrade head
```

Frontend at `http://localhost:5173`, backend at `:8000`. Vite proxies API calls
so the SameSite auth cookie works on a single origin.

Full deployment and operations guide: [docs/deployment.md](docs/deployment.md).

## Code style

### Python
- `uv run ruff check .` — linting (zero errors required)
- `uv run ruff check --fix .` — auto-fix where possible
- Line length: 99 chars

### Frontend (TypeScript/React)
- Type checking must pass with zero errors (run it in the container, see Quality gates)
- Components use `useDashboard()` from `hooks/DashboardContext` for shared state
- New `data-testid` attributes required for all interactive elements

## Quality gates (must pass before commit)

Run these **in the containers**:

```bash
# Backend unit + integration, with the 100% line/branch coverage gate
docker compose -f docker-compose.dev.yml exec backend uv run pytest

# Frontend unit/component tests
docker compose -f docker-compose.dev.yml exec frontend npm run test:unit -- --run

# Frontend type check
docker compose -f docker-compose.dev.yml exec frontend npx tsc --noEmit
```

Formatting and linting may run on the host:

```bash
uv run ruff format
uv run ruff check --fix
```

Playwright E2E runs in a throwaway official Playwright container joined to the
compose network — the exact command (including the `node_modules` volume mount
it requires) is in [docs/deployment.md](docs/deployment.md#运行-e2e-测试).

Install the pre-commit hook once: `pre-commit install`. It runs gitleaks against
staged changes; do not bypass it with `--no-verify`.

## Pull request process

1. Create a feature branch from `master`
2. Make changes with tests where applicable
3. Ensure all quality gates pass locally
4. Open a PR — CI runs the same gates in GitHub Actions
5. At least one review required before merge

## Project structure

```
src/qarunner/
  api/                 FastAPI routes, deps, schemas, app factory
  core/                Business logic (orchestrator, runners, auth, cron, regression, ...)
  adapters/            Real implementations of ports (Docker, Postgres, SQLite, LLM, ...)
  ports/               Interfaces (Store, ProcessRunner, Clock, FailureAnalyzer, ...)
  domain/              Greenfield domain model (see docs/greenfield/ — design, not wired
                       into the runtime path yet)
  application/         Greenfield application layer, same caveat as domain/
  migrations/          Alembic migrations + operator CLI
  pytest_plugin/       Plugin injected into executed pytest suites
  playwright_reporter/ Reporter injected into executed Playwright suites
  models.py            Domain models
  config.py            Settings from environment
  errors.py            Domain exceptions

frontend/src/
  components/          React components (Header, RunsTable, drawers, modals, ...)
  hooks/               Custom hooks (useApi, useAuth, useRuns, DashboardContext, ...)
  i18n.ts              Translations (en/zh)
  types.ts             Shared TypeScript types

tests/
  unit/                Unit tests (adapters, api, core, domain, application)
  integration/         Integration tests (real Postgres, real Docker)
  e2e/                 Backend end-to-end smoke tests
  model/               Domain model property tests
  fakes/               Test doubles for ports — put all test doubles here,
                       not inline in test files
  fixtures/            Shared pytest fixtures

frontend/tests-e2e/
  *.spec.ts            Playwright E2E tests
```
