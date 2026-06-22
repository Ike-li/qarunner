# QA Platform v2 — 全面修复计划（Remediation Plan）

> 生成日期：2026-06-22
> 依据：六维度并行代码审查（安全 / 后端架构 / 前端 / 测试 / 并发数据 / 部署）+ 实跑验证（pytest 覆盖率、ruff）
> 目标分支：master（HEAD `9b6fe6a`）；含未提交的前端大改与新增的 `Dockerfile.platform` / `docker-compose.yml`

---

## 0. 背景与现状

本平台 `qarunner` 接收用户请求后**执行外部 pytest 代码**（subprocess / Docker）、生成 Allure 报告并经 HTTP 提供。架构骨架有水准（六边形分层、`uv.lock`、多阶段构建、bcrypt），但执行层存在多条由**普通登录用户即可触发**的致命安全链，质量门禁形同虚设，并发数据层在真实负载下不安全。**当前不可上生产。**

### 维度健康度（越高越好）

| 维度 | 健康度 | 核心问题 |
|---|---|---|
| 🔒 安全 | 🔴 ~2/10（风险 9/10） | 普通用户可 RCE、零执行隔离、密钥硬编码、IDOR、无登录限流 |
| 🏗️ 后端架构 | 🟠 5.5/10 | `env` 死功能、路由过厚、core 被 FastAPI 污染、`Settings()` 满天飞 |
| 🎨 前端 | 🟠 4/10 | App.tsx 3703 行单组件、token 进 URL、SSE 每 1.5s 重连、零 a11y |
| 🧪 测试可信度 | 🟠 5/10 | 门禁默认不触发、真实 98.9% 已破线、大量"点亮行号"式假覆盖 |
| ⚙️ 并发/数据 | 🔴 3/10 | 单连接共享、后台任务可被 GC、崩溃无恢复、shutdown 不收尾 |
| 🚀 部署就绪 | 🔴 3/10 | 明文密钥入库、容器全 root、镜像绕过 lockfile、无健康检查/CI |

### 已用硬证据确认的客观事实

- `uv run pytest` → **255 passed，零覆盖率输出**：`pyproject` 未把 `--cov` 接进默认命令，门禁日常不触发。
- `uv run pytest --cov` → **TOTAL 98.90%，FAIL**，未提交新端点 `routes.py:411-429`（`get_report_assets`）零测试。
- `uv run ruff check .` → **195 errors**（141 可自动修）。
- 测试期 PyJWT 抛 `InsecureKeyLengthWarning: HMAC key is 20 bytes`。

### 修复原则

1. **先堵命门，后做美化**：P0 全是"不修就别上线"的安全 + 门禁项。
2. **每项有验收**：完成判据写死（命令 / 测试 / 行为），避免"改了但没生效"。
3. **不破坏既有未提交工作**：前端大改正在进行，重构 App.tsx（FE-1）安排在其落地后。
4. **跨维度共识优先**：被多个独立维度命中的问题（如 `env` 死功能）置顶，可信度最高。

### 阶段总览与执行顺序

```
P0 阻断（上线前必须）   ──►  P1 正确性   ──►  P2 架构/可维护性   ──►  P3 部署加固
  SEC-1..5, QG-1..2          DATA/FUNC/CONC      ARCH-*, FE-*           DEP-*, TEST-*
```

工作量记号：**S** <0.5d ｜ **M** 0.5–2d ｜ **L** 2–5d ｜ **XL** >5d

---

## P0 — 上线前阻断项（安全命门 + 质量门禁）

### SEC-1 🔴 堵死 RCE 注入面（任意 user → 宿主代码执行）
- **问题**：`selected_files` / `extra_args` 未经白名单直通 pytest argv，加上 pytest 收集阶段自动 import `conftest.py`，任意 user 角色即可执行任意代码。
- **位置**：`src/qarunner/core/orchestrator.py:82-90`、`src/qarunner/core/runners/pytest_runner.py:15-24`、`src/qarunner/api/routes.py:313-327`
- **改法**：
  1. `selected_files` 每项过 `safe_subpath(tests_root, f)` 并校验 `.py` 后缀；拒绝任何以 `-` 开头的项。
  2. `extra_args` 改严格白名单（仅 `-k EXPR` / `-x` / `--maxfail=N` / `-q`），或彻底移除；保留则对 `shlex.split` 结果逐项校验，禁 `-p/-c/--pyargs/--rootdir/--import-mode/-o`。
  3. pytest 调用统一加 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`-p no:cacheprovider`、`-o addopts=`；按需 `--noconftest`（若不信任被测目录 conftest）。
- **验收**：新增测试——构造 `extra_args="-p evil"` 与 `selected_files=["-x"]` / `"../etc"` 均被拒（400）；含 `conftest.py` 的恶意目录在启用 `PYTEST_DISABLE_PLUGIN_AUTOLOAD` 后不执行其顶层代码。
- **依赖**：无。**估时**：M

### SEC-2 🔴 密钥与默认凭据治理
- **问题**：JWT 密钥默认 `super-secret-dev-key`（HS256 对称，泄露即可伪造任意 admin）；默认 `admin/admin123`；`docker-compose.yml` 明文写死"生产"密钥与口令。
- **位置**：`src/qarunner/config.py:20,24`、`docker-compose.yml:14-15`、`src/qarunner/adapters/sqlite_store.py:130-136`
- **改法**：
  1. `config.py` 删除 `secret_key` / `admin_password` 默认值，设为必填（缺失 → pydantic `ValidationError` → 启动失败）。
  2. 启动校验：若 `secret_key` 等于任何已知占位串则拒绝启动；建议 `secrets.token_urlsafe(64)` 或从 secret manager 注入。
  3. `docker-compose.yml` 改 `${QARUNNER_SECRET_KEY:?must set}` 形式 + `.env.example`（占位符）。
  4. 首次登录强制改密，或检测到默认口令时拒绝对外监听。
- **验收**：缺 `QARUNNER_SECRET_KEY` 时进程拒绝启动并给出明确错误；compose 文件内无任何真实密钥；README 示例改占位符。
- **依赖**：无。**估时**：S–M

### SEC-3 🔴 执行环境真隔离
- **问题**：自称的 "Workspace Jail" 只是 `shutil.copytree`，子进程继承 `os.environ` 全量（含密钥）、无 RLIMIT、可联网；Docker 执行器 root + bridge 网络 + rw 挂宿主路径、无 `cap_drop`。
- **位置**：`src/qarunner/core/orchestrator.py:146-188`、`src/qarunner/adapters/subprocess_runner.py:37-48`、`src/qarunner/adapters/docker_runner.py:103-124`
- **改法**：
  1. Docker：`containers.run` 显式 `user="1000:1000"`、`network_mode="none"`、`cap_drop=["ALL"]`、`security_opt=["no-new-privileges"]`、`read_only=True`（结果目录单独可写卷）、`mem_limit`/`pids_limit`/`nano_cpus`；results 卷与 DB 路径分离。
  2. subprocess：用最小白名单环境（不传 `dict(os.environ)`），加 `preexec_fn` 设 `RLIMIT_CPU/AS/NPROC/FSIZE`，以低权用户运行、禁网；不可信场景下禁用 subprocess 模式。
  3. 平台机密移出进程环境（文件 + 受限读取），避免被子进程继承。
- **验收**：Docker run 的容器以非 root、无网络、cap 全 drop 启动（集成测试断言 `docker inspect`）；subprocess 子进程环境中不含 `QARUNNER_SECRET_KEY`。
- **依赖**：与 DEP-1 协同。**估时**：L

### SEC-4 🟠 对象级授权（IDOR）
- **问题**：run 相关端点只校验登录，不比对 `created_by`；`GET /runs` 把全部用户的 run 列给任意登录者，UUID 不再是秘密。
- **位置**：`src/qarunner/api/routes.py:330-338`（list）、`341-515`（get/report/assets/stream/lock）
- **改法**：取出 run 后校验 `run.created_by == current_user.username or role == admin`，否则 404/403；`GET /runs` 对非 admin 仅返回本人 run。
- **验收**：user A 无法 `GET /runs/{B 的 run}`（403/404）；`GET /runs` 非 admin 不含他人 run。
- **依赖**：无。**估时**：M

### SEC-5 🟠 登录暴力破解防护
- **问题**：`POST /auth/login` 无限流 / 无失败计数 / 无锁定 / 无审计。
- **位置**：`src/qarunner/api/routes.py:42-54`
- **改法**：接入 `slowapi` 或反代层限流（IP + 用户名）、连续失败指数退避锁定、记录失败审计（不记明文口令）。
- **验收**：N 次失败后该 IP/账号被临时拒绝；审计日志可见失败尝试。
- **依赖**：无。**估时**：M

### QG-1 🔴 修复质量门禁（让"门禁"名副其实）
- **问题**：`uv run pytest` 默认不触发覆盖率；真实 98.9% 已破线；新端点 `get_report_assets` 零测试；越权路径（`test_routes.py` 全 override 认证）几乎盲区。
- **位置**：`pyproject.toml:37-42`（`[tool.pytest.ini_options]` 缺 `addopts`）、`src/qarunner/api/routes.py:403-429`、`tests/unit/api/test_routes.py:33-41`
- **改法**：
  1. `pyproject` 加 `addopts = "--cov=qarunner --cov-branch --cov-fail-under=100 --cov-report=term-missing -m 'not e2e'"`（e2e 不计门禁）。
  2. 补 `get_report_assets` 5 条分支测试，含真实遍历攻击向量（`/runs/r1/report/..%2f..%2fetc%2fpasswd`），断言 403 且不泄露内容；并把其防护逻辑改用 `safe_subpath`（与其他端点一致）。
  3. 新增一组**不 override 认证**的测试：无 token→401、普通用户打 `/runs/cleanup`/`/users`→403。
- **验收**：`uv run pytest` 默认即跑覆盖率且门禁生效；覆盖率回到 100%；越权测试通过。
- **依赖**：QG-2 先行（清 ruff 噪音）。**估时**：M

### QG-2 🟠 代码规范 + CI 接入
- **问题**：`ruff check` 195 错（141 可自动修）；无任何 CI。
- **位置**：仓库全局；缺失 `.github/workflows/`
- **改法**：
  1. `uv run ruff check --fix .` 清可修项，人工处理剩余（含 E501、空行空白、import 顺序）。
  2. 新增 `.github/workflows/ci.yml`：`uv sync --frozen --all-extras` → `ruff check` → `pytest`（含覆盖率门禁）→ `docker build` → 镜像扫描（trivy/grype）。
- **验收**：`ruff check .` 0 错；CI 在 PR 上自动跑并对 ruff/覆盖率失败亮红。
- **依赖**：无（应最先做，降低后续噪音）。**估时**：M

---

## P1 — 正确性（数据 / 功能 / 并发）

### DATA-1 🔴 SQLite 改每操作独立连接
- **问题**：全进程共享单条 `aiosqlite` 连接，`get`/`list` 在 execute 与 fetchone 间让出事件循环 → 并发交错脏读 / `ProgrammingError` / 状态回退。WAL 在单连接下是摆设。
- **位置**：`src/qarunner/adapters/sqlite_store.py:78,89` 及全部方法体
- **改法**：去掉 `self._db`，每方法 `async with aiosqlite.connect(path) as db:`，**每条连接**都 `PRAGMA busy_timeout=5000` + WAL；或保留单连接但用 `asyncio.Lock` 包住每个"execute→fetch→commit"全过程（次选）。
- **验收**：并发压测（≥8 并发混合读写）无 `ProgrammingError`、无脏读；现有测试全绿。
- **依赖**：无。**估时**：M

### DATA-2 🔴 后台任务强引用 + 异常可见
- **问题**：`asyncio.create_task` 返回值丢弃，task 可能被 GC 静默取消 → run 永卡 QUEUED 且无日志。
- **位置**：`src/qarunner/adapters/asyncio_scheduler.py:16-21`
- **改法**：`self._tasks: set` 持有强引用；`task.add_done_callback(self._tasks.discard)`；callback 内 `task.exception()` 记日志。
- **验收**：高并发触发大量 run 无卡 QUEUED；task 异常被记录。
- **依赖**：无（与 DATA-4 协同）。**估时**：S

### DATA-3 🔴 崩溃恢复（孤儿 run 扫描）
- **问题**：进程重启后 RUNNING/QUEUED 的 run 永不恢复，成僵尸，cleanup 也不回收。
- **位置**：`src/qarunner/api/app.py`（lifespan）、`src/qarunner/adapters/sqlite_store.py`
- **改法**：startup（`store.initialize()` 后、`start_scheduler` 前）调用新方法 `mark_interrupted_runs()`：`UPDATE ... SET status='failed', error='interrupted by restart', finished_at=now WHERE status IN ('queued','running')`。
- **验收**：杀进程后重启，原 RUNNING run 变 FAILED；前端不再无限转圈。
- **依赖**：无。**估时**：S

### DATA-4 🟠 shutdown 优雅收尾
- **问题**：shutdown 不等待在途 `execute` task，`store.close()` 后在途任务再 `save` 会撞已关闭连接 / AssertionError。
- **位置**：`src/qarunner/api/app.py:24-27`
- **改法**：配合 DATA-2 的 task 集合，shutdown 先 `await asyncio.gather(*tasks, return_exceptions=True)`（可加超时）再关 store。
- **验收**：SIGTERM 时在途 run 落终态或被干净取消，无 AssertionError 日志。
- **依赖**：DATA-2。**估时**：S

### FUNC-1 🔴 修复 `env` 死功能（4 维度共识）
- **问题**：`env` 被 API 接收并持久化，但 orchestrator/scheduler 执行时从不传给子进程 → 用户配的环境变量被静默丢弃（契约撒谎）。
- **位置**：`src/qarunner/core/orchestrator.py:92-103`（create 未设 `env`）、`:189-209`（execute 未传 `env`）、`src/qarunner/core/scheduler.py:37-47`（映射未带 `env`）
- **改法**：`create()` 的 `Run(...)` 加 `env=req.env`；`execute()` 的 `runner.run(...)` 加 `env=run.env`；scheduler 的 `RunRequest(...)` 加 `env=profile.env`。**注意**：与 SEC-3 协同，env 需白名单键名（拒 `LD_*`/`PYTHON*`/`PATH`/`BASH_ENV`），并显式构造最小环境而非叠加 `os.environ`。
- **验收**：集成测试断言子进程实际收到自定义 `env`；危险键被拒。
- **依赖**：SEC-3（环境收窄）。**估时**：M

### CONC-1 🟠 SSE 修复（断开检测 + 非阻塞）
- **问题**：同步 `open()`/`readline()` 阻塞事件循环；不检测客户端断开 → 遗弃连接常驻轮询 DB + 持文件句柄；整文件读入内存 DoS。
- **位置**：`src/qarunner/api/routes.py:451-486`、`368-377`、`482`
- **改法**：循环加 `if await request.is_disconnected(): break`；文件读改 `await asyncio.to_thread(...)` 或 `aiofiles`；日志只取尾部 N KB；加最大时长上限；并发 SSE 限流。
- **验收**：关闭客户端后协程及时退出（无残留句柄）；GB 级日志不撑爆内存。
- **依赖**：DATA-1。**估时**：M

### CONC-2 🟠 调度器多副本去重
- **问题**：in-process `AsyncIOScheduler` 在 `--workers>1` / 多副本下每个 cron 点触发 N 次；`trigger_schedule_run` 无去重；多进程空库首启的 `ALTER`/seed 竞态会启动失败。
- **位置**：`src/qarunner/core/scheduler.py:54-67,107-131`、`src/qarunner/adapters/sqlite_store.py:80-136`
- **改法**：触发时 DB 条件更新做 leader 去重：`UPDATE test_schedules SET last_run_at=? WHERE id=? AND (last_run_at IS NULL OR last_run_at < ?)`，靠 `rowcount==1` 决定是否执行；或将调度拆为独立单实例进程。seed 改 `INSERT OR IGNORE`，`ALTER` 包 `OperationalError` 容错。
- **验收**：模拟双 worker 同一 cron 点仅一个 run 被创建；空库双进程首启不崩。
- **依赖**：DATA-1。**估时**：M
- ⚠️ **关联缺陷（DATA-3 已落地代码引入，self-review 发现）**：启动时的孤儿恢复 `SqliteStore.mark_interrupted_runs()`（由 `api/app.py` lifespan 调用）无条件把所有 `QUEUED/RUNNING` 置 FAILED，**隐含"单实例"假设**。多 worker 下，后启动的 worker 会把其它 worker 此刻正在执行的 RUNNING run 误杀为 FAILED。当前单 worker 部署不触发；放开多 worker 前必须连同本项一并解决——要么把恢复/调度限定在单实例进程，要么按 `started_at` + 进程心跳/租约只回收真正的孤儿，而非全量。

### SEC-6 🟠 token 不再进 URL
- **问题**：`?token=<JWT>` 进访问日志 / 历史 / Referer；前端 SSE/iframe/下载链接都拼了长效 JWT。
- **位置**：`src/qarunner/api/deps.py:78-79`、`frontend/src/App.tsx:1349,2690,2711`
- **改法**：报告/SSE/下载改用短时效一次性 ticket（绑 run_id + 短 exp），或认证走 HttpOnly+SameSite cookie（EventSource/iframe 自动带 cookie，无需 query token）。
- **验收**：URL 中不再出现长效 JWT；下载/串流仍可用。
- **依赖**：与 FE-2 协同。**估时**：M

### SEC-7 🟡 junit XML 加固
- **问题**：`ET.parse` 解析攻击者可影响的 junit.xml，存在 entity-expansion DoS 面。
- **位置**：`src/qarunner/core/junit.py:21`
- **改法**：改用 `defusedxml.ElementTree.parse`，并限制文件大小。
- **验收**：恶意 entity-expansion XML 被安全拒绝。**依赖**：无。**估时**：S

### SEC-8 🟡 日志/报告 DoS 防护
- **问题**：`get_run` 与 subprocess 结束后把 stdout/stderr 整文件读入内存。
- **位置**：`src/qarunner/api/routes.py:368-377`、`src/qarunner/adapters/subprocess_runner.py:113-118`
- **改法**：日志读取分页/截断（尾部 N KB）；`ProcessResult` 不回灌全文件；单 run 输出设上限，超限中止。
- **验收**：单 run 巨量输出不致 OOM。**依赖**：无。**估时**：S

---

## P2 — 架构与可维护性

### ARCH-1 🔴 调度 port 化（去除 core 对 FastAPI 的依赖）
- **问题**：`core/scheduler.py` 直接 `import FastAPI` 并读写 `app.state`，core 被框架污染；路由绕过 orchestrator 直接驱动调度，形成环状耦合。
- **位置**：`src/qarunner/api/routes.py:632-633,720-724,742-743`、`src/qarunner/core/scheduler.py`
- **改法**：抽象 `SchedulePort.upsert(schedule)/remove(id)`，APScheduler adapter 实现并注入 container；路由只调 `container.scheduler.upsert(...)`，不碰 `app.state`、不 import core。
- **验收**：`core/` 内无 `import fastapi`；路由无 `from qarunner.core.scheduler import`。
- **依赖**：无。**估时**：L

### ARCH-2 🟠 路由瘦身（引入 service 层）
- **问题**：`create_schedule`/`update_schedule` 各塞 7 件事且逐行重复；`create_profile` 在路由里拼领域对象。
- **位置**：`src/qarunner/api/routes.py:226-253,579-635,664-726`
- **改法**：新增 `ScheduleService`/`ProfileService`（或扩展 orchestrator），收纳校验+计算+构造+持久化；路由瘦身到 try/调用/return（向 `create_run` 看齐）。
- **验收**：schedule/profile 路由函数 ≤ 15 行；create/update 复用同一 service 逻辑。
- **依赖**：ARCH-1、ARCH-3。**估时**：L

### ARCH-3 🟠 cron 逻辑抽取
- **问题**：`zoneinfo + croniter.is_valid + get_next` 重复 5 处，routes 与 scheduler 各算各的。
- **位置**：`src/qarunner/api/routes.py:560-574,597-614,687-703`、`src/qarunner/core/scheduler.py:58-63,118-125`
- **改法**：新建 `core/cron.py`：`validate_cron(expr)`、`compute_next_runs(expr, tz, n)`；所有调用点统一走它。`next_run_at` 展示值统一用 APScheduler `job.next_run_time` 作唯一真相，避免与 CronTrigger 语义漂移。
- **验收**：cron 逻辑单点定义并有单测；预览与实际触发一致。
- **依赖**：无。**估时**：M

### ARCH-4 🟠 Settings 进 DI 容器
- **问题**：`Settings()` 每请求重新实例化、重读 env；auth 每次签发/校验都 new。
- **位置**：`src/qarunner/api/routes.py:120,139,189,354,448,531`、`src/qarunner/core/auth.py:33,46`
- **改法**：`settings` 存入 `Container`（`create_container` 已有 `cfg`），路由用 `container.settings`；auth 改为接收注入的 settings；或给 `Settings` 加 `@lru_cache` 工厂。
- **验收**：路由层无 `Settings()` 即席调用；测试可经 container 注入配置。
- **依赖**：无。**估时**：M

### ARCH-5 🟠 收口 store port
- **问题**：`Container.store` 标注具体类 `SqliteStore`，路由耦合到 port 之外的方法（user/profile/schedule/lock/cleanup）；`RunStore` port 形同虚设。
- **位置**：`src/qarunner/api/deps.py:32-33`、`src/qarunner/ports/store.py`
- **改法**：扩充 `RunStore` port 覆盖路由真实用到的方法，或拆为 `UserStore`/`ProfileStore`/`ScheduleStore`；`Container.store` 标 port 类型。
- **验收**：`Container` 字段全为 port 类型；fakes 实现完整 port。
- **依赖**：无。**估时**：M

### ARCH-6 🟡 收窄裸 except
- **问题**：11+ 处 `except Exception: pass` 吞 IO/解析/逻辑错误，连日志都没有。
- **位置**：`src/qarunner/api/routes.py:175,220,371,376,540,613,702`、`src/qarunner/core/scheduler.py:126`、`src/qarunner/adapters/docker_runner.py:146,173,201`
- **改法**：收窄到具体异常（`OSError`/`UnicodeDecodeError`/`ET.ParseError`/`JobLookupError`），至少 `logger.warning(..., exc_info=True)`。
- **验收**：无裸 `except ...: pass`；异常路径有日志。
- **依赖**：无。**估时**：M

### ARCH-7 🟡 `assert` → 显式校验
- **问题**：`assert self._db is not None` 在 `-O` 下被剥离（~20 处）。
- **位置**：`src/qarunner/adapters/sqlite_store.py` 全文
- **改法**：随 DATA-1 改为每操作连接后此问题自然消除；若保留单连接则改 property `_conn` 统一守卫 + 显式 `raise RuntimeError`。
- **验收**：`python -O` 下连接未初始化给明确错误。**依赖**：DATA-1。**估时**：S

### ARCH-8 🟡 schema 迁移版本化
- **问题**：`initialize()` 里运行时累积 `ALTER TABLE`，无版本号、非并发安全。
- **位置**：`src/qarunner/adapters/sqlite_store.py:80-136`
- **改法**：用 `PRAGMA user_version` + 版本化迁移列表（或 alembic），迁移只跑一次且单进程。
- **验收**：迁移幂等、有版本记录；多进程首启不撞。**依赖**：CONC-2。**估时**：M

### FE-1 🔴 拆分 App.tsx（3703 行 god component）
- **问题**：单组件 69 个 useState、16 个 useEffect、21 处 fetch、13 个功能域，已严重阻碍维护与协作。
- **位置**：`frontend/src/App.tsx`
- **改法**：先抽零风险部分——`translations`→`src/i18n.ts`、类型→`src/types.ts`、`apiFetch`→`src/hooks/useApi.ts`、`useAuth`；再按域拆 `LoginScreen`/`RunDrawer`/`TerminalConsole`/`TriggerModal`/`UserModal`/`ScheduleModal`/`SuiteTree`；App.tsx 压到 <300 行布局层。CSS 随组件 co-locate。
- **验收**：App.tsx <300 行；E2E 全绿；无功能回归。
- **依赖**：**在当前未提交前端改动落地后再做**（避免冲突）；FE-2/FE-3 先行。**估时**：XL

### FE-2 🔴 统一 apiFetch 封装
- **问题**：21 处裸 fetch、16 处复制粘贴的 401 处理，cleanup 漏了 401 分支；token 存 localStorage。
- **位置**：`frontend/src/App.tsx`（全局 fetch）、`:420`（localStorage）、`:3654-3681`
- **改法**：`apiFetch(path, opts)` 统一附 token、`401→logout()`、统一 `throw ApiError(detail)`；配合 SEC-6 迁移 token 到 HttpOnly cookie。
- **验收**：所有请求走 apiFetch；401 行为一致；约 200 行重复消除。
- **依赖**：SEC-6。**估时**：M

### FE-3 🟠 补类型（去 any）+ 修 SSE 重连
- **问题**：9 处 `: any` 掏空 profile/schedule/tree 类型；SSE effect 依赖含高频 `runs` 导致每 1.5s 重连、日志闪烁。
- **位置**：`frontend/src/App.tsx:450-451,494,1570-1651`（any）、`:1328-1367`（SSE）
- **改法**：补 `Profile`/`Schedule`/`TreeNode` 接口删 any；SSE effect 依赖收敛到 `[token, selectedRunId]`，用 ref 判活跃态；`onerror` 不直接驱动改依赖项的 state，重连加退避。
- **验收**：`tsc` 无 any 警告；SSE 活跃期不再周期性重连。
- **依赖**：无。**估时**：M

### FE-4 🟡 修 formatLogLine 优先级 bug + 纯函数单测
- **问题**：`||` 与 `&&` 缺括号致含 `PASSED` 的行误判颜色。
- **位置**：`frontend/src/App.tsx:1764`
- **改法**：补括号 `(includes('PASSED')||includes('passed')) && (...)`；为 `formatLogLine`/`matchesLogLevel`/`formatDuration` 加 Vitest 单测。
- **验收**：失败汇总行不误标绿；单测覆盖。**依赖**：无。**估时**：S

### FE-5 🟡 可访问性
- **问题**：aria/role/htmlFor 全 0；可点击 `<div>` 键盘不可达；modal 无 `role=dialog`/Esc/焦点陷阱。
- **位置**：`frontend/src/App.tsx`（全局）
- **改法**：label/input 加 `htmlFor`+`id`；可点击 div 改 `<button>` 或加 role+tabIndex+onKeyDown；modal 加 `role="dialog" aria-modal`+Esc+focus trap。关键交互加 `data-testid` 供 E2E。
- **验收**：键盘可完整操作主流程；E2E 改用 `getByTestId`。
- **依赖**：FE-1（拆分后更易做）。**估时**：M

---

## P3 — 部署加固 + 测试增强

### DEP-1 🟠 Dockerfile 非 root
- **位置**：`Dockerfile`、`Dockerfile.platform`（均无 `USER`）
- **改法**：两镜像加 `useradd -r app` + `USER app`；executor 镜像配合 `--read-only`/`--cap-drop ALL`。
- **验收**：容器内 `whoami` 非 root。**依赖**：SEC-3。**估时**：S

### DEP-2 🔴 镜像走 uv.lock frozen
- **问题**：`Dockerfile.platform` 用 `pip install .` 解析裸版本，绕过 `uv.lock`，依赖不可复现。
- **位置**：`Dockerfile.platform:28,32`、`pyproject.toml:10-22`
- **改法**：`COPY pyproject.toml uv.lock ./` + `uv sync --frozen --no-dev`；给关键依赖补上下界（`fastapi>=0.115,<1`、`pydantic>=2,<3`、`docker>=7,<8`、`pyjwt>=2.8`、`bcrypt>=4.1`）。
- **验收**：同 commit 重复构建依赖集一致。**依赖**：无。**估时**：S

### DEP-3 🟠 新增 .dockerignore
- **问题**：无 `.dockerignore`，`.venv`/`artifacts`(含 DB)/`.env`/`node_modules` 可能进构建上下文与镜像层。
- **位置**：缺失 `/.dockerignore`
- **改法**：新增，排除 `.git .venv node_modules frontend/node_modules frontend/dist artifacts external_tests *.db* .coverage .pytest_cache .ruff_cache __pycache__ .env .env.* .claude .codegraph .reasonix`。
- **验收**：构建上下文不含上述目录。**依赖**：无。**估时**：S

### DEP-4 🟠 健康检查 + compose 加固
- **问题**：无 `/health` 端点；compose 无 healthcheck/restart/资源限制。
- **位置**：`src/qarunner/api/routes.py`、`docker-compose.yml`
- **改法**：加 `@router.get("/health")`（就绪探针额外校验 DB）；compose 补 `restart: unless-stopped`、`healthcheck`、`deploy.resources.limits`（如 cpus 2.0 / mem 2G）。
- **验收**：编排器可探活；进程崩溃自愈；单测试不拖垮整机。**依赖**：无。**估时**：S

### DEP-5 🟡 executor 镜像依赖 pin + 预构建
- **问题**：base `Dockerfile` 的 pytest 等 4 工具未 pin；`_ensure_image` 运行时自动 build 致生产期漂移。
- **位置**：`Dockerfile:6-10`、`src/qarunner/adapters/docker_runner.py:35-46`
- **改法**：pin 精确版本；executor 镜像 CI 预构建打 tag 推 registry，生产禁用运行时 build。
- **验收**：executor 镜像版本固定且预存在。**依赖**：QG-2(CI)。**估时**：M

### DEP-6 🟡 README 生产部署章节
- **改法**：补"生产部署（Docker Compose）"：必设环境变量清单（标注密钥必填）、构建/启动、健康检查路径、"前后端同源部署"约束；修正 `tests_root` 尾斜杠不一致；更新 API 表（实际端点远多于现文档）。
- **验收**：照 README 可完成一次生产部署。**依赖**：SEC-2、DEP-4。**估时**：S

### TEST-1 🟠 真实集成 e2e（docker / allure）
- **问题**：DockerRunner "100% 覆盖"全是 mock 自证；e2e 不覆盖招牌的 allure 报告生成。
- **位置**：`tests/unit/adapters/test_docker_runner.py`、`tests/e2e/test_smoke.py`
- **改法**：加 `@pytest.mark.docker` 真实 docker 集成测试（卷挂载+退出码+日志落盘）；e2e 经 `RunOrchestrator.execute` 跑完整生命周期含 `AllureCliReporter`，断言 `html_generated` 且 `index.html` 存在。
- **验收**：CI 可选跑真实 docker/allure 链路并通过。**依赖**：SEC-3。**估时**：M

### TEST-2 🟠 断言升级（消除假覆盖）
- **问题**：`env` 三层零断言、`schedules/preview` 只数个数、SSE 只查子串、异常分支只验"没崩"。
- **位置**：`tests/unit/api/test_routes.py`、`test_schemas.py`
- **改法**：profile create/update 传 `env` 并断言原样返回；`preview` 断言时间严格递增、间隔正确、含 DST 时区用例；SSE 断言完整顺序而非子串；去掉对 `call_count` 的实现耦合。
- **验收**：把上述代码逻辑故意改错后对应测试转红。**依赖**：FUNC-1。**估时**：M

---

## 验收门禁（全部完成后应满足）

- [ ] 安全：普通 user 无法注入 pytest argv / 执行 conftest；执行容器非 root+无网+cap drop；缺密钥拒启动；无默认弱口令；run 端点有对象级授权；登录有限流。
- [ ] 质量：`uv run pytest` 默认跑覆盖率且 100% 门禁生效；`ruff check .` 0 错；CI 在 PR 上守门。
- [ ] 正确性：8+ 并发混合读写无 SQLite 错；重启后无僵尸 run；`env` 真实下发；SSE 客户端断开即释放。
- [ ] 架构：`core/` 无 `import fastapi`；schedule/profile 路由 ≤15 行；cron 逻辑单点；`Container` 全 port 类型。
- [ ] 前端：App.tsx <300 行；统一 apiFetch；无 any；SSE 不周期重连；主流程键盘可达。
- [ ] 部署：镜像非 root + 走 `uv.lock`；有 `.dockerignore`/健康检查/资源限制；README 生产章节可复现。

## 风险与排期建议

- **P0 必须在任何对外暴露前完成**（SEC-1/2/3 是命门，QG-1/2 让回归可被发现）。粗估 P0 合计约 1.5–2.5 周（单人）。
- P1 紧随其后（数据安全 + 功能正确），约 1–2 周。
- P2/P3 可并行推进，FE-1（App.tsx 拆分）建议在当前未提交前端改动落地后单独成一个迭代。
- 跨维度共识项（FUNC-1 `env`、SEC-6 token、ARCH-1 调度污染）优先级高于其严重度标记，因影响面经多维度独立确认。
