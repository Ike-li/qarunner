# qarunner

> **qarunner** — for the product direction, see [docs/DIRECTION.md](docs/DIRECTION.md) (the single source of truth).

## Quick start (local development)

The app refuses to start without a JWT secret and an admin password (SEC-2), so
export them first — even for local dev:

```bash
# Install dependencies
uv sync --all-extras

# Required: a strong JWT secret and a non-default admin password
export QARUNNER_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(64))")
export QARUNNER_ADMIN_PASSWORD='choose-a-strong-dev-password'

# Run unit tests (100% coverage gate)
uv run pytest

# Run e2e smoke tests (requires real pytest + allure CLI)
uv run pytest -m e2e

# Lint
uv run ruff check .

# Frontend: unit tests + type check
cd frontend && npm ci && npx vitest run && npx tsc --noEmit

# Frontend: E2E tests (requires Playwright browsers)
cd frontend && npx playwright install chromium && npx playwright test

# Start server (serves the API; build frontend/ separately for the SPA)
uv run uvicorn qarunner.api.app:app --reload
```

For local HTTP dev the auth cookie stays non-`Secure` (`QARUNNER_COOKIE_SECURE`
defaults to `false`). **Set it to `true` in production** — see below.

## Local development with Docker (hot reload)

`docker-compose.dev.yml` runs the API and the React app as **two separate
containers with live reload**, so edits to `src/` or `frontend/src/` take effect
without a rebuild. Use this for day-to-day development instead of the baked
production image.

```bash
# Secrets are optional here — the dev compose ships safe placeholders
# (admin password "admin123"); override via a local .env if you like.
docker compose -f docker-compose.dev.yml up
```

- **Open the app at `http://localhost:5173`** — the Vite dev server, *not* port
  8000. The backend API stays on `:8000`, but Vite proxies `/auth`, `/runs`,
  `/tests`, `/profiles`, `/schedules`, and `/users` to it, so the browser sees a
  single origin and the `SameSite=Strict` auth cookie (SEC-6) still rides along.
  Hitting `:8000` directly in dev returns `404` at `/` — that container serves no SPA.
- **Hot reload**: the frontend uses Vite HMR (filesystem polling via
  `VITE_USE_POLLING`, to survive the macOS Docker bind mount); the backend runs
  `uvicorn --reload` with `WATCHFILES_FORCE_POLLING` as a fallback for when
  inotify events don't cross the mount. `frontend/node_modules` is an anonymous
  volume, so the container's Linux binaries never collide with the host's.
- **Linking a local suite**: "Add suite → local path" symlinks a host project
  into `external_tests`. For the symlink to resolve *inside* the container, the
  host projects root is bind-mounted read-only at the same path
  (`QARUNNER_PROJECTS_ROOT`, default `~/code`); narrow it in `.env` for a tighter
  mount. Git suites instead clone straight into `external_tests` and need no such
  mount.
- **Don't use a bare `docker compose up` for development** — with no `-f` flag it
  starts the *production* `docker-compose.yml` (baked SPA on `:8000`, zero hot
  reload).

## Production deployment (Docker Compose)

The bundled `Dockerfile.server` builds a single image that serves **both the
API and the built React SPA from the same origin** (port 8000) — a non-root
runtime (DEP-1), dependencies pinned via `uv.lock` (DEP-2), and a `/health`
readiness probe (DEP-4). `docker-compose.yml` wires the bind mounts, resource
limits, healthcheck, and `restart: unless-stopped`.

```bash
# 1. Create .env with strong, unique secrets (NEVER commit it)
cp .env.example .env
#    QARUNNER_SECRET_KEY     — python -c "import secrets; print(secrets.token_urlsafe(64))"
#    QARUNNER_ADMIN_PASSWORD — a strong, unique password
#    QARUNNER_DOCKER_GID     — host docker group gid, REQUIRED by this compose
#                              (getent group docker | cut -d: -f3); compose won't start unset
#    QARUNNER_COOKIE_SECURE  — add `QARUNNER_COOKIE_SECURE=true` (see same-origin note)

# 2. Build and start
docker compose up -d --build

# 3. Verify readiness (200 + {"status":"ok"} only when the DB round-trips)
curl -f http://localhost:8000/health
```

### Same-origin requirement (SEC-6)

Authentication uses an **`HttpOnly; SameSite=Strict; Secure` cookie**, planted at
`/auth/login`. The browser only attaches it to **same-origin** requests, and the
report `<iframe>`, the SSE log stream, and every `fetch` rely on it riding along
automatically (no token is ever placed in a URL). Therefore:

- **The frontend must be served from the same origin as the API.** The server
  image already does this (it mounts the built SPA at `/`), so publish a single
  origin — e.g. `https://qa.example.com` fronting container port 8000. Splitting
  the SPA and API onto different origins breaks auth: `SameSite=Strict` drops the
  cookie on cross-origin SSE/iframe/fetch. Don't.
- **Terminate TLS in front and set `QARUNNER_COOKIE_SECURE=true`.** Put a
  reverse proxy (nginx / Caddy / cloud LB) ahead of port 8000. Without `Secure`,
  the HttpOnly auth cookie could ride a plaintext hop.

### Operational notes

- **Health / readiness**: `GET /health` returns 200 only when the DB is
  reachable, else 503. The compose healthcheck already polls it; point your
  orchestrator's readiness probe at the same path.
- **Persistence**: the SQLite DB and run artifacts both live under the
  `./artifacts` bind-mounted host directory. Back it up to retain run history.
- **Test suites**: the single-instance server clones / pulls / prepares git
  suites into the writable `external_tests` root. It is bind-mounted
  source==target so the DooD executor's jail-fallback path stays host-resolvable;
  on a server point `${PWD}/external_tests` at a persistent directory and back it
  up too. No named volume is used — the executor mounts the per-run jail under
  artifacts, never this root.
- **Executor**: runs default to the hardened **Docker executor** (SEC-3:
  non-root, no network, `cap_drop=ALL`, read-only rootfs, pid/mem/cpu limits),
  which runs tests in a throwaway `qarunner-executor:latest` container. The
  bundled prod compose mounts the Docker daemon socket and joins the host docker
  group (`QARUNNER_DOCKER_GID`) so this path works — note that socket access is
  effectively host-root, so the real isolation is that untrusted tests run in the
  executor container, **not** the server process. The in-process `subprocess`
  executor runs test code **in the server process**, so it is opt-in: admins
  always, non-admins only when `QARUNNER_ALLOW_SUBPROCESS_FOR_NON_ADMINS=true`
  (default false; the dev compose enables it as a single-host convenience). In dev
  the executor images are built on demand; in production set
  `QARUNNER_EXECUTOR_AUTOBUILD=false` and pre-build them so a missing image
  fails fast instead of being silently (re)built:
  `docker build -f Dockerfile -t qarunner-executor:latest .` and
  `docker build -f Dockerfile.playwright -t qarunner-playwright-executor:latest .`.
  `pytest` runs use the python executor image; `playwright` runs use the
  Playwright executor image and may run with `executor_mode='docker'`.
- **Playwright external paths**: docker executor containers only get the per-run
  workspace and artifact directory by default. If a Playwright profile needs an
  explicit env directory such as `APP_REPO_PATH=/Users/me/code/app`, set
  `QARUNNER_EXECUTOR_EXTRA_READONLY_ROOTS` to an allowlisted parent mounted at
  the same path by the server (for local dev, `docker-compose.dev.yml` sets it
  from `QARUNNER_PROJECTS_ROOT`). Matching env directory values are mounted
  read-only into the executor.
- **Single instance only**: crash recovery and the in-process scheduler assume
  one instance owns the DB (CONC-2). Do **not** scale `app` beyond one
  replica without setting `QARUNNER_CRASH_RECOVERY_ON_STARTUP=false` on all but
  one instance and moving scheduling out — otherwise a starting replica fails
  runs still executing in its siblings, and each cron point fires N times.

## Configuration

All settings are read from environment variables with the `QARUNNER_` prefix.
`QARUNNER_SECRET_KEY` and `QARUNNER_ADMIN_PASSWORD` are **required** — the app
refuses to start if either is unset or left as a known placeholder (SEC-2). See
`.env.example` for a starting point.

| Variable | Default | Description |
|----------|---------|-------------|
| `QARUNNER_TESTS_ROOT` | `./external_tests/` | Root directory containing test code |
| `QARUNNER_ARTIFACTS_ROOT` | `./artifacts` | Where run artifacts are stored |
| `QARUNNER_DB_PATH` | `./artifacts/qarunner.db` | SQLite database path |
| `QARUNNER_ALLURE_BIN` | `allure` | Path to allure CLI binary |
| `QARUNNER_EXECUTABLE` | (sys.executable) | Python executable for running tests |
| `QARUNNER_DEFAULT_TIMEOUT_SECONDS` | `1800` | Default test execution timeout |
| `QARUNNER_MAX_CONCURRENCY` | `4` | Maximum concurrent test runs |
| `QARUNNER_MAX_INFLIGHT_RUNS_PER_USER` | `20` | Per-user cap on simultaneously queued/running runs; over it `POST /runs` returns 429 (admins exempt). `0` disables the limit |
| `QARUNNER_ALLOW_SUBPROCESS_FOR_NON_ADMINS` | `false` | Let non-admins use the in-process `subprocess` executor (which runs test code in the server process). Keep `false` in production; the dev compose sets it `true` |
| `QARUNNER_EXECUTOR_AUTOBUILD` | `true` | Build the `qarunner-executor:latest` image at runtime if missing. **Set `false` in production** and pre-build the image, so a missing image fails fast instead of being silently (re)built and drifting from the Dockerfile |
| `QARUNNER_PLAYWRIGHT_EXECUTOR_IMAGE` | `qarunner-playwright-executor:latest` | Docker image used for `runner=playwright` docker executions |
| `QARUNNER_EXECUTOR_EXTRA_READONLY_ROOTS` | empty | `os.pathsep`-separated allowlist of roots whose explicit env directory values may be mounted read-only into executor containers |
| `QARUNNER_SECRET_KEY` | **(required)** | JWT signing secret. No default; known placeholders rejected. Generate via `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `QARUNNER_ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | JWT / auth-cookie lifetime in minutes |
| `QARUNNER_COOKIE_SECURE` | `false` | Add the `Secure` flag to the HttpOnly auth cookie. **Set `true` in production** (HTTPS) so the cookie never rides a plaintext connection (SEC-6) |
| `QARUNNER_ADMIN_USER` | `admin` | Initial default administrator username |
| `QARUNNER_ADMIN_PASSWORD` | **(required)** | Initial administrator password. No default; known weak/default values rejected |
| `QARUNNER_STATIC_ROOT` | (project `frontend/dist`) | Directory of the built SPA to serve at `/`. The server image sets this; override for a custom layout |
| `QARUNNER_CRASH_RECOVERY_ON_STARTUP` | `true` | Fail QUEUED/RUNNING runs left by a previous process on startup. Assumes a single instance owns the DB — set `false` on all but one replica when scaling out, or sibling runs in flight will be wrongly failed |
| `QARUNNER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` | `30` | Grace period on shutdown to let in-flight runs persist their terminal state before the DB closes. Runs still executing after this are cancelled (and recovered as FAILED on the next start) |

## API

The full endpoint reference for external / integration testing lives in
[docs/API_REFERENCE.md](docs/API_REFERENCE.md) — every route, request/response
field, status code, and auth rule, cross-checked against the source. The
running server also serves FastAPI's built-in docs:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI schema (authoritative): `http://localhost:8000/openapi.json`

Quick smoke test — log in as the seeded admin (default username `admin`, using
the password you exported above), then list the available test suites:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\": \"admin\", \"password\": \"$QARUNNER_ADMIN_PASSWORD\"}" \
  | jq -r '.access_token')

curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/tests
```

Triggering runs, polling results, and the SSE log stream are documented in
[docs/API_REFERENCE.md](docs/API_REFERENCE.md).

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full picture.

Quick summary: **Hexagonal (ports & adapters)** architecture — 8 abstract ports,
9 adapters, explicit DI container, zero FastAPI imports in `core/`.

| Layer | Contents |
|-------|----------|
| **Core** | orchestrator, runners (pytest + playwright), junit parser, allure command builder, path safety, auth (JWT + bcrypt), cron validation, login throttle, profile service, schedule service |
| **Ports** | Store, ProcessRunner, Clock, IdGenerator, TaskScheduler, ResultCollector, AllureReporter, SchedulePort |
| **Adapters** | sqlite_store, subprocess_runner, docker_runner, asyncio_scheduler, apscheduler_schedule, junit_collector, allure_cli_reporter, system_clock, uuid_ids |

## License

MIT
