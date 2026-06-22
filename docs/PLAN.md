# qa_platform_v2 后端实现计划（v2 · 经设计拷问修订）

> 修订说明：本版在 v1 基础上吸收了一轮逐分支的设计拷问（grill），落定了执行生命周期、
> 终态语义、可测性边界、错误模型、SQLite 并发、Allure 报告服务等关键决策。日期 2026-06-16。

## Context

`qa_platform_v2` 是参照 `~/code/qa_platform` 重写的**精简后端**。v1 只做一件事：
**把外部测试代码拿进来执行 → 产出 allure-results + 生成 Allure HTML 报告**，并通过一个薄 HTTP API
触发/查询/查看。硬性要求：**后端 only、TDD、单元测试 100% 覆盖**。

为支撑 100% 覆盖，采用**端口与适配器（hexagonal）**：所有副作用都抽象成端口（`typing.Protocol`），
核心编排（orchestrator）只编排端口、**完全不碰文件系统**，用 fake 确定性测到 100%；薄适配器用真实
资源做针对性测试。

## 已确认选型（含拷问拍板）

| 维度 | 决策 |
|---|---|
| 架构 | 精简内核 + 薄 FastAPI (包含完整的 JWT 鉴权与管理员用户管理中台，不引入 Postgres/Redis/arq/S3) |
| 执行隔离 | 子进程 subprocess（`asyncio.create_subprocess_exec`），挡在 `ProcessRunner` 端口后 |
| **测试运行环境** | 复用 qarunner 自己的 venv（默认 `sys.executable`，可配 `executable`）；**外部测试的依赖须装进此 venv** |
| 测试框架 | 统一 Runner 插件接口，首版只实现 pytest（`allure-pytest` 因此是**运行时**依赖） |
| **执行生命周期** | **异步后台 + 轮询**：POST 建 `QUEUED` → `TaskScheduler` 后台执行 → 客户端轮询；POST 返回 `202 + id` |
| Allure 产物 | allure-results 原始结果 + **`allure generate --single-file`** 出单个自包含 HTML |
| **终态语义** | 执行语义：`COMPLETED`(跑完拿到结果) / `FAILED`(运行炸了) / `TIMEOUT`；绿红看 summary |
| 持久化 | SQLite（aiosqlite），**只存 Run 行**（summary+report 为 JSON 列） |
| 并发 | 可配全局信号量，默认 4，超出停 `QUEUED` |
| 取消 | v1 不做（TIMEOUT 兜底），无 `CANCELLED` 状态 |
| 工具链 | Python 3.12、uv、ruff、pytest + pytest-asyncio + pytest-cov；包名 `qarunner` |

## 范围

**做：** 触发 pytest 运行 → 收 junit 出摘要 → 产 allure-results + 生成单文件 HTML → 落 SQLite → API 查询/查看。已包含完整的 JWT 鉴权与管理员账户管理体系。

**不做（留接口/后续）：** 真正的多租户隔离、Docker 执行、git clone、取消、Redis/SSE、arq、S3、通知、调度、
分析/triage、quarantine、审计、报告分享 token、per-case 入库、依赖隔离 venv。

外部测试代码来源：**固定 `tests_root`（默认 `./external_tests/`）下的相对路径**，`safe_subpath` 防穿越。

---

## 架构总览：7 个端口

| 端口 (Protocol) | 方法 | 真实适配器 | 测试 fake |
|---|---|---|---|
| `ProcessRunner` | `run(cmd,cwd,env,timeout) -> ProcessResult` | `subprocess_runner`（超时→kill→`timed_out=True`） | `FakeProcessRunner`(脚本化, 记录调用) |
| `Clock` | `now()` | `system_clock` | `FakeClock` |
| `IdGenerator` | `new_id()` | `uuid_ids` | `FakeIdGenerator`(确定序列) |
| `RunStore` | `save / get / list` | `sqlite_store`（单连接+WAL+busy_timeout） | `InMemoryRunStore` |
| `TaskScheduler` | `schedule(coro)` / `drain(timeout)` | `asyncio_scheduler`（semaphore=max_concurrency + create_task；drain 排空在途任务，超时则 cancel） | `FakeScheduler`(内联执行) |
| `ResultCollector` | `collect(run_dir) -> CollectResult \| None` | `junit_collector`（调 core/junit） | `FakeResultCollector` |
| `AllureReporter` | `generate(run_dir, enabled) -> ReportRef` | `allure_cli_reporter`（**内部依赖 ProcessRunner**） | `FakeAllureReporter` |

`AllureReporter` 适配器内部依赖 `ProcessRunner`，故其分支（空结果跳过 / `--single-file` 生成 /
exit127 缺 CLI / 其它失败）用 `FakeProcessRunner` 即可测，**无需真 allure**。

## 目录结构

```
qa_platform_v2/
  pyproject.toml  README.md  .gitignore
  external_tests/             # 默认 tests_root
  src/qarunner/
    config.py                # Settings: tests_root, artifacts_root, db_path, allure_bin,
                             #           executable, default_timeout_seconds, max_concurrency
    models.py                # Run, RunStatus, RunRequest, TestCaseResult, TestSummary,
                             #   ReportRef, ProcessResult, CollectResult
    errors.py                # RunNotFound, UnknownRunner, UnsafePath, RunnerError
    ports/                   # process / clock / ids / store / scheduler / collector / reporter
    core/
      runners/ base.py(Runner协议+BuildContext) pytest_runner.py registry.py
      junit.py               # parse_junit_xml 纯函数
      allure.py              # build_generate_command(--single-file) / should_generate
      paths.py               # safe_subpath
      orchestrator.py        # RunOrchestrator.create / execute
    adapters/                # subprocess_runner / system_clock / uuid_ids / sqlite_store /
                             #   asyncio_scheduler / junit_collector / allure_cli_reporter
    api/  schemas.py  deps.py(Container)  routes.py  app.py
  examples/sample_tests/     # e2e 最小 pytest 工程
  tests/  fakes/  fixtures/  unit/{core,adapters,api}/  e2e/
```

## 状态机

```
QUEUED ──(scheduler 取得信号量)──▶ RUNNING ──▶ COMPLETED | FAILED | TIMEOUT
```
无 PENDING、无 CANCELLED。每个 run 行只被它自己的后台 task 写（`create()` 在前），单一 owner。

## 核心数据模型（`models.py`，Run 为 frozen Pydantic model）

- `RunStatus`: `QUEUED / RUNNING / COMPLETED / FAILED / TIMEOUT`
- `RunRequest`: `tests_path:str`, `runner="pytest"`, `args:list[str]=[]`, `allure=True`, `timeout:int|None=None`
- `ProcessResult`: `exit_code:int`, `stdout:str`, `stderr:str`, `duration_ms:int`, **`timed_out:bool`**
- `TestCaseResult`: `suite, name, status(passed/failed/skipped/error), duration_ms, message|None`
- `TestSummary`: `total, passed, failed, skipped, error, duration_ms, pass_rate`
- `CollectResult`: `summary:TestSummary`, `cases:list[TestCaseResult]`（cases 只用于算 summary，不入库）
- `ReportRef`: `allure_results_dir`, `allure_report_file|None`(单个 index.html), `html_generated:bool`
- `Run`: `id, status, runner, tests_path, args|[], allure_enabled|True, timeout|None, summary|None, report|None, exit_code|None, error|None,`
  `created_at, started_at|None, finished_at|None`
  > 注：`args`/`allure_enabled`/`timeout` 来自 `RunRequest`，存于 Run 以使 `execute(run_id)` 自包含。
- API 响应额外派生 **`passed:bool|None`** = `COMPLETED` 时 `summary.failed==0 and summary.error==0`，其他状态为 `None`

## 核心执行流程

**`orchestrator.create(req) -> Run`（同步、在请求内）**
1. `registry.get(req.runner)` 否则 `UnknownRunner` → 400。
2. `tests_dir = safe_subpath(tests_root, req.tests_path)` 否则 `UnsafePath` → 400。
3. `id=ids.new_id()`、`now=clock.now()`；`Run(status=QUEUED, created_at=now)`；`store.save`。
4. `scheduler.schedule(execute(id))`；返回 `Run`（HTTP 202）。

**`orchestrator.execute(run_id)`（后台 task，错误模型见下）**
1. `run=store.get`；置 `RUNNING`（`started_at=now`）；`save`。
2. `cmd=runner.build_command(BuildContext)`：输出用**绝对路径**指向 `artifacts_root/<id>/results/`
   （`junit.xml`、`allure-results/`），`cwd=tests_dir`，不显式追加测试路径（靠 cwd 收集），`args` 透传。
   pytest 形如：`{executable} -m pytest --junitxml=<ABS>/junit.xml --alluredir=<ABS>/allure-results`。
3. `proc = await process.run(cmd, cwd=tests_dir, timeout=req.timeout or default)`；存 `exit_code`。
4. **无条件** `collected = collector.collect(results_dir)`。
5. `report = reporter.generate(results_dir, enabled=req.allure)`（适配器内部隔离，**永不外抛**）。
6. 定状态：`proc.timed_out → TIMEOUT`；否则 `collected → COMPLETED` / `None → FAILED`。
7. 填 `summary`(若 collected)、`report`、`finished_at`；`save`。

**错误模型（Area A 拍板）**
- 超时**标志式**：`ProcessResult.timed_out=True`（不抛异常）；`exec 不存在/无权限`才抛。
- `execute()` **顶层 `try/except Exception`**：任何意外 → 置 `FAILED`、`error=repr(exc)+traceback 尾部截断(~2000 字符)`、
  `finished_at`，**尽力落库**（二次失败只记日志），保证**绝不卡在 RUNNING**。
- `error` 字段：简讯 + stderr 尾部截断；脱敏后续再做。

## API（薄层）

- `POST /runs` → **202** + `RunResponse`(QUEUED, 含 id)；body=`RunRequest`。客户端轮询。
- `GET /runs` → 列表，按 `created_at` **倒序**，不分页。
- `GET /runs/{id}` → `RunResponse`；不存在 → 404。
- `GET /runs/{id}/report` → **单个** `FileResponse(report.allure_report_file)`；无报告/未生成 → 404。
  （单文件后**不再需要** `/report/{path}` 资源路由与穿越防护。）

`app.py`：`create_app(container=None)` + lifespan 初始化 sqlite（建表 + `PRAGMA journal_mode=WAL` +
`busy_timeout=5000`）；`deps.py` 的 `Container` 装配真实适配器，测试注入 fake 容器。

## 持久化（SQLite · Area B 拍板）

- **单一共享 aiosqlite 连接** + **WAL** + `busy_timeout=5000`：连接内串行化，避免自并发写互撞；
  单行小事务（upsert）；**不需要** `WHERE status=expected` 条件更新（单一 owner）。
- 单表 `runs(id PK, status, runner, tests_path, args_json, allure_enabled, timeout,`
  `summary JSON, report JSON, exit_code, error, created_at, started_at, finished_at)`；时间存 ISO 字符串。

## Allure（Area C 拍板）

- 命令：`{allure_bin} generate <results_dir>/allure-results --single-file --clean -o <results_dir>/allure-report`
  → 产 `<results_dir>/allure-report/index.html`（自包含）。
- `should_generate`：`allure-results` 非空才跑；空则跳过（`html_generated=False`）。
- exit 127（缺 CLI）/ 其它非 0 → 记 warning、`html_generated=False`、**不拖垮 run**。

## TDD 与 100% 覆盖（Area/Q7 拍板：双 lane）

- **默认 `uv run pytest`** = 单测 + 适配器测试，确定性、always-available 资源，跑 `--cov=qarunner
  --cov-branch --cov-fail-under=100 --cov-report=term-missing`；`asyncio_mode="auto"`。
- **`uv run pytest -m e2e`** = 真 pytest(+真 allure) 冒烟，单独 marker、**不计入覆盖门禁**、缺 allure 则 skip。
- 子进程适配器测试用 **`sys.executable -c "..."`**（可移植、确定）覆盖 成功/非零/超时→kill/exec 不存在。
- `coverage.exclude_also`：`if TYPE_CHECKING:`、`@abstractmethod`、Protocol 的 `...` 省略体；
  `app.py` 的 `if __name__=="__main__"` 用 `# pragma: no cover`。
- 核心层（orchestrator/runners/junit/allure/paths）全用 fake 端口测，零文件系统，确定性 100%。

## 小约定

- `pass_rate = passed/total`（total 含 skipped+error），对齐参考仓库；另暴露派生 `passed` 布尔。
- 超时：config `default_timeout_seconds=1800` + `RunRequest.timeout` 可选覆盖；到点 SIGKILL → TIMEOUT。
- junit 状态映射：`<failure>`→failed、`<error>`→error、`<skipped>`(含 xfail)→skipped、其余→passed。
- 日志：stdlib `logging`（v1 不上 structlog）。

## 复用参考仓库的设计点（重写、不直接 import）

| v1 模块 | 参考来源 |
|---|---|
| pytest 命令构造（`--junitxml`/`--alluredir`/args） | `plugins/builtin/pytest_runner.py` `_build_command` |
| allure 生成命令（加 `--single-file`） | `engine/executor_specs.py` `allure_report_command()` |
| junit 解析 / pass_rate 算法 | `plugins/builtin/junit_collector.py`、`engine/executor_results.py` `build_results_summary` |
| 路径防穿越 `safe_subpath` | `plugins/builtin/_paths.py` 的 `safe_workspace_*` |
| Runner 插件接口 + registry | `plugins/registry.py` |
| 顶层 catch-all 保证终态 | `worker/tasks.py`(execute→except→fail→finally) |
| 终态语义（我们**有意分化** exit≠0） | `engine/executor_terminal.py`（参考用朴素 exit≠0→FAILED，我们改为执行语义） |

## 依赖（pyproject）

- runtime：`fastapi`、`uvicorn[standard]`、`pydantic>=2`、`pydantic-settings`、`aiosqlite`、**`allure-pytest`**（运行时跑外部测试需要）
- dev/test：`pytest`、`pytest-asyncio`、`pytest-cov`、`httpx`、`ruff`
- 系统依赖（非 pip）：`allure` commandline（Java），仅 `--single-file` HTML 用；缺失 → `html_generated=False`，e2e skip。

## 验证

1. `uv run pytest` → 全绿且**覆盖率 100%**（门禁失败即红）。
2. `uv run ruff check`。
3. e2e（手动）：`external_tests/`/`examples/sample_tests/` 放含通过+失败用例的最小 pytest 工程；
   `uv run uvicorn qarunner.api.app:app`；`POST /runs {"tests_path":"sample_tests"}` → 202+id；
   轮询 `GET /runs/{id}` 到 `COMPLETED`，看 `summary`/`passed`；浏览器开 `GET /runs/{id}/report`。
4. `uv run pytest -m e2e` → 真子进程实跑 pytest，断言 junit 解析/summary；allure HTML 段无 CLI 则 skip。

## 构建顺序（每步 红→绿→重构，始终保持 100%）

1. 脚手架（`pyproject.toml` + 覆盖门禁 + `.gitignore` + 空包）→
2. `models`/`errors` →
3. `ports`(7) + `tests/fakes` →
4. `core/paths` →
5. `core/runners`(base+pytest+registry) →
6. `core/junit`(解析) →
7. `core/allure`(`--single-file` 命令) →
8. `core/orchestrator`(create+execute+错误模型) →
9. `adapters/*`（subprocess / sqlite(WAL) / asyncio_scheduler / junit_collector / allure_cli_reporter / clock / ids）→
10. `api/*`（schemas/deps/routes/app）→
11. 接真实 `Container` →
12. `examples/sample_tests` + `-m e2e` 冒烟。
