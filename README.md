# qarunner

> QA test runner — execute external pytest code, collect results, generate Allure reports.

## Quick start

```bash
# Install dependencies
uv sync --all-extras

# Run unit tests (100% coverage gate)
uv run pytest

# Run e2e smoke tests (requires real pytest + allure CLI)
uv run pytest -m e2e

# Lint
uv run ruff check .

# Start server
uv run uvicorn qarunner.api.app:app --reload
```

## API

### Authentication & Users

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/login` | None | Authenticate user and receive JWT token |
| `GET` | `/auth/me` | Bearer | Retrieve logged-in user profile details |
| `POST` | `/users` | Admin | Create a new user account (Admin-only) |
| `GET` | `/users` | Admin | List all registered platform users (Admin-only) |

### Test Orchestration

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/runs` | Bearer | Trigger a test run (returns 202 + id) |
| `GET` | `/runs` | Bearer | List all runs (newest first) |
| `GET` | `/runs/{id}` | Bearer | Get run details + summary |
| `GET` | `/runs/{id}/report` | Bearer/Query | Get Allure HTML report (accepts `Authorization` header or `?token=...`) |

### Example

```bash
# 1. Sign in to obtain access token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"admin\", \"password\": \"$QARUNNER_ADMIN_PASSWORD\"}" | jq -r '.access_token')

# 2. Trigger a run with Authorization header
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"tests_path": "sample_tests"}'

# 3. Poll for results with Authorization header
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/runs/<id>
```

## Configuration

All settings are read from environment variables with `QARUNNER_` prefix.
`QARUNNER_SECRET_KEY` and `QARUNNER_ADMIN_PASSWORD` are **required** — the app
refuses to start if either is unset or left as a known placeholder (SEC-2). See
`.env.example` for a starting point.

| Variable | Default | Description |
|----------|---------|-------------|
| `QARUNNER_TESTS_ROOT` | `./external_tests` | Root directory containing test code |
| `QARUNNER_ARTIFACTS_ROOT` | `./artifacts` | Where run artifacts are stored |
| `QARUNNER_DB_PATH` | `./artifacts/qarunner.db` | SQLite database path |
| `QARUNNER_ALLURE_BIN` | `allure` | Path to allure CLI binary |
| `QARUNNER_EXECUTABLE` | (sys.executable) | Python executable for running tests |
| `QARUNNER_DEFAULT_TIMEOUT_SECONDS` | `1800` | Default test execution timeout |
| `QARUNNER_MAX_CONCURRENCY` | `4` | Maximum concurrent test runs |
| `QARUNNER_SECRET_KEY` | **(required)** | JWT signing secret. No default; known placeholders rejected. Generate via `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `QARUNNER_ADMIN_USER` | `admin` | Initial default administrator username |
| `QARUNNER_ADMIN_PASSWORD` | **(required)** | Initial administrator password. No default; known weak/default values rejected |
| `QARUNNER_CRASH_RECOVERY_ON_STARTUP` | `true` | Fail QUEUED/RUNNING runs left by a previous process on startup. Assumes a single instance owns the DB — set `false` on all but one replica when scaling out, or sibling runs in flight will be wrongly failed |
| `QARUNNER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` | `30` | Grace period on shutdown to let in-flight runs persist their terminal state before the DB closes. Runs still executing after this are cancelled (and recovered as FAILED on the next start) |

## Architecture

Hexagonal (ports & adapters) architecture with 7 ports:

- **Core**: orchestrator, runners, junit parser, allure command builder, path safety
- **Ports**: ProcessRunner, Clock, IdGenerator, RunStore, TaskScheduler, ResultCollector, AllureReporter
- **Adapters**: subprocess, sqlite (WAL), asyncio scheduler, junit collector, allure CLI reporter

## License

MIT
