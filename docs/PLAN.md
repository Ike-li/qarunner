# qa_platform_v2 后端实现计划 —— 执行外部测试并生成 Allure 报告（v1）

## Context

`qa_platform_v2` 目前是空仓库（仅 `.git`，0 commit）。要参照 `~/code/qa_platform`
重写一版**精简后端**。v1 只做一件事：**把外部测试代码拿进来执行 → 产出 allure-results +
生成 Allure HTML 报告**，并通过一个薄 HTTP API 触发/查询/查看。

硬性要求：**后端 only、TDD、单元测试 100% 覆盖**。为此采用**端口与适配器（hexagonal）**架构——
所有副作用（子进程、文件系统、时钟、ID、持久化）都抽象成端口（`typing.Protocol`），
核心编排逻辑用 fake 实现确定性测到 100%，薄适配器用真实资源做针对性测试。

已确认选型：
- 架构：**精简内核 + 薄 FastAPI**（不引入 Postgres/Redis/arq/S3/多租户）
- 执行隔离：**子进程 subprocess**（`asyncio.create_subprocess_exec`，挡在 `ProcessRunner` 端口后）
- 测试框架：**定义统一 Runner 插件接口，首版只实现 pytest**
- Allure：**两者都产**——`--alluredir` 出原始结果 + `allure generate` 出 HTML

工具链（沿用参考仓库、不必再问）：Python 3.12、uv、ruff、pytest + pytest-asyncio + pytest-cov、
持久化用 SQLite（aiosqlite）。包名 `qarunner`（与你选定的目录预览一致）。

---

## 范围（Scope）

**v1 要做：** 触发一次 pytest 测试运行 → 收集 junit 结果出摘要 → 产 allure-results + 生成 allure HTML
→ 持久化 run 记录 → API 查询 run 与在线查看报告。

**v1 明确不做（留接口/后续）：** 多租户/鉴权/RBAC、Docker 执行、git clone 拉代码（v1 用本地目录路径）、
Redis/SSE 实时日志、arq 异步队列、S3、通知、调度、分析/triage、quarantine、审计、报告分享 token。

外部测试代码的来源：**本地目录路径**（请求里给相对 `tests_root` 的路径，做路径逃逸校验）。

---

## 目录结构

```
qa_platform_v2/
  pyproject.toml          # uv + 依赖 + pytest/coverage 配置(fail_under=100, branch)
  README.md  .gitignore
  src/qarunner/
    config.py             # Settings(pydantic-settings): tests_root, artifacts_root, allure_bin, db_path
    models.py             # Run, RunStatus, RunRequest, TestCaseResult, TestSummary, ReportRef
    errors.py             # 领域异常: RunNotFound, UnknownRunner, UnsafePath, RunnerError
    ports/                # 全部副作用接口(Protocol)
      process.py          # ProcessRunner.run(cmd,cwd,env,timeout) -> ProcessResult
      clock.py            # Clock.now() -> datetime
      ids.py              # IdGenerator.new_id() -> str
      store.py            # RunStore.save/get/list
    core/
      runners/
        base.py           # Runner 协议: name; build_command(BuildContext) -> list[str]
        pytest_runner.py  # PytestRunner: python -m pytest --junitxml=.. --alluredir=.. <paths>
        registry.py       # RunnerRegistry: register/get(name) —— 插件接口
      allure.py           # build_generate_command(results,report); should_generate(results_dir)
      results.py          # parse_junit_xml(path|content) -> (TestSummary, list[TestCaseResult])
      paths.py            # safe_subpath(root, rel) 防逃逸(参考 safe_workspace_*)
      orchestrator.py     # RunOrchestrator.execute(req) -> Run  (核心工作流)
    adapters/
      subprocess_runner.py # 真 ProcessRunner(asyncio.create_subprocess_exec, 超时 kill)
      system_clock.py      # 真 Clock
      uuid_ids.py          # 真 IdGenerator(uuid4)
      sqlite_store.py      # 真 RunStore(aiosqlite)
    api/
      schemas.py           # 请求/响应 DTO
      deps.py              # Container: 组装 orchestrator + store
      routes.py            # POST /runs, GET /runs, GET /runs/{id}, GET /runs/{id}/report[/{path}]
      app.py               # create_app() 工厂 + lifespan(初始化 db)
  examples/sample_tests/   # 一个最小 pytest 样例工程, 供 e2e 验证
  tests/
    fakes/                 # FakeProcessRunner / FakeClock / FakeIdGenerator / InMemoryRunStore
    fixtures/              # 样例 junit.xml、allure-results 目录
    unit/{core,adapters,api}/...
```

---

## 核心数据模型（`models.py`）

- `RunStatus`(Enum): `PENDING / RUNNING / COMPLETED / FAILED / TIMEOUT`
  （run 生命周期；测试本身通过/失败体现在 summary，测试有 fail 但跑完了仍算 `COMPLETED`）
- `RunRequest`: `tests_path:str`, `runner:str="pytest"`, `args:list[str]=[]`, `allure:bool=True`
- `TestCaseResult`: `suite, name, status(passed/failed/skipped/error), duration_ms, message|None`
- `TestSummary`: `total, passed, failed, skipped, errors, duration_ms, pass_rate`
- `ReportRef`: `allure_results_dir, allure_report_dir|None, html_generated:bool`
- `Run`: `id, status, runner, tests_path, summary|None, report|None, exit_code|None, error|None,`
  `created_at, started_at|None, finished_at|None`

用 Pydantic（frozen）建模，便于序列化与 API 复用。

---

## 端口与适配器

| 端口 (Protocol) | 方法 | 真实适配器 | 测试用 fake |
|---|---|---|---|
| `ProcessRunner` | `run(cmd, cwd, env, timeout) -> ProcessResult(exit_code,stdout,stderr,duration_ms)` | `subprocess_runner.py` | `FakeProcessRunner`(按命令脚本化返回, 记录调用) |
| `Clock` | `now()` | `system_clock.py` | `FakeClock`(可步进) |
| `IdGenerator` | `new_id()` | `uuid_ids.py` | `FakeIdGenerator`(确定序列) |
| `RunStore` | `save(run)/get(id)/list()` | `sqlite_store.py` | `InMemoryRunStore` |

文件系统副作用很少（建结果目录、读 junit、列 allure-results），直接用 `pathlib` + 测试里的 `tmp_path`，
不单独抽端口，避免过度设计。

---

## 核心执行流程（`orchestrator.execute(req)`）

1. 校验 `runner` 存在（`registry.get` 否则 `UnknownRunner`）；`safe_subpath(tests_root, req.tests_path)`
   解析并防逃逸（否则 `UnsafePath`）。
2. `id=ids.new_id()`、`created_at=started_at=clock.now()`；建 `Run(status=RUNNING)` 并 `store.save`。
3. 准备每次运行的输出目录 `artifacts_root/<id>/results/`（含 `junit.xml`、`allure-results/`）。
4. `cmd = runner.build_command(ctx)`，其中 ctx 带 junit 路径、allure 开关与 `allure-results` 路径、
   `args`、测试路径。pytest 形如：
   `python -m pytest --junitxml=<out>/junit.xml --alluredir=<out>/allure-results <tests_path>`
   （照搬参考 `pytest_runner.py` 的 flag 方式）。
5. `proc = await process.run(cmd, cwd=tests_path, timeout=...)`。超时 → `status=TIMEOUT`。
6. 解析 `junit.xml` → `TestSummary` + cases（`results.parse_junit_xml`，照搬参考 `junit_collector`）。
7. 若 `allure` 且 `allure.should_generate(results_dir)`（目录非空）：
   `allure generate <results> -o <report> --clean`（照搬参考 `allure_report_command()`）。
   exit==127 → allure CLI 缺失 → 记 warning、`html_generated=False`、不报错；exit==0 → `html_generated=True`。
8. 定终态：能跑完并收集到结果 → `COMPLETED`；进程无法启动/收集失败 → `FAILED`（带 `error`）。
   填 `summary/report/exit_code/finished_at`，`store.save`，返回 `Run`。

v1 **同步 await** 执行（POST 内直接跑完返回）。异步后台执行留作后续（设计上 orchestrator 不依赖请求上下文，便于以后搬到 BackgroundTask/队列）。

---

## API（薄层，`routes.py`）

- `POST /runs` —— body=`RunRequest` → 执行并返回完成的 `Run`（含 summary、report 链接）
- `GET /runs` —— 列出 runs
- `GET /runs/{id}` —— run 详情；不存在 → 404
- `GET /runs/{id}/report` 与 `GET /runs/{id}/report/{path:path}` —— 用 `FileResponse` 直接服务本地
  `allure-report/`（index 与 assets）；报告未生成 → 404/409。无 S3、无 JWT 预览 token（v1 从简）。

`app.py` 提供 `create_app(container=None)` 工厂 + lifespan 初始化 sqlite；`deps.py` 的 `Container`
按配置装配真实适配器，测试时注入 fake 容器。

---

## TDD 与 100% 覆盖策略（关键）

- **覆盖配置**（pyproject）：`branch=true`、`--cov=qarunner --cov-fail-under=100 --cov-report=term-missing`；
  `asyncio_mode="auto"`。`exclude_also` 排除 `if TYPE_CHECKING:`、`@abstractmethod`、Protocol 的 `...` 省略体、
  以及 `app.py` 里 `if __name__=="__main__"` 的 uvicorn 入口（`# pragma: no cover`）。
- **核心层**（runners/allure/results/paths/orchestrator）：全部用 fake 端口测，确定性覆盖 100%，
  包含各分支（allure 关/开、results 空/非空、CLI 缺失 exit 127、超时、未知 runner、路径逃逸、junit 各状态）。
- **适配器层**用真实资源做隔离测试：
  - `subprocess_runner`：跑 `true`/`false`（成功/非零）、`sleep`（触发超时→kill）、不存在的命令（FileNotFoundError）。
  - `sqlite_store`：`:memory:`/`tmp_path`，覆盖 save/get/list/未命中。
  - `system_clock`/`uuid_ids`：平凡断言。
- **API 层**：`httpx.AsyncClient`+`ASGITransport`，注入 fake 容器，覆盖全部路由与错误路径（404/409/422）。
- **构建顺序（每步红→绿→重构，始终保持 100%）**：
  1. 脚手架 + 覆盖门禁 →
  2. `models`/`errors` →
  3. `ports` + `tests/fakes` →
  4. `core/paths` →
  5. `core/runners`(base+pytest+registry) →
  6. `core/results`(junit 解析) →
  7. `core/allure` →
  8. `core/orchestrator` →
  9. `adapters/*` →
  10. `api/*` →
  11. 接真实 `Container` + e2e 样例。

---

## 复用参考仓库的设计点（重写、不直接 import）

| v1 模块 | 参考来源 |
|---|---|
| pytest 命令构造（`--junitxml`/`--alluredir`/extra args/test paths） | `plugins/builtin/pytest_runner.py` `_build_command` |
| allure 生成命令 `allure generate .. -o .. --clean` | `engine/executor_specs.py` `allure_report_command()` |
| junit 解析 → 结果模型 | `plugins/builtin/junit_collector.py` |
| 路径防逃逸 `safe_subpath` | `plugins/builtin/_paths.py` 的 `safe_workspace_*` |
| Runner 插件接口 + registry | `plugins/registry.py` + builtin runner 接口 |
| run 生命周期/状态机思路 | `domain/services/execution.py` |

---

## 依赖（pyproject）

- runtime：`fastapi`、`uvicorn[standard]`、`pydantic>=2`、`pydantic-settings`、`aiosqlite`
- test/dev：`pytest`、`pytest-asyncio`、`pytest-cov`、`httpx`、`allure-pytest`（供样例产 allure-results）、`ruff`
- 系统依赖（非 pip）：`allure` commandline（Java），仅生成 HTML 用；缺失时降级为只产 allure-results，
  相关单测用 fake 覆盖、e2e 测试遇缺失则 skip。

---

## 验证（Verification）

1. **单测 + 覆盖**：`uv run pytest` → 全绿且**覆盖率 100%**（门禁 `--cov-fail-under=100`，未达直接失败）。
2. **静态检查**：`uv run ruff check`.
3. **端到端**（手动）：
   - `examples/sample_tests/` 放一个含通过/失败用例的最小 pytest 工程（装 `allure-pytest`）。
   - 起服务：`uv run uvicorn qarunner.api.app:app --reload`。
   - `curl -X POST localhost:8000/runs -d '{"tests_path":"examples/sample_tests"}'` → 返回 `Run`，`summary` 计数正确。
   - `GET /runs/{id}` 查状态/摘要；浏览器开 `GET /runs/{id}/report` 看 Allure HTML。
   - 若本机装了 `allure`(Java)，HTML 正常；否则 `report.html_generated=False` 但 `allure-results` 仍在。
4. **真实子进程冒烟**：一条 e2e 测试对 `examples/sample_tests` 实跑 pytest，断言 junit 解析与 summary 正确
   （allure HTML 段若无 CLI 则 skip）。

---

## 待你拍板后即可开工的第一步
脚手架（`pyproject.toml` + 覆盖门禁 + 空包结构 + 第一个失败测试），随后按上面构建顺序逐模块 TDD。
