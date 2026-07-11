# qarunner 功能总览（FEATURES）

> 从**能力视角**俯瞰整个 qarunner:它能做什么、边界在哪。所有功能均经源码核对。
>
> 与其他文档分工(各看一面、避免重复):
> - **本文 `FEATURES.md`** — qarunner **能做什么**(能力全景)
> - [`API_REFERENCE.md`](API_REFERENCE.md) — 接口**怎么调**(端点契约、字段、状态码)
> - [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — 系统**怎么搭**(六边形分层、端口/适配器)
> - [`../README.md`](../README.md) — 项目**怎么跑**(快速开始、部署、配置)

## 一句话定位

> 产品方向(一句话 + 四支柱 + 边界)以 [`DIRECTION.md`](DIRECTION.md) 为**唯一权威源**;本文不再重复,只从**能力视角**展开它能做什么、边界在哪。

核心取舍:把每次运行的隔离做**重**(纵深防御),主要防的是**依赖供应链风险**——pytest 在 collection 阶段就 import 整棵依赖树,被投毒的传递依赖在「测试时」即执行任意代码;这同时补上了**自托管 CI runner 默认不隔离**这个公认软肋。代价是把外围(通知 / 导出 / 多租户)**留白**,换取部署简单、易于审计。

---

## 1. 测试代码接入（Suite 管理）

把测试代码变成 qarunner 可运行的「套件」,四种来源 / 操作:

- **Link 本地目录** — 软链宿主上现有的项目目录为套件。
- **Clone Git 仓库** — 浅克隆(`--depth 1`)指定分支 / ref;**私有仓库**可绑定凭证认证。
- **Pull 更新** — 对 git 套件拉取最新(复用 clone 时绑定的凭证)。
- **Prepare 依赖** — 对含 `package.json` 的套件跑 `npm ci`(playwright 套件常用)。

辅助:**文件树浏览**(挑选要跑的测试文件)、**pytest marker / Playwright `@tag` 解析**(按标记过滤用例)、**Playwright tag grep**(选中标记编译为 `--grep @tag`)、删除套件。

- **凭证管理(私有仓库认证)** — `POST/GET/DELETE /credentials` 存 HTTPS token:secret **加密落库**(Fernet,密钥由 `SECRET_KEY` 经 HKDF 派生)、**只写不回读**;clone/pull 时 token 经 **`GIT_ASKPASS` env 注入**,绝不进 argv / URL / 日志;owner-scope(非 admin 只能用自己的凭证)。

安全约束:Git URL **白名单**(仅 `https://`、`git@`,挡 `file://` / `http://` / 命令注入,防 SSRF 与读本地文件);套件名**路径穿越防护**(禁 `/`、`\`、前导 `.`)。归属:已注册套件按 owner,磁盘存在但未注册者仅 admin 可删。
→ `src/qarunner/api/routes.py`(link/clone/pull/prepare)、`core/paths.py`、`core/credentials.py`(凭证加密)

## 2. 测试执行引擎（执行核心）

**两种 Runner × 两种 Executor**:

|  | docker（默认，强隔离） | subprocess（管理员 / 显式开放） |
|---|---|---|
| **pytest** | python executor 镜像 | 宿主进程内 |
| **playwright** | 专用 playwright 镜像(含 Node + 浏览器) | 宿主进程内 |

**docker 隔离矩阵**(跑不受信任代码的主防线):非 root · `network_mode=none` 无网络 · `cap_drop=ALL` · 只读根文件系统 · 内存 2g / CPU 2 核 / PID 512 上限 · `no-new-privileges` · `/tmp` 为 tmpfs · playwright 另配 `shm_size=1g`。
**subprocess 隔离**(无容器时的退路):POSIX rlimits(CPU 1h / 地址空间 2GiB / 子进程 128 / 单文件 512MiB)+ **环境变量白名单**(挡 `LD_*` / `DYLD_*` / `NODE_OPTIONS` / `PYTHON*` 等注入面)。
**通用**:每次运行在独立 **workspace jail**(套件副本,忽略 `.git` / `.venv` / `__pycache__` 等)中执行,互不污染;arg 注入防护(拒危险 pytest / playwright flag);stdout/stderr 仅留末尾 256KB 防 OOM。

资源治理:超时(默认 30min,上限 24h)· 全局并发上限(默认 4)· **单用户在途 run 上限**(默认 20,超额 `429`,admin 豁免)。
→ `src/qarunner/adapters/docker_runner.py`、`subprocess_runner.py`、`core/orchestrator.py`

## 3. 执行配置与触发（Profile / Run）

- **Profile（执行配置档）** — 把一组运行配置(套件 / runner / 选中文件 / marker / 参数 / env / executor / 超时)存下来复用,增删改全支持。
- **触发运行** — 即席填表触发,或在 Profile 上**一键触发**。
- **重跑** — `POST /runs/{id}/rerun`(owner)用原 run 的参数建一个新 run(原 run 不动),省去重填表单。
- **异步执行** — `POST /runs` 立即受理(`202`),后台运行;状态机 `queued → running → completed / failed / timeout / cancelled`;**运行中的 run 可主动取消**(`POST /runs/{id}/cancel` 终止子进程 / 容器、释放并发槽);调用方轮询或订阅日志流取结果。
→ `core/orchestrator.py`、`core/profile_service.py`

## 4. 结果、报告与实时观测

- **Allure 报告** — 单文件 HTML,可内嵌 iframe 或全屏查看。
- **JUnit 解析** — `defusedxml` 防 XXE,10MB 上限防 billion-laughs;汇总 `total / passed / failed / skipped / error / duration` + 计算 `pass_rate`。
- **Runner 产物** — Playwright trace / screenshot / video 保存在 run 目录,详情抽屉可列出并下载。
- **实时日志（SSE）** — `GET /runs/{id}/stream` 流式 stdout;前端配全屏终端、日志级别过滤(ERR/WARN/OK)、搜索、下载、自动滚动。
- **运行锁定** — 锁住的 run 不被清理删除。
- **删除单条 run** — `DELETE /runs/{id}`(owner)彻底删除一条 run 的元数据与物理产物;锁定或仍在 queued/running 的 run 拒删(`409`,需先解锁 / 取消)。
- **产物清理** — 按保留天数删除过期且未锁定 run 的物理产物(元数据保留,admin)。
→ `core/allure.py`、`core/junit.py`、`api/routes.py`

## 5. 跨次对比（回归视图）

把孤立的单次结果连成时间线——回归工具的灵魂,也是 qarunner 区别于「跑一次看一次」runner 的核心。四个能力共享一份**持久化的 per-case 结果**(每个用例的 `suite/name/status/duration/message` 逐条落库,而非只存汇总):

- **基线 diff** — `GET /runs/{id}/diff`:把本次 run 与**同执行范围**(同套件 + runner + 参数)的最近一次 `COMPLETED` run 比对,分五桶——**新增失败 / 已修复 / 持续失败 / 新增用例 / 消失用例**。范围不可比则不强行 diff(返回空基线),不把没跑的用例误判成「消失」。
- **通过率趋势** — `GET /runs/trend`:某套件历次 `COMPLETED` run 的通过率随时间曲线(升序);前端在仪表板手绘 SVG 折线(零图表依赖)。
- **Flaky 检测** — 单用例最近 N 次结果在 `pass ↔ fail/error` 间反复翻转即标记**不稳定**,默认阈值**至少 4 次观测、翻转 ≥3 次**,区别于单次回归 / 单次修复(单调变化不算 flaky)。阈值为保守占位,待真实数据校准。
- **用例级历史** — `GET /cases/history`:点开 diff 里任一用例,懒加载它的跨 run 结果序列(状态格条 + flaky 徽章)。

owner-scope 贯穿三端点:**非 admin 只对比 / 趋势 / 翻看自己的 run**(diff 的基线候选亦然,不泄露他人 run),admin 跨全部。纯判定逻辑(diff 分桶 / 基线选择 / flaky 翻转)均为 `core/` 下**零 DB 依赖的纯函数**,可独立单测。
→ `core/regression.py`(diff + 基线)、`core/trend.py`、`core/flaky.py`、`adapters/sqlite_store.py`(`run_test_cases` 表 + `get_case_history`)、`frontend/src/components/{RunDetailsDrawer,SuiteTrend}.tsx`

## 6. AI 失败诊断

在跨次对比的基础上,把失败用例交给 LLM 做结构化根因诊断——只读,从不修改测试或代码:

- **结构化诊断** — `POST /runs/{id}/ai-analysis` 聚合本次 run 的失败用例 + **stdout/stderr 日志尾部** + 基线 diff + 通过率趋势 + 逐用例 flaky 历史,一并交给 LLM,产出**六类根因**之一(新增失败 / 历史 flaky / 环境问题 / 断言不符 / 超时 / 权限路径问题)+ **置信度**(HIGH/MED/LOW)+ 证据列表 + 是否疑似回归 + 建议下一步;`GET /runs/{id}/ai-analysis` 取回缓存的诊断结果。
- **Provider 中立、可切换** — `FailureAnalyzer` 端口(`ports/ai.py`)抽象 LLM 调用,`anthropic` / `openai` 两个 provider 实现共享同一套 prompt 构建与回复解析(`core/failure_analysis.py`),诊断内容不因换 provider 而变;经 `QARUNNER_AI_PROVIDER` 选择,`QARUNNER_AI_BASE_URL` 可指向自建网关。
- **无 key 自动降级** — `QARUNNER_AI_API_KEY` 为空时端点返回 `enabled:false`,前端 **Tab 入口仍可见**,点入后展示未配置说明(不报错、不调 LLM),不影响平台正常启动与使用;LLM 调用失败或回复解析失败同样降级为 LOW 置信度的兜底诊断,不让端点 500。
- **POST 限流** — 按认证用户滑动窗口限制 `POST` 频率(默认 10 次 / 60 秒,可用 `QARUNNER_AI_POST_MAX_CALLS` / `QARUNNER_AI_POST_WINDOW_SECONDS` 调整;`max_calls=0` 关闭),超额返回 `429` + `Retry-After`。
- **owner-scope + read-only** — 鉴权与 `/diff`、`/cases/history` 完全一致(非 admin 只能诊断自己的 run);诊断过程只读聚合数据,不落回写测试代码或用例文件。
- **前端入口** — `RunDetailsDrawer` 详情抽屉第 4 个 Tab「AI 分析」(始终显示;未配置时内页说明)。
→ `core/failure_analysis.py`(prompt 构建 + 回复解析)、`ports/ai.py`(`FailureAnalyzer` 协议)、`adapters/anthropic_analyzer.py`、`adapters/openai_analyzer.py`、`api/routes.py`(`/runs/{id}/ai-analysis`)、`frontend/src/components/AiInsightsTab.tsx`

## 7. 定时调度

- **cron + 时区** — 标准 cron 表达式,每个调度独立 IANA 时区。
- **下次运行预览** — 创建前预览未来 5 次触发时间。
- **手动立即触发** — `POST /schedules/{id}/trigger`(owner)按调度的 profile 立刻建一次 run,不等 cron;不走去重、run 归属触发者(非 `system:schedule`),与 cron 自动触发共用同一套 profile→run 映射。
- **多副本安全** — 数据库级 `claim_schedule_run()` 原子竞选,保证每个触发点只有一个副本真正建 run(防重复触发);启动**崩溃恢复**把上次遗留的 queued/running 标 failed(单实例前提)。
→ `core/cron.py`、`adapters/apscheduler_schedule.py`、`core/schedule_service.py`

## 8. 账户与权限

- **认证** — JWT(HS256)+ bcrypt;令牌走 `Authorization: Bearer` 或 HttpOnly Cookie。
- **角色** — `admin` / `user` 两级。
- **owner-scope 对象级授权** — 非 admin 只能访问 / 改自己创建的 run / profile / schedule(他人资源 `403`,不存在 `404`;列表静默过滤)。
- **用户管理** — admin 建用户、列用户、改用户密码 / 角色(`PUT /users/{username}`)、删用户(`DELETE`)。守护:不能删自己、不能降级最后一个 admin。
- **登录限流** — 同 `用户名|IP` 连错 5 次指数退避锁定(60→900s),防爆破。
→ `core/auth.py`、`core/login_throttle.py`、`api/routes.py`

## 9. 两种使用界面

- **Web 控制台**(React 18 + Vite + Semi UI 单页应用):登录;仪表板 4 张统计卡片(总数 / 成功率 / 失败 / 活跃队列);左栏套件 + Profile 管理;右栏运行表格(按状态 / 引擎 / 归属 / 手动·调度多维过滤 + 搜索);详情抽屉(日志 / 报告 / 基线 diff / AI 分析四个 Tab);触发弹窗(文件树 + marker + env 编辑);调度 / 用户管理弹窗。工程特性:中英 **i18n**、**明暗主题**、**无障碍 a11y**(焦点管理 + 键盘激活)、SSE 实时更新 + 轮询。
- **REST API**(端点数随迭代增长,完整清单见 [`API_REFERENCE.md`](API_REFERENCE.md))+ FastAPI 自带 **`/docs`**(Swagger UI)、**`/redoc`**、**`/openapi.json`**(权威 schema)。
→ `frontend/src/components/*`、`frontend/src/hooks/*`

## 10. 部署与运维

- **Docker Compose 两套** — 生产单镜像同源(API + SPA 同端口 8000)、开发双容器热重载。
- **就绪探针** — `GET /health`(DB 可达才 200)。
- **优雅停机** — 留缓冲期让在途 run 落盘。
- **全环境变量配置** — `QARUNNER_` 前缀;`SECRET_KEY` / `ADMIN_PASSWORD` 必填且拒弱值。
→ `../README.md` 配置表、`config.py`

## 11. 产品边界（刻意不做的）

经源码逐项查证**确实不存在**,便于理解产品范围:

- ❌ 命令行 CLI(只有 HTTP API + 启动脚本,`pyproject.toml` 无 `[project.scripts]`)
- ❌ Webhook / 通知 / 告警
- ❌ 结果导出专用端点(CSV/JSON;API 本身返回 JSON)
- ❌ 多租户 / 团队 / 组织(仅用户级 owner-scope)
- ❌ 中央审计日志(仅登录记 IP + 用户名)
- ❌ Prometheus metrics / 消息队列

这些留白是有意取舍:聚焦「跑测试 + 调度 + 报告」核心,降低部署与审计复杂度。

---

## 附：可信度与交叉引用

- 全文功能均经源码核对(`routes.py` / `orchestrator.py` / `docker_runner.py` / `subprocess_runner.py` / `apscheduler_schedule.py` / `auth.py` / `config.py` 及 `frontend/src/*`)`[KNOWN] HIGH`;第 11 节否定结论为逐项查证「不存在」,非「未找到」。
- 接口细节(字段 / 状态码 / 权限)→ [`API_REFERENCE.md`](API_REFERENCE.md);架构分层 → [`../ARCHITECTURE.md`](../ARCHITECTURE.md);部署配置 → [`../README.md`](../README.md)。
