# qarunner

> **qarunner** — 产品方向见 [docs/DIRECTION.md](docs/DIRECTION.md)。运行详情支持只读的 AI 失败诊断（无 API key 时 Tab 内展示未配置说明，不隐藏入口），详见 [docs/FEATURES.md](docs/FEATURES.md)。

> [!CAUTION]
> **当前版本请勿用来执行你不信任的测试代码。**
>
> 控制面进程直接挂载宿主的 `/var/run/docker.sock`。挂载 Docker socket 等价于把宿主 root 权限交给该进程 —— 测试代码一旦逃出执行容器，就能控制整台宿主机。
>
> - ✅ **适用**：你自己或团队编写、依赖来源可控的回归套件，部署在受信内网。
> - ❌ **不适用**：来源不明的测试代码、外部贡献者提交的 PR 测试、多租户共享环境。
>
> 目标架构（控制面与专用 Worker 主机分离、控制面不持有 Docker socket、每 Run 一次性容器）尚未落地，内部追踪编号 GAP-021 / SOR-GAP-023。

## 本地部署

**日常开发和部署指南详见 [docs/deployment.md](docs/deployment.md)**。

快速启动（开发模式）：

```bash
# 1) 准备配置。SECRET_KEY 与 ADMIN_PASSWORD 必须设为强值：
#    平台会拒绝 change-me / admin123 等占位口令并拒绝启动。
cp .env.example .env
$EDITOR .env   # 填入自己的 QARUNNER_SECRET_KEY 与 QARUNNER_ADMIN_PASSWORD

# 2) 启动开发环境（前后端热更新）
docker compose -f docker-compose.dev.yml up -d

# 3) 首次启动需要先执行数据库迁移，否则后端会按设计拒绝启动，
#    具体命令见 docs/deployment.md 的 "PostgreSQL migration operator" 一节。

# 访问 http://localhost:5173
# 管理员用户名 admin，密码为你在 .env 中设置的值
```

代码更新后：

| 修改了什么 | 需要做什么 |
|-----------|-----------|
| 前端 `src/*.tsx` | 自动生效，刷新浏览器 |
| 后端 `src/*.py` | 自动重启，等 2-3 秒 |
| 新增依赖包 | `docker compose -f docker-compose.dev.yml up -d --build` |

详细说明（开发与 legacy 验证部署、运行测试、容器运维）见 **[docs/deployment.md](docs/deployment.md)**。

## 文档索引

| 文档 | 说明 |
|------|------|
| [docs/deployment.md](docs/deployment.md) | **本地部署与更新指南**（开发/legacy 验证模式、代码更新、运维命令） |
| [docs/DIRECTION.md](docs/DIRECTION.md) | 产品方向（唯一权威来源） |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | 产品需求（用户结果、核心范围、KPI、路线图） |
| [docs/SYSTEM_REQUIREMENTS.md](docs/SYSTEM_REQUIREMENTS.md) | 系统需求（Worker 协议、provenance、授权、环境租约、证据、状态与接口） |
| [docs/SECURITY_OPERATIONS_REQUIREMENTS.md](docs/SECURITY_OPERATIONS_REQUIREMENTS.md) | 安全与运维需求（控制面/Worker 边界、供应链、宿主隔离、部署与门禁） |
| [docs/WORKER_PROTOCOL.md](docs/WORKER_PROTOCOL.md) | Worker 协议参考（mTLS 长轮询、claim/commit-start、fencing、上传、恢复与错误码） |
| [docs/REQUIREMENTS_TRACEABILITY.md](docs/REQUIREMENTS_TRACEABILITY.md) | 需求追踪矩阵（PRD → 系统/安全/运维 → 验收目录 → 发布证据） |
| [docs/RELEASE_GATE_CATALOG.md](docs/RELEASE_GATE_CATALOG.md) | 发布验收目录（24 个 family、稳定 case ID、适用性、样本与阻断状态） |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | API 端点参考（所有路由、请求/响应字段） |
| [docs/FEATURES.md](docs/FEATURES.md) | 功能列表 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 代码架构（层次、端口、适配器） |
| [specs/ui-test-plan.md](specs/ui-test-plan.md) | E2E 测试计划 |
| [specs/TEST_PLAN_TEMPLATE.md](specs/TEST_PLAN_TEMPLATE.md) | 测试计划模板（含组件交互清单） |

## 配置参考

所有配置通过 `QARUNNER_` 前缀的环境变量设置，见 [docs/deployment.md](docs/deployment.md) 和 `.env.example`。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `QARUNNER_SECRET_KEY` | **（必填）** | JWT 签名密钥 |
| `QARUNNER_ADMIN_PASSWORD` | **（必填）** | 管理员初始密码 |
| `QARUNNER_DB_PATH` | `./artifacts/qarunner.db` | SQLite 数据库路径 |
| `QARUNNER_ALLOW_SUBPROCESS_FOR_NON_ADMINS` | `false` | 非管理员能否使用 subprocess 执行器 |
| `QARUNNER_COOKIE_SECURE` | `false` | 生产环境需设为 `true` |
| `QARUNNER_MAX_CONCURRENCY` | `4` | 最大并发测试数 |
| `QARUNNER_AI_API_KEY` | （空） | LLM provider 的 API key；选填，留空则诊断生成禁用（端点 `enabled:false`，Tab 仍显示未配置说明），不影响其余功能 |
| `QARUNNER_AI_PROVIDER` | `anthropic` | AI 诊断使用的 LLM provider，`anthropic` 或 `openai` |
| `QARUNNER_AI_MODEL` | `claude-opus-4-8` | AI 诊断使用的模型名称 |
| `QARUNNER_AI_POST_MAX_CALLS` | `10` | 每用户滑动窗口内允许的 AI POST 次数；`0` 关闭限流 |
| `QARUNNER_AI_POST_WINDOW_SECONDS` | `60` | AI POST 限流窗口秒数 |

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
  built on demand; for release-like legacy validation set
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
