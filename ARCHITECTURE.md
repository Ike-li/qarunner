# qarunner Architecture

> **qarunner** — for the product direction, see [docs/DIRECTION.md](docs/DIRECTION.md) (the single
> source of truth). This document covers the **architecture** only.

## Table of Contents

- [Layered Overview](#layered-overview)
- [Hexagonal (Ports & Adapters) Architecture](#hexagonal-ports--adapters-architecture)
- [Dependency Injection & Wiring](#dependency-injection--wiring)
- [API Layer](#api-layer)
- [Core Execution Flow](#core-execution-flow)
- [Security Design](#security-design)
- [Frontend Architecture](#frontend-architecture)
- [Testing Strategy](#testing-strategy)
- [Deployment Architecture](#deployment-architecture)

---

## Layered Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                   frontend/ (React + Vite + SemiUI)               │
│  TypeScript SPA — components, domain hooks, i18n, dark/light mode │
└───────────────────────────┬──────────────────────────────────────┘
                            │ HTTP (JSON API, SSE log streaming)
┌───────────────────────────▼──────────────────────────────────────┐
│  src/qarunner/api/         FastAPI HTTP 层                        │
│  app.py (application factory)                                    │
│  deps.py (DI container, auth dependencies)                       │
│  routes.py (REST endpoints)                                      │
│  schemas.py (Pydantic request/response models)                   │
├──────────────────────────────────────────────────────────────────┤
│  src/qarunner/core/        核心业务逻辑 (zero FastAPI imports)     │
│  orchestrator — test run orchestration, arg/env/path validation   │
│  runners/ — PytestRunner, PlaywrightRunner, RunnerRegistry        │
│  auth, cron, login_throttle, profile_service, schedule_service    │
├──────────────────────────────────────────────────────────────────┤
│  src/qarunner/ports/       端口接口 (抽象 Protocol)                │
│  Store | Clock | IdGenerator | ProcessRunner                     │
│  ResultCollector | AllureReporter | TaskScheduler | SchedulePort │
├──────────────────────────────────────────────────────────────────┤
│  src/qarunner/adapters/    适配器 (具体实现)                       │
│  SqliteStore | SystemClock | UuidIds                             │
│  SubprocessRunner | DockerRunner | AsyncioScheduler              │
│  JunitCollector | AllureCliReporter | ApschedulerSchedulePort    │
├──────────────────────────────────────────────────────────────────┤
│  src/qarunner/models.py    领域模型 (Pydantic frozen)             │
│  Run, RunRequest, TestProfile, TestSuite, TestSchedule          │
│  User, TestCaseResult, TestSummary, ReportRef                    │
├──────────────────────────────────────────────────────────────────┤
│  tests/                    测试套件 (100% coverage gate)           │
│  fakes/ (fake adapters for every port)                           │
│  unit/ + integration/ + e2e/                                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## Hexagonal (Ports & Adapters) Architecture

The backend follows a strict hexagonal (ports & adapters) pattern. Core business
logic (`core/`) depends only on abstract **ports** (`ports/`), never on concrete
**adapters** (`adapters/`). Adapters are wired at startup by the DI container.

### Ports

| Port | Protocol | Responsibility |
|------|----------|----------------|
| `Store` | `RunStore + UserStore + ProfileStore + ScheduleStore + SuiteStore + CredentialStore` | Full persistence surface |
| `Clock` | `Clock` | Returns current UTC time |
| `IdGenerator` | `IdGenerator` | Generates unique IDs |
| `ProcessRunner` | `ProcessRunner` | Executes external subprocesses |
| `ResultCollector` | `ResultCollector` | Parses JUnit XML results |
| `AllureReporter` | `AllureReporter` | Generates Allure HTML reports |
| `TaskScheduler` | `TaskScheduler` | Controls run concurrency (max N in-flight) |
| `SchedulePort` | `SchedulePort` | Cron-schedule lifecycle (start/stop/list) |

### Adapters

| Port → Adapter | Technology | Notes |
|----------------|------------|-------|
| `Store → SqliteStore` | `aiosqlite` (WAL mode, per-op connections) | Versioned schema via `PRAGMA user_version` |
| `Clock → SystemClock` | `datetime.now(UTC)` | Standard wall clock |
| `IdGenerator → UuidIds` | `uuid.uuid4()` | Standard UUID v4 |
| `ProcessRunner → SubprocessRunner` | `asyncio.create_subprocess_exec` | Direct host execution (admin-only by default) |
| `ProcessRunner → DockerRunner` | `docker-py` | **Default** — isolated container execution |
| `ResultCollector → JunitCollector` | `defusedxml` | Safe XML parsing (XXE protection) |
| `AllureReporter → AllureCliReporter` | `allure generate` CLI | Shells out to the `allure` binary |
| `TaskScheduler → AsyncioScheduler` | `asyncio.Semaphore` | Concurrency gate |
| `SchedulePort → ApschedulerSchedulePort` | `APScheduler` | Cron trigger management |

### Why Hexagonal?

- **Testability**: Every adapter has a `fake_*.py` counterpart in `tests/fakes/`.
  Unit tests inject fakes; no real database, subprocess, or Docker needed.
- **Swapability**: Swap `SqliteStore` for Postgres, or `DockerRunner` for
  Kubernetes, without touching a single line in `core/`.
- **Enforced boundaries**: `ports/` are pure `Protocol` classes. `core/` imports
  only from `ports/`, not from `adapters/`. Violations are caught at import time.

---

## Dependency Injection & Wiring

`src/qarunner/api/deps.py` defines the explicit DI container:

```python
@dataclass
class Container:
    orchestrator: RunOrchestrator
    store: Store
    scheduler: SchedulePort
    schedule_service: ScheduleService
    profile_service: ProfileService
    login_throttle: LoginThrottle
    settings: Settings
```

`create_container()` wires the real adapters at application startup:

```python
def create_container(settings=None) -> Container:
    store = SqliteStore(cfg.db_path)
    clock = SystemClock()
    ids = UuidIds()
    process = SubprocessRunner()
    docker_process = DockerRunner(allow_runtime_build=cfg.executor_autobuild)
    collector = JunitCollector()
    reporter = AllureCliReporter(process=process, allure_bin=cfg.allure_bin)
    scheduler = AsyncioScheduler(max_concurrency=cfg.max_concurrency)
    registry = RunnerRegistry()
    registry.register(PytestRunner())
    registry.register(PlaywrightRunner())
    orchestrator = RunOrchestrator(registry, store, scheduler, process, ...)
    # ...
    return Container(orchestrator, store, scheduler, ...)
```

Tests skip `create_container()` and construct `Container(...)` directly with
fake adapters.

---

## API Layer

- **Framework**: FastAPI
- **Factory**: `create_app()` in `app.py` — registers router, attaches lifespan,
  mounts static SPA if `frontend/dist/` exists
- **Lifespan**: Database init → crash recovery → scheduler start → graceful shutdown
- **Routes** (`routes.py`, ~1900 lines, 46 endpoints):
  完整端点列表见 [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md)。
  主要域：auth/users、credentials、suites (git ops)、profiles、runs (CRUD + SSE + diff/trend/metrics)、schedules。

---

## Core Execution Flow

```
User triggers run (POST /runs)
        │
        ▼
RunOrchestrator.create_run()
  ├── Validate RunRequest fields
  ├── _compile_args() → reject dangerous pytest/playwright flags
  ├── _sanitize_env() → reject LD_*/PYTHON*/PATH/NODE_OPTIONS
  ├── safe_subpath() → reject path traversal
  │
  ├── executor_mode = "docker"  → DockerRunner (default, isolated)
  └── executor_mode = "subprocess" → SubprocessRunner (admin-only gate)
        │
        ▼
  RunnerRegistry.get(runner_name) → PytestRunner / PlaywrightRunner
        │
        ▼
  Runner.build_command(BuildContext) → produce argv
        │
        ▼
  ProcessRunner.run(cmd, cwd, env_vars, timeout)
    └── Capture stdout/stderr, exit code, duration
        │
        ▼
  ResultCollector.collect(junit_xml_path) → TestSummary + TestCaseResult[]
        │
        ▼
  Store.save_cases(run_id, …, cases) → persist per-case results (cross-run analysis)
        │
        ▼
  AllureReporter.generate(allure_results_dir, output_dir)
    └── Allure CLI produces HTML report
        │
        ▼
  Store.save(run) → persist final state
```

### Workspace Jail

For subprocess execution, each run gets a temporary jail directory under
`artifacts/` containing a copy of the test suite (minus `.git/`, `.venv/`,
`__pycache__/`). The jail prevents the test from writing to or reading from
unintended parts of the host filesystem.

---

## Security Design

Security is woven into every layer of the application.

| Mechanism | Location | Description |
|-----------|----------|-------------|
| **Isolated execution** | `DockerRunner` | **Default** executor mode. Container runs `--network none --read-only --cap-drop ALL` as non-root. Untrusted test code never touches the server process |
| **Subprocess admin gate** | `Settings.allow_subprocess_for_non_admins` | Subprocess executor requires explicit opt-in; non-admins are blocked by default |
| **Arg injection protection** | `orchestrator._compile_args()` | Rejects dangerous pytest flags (`-p`, `--config`, `--alluredir`, `--junitxml`) and Playwright flags (`--config`, `--global-setup`) |
| **Env variable sanitization** | `orchestrator._sanitize_env()` | Rejects `LD_*`, `DYLD_*`, `PYTHON*`, `PATH`, `BASH_ENV`, `NODE_OPTIONS` |
| **Path traversal protection** | `core/paths.safe_subpath()` | Ensures resolved paths stay within the allowed test root |
| **JWT secret validation** | `config.Settings` | Startup rejects blank, placeholder, and known-weak secret keys |
| **Login brute-force throttle** | `core/login_throttle.py` | Exponential backoff lockout per identity, returns HTTP 429 `Retry-After` |
| **Object-level authorization** | `routes._require_owner_access()` | Non-admin users can only access their own runs, profiles, and schedules (IDOR prevention) |
| **Secure cookie** | `deps.get_current_user()` | JWT delivered via HttpOnly `SameSite=Strict` cookie, never in URL query parameters |
| **Secure XML parsing** | `JunitCollector` | Uses `defusedxml` to block XXE and billion-laughs attacks |
| **Non-root runtime** | `Dockerfile`, `Dockerfile.server` | All containers run as a non-root user (uid 1000) |
| **Output size limits** | `routes.get_run_stream()` | Log tail capped to prevent OOM from unbounded test output |

---

## Frontend Architecture

```
frontend/src/
├── main.tsx                         — Entry point
├── App.tsx                          — Root: theme/lang toggle, auth gate
├── i18n.ts                          — zh/en translations
├── types.ts                         — Shared TypeScript types
├── logUtils.ts                      — Log parsing utilities
│
├── components/
│   ├── DashboardLayout.tsx          — Main layout shell
│   ├── Header.tsx                   — App header (user menu, settings)
│   ├── ProjectSidebar.tsx           — Suite browser + file tree
│   ├── RunsTable.tsx                — Run history table
│   ├── RunDetailsDrawer.tsx         — Run detail panel (logs, results)
│   ├── TriggerRunModal.tsx          — Run trigger form
│   ├── ScheduleModal.tsx            — Cron schedule editor
│   ├── AddSuiteModal.tsx            — Suite registration dialog
│   ├── LoginScreen.tsx              — Login page
│   ├── UserManagementModal.tsx      — Admin user management
│   ├── StatsCards.tsx               — Dashboard statistics
│   ├── TestFileTree.tsx             — File tree browser
│   ├── FullscreenTerminalOverlay.tsx— Terminal-style log viewer
│   ├── FullscreenReportOverlay.tsx  — Allure report in-app viewer
│   ├── SuiteTrend.tsx              — Pass-rate trend SVG sparkline
│   └── runStatus.ts                — Status badge/color mapping
│
├── (utility modules)
│   ├── filterRuns.ts                — RunsTable filter pipeline (pure fn)
│   ├── runDiff.ts                   — Diff bucket classification (pure fn)
│   ├── runTrend.ts                  — Trend geometry helpers (pure fn)
│   ├── runCaseHistory.ts            — Case history cell rendering (pure fn)
│   └── logUtils.ts                  — Log line classification + formatting
│
└── hooks/
    ├── DashboardContext.tsx          — Global state context
    ├── useApi.ts                     — HTTP client + base URL
    ├── useAuth.ts                    — Authentication state
    ├── useRuns.ts                    — Run CRUD + polling
    ├── useProfiles.ts               — Profile CRUD
    ├── useSchedules.ts              — Schedule CRUD
    ├── useSuites.ts                 — Suite CRUD + file tree
    ├── useUsers.ts                  — User management CRUD
    ├── useCredentials.ts            — Credential CRUD
    ├── useTriggerForm.ts            — Run trigger form state/validation
    ├── useTerminalView.tsx           — SSE log streaming + ANSI rendering
    ├── useFileTreeSelection.ts       — File/folder multi-select
    └── useDialogA11y.ts              — Accessibility (focus trap, ESC close)
```

Key design points:

- **Centralized state**: `DashboardContext` provides shared state via
  `useDashboard()` to all components. Each domain concept (runs, profiles,
  schedules, suites, users) has a dedicated hook.
- **UI library**: SemiUI (bytedance) — supports dark/light theme and
  zh/en i18n out of the box.
- **Log streaming**: SSE connection with backoff reconnect, ANSI escape
  rendering, bounded tail buffer.
- **Accessibility**: Focus traps in modals, `data-testid` attributes on all
  interactive elements, keyboard navigation.

---

## Testing Strategy

```
tests/
├── fakes/                  — Fake adapters (in-memory implementations)
│   ├── fake_store.py       — dict-based Store
│   ├── fake_clock.py       — Manual-time Clock
│   ├── fake_process.py     — Captures commands, returns canned results
│   ├── fake_collector.py   — Returns predefined CollectResult
│   ├── fake_reporter.py    — Records calls without shelling out
│   ├── fake_scheduler.py   — In-memory concurrency gate
│   └── fake_ids.py         — Deterministic ID generation
├── unit/
│   ├── adapters/           — Adapter tests (sqlite_store, runners, etc.)
│   ├── api/                — API route tests with TestClient + fakes
│   ├── core/               — Orchestrator, auth, cron, throttle tests
│   ├── test_models.py      — Domain model invariants
│   └── test_ports_and_fakes.py — Port contract conformance
├── integration/
│   ├── test_docker_runner_integration.py  — Real Docker (opt-in)
│   └── test_image_hardening.py            — Container security hardening
├── e2e/                    — End-to-end tests (Playwright)
└── conftest.py             — Shared fixtures (fakes, test client, settings)
```

### Principles

1. **100% coverage gate** — `--cov-fail-under=100` enforced in CI
2. **Fake every port** — Unit tests never touch real I/O
3. **Port conformance tests** — `test_ports_and_fakes.py` validates that every
   fake satisfies the same contract as the real adapter
4. **Integration tests gated** — Docker-dependent tests tagged `@pytest.mark.docker`,
   excluded from the default run

---

## Deployment Architecture

### Production (single-container)

```
docker-compose.yml
┌──────────────────────────────────────────────────────┐
│  qarunner:latest (Dockerfile.server)                  │
│                                                        │
│  Single image serving both API + built React SPA       │
│  on port 8000. Non-root `app` user.                   │
│                                                        │
│  Volumes:                                              │
│  ├── ./artifacts/        → DB, results, reports       │
│  ├── ./external_tests/   → Git/local test suites      │
│  └── /var/run/docker.sock → Docker-outside-Docker     │
│                                                        │
│  Enforced: QARUNNER_SECRET_KEY, QARUNNER_ADMIN_PASSWORD│
│  Healthcheck: /health (DB round-trip)                 │
│  Resource limits: 2 CPU, 2G memory                    │
└──────────────────────┬───────────────────────────────┘
                       │ Docker API
┌──────────────────────▼───────────────────────────────┐
│  qarunner-executor:latest (Dockerfile)                │
│  Temporary per-run container                          │
│  --network none --read-only --cap-drop ALL            │
│  Non-root `runner` user (uid 1000)                   │
│  Pre-installed: pytest, allure-pytest                 │
└──────────────────────────────────────────────────────┘
```

### Development (hot-reload)

```
docker-compose.dev.yml
┌──────────────────┐     ┌──────────────────┐
│  API Container    │     │  Vite Container   │
│  :8000            │     │  :5173            │
│  uvicorn --reload │◄────┤  HMR dev server   │
│                   │     │  proxies /api/*   │
│  WATCHFILES_FORCE │     │  to :8000         │
│  _POLLING=true    │     │                   │
└──────────────────┘     └──────────────────┘
```

- Dev compose runs **two** containers with live reload (vs production's single
  baked image)
- Frontend HMR uses file polling (`VITE_USE_POLLING=true`) for macOS Docker
  bind-mount compatibility
- `QARUNNER_PROJECTS_ROOT` (default `~/code`) bind-mounted read-only so local
  suite symlinks resolve inside the container
- `allow_subprocess_for_non_admins=true` in dev compose for convenience
