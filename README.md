# qarunner — self-hosted regression test runner for pytest and Playwright

[![CI](https://github.com/Ike-li/qarunner/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/Ike-li/qarunner/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

English | [简体中文](README.zh-CN.md)

**qarunner is a self-hosted service that runs your existing pytest and Playwright
regression suites in throwaway Docker containers, keeps the full evidence of every
run, and compares each run with a comparable earlier run to show new failures,
fixes and flaky tests.** It is built for a single team on a trusted internal
network: a FastAPI backend, a React web console and a REST API, deployed with
Docker Compose.

> [!CAUTION]
> **Do not use the current version to run test code you do not trust.**
>
> The control-plane process mounts the host's `/var/run/docker.sock`, which is
> equivalent to host root: test code that escapes its execution container can take
> over the whole machine.
>
> - ✅ **Fits**: regression suites written by you or your team, with dependencies
>   from sources you control, deployed on a trusted internal network.
> - ❌ **Does not fit**: test code of unknown origin, tests from external
>   contributors' pull requests, shared multi-tenant environments.
>
> The target architecture (control plane separated from a dedicated worker host,
> no Docker socket on the control plane, one disposable container per run) is not
> implemented yet; it is tracked internally as GAP-021 / SOR-GAP-023.

## Why qarunner

Running a test suite once tells you whether it passed. A regression signal needs
more: which cases changed since the last run *that is actually comparable*,
whether a failure is new or has been flipping for weeks, and the logs and report
to act on it. qarunner keeps per-case results for every run and only diffs runs
that share the same suite, runner and arguments — when there is no comparable
baseline it says so instead of showing a misleading diff.

## Features

- **Isolated execution** — pytest and Playwright run in disposable Docker
  containers: non-root, no network, all Linux capabilities dropped, read-only
  root filesystem, memory / CPU / PID limits, a fresh workspace per run.
- **Bring your own tests** — link a local directory or clone a Git repository
  (private repos via encrypted HTTPS tokens), browse files, filter by pytest
  markers or Playwright `@tags`.
- **Profiles, runs and schedules** — save run configurations, trigger on demand,
  re-run, cancel, or schedule with cron expressions and per-schedule IANA time zones.
- **Evidence for every run** — live log streaming (SSE), a JUnit-based summary,
  an Allure HTML report, and Playwright traces, screenshots and videos.
- **Cross-run regression view** — a baseline diff in five buckets (new failures,
  fixed, still failing, new cases, missing cases), pass-rate trends, flaky-test
  detection, per-case history and 7-day quality metrics.
- **Optional AI failure diagnosis** — sends failed cases, log tails, the baseline
  diff and flaky history to Anthropic or OpenAI and returns one of six root-cause
  categories with a confidence level and evidence. Read-only; disabled when no
  API key is set.
- **Web console and REST API** — React UI with English/Chinese, light/dark themes
  and keyboard accessibility; a REST API with Swagger UI, ReDoc and an OpenAPI schema.
- **Accounts** — admin and user roles, owner-scoped access to runs, profiles and
  schedules, login throttling; Feishu (Lark) notifications when a run finishes.

See [docs/FEATURES.md](docs/FEATURES.md) for the complete, source-checked
capability list, including what qarunner deliberately does not do.

## Who it is for

QA engineers, test developers and quality owners who already maintain pytest or
Playwright suites and want one low-maintenance place to run them on a schedule,
keep the evidence, and see what changed. Developers consume the results before a
commit, a release or an incident review.

It is **not** a CI system, not a command-line tool, and not multi-tenant. See
[docs/DIRECTION.md](docs/DIRECTION.md) for the product direction and trust model.

## Quick start (development mode)

Requires Docker with Docker Compose. Full guide: **[docs/deployment.md](docs/deployment.md)**.

```bash
# 1) Configure. QARUNNER_SECRET_KEY and QARUNNER_ADMIN_PASSWORD must be strong:
#    the server refuses placeholders such as change-me or admin123.
cp .env.example .env
$EDITOR .env

# 2) Start the development stack (hot reload for backend and frontend)
docker compose -f docker-compose.dev.yml up -d

# 3) On first start, run the database migration — the backend refuses to start
#    until it is done. See "PostgreSQL migration operator" in docs/deployment.md.

# Open http://localhost:5173 and sign in as `admin` with the password from .env
```

After changing code:

| What changed | What to do |
|--------------|------------|
| Frontend `src/*.tsx` | Applied automatically; refresh the browser |
| Backend `src/*.py` | Server restarts automatically within 2–3 seconds |
| Dependencies | `docker compose -f docker-compose.dev.yml up -d --build` |

## FAQ

### What is qarunner?

A self-hosted regression test runner. It executes existing pytest and Playwright
suites in isolated Docker containers, stores logs, reports and per-case results for
every run, and compares each run with a comparable earlier run.

### Which test frameworks does qarunner support?

pytest and Playwright. Each has its own executor image; qarunner collects JUnit
results, builds an Allure report, and keeps Playwright traces, screenshots and videos.

### How does qarunner detect regressions and flaky tests?

Each run is compared with the most recent completed run of the same suite, runner
and arguments, and every case lands in one of five buckets: new failure, fixed,
still failing, new case or missing case. A case is marked flaky when its recent
results keep flipping between pass and fail (by default at least three flips over
at least four observations).

### Is qarunner a replacement for CI?

No. qarunner is where regression suites run on a schedule or on demand and where
results are compared over time. A CI pipeline can trigger runs through the REST API.

### Is it safe to run untrusted test code?

Not in the current version — see the caution at the top. Tests run in hardened
containers, but the control plane holds the Docker socket, so use it only for test
code you trust, on a trusted network.

### Does qarunner need an LLM API key?

No. AI failure diagnosis is optional; without `QARUNNER_AI_API_KEY` everything
else works and the AI tab explains how to enable it.

### Does it have a CLI or Slack/email notifications?

No. qarunner has a web console and a REST API, and sends Feishu (Lark) cards when
a run finishes. Other notification channels and a CLI are out of scope.

### How is qarunner licensed?

MIT.

## Documentation

| Document | What it covers |
|----------|----------------|
| [docs/deployment.md](docs/deployment.md) | Local deployment and updates (development / legacy validation modes, operations) |
| [docs/FEATURES.md](docs/FEATURES.md) | Capability overview, checked against the source |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Every API route with request/response fields |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Code architecture (layers, ports, adapters) |
| [docs/DIRECTION.md](docs/DIRECTION.md) | Product direction (single source of truth) |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | Product requirements (outcomes, scope, KPIs, roadmap) |
| [docs/SYSTEM_REQUIREMENTS.md](docs/SYSTEM_REQUIREMENTS.md) | System requirements (worker protocol, provenance, authorization, evidence) |
| [docs/SECURITY_OPERATIONS_REQUIREMENTS.md](docs/SECURITY_OPERATIONS_REQUIREMENTS.md) | Security and operations requirements |
| [docs/WORKER_PROTOCOL.md](docs/WORKER_PROTOCOL.md) | Worker protocol reference |
| [docs/REQUIREMENTS_TRACEABILITY.md](docs/REQUIREMENTS_TRACEABILITY.md) | Requirements traceability matrix |
| [docs/RELEASE_GATE_CATALOG.md](docs/RELEASE_GATE_CATALOG.md) | Release acceptance catalog |
| [docs/METRICS.md](docs/METRICS.md) | Quality metric definitions (pass rate, flakiness, duration) |
| [docs/greenfield/](docs/greenfield/README.md) | Greenfield design set — **target design, not delivered capability** |
| [specs/ui-test-plan.md](specs/ui-test-plan.md) | End-to-end UI test plan |

Most design and requirements documents are written in Chinese.

Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) · Security reports: [SECURITY.md](SECURITY.md) ·
Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) · Changes: [CHANGELOG.md](CHANGELOG.md)

## Legacy single-host validation deployment (not V7.4 production)

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
  suites into the writable `external_tests` root. This is a legacy single-host
  validation path, not the V7.4 production topology. It is bind-mounted
  source==target so the DooD executor's jail-fallback path stays host-resolvable;
  on a server point `${PWD}/external_tests` at a persistent directory and back it
  up too. No named volume is used — the executor mounts the per-run jail under
  artifacts, never this root.
- **Executor**: runs default to the hardened **Docker executor** (SEC-3:
  non-root, no network, `cap_drop=ALL`, read-only rootfs, pid/mem/cpu limits),
  which runs tests in a throwaway `qarunner-executor:latest` container. The
  bundled legacy validation compose mounts the Docker daemon socket and joins the
  host docker group (`QARUNNER_DOCKER_GID`) so this path works — note that socket access is
  effectively host-root, so the real isolation is that untrusted tests run in the
  executor container, **not** the server process. The in-process `subprocess`
  executor is current legacy behavior: admins can select it, and non-admins can
  select it only when `QARUNNER_ALLOW_SUBPROCESS_FOR_NON_ADMINS=true`. V7.4 forbids
  this path and never permits it as Worker/Docker failover. The dev compose enables
  it only as a single-host development convenience. In dev the executor images are
  built on demand — only when missing, so rebuild them with the commands below
  after `Dockerfile` / `Dockerfile.playwright` change; for release-like legacy validation set
  `QARUNNER_EXECUTOR_AUTOBUILD=false` and pre-build them so a missing image
  fails fast instead of being silently (re)built:
  `docker build -f Dockerfile -t qarunner-executor:latest .` and
  `docker build -f Dockerfile.playwright -t qarunner-playwright-executor:latest .`.
  `pytest` runs use the python executor image; `playwright` runs use the
  Playwright executor image and may run with `executor_mode='docker'`.
  For Playwright, qarunner owns `--reporter=junit` and
  `--output=<run-results>/playwright-results` so JUnit collection and
  Playwright artifacts remain under the run's artifact directory.
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
| `QARUNNER_ALLOW_SUBPROCESS_FOR_NON_ADMINS` | `false` | Legacy validation-only switch; the in-process executor runs test code in the server process and is forbidden by the V7.4 target. Keep `false` everywhere except isolated local development |
| `QARUNNER_EXECUTOR_AUTOBUILD` | `true` | Legacy validation-only runtime image build. Keep `false` for any release-like validation and pre-build immutable images; V7.4 requires Worker-side image digests |
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
| `QARUNNER_AI_API_KEY` | (empty) | LLM provider API key. Optional; when empty, AI diagnosis is disabled (`enabled:false`) and the AI tab shows setup instructions |
| `QARUNNER_AI_PROVIDER` | `anthropic` | LLM provider for AI diagnosis: `anthropic` or `openai` |
| `QARUNNER_AI_MODEL` | `claude-opus-4-8` | Model name used for AI diagnosis |
| `QARUNNER_AI_POST_MAX_CALLS` | `10` | AI diagnosis requests allowed per user per window; `0` disables the limit |
| `QARUNNER_AI_POST_WINDOW_SECONDS` | `60` | Rate-limit window for AI diagnosis requests, in seconds |

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

[MIT](LICENSE)
