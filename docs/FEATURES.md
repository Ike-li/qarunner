# qarunner 功能总览（FEATURES）

> 从**能力视角**俯瞰整个平台:它能做什么、边界在哪。所有功能均经源码核对。
>
> 与其他文档分工(各看一面、避免重复):
> - **本文 `FEATURES.md`** — 平台**能做什么**(能力全景)
> - [`API_REFERENCE.md`](API_REFERENCE.md) — 接口**怎么调**(端点契约、字段、状态码)
> - [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — 系统**怎么搭**(六边形分层、端口/适配器)
> - [`../README.md`](../README.md) — 项目**怎么跑**(快速开始、部署、配置)

## 一句话定位

一个**自托管、单节点的测试执行 + 调度服务**:把团队的 pytest、playwright 测试套件接进来,每次运行都跑在**硬化的一次性沙箱**里,收集结果、生成 Allure 报告、支持 cron 定时,全程配 Web 控制台与 REST API。

**定位边界**(完整推理见 [`POSITIONING.md`](POSITIONING.md)):它是 test **runner** + scheduler + reporter——**不是** CI(无 VCS 集成 / 流水线 / 多阶段构建)、**不是** QA 平台(无用例 / 缺陷管理 / 需求追溯)、**不是**多租户 SaaS(仅用户级 owner-scope)。

核心取舍:把每次运行的隔离做**重**(纵深防御),主要防的是**依赖供应链风险**——pytest 在 collection 阶段就 import 整棵依赖树,被投毒的传递依赖在「测试时」即执行任意代码;这同时补上了**自托管 CI runner 默认不隔离**这个公认软肋。代价是把外围(通知 / 导出 / 多租户)**留白**,换取部署简单、易于审计。

---

## 1. 测试代码接入（Suite 管理）

把测试代码变成平台可运行的「套件」,四种来源 / 操作:

- **Link 本地目录** — 软链宿主上现有的项目目录为套件。
- **Clone Git 仓库** — 浅克隆(`--depth 1`)指定分支 / ref;**私有仓库**可绑定凭证认证。
- **Pull 更新** — 对 git 套件拉取最新(复用 clone 时绑定的凭证)。
- **Prepare 依赖** — 对含 `package.json` 的套件跑 `npm ci`(playwright 套件常用)。

辅助:**文件树浏览**(挑选要跑的测试文件)、**pytest marker 解析**(按标记过滤用例)、删除套件。

- **凭证管理(私有仓库认证)** — `POST/GET/DELETE /credentials` 存 HTTPS token:secret **加密落库**(Fernet,密钥由 `SECRET_KEY` 经 HKDF 派生)、**只写不回读**;clone/pull 时 token 经 **`GIT_ASKPASS` env 注入**,绝不进 argv / URL / 日志;owner-scope(非 admin 只能用自己的凭证)。

安全约束:Git URL **白名单**(仅 `https://`、`git@`,挡 `file://` / `http://` / 命令注入,防 SSRF 与读本地文件);套件名**路径穿越防护**(禁 `/`、`\`、前导 `.`)。归属:已注册套件按 owner,磁盘存在但未注册者仅 admin 可删。
→ `src/qarunner/api/routes.py`(link/clone/pull/prepare)、`core/paths.py`、`core/credentials.py`(凭证加密)

## 2. 测试执行引擎（平台核心）

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
- **实时日志（SSE）** — `GET /runs/{id}/stream` 流式 stdout;前端配全屏终端、日志级别过滤(ERR/WARN/OK)、搜索、下载、自动滚动。
- **运行锁定** — 锁住的 run 不被清理删除。
- **删除单条 run** — `DELETE /runs/{id}`(owner)彻底删除一条 run 的元数据与物理产物;锁定或仍在 queued/running 的 run 拒删(`409`,需先解锁 / 取消)。
- **产物清理** — 按保留天数删除过期且未锁定 run 的物理产物(元数据保留,admin)。
→ `core/allure.py`、`core/junit.py`、`api/routes.py`

## 5. 定时调度

- **cron + 时区** — 标准 cron 表达式,每个调度独立 IANA 时区。
- **下次运行预览** — 创建前预览未来 5 次触发时间。
- **手动立即触发** — `POST /schedules/{id}/trigger`(owner)按调度的 profile 立刻建一次 run,不等 cron;不走去重、run 归属触发者(非 `system:schedule`),与 cron 自动触发共用同一套 profile→run 映射。
- **多副本安全** — 数据库级 `claim_schedule_run()` 原子竞选,保证每个触发点只有一个副本真正建 run(防重复触发);启动**崩溃恢复**把上次遗留的 queued/running 标 failed(单实例前提)。
→ `core/cron.py`、`adapters/apscheduler_schedule.py`、`core/schedule_service.py`

## 6. 账户与权限

- **认证** — JWT(HS256)+ bcrypt;令牌走 `Authorization: Bearer` 或 HttpOnly Cookie。
- **角色** — `admin` / `user` 两级。
- **owner-scope 对象级授权** — 非 admin 只能访问 / 改自己创建的 run / profile / schedule(他人资源 `403`,不存在 `404`;列表静默过滤)。
- **用户管理** — admin 建用户、列用户、改用户密码 / 角色(`PUT /users/{username}`)、删用户(`DELETE`)。守护:不能删自己、不能降级最后一个 admin。
- **登录限流** — 同 `用户名|IP` 连错 5 次指数退避锁定(60→900s),防爆破。
→ `core/auth.py`、`core/login_throttle.py`、`api/routes.py`

## 7. 两种使用界面

- **Web 控制台**(React 18 + Vite + Semi UI 单页应用):登录;仪表板 4 张统计卡片(总数 / 成功率 / 失败 / 活跃队列);左栏套件 + Profile 管理;右栏运行表格(按状态 / 引擎 / 归属 / 手动·调度多维过滤 + 搜索);详情抽屉(日志 + 报告);触发弹窗(文件树 + marker + env 编辑);调度 / 用户管理弹窗。工程特性:中英 **i18n**、**明暗主题**、**无障碍 a11y**(焦点管理 + 键盘激活)、SSE 实时更新 + 轮询。
- **REST API**(42 端点)+ FastAPI 自带 **`/docs`**(Swagger UI)、**`/redoc`**、**`/openapi.json`**(权威 schema);完整契约见 [`API_REFERENCE.md`](API_REFERENCE.md)。
→ `frontend/src/components/*`、`frontend/src/hooks/*`

## 8. 部署与运维

- **Docker Compose 两套** — 生产单镜像同源(API + SPA 同端口 8000)、开发双容器热重载。
- **就绪探针** — `GET /health`(DB 可达才 200)。
- **优雅停机** — 留缓冲期让在途 run 落盘。
- **全环境变量配置** — `QARUNNER_` 前缀;`SECRET_KEY` / `ADMIN_PASSWORD` 必填且拒弱值。
→ `../README.md` 配置表、`config.py`

## 9. 产品边界（刻意不做的）

经源码逐项查证**确实不存在**,便于理解平台范围:

- ❌ 命令行 CLI(只有 HTTP API + 启动脚本,`pyproject.toml` 无 `[project.scripts]`)
- ❌ Webhook / 通知 / 告警
- ❌ 结果导出专用端点(CSV/JSON;API 本身返回 JSON)
- ❌ 多租户 / 团队 / 组织(仅用户级 owner-scope)
- ❌ 中央审计日志(仅登录记 IP + 用户名)
- ❌ Prometheus metrics / 消息队列

这些留白是有意取舍:聚焦「跑测试 + 调度 + 报告」核心,降低部署与审计复杂度。

---

## 附：可信度与交叉引用

- 全文功能均经源码核对(`routes.py` / `orchestrator.py` / `docker_runner.py` / `subprocess_runner.py` / `apscheduler_schedule.py` / `auth.py` / `config.py` 及 `frontend/src/*`)`[KNOWN] HIGH`;第 9 节否定结论为逐项查证「不存在」,非「未找到」。
- 接口细节(字段 / 状态码 / 权限)→ [`API_REFERENCE.md`](API_REFERENCE.md);架构分层 → [`../ARCHITECTURE.md`](../ARCHITECTURE.md);部署配置 → [`../README.md`](../README.md)。
