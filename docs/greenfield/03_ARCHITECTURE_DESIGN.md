# 测试执行平台演进式架构设计

> 文档编号：QEP-ARCH-001<br>
> 版本：V0.1.0<br>
> 状态：草稿，待架构/安全/运维评审<br>
> 日期：2026-07-12<br>
> 输入：MVP PRD（未随仓库发布）、企业 PRD（未随仓库发布）<br>
> 约束：本设计形成时未以现有 qarunner 代码为设计前提

---

## 1. 架构结论

### 1.1 一句话结论

采用“稳定控制面领域模型 + 可替换执行后端”的演进式架构：

- 单 ECS MVP 使用 FastAPI、PostgreSQL、云对象存储和独立 Worker Agent；每个 Attempt 由 Worker 在专用 rootless/受限 Docker Runtime 中创建全新容器。
- 先通过相同 Worker 协议扩展到少量静态 Worker VM；当容量、异构资源、故障域和弹性门禁触发后，再升级为托管 Kubernetes Jobs/Indexed Jobs + Kueue + 云节点自动扩缩。
- 无论执行后端如何变化，`Batch → Manifest → Run → Attempt → Evidence`、start commit、fencing、unknown 和 Target Grant 语义保持不变。
- 测试代码永不在 FastAPI、Scheduler、Planner、Reconciler 或其他控制面进程中运行。

### 1.2 关键取舍

| 议题 | 决定 | 原因 |
|---|---|---|
| Web/API | FastAPI + Uvicorn | API 强契约、Pydantic/OpenAPI、异步 I/O 和流式状态；不在 Web 进程跑测试 |
| 正式 MVP 数据库 | PostgreSQL | 多个控制角色并发写、租约/CAS/审计、未来多 Worker；避免正式 MVP 后重写一致性模型 |
| SQLite | 仅可丢弃的本地演示 Profile | 单文件简单，但正式 MVP 已有 API/Scheduler/Dispatcher/Event/Finalizer 多写者 |
| MVP 任务队列 | PostgreSQL 状态表 + lease/CAS + outbox | 一套事实源即可恢复；不额外运维 Redis/RabbitMQ/Celery |
| MVP 执行 | 独立 Worker Agent + 每 Attempt 全新 Docker 容器 | 单机最小闭环，控制面不接触执行引擎 |
| 企业执行 | 先静态 VM Worker，达到门禁后 Kubernetes Job + Kueue | 小规模不背集群成本，大规模获得配额、异构 Pool 和节点弹性 |
| Artifact | 从 MVP 起使用云对象存储 | 避免证据与 ECS 同故障域；数据库只存索引和 digest |
| 工作流 | 固定领域状态机 + Reconciler | 业务流程固定，不预装 Temporal/Argo 等通用编排系统 |
| 观测 | MVP 冻结 OTel/指标字段，企业集中采集 | 数据从第一天可用于资源画像，按规模渐进增加后端 |
| 不可信隔离 | 单机 best-effort + 明示风险；企业独立节点池并优先强沙箱 | 普通容器共享内核，不能包装为抵御主动恶意代码的完整边界 |

### 1.3 为什么正式 MVP 使用 PostgreSQL，而不是 SQLite

如果 MVP 只是单进程、可删除数据的两周演示，SQLite WAL 足够。但本 PRD 的 MVP 包含：

- API、Schedule Service、Dispatcher/Reconciler、Worker 事件和 Artifact Finalizer 并发写状态；
- Assignment claim、lease renew、cancel、终态 finalize 和审计的条件事务；
- 异机备份恢复和后续远程 Worker；
- 日常真实测试数据不能随意丢弃。

SQLite 官方说明每个数据库文件同一时刻只有一个 writer，并建议“很多并发 writer”使用 client/server 数据库；PostgreSQL 的 `FOR UPDATE SKIP LOCKED` 官方明确适合多个消费者访问 queue-like table。因此：

- `demo`：可选 SQLite，明确不进入真实回归和恢复 SLO。
- `mvp/production`：PostgreSQL 是唯一正式状态库。

这不是因为 10 万 Case “太大”，而是因为状态写入并发、恢复语义和演进成本。

---

## 2. 架构驱动因素

### 2.1 质量属性优先级

| 优先级 | 质量属性 | 架构响应 |
|---:|---|---|
| 1 | 不可信执行隔离 | 控制面/执行面分离、每 Attempt 全新沙箱、无平台长期秘密、默认拒绝网络 |
| 2 | 结果与状态正确性 | 不可变 Manifest、start commit、fencing、幂等事件、Evidence finalize、Reconciler |
| 3 | SUT 保护 | Target Grant、Environment Lease、短期秘密和出站网关先于计算准入 |
| 4 | 有界容量 | 多维 Resource Profile、硬限制、队列背压、不可行计划明确拒绝 |
| 5 | 故障隔离 | Run/Attempt 为最小影响单位，Worker 独立故障域，N+1 和 blast radius |
| 6 | 可演进性 | 领域模型与执行后端解耦，协议和证据格式从 MVP 支持多 Worker |
| 7 | 运维成本 | MVP 不引入集群和消息中间件；企业优先托管服务和已有平台 |
| 8 | 性能 | 控制面按批准峰值设计；真正算力扩展在执行 Pool，不通过堆 API 实例跑测试 |

### 2.2 必须保持的系统不变量

1. 控制面永不加载、安装或执行用户测试代码及依赖。
2. 每个 Attempt 只有一个当前有效 Assignment/fence。
3. 每个原始 Case/原子组在一个 Shard Plan 中恰好归属一个 Run。
4. 测试输出不能直接决定平台终态；终态需要受信平台事实和 Evidence Manifest。
5. 计算容量不能越过 SUT、账号、数据、安全、配额或预算硬上限。
6. unknown 非幂等 Attempt 默认不自动重跑。
7. Artifact 路径按 Attempt 隔离且内容寻址/摘要校验，重试不能覆盖历史证据。
8. Worker/执行后端失效不允许让已完成状态倒退或旧事件覆盖新 fence。

### 2.3 架构非目标

- 不使用 Web 框架的 background task 运行测试；FastAPI 官方也建议重型计算使用多进程/多服务器机制。
- 不把消息队列当业务事实源，也不以“消息已 ack”推断测试终态。
- 不把 Kubernetes 当需求；只有达到可验证门禁时才引入。
- 不因企业目标预装 Kafka、ClickHouse、Temporal、Argo、Service Mesh 或自建 Vault。
- 不在同一 ECS 自建“高可用”对象存储或数据库副本并声称消除宿主故障域。

---

## 3. 统一逻辑架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│ 用户 / CI / 定时触发                                                │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS / OIDC
┌──────────────────────────────▼──────────────────────────────────────┐
│ Control API                                                        │
│ Suite · Batch · Query · Cancel · Admin · RBAC · Audit              │
└──────────┬───────────────────┬──────────────────┬───────────────────┘
           │                   │                  │
┌──────────▼────────┐ ┌────────▼────────┐ ┌──────▼───────────────────┐
│ Schedule Service  │ │ Planner/Admission│ │ Event/Result Finalizer │
│ 只创建 Batch      │ │ Manifest/Shard/  │ │ 状态、结果、Evidence   │
│                   │ │ Capacity/Fairness│ │                        │
└──────────┬────────┘ └────────┬────────┘ └──────┬───────────────────┘
           └───────────────────┴─────────────────┘
                               │
                     ┌─────────▼─────────┐
                     │ PostgreSQL        │
                     │ 业务事实/租约/    │
                     │ outbox/审计/索引  │
                     └─────────┬─────────┘
                               │ Assignment / Job intent
                  ┌────────────▼─────────────┐
                  │ Execution Coordinator   │
                  │ Docker Worker Backend   │
                  │ 或 Kubernetes Backend   │
                  └────────────┬─────────────┘
                               │
             ┌─────────────────▼──────────────────┐
             │ 独立 Worker/Job/Attempt Sandbox    │
             │ 不可信 pytest / Playwright / deps │
             └───────┬────────────────┬───────────┘
                     │                │
          ┌──────────▼───────┐ ┌──────▼─────────────────┐
          │ Egress Gateway   │ │ Artifact Upload       │
          │ TargetGrant/Lease│ │ Object Storage        │
          └──────────┬───────┘ └────────────────────────┘
                     │
                获批真实 SUT
```

### 3.1 控制面边界

控制面负责：

- 用户身份、RBAC、SuiteRevision 和配置治理；
- Batch、Manifest、Shard Plan、Capacity Plan 和队列事实；
- Target Grant、Environment Lease、配额、预算和审计；
- Assignment/fence、状态机、结果聚合和 Evidence 索引；
- Worker/执行后端健康、对账、告警和运维入口。

控制面禁止：

- Git hook、依赖安装脚本、pytest collection、Playwright 或用户命令在其进程/文件系统执行；
- 挂载原始 Docker socket 或把 Kubernetes/云管理凭据暴露给 Web API；
- 接受用户提供的任意容器参数、宿主路径、网络模式或安全配置；
- 把进程内内存队列作为唯一任务事实。

### 3.2 执行协调边界

Execution Coordinator 是唯一能把批准 Run 转换成实际沙箱的控制服务。它暴露窄领域接口：

- `claim/commit-start/renew/event/upload/release/reconcile`（Docker Worker）；或
- `create/suspend/observe/delete/reconcile Job`（Kubernetes 后端）。

它不接受普通用户的原始执行引擎参数。容器/Pod 模板来自经过版本化批准的 Resource Profile 和 Security Profile。

### 3.3 执行沙箱边界

每个 Attempt 沙箱只获得：

- 冻结的源码/依赖/配置/执行环境引用；
- 当前 Run 的 Case 清单；
- 当前 Resource/Security Profile；
- 只用于上报当前 Attempt 的短期 token；
- 当前 Target Grant 所需的短期秘密和出站能力。

它不能获得平台数据库、OIDC/管理员秘密、云主机元数据、Worker 管理身份、执行引擎接口、其他 Attempt 目录或任意内网访问。

---

## 4. 单 ECS MVP 物理架构

### 4.1 拓扑

```text
单台加固 ECS
├── 主机入口/防火墙
├── Control Runtime（Docker Compose，独立系统用户）
│   ├── Nginx：TLS、内网入口、静态 UI、API 反向代理
│   ├── React 静态前端
│   ├── FastAPI Control API
│   ├── Schedule Service
│   ├── Planner + Dispatcher/Reconciler
│   └── PostgreSQL（持久卷；备份到异机目标）
├── Worker Runtime（独立系统用户）
│   ├── Worker Agent（systemd 服务，无入站管理端口）
│   └── 专用 rootless Docker daemon
│       └── 每 Attempt 全新容器
├── Egress Gateway + 主机防火墙策略
└── 本地有界 Artifact spool
             └── 云对象存储 / 公司对象存储（异机）
```

控制面和 Worker Runtime 使用不同 Unix 用户、文件权限、网络和凭据，但仍共享宿主内核；这是降低风险，不是强物理隔离。

### 4.2 MVP 组件选型

| 层 | 选择 | 说明 |
|---|---|---|
| OS | 当前受支持的加固 Linux LTS | 自动安全更新窗口、最小软件、主机审计和备份 agent |
| 入口 | Nginx + 内部 TLS 证书 | 只监听批准内网接口/IP；管理端点单独限制 |
| 前端 | React + TypeScript + Vite | 大批次交互、状态流、分页和类型化 API 客户端 |
| API | Python 3.12+、FastAPI、Uvicorn | OpenAPI/Pydantic；API 无本地业务状态，不执行测试 |
| 数据访问 | SQLAlchemy 2 Core/ORM + Alembic；关键 claim 使用显式 SQL | 普通 CRUD 保持可维护，租约/CAS 明确控制事务 |
| 状态库 | PostgreSQL 当前受支持主版本 | 单实例运行但有异机备份；从第一天支持多写者和恢复 |
| 定时 | 独立 Schedule Service + cron 解析库 | 仅计算触发并创建幂等 Batch，不运行测试 |
| 队列 | PostgreSQL `run_queue` + `SKIP LOCKED`/CAS + outbox | 无 Redis/RabbitMQ/Celery；崩溃可重放 |
| Worker | Python Worker Agent，HTTPS 长轮询 + mTLS | 主动连接控制面，无入站端口；协议与企业 VM Worker 相同 |
| 执行 | 专用 rootless Docker daemon | 只有 Worker Agent 可访问其 socket；测试容器永不获得 socket |
| 证据 | 云/公司对象存储；本地仅有界 spool | 元数据和 digest 在 PostgreSQL；大文件不进入数据库 |
| 观测 | JSON 日志、OTel SDK、Prometheus 格式 `/metrics`、健康端点 | 优先接公司现有平台；没有时先保留可抓取接口和轮转日志 |
| 身份/秘密 | 公司 OIDC/SSO 与现有 Secret Manager 优先 | 不在同 ECS 自建复杂身份/Vault；无企业能力时使用显式降级方案 |

### 4.3 FastAPI 的边界

选择 FastAPI 的原因是 API 契约和异步 I/O，不是测试吞吐：

- Pydantic 模型表达 Batch、Run、Attempt、Grant 和 Worker 协议；
- OpenAPI 生成前端/Worker 客户端和契约测试；
- SSE/流式接口适合状态与有限日志 tail；
- 依赖注入适合身份、权限、事务和审计上下文。

FastAPI `BackgroundTasks` 只允许发送轻量通知等非关键工作。测试执行、计划、证据 finalize 和任何需要恢复的操作都进入持久状态机/独立服务。

### 4.4 PostgreSQL 使用边界

PostgreSQL 保存：

- Suite/Revision、Batch、Manifest/Plan 元数据；
- Run/Attempt、Assignment、lease/fence、状态事件索引；
- Grant/Lease、Quota/Budget、审计；
- Case 结构化结果的在线窗口和 Artifact 索引；
- outbox、调度和对账游标。

PostgreSQL 不保存：

- 完整 stdout/stderr、截图、视频、trace、压缩报告和源码快照；
- 无界高频遥测原始样本；
- 用户可控的大型 JSON/HTML 作为热行字段。

MVP 不先做表分区；达到第 9.4 节数据触发器后再对 Case result/event 按时间分区。备份必须验证恢复，不能只验证备份任务成功。

### 4.5 Worker Agent 与 Docker 边界

Worker Agent 是最小特权执行守门人：

- 只接受控制面签发的规范化 ExecutionSpec，不接受用户 Docker flags。
- 在创建容器前校验 digest、Profile、Grant、租约和 start commit。
- 强制 CPU、memory、PID、临时盘、日志大小和硬超时。
- 禁止 privileged、host mount、host network/PID/IPC、额外 capability、设备和 Docker socket。
- 默认非 root、只读根文件系统、独立可写 tmpfs/工作区、`no-new-privileges` 和 seccomp/LSM。
- 容器退出后先停止目标访问，再上传/校验证据，最后销毁工作区。
- 启动时按容器标签与数据库状态 reconcile，清理孤儿但不擅自推断成功。

Docker 官方说明容器默认没有资源限制，可能耗尽宿主资源；因此所有硬限制均是发布阻断项。Rootless mode 让 daemon 和容器以非 root 用户运行，可降低 daemon/runtime 漏洞影响，但不消除共享内核风险。

### 4.6 Playwright 沙箱

官方 Playwright Docker 镜像默认以 root 运行浏览器，这会关闭 Chromium sandbox；本架构不直接照搬该默认值。MVP 必须：

- 固定 Playwright package、浏览器镜像和执行镜像 digest，版本严格匹配；
- 使用专用非 root 用户和经过验证的 Chromium sandbox/seccomp；
- 不使用 `--privileged`、`SYS_ADMIN` 或宿主 IPC 作为正式绕过方案；
- 为 `/dev/shm` 提供有界独立空间，不共享宿主 IPC；
- 用真实 pytest/Playwright Suite 验证 rootless 和可选 gVisor 兼容性。

gVisor 官方明确存在未实现特性和兼容性缺口，因此它是“安全收益 + 实测门禁”的候选，不是未经验证的口号。如果强沙箱不兼容且安全负责人不接受普通容器风险，应增加独立可销毁 Worker 主机，而不是降低控制后继续宣称同等安全。

### 4.7 MVP 网络与秘密

```text
Attempt network namespace
       │ 仅能到达
       ▼
Egress Gateway / policy enforcement
       ├── 当前 Target Grant 的 SUT host:port
       ├── 批准依赖镜像/包代理
       └── 当前 Attempt 的 Artifact upload endpoint
```

- 主机防火墙对执行网段默认拒绝，阻止直接绕过网关访问内网、公网和元数据地址。
- Egress Gateway 解析并固定批准目标，校验重定向/DNS 变化，记录 Attempt/Grant/目标/字节数。
- Environment Lease 在控制状态中全局分配；网关同时执行 TTL/fence，避免仅靠 Worker 自觉。
- 测试秘密按 Attempt/目标/用途短期签发；测试必须使用时能读取它，因此重点是最小权限、短 TTL、出站限制、脱敏和审计。
- 不在同一 ECS 自建 Secret Manager 来制造虚假隔离；优先使用公司/云现有服务。无外部服务时，主机密钥加密存储仅是明确的 MVP 降级，并不能抵御宿主失陷。

### 4.8 MVP 故障语义

| 故障 | 行为 |
|---|---|
| API 进程重启 | 状态仍在 PostgreSQL；客户端用幂等键和查询确认结果 |
| Schedule/Planner 重启 | 通过 leader lease/outbox 继续，重复触发返回同一 Batch |
| Worker Agent 重启 | reconcile 容器与 Assignment；无法证明的执行进入 unknown |
| Docker daemon/容器失效 | 当前 Attempt `infra_failed` 或 unknown；其他容器按事实处理 |
| PostgreSQL 短暂不可用 | 不启动新 Attempt；活动 Attempt 受本地硬 TTL 约束，恢复后对账 |
| 对象存储不可用 | 本地有界 spool；达到水位后背压，不标记 Evidence 完成 |
| ECS 整机失效 | 全部服务和 Attempt 中断；从异机备份恢复，明确这是单机残余风险 |
| Target Grant/Lease 失效 | 网关在 TTL 内停止访问，不能因控制面不可达无限放行 |

---

## 5. 企业演进架构

### 5.1 E1：静态多 Worker VM

在日常容量刚超过一台 ECS、但尚不需要大规模弹性时，先复用 MVP Worker 协议扩展 2～10 台专用执行 VM：

```text
高可用/托管控制面
├── FastAPI replicas
├── Schedule leader
├── Planner / Dispatcher / Reconciler replicas
├── PostgreSQL HA + PITR
└── Object Storage
          │ mTLS outbound claim
          ├── API Worker VM Pool
          ├── Browser Worker VM Pool
          └── Restricted-network Worker VM Pool
                 └── each Attempt = fresh sandbox/container
```

特点：

- 保留简单运维模型和既有 Worker Agent；无需立即学习 Kubernetes。
- Worker VM 不运行控制面或状态库，使用唯一身份和 generation。
- Pool 按 pytest/API、Playwright/browser、网络区和安全级别划分。
- Worker 由不可变镜像/自动化脚本重建，不做长期“手工修复”。
- Dispatcher 按资源向量、fence 和 Lease 动态分配未启动 Run。
- 单 Worker 最大活动份额和 N+1 容量由 Capacity Plan 约束。

这一步可能已经足以承载每日 60,000 Case；是否继续上 Kubernetes 取决于真实峰值 slot、节点数量、Pool 异构、扩缩频率和运维成本，而不是“企业级”标签。

### 5.2 E2/E3/E4：托管 Kubernetes 执行平台

当静态 VM Pool 无法经济地满足弹性、异构资源、滚动维护或故障域目标时，采用：

```text
Control Plane（独立节点池/集群）
├── FastAPI Deployment
├── Schedule leader
├── Planner / Admission / Reconciler
├── PostgreSQL HA（优先托管）
├── Object Storage
└── OTel / Metrics / Audit
          │
          ▼
Kubernetes Execution Backend
├── Kueue：quota / priority / fair sharing / ResourceFlavor / admission
├── Kubernetes Job / Indexed Job：一次性 Shard Attempt
├── Cloud Node Autoscaler：根据 Pending Pod requests 增减节点
├── api-small/api-large node pools
├── browser-small/browser-large node pools
└── restricted/sandbox node pools + RuntimeClass
          │
          ▼
Egress Gateway → approved SUT
```

优先使用公司现有托管 Kubernetes。若公司没有对应运维能力、节点数长期很少且容量稳定，则继续使用 VM Worker Pool 可能是更优解。

### 5.3 Kubernetes 执行映射

| 领域对象 | Kubernetes 映射 | 约束 |
|---|---|---|
| Batch | 平台业务对象，不直接等同单个 Job | 一个 Batch 可跨多个 Profile/Pool |
| Shard Plan | 不可变对象/清单，按 digest 引用 | 每个 index 对应一个固定 Run |
| 同质 Run 组 | Indexed Job | 相同镜像、资源、安全、网络与重试策略 |
| 异质 Run | 不同 Job | 不为省对象数量混用 Profile |
| Attempt | Pod execution + 平台 Attempt ID/fence | Pod UID 不是业务主键；重复 Pod 不覆盖历史 |
| Resource Profile | Pod requests/limits + RuntimeClass + node affinity | requests 来自资源画像，不由 autoscaler 猜测 |
| Admission | 平台硬约束 + Kueue quota admission | SUT/预算/业务 deadline 仍由平台判断 |
| Worker/故障域 | Node/zone/pool + runtime identity | 调度拓扑限制最大 blast radius |
| Evidence | Pod 短期上传授权 → Object Storage | Pod 无数据库和对象存储长期凭据 |

Kubernetes Job 官方明确说明，即使 `parallelism=1`、`completions=1` 和 `restartPolicy=Never`，同一程序仍可能被启动两次。因此：

- Job/Pod 只能表达基础设施期望，不是业务 exactly-once 证明。
- Pod 启动后仍需用 Attempt token/fence 完成 start commit/身份确认。
- Artifact key 包含 Attempt/fence/content digest，不允许覆盖。
- 默认关闭 Kubernetes 层的业务自动重试；由平台根据 `test_failed/infra_failed/unknown` 显式创建新 Attempt。
- 同质大批可使用 Indexed Job；不同 Profile/Grant/Security policy 必须拆 Job。

### 5.4 Kueue 与平台调度的职责边界

Kueue 负责：

- Kubernetes Workload 的准入、ClusterQueue/LocalQueue 配额；
- ResourceFlavor 对应异构节点 Pool；
- Workload Priority、公平共享、借用/归还和部分准入；
- 与节点自动扩缩的容量申请衔接。

平台仍负责：

- Case Manifest、原子约束、加权 Shard Plan 和 Batch deadline；
- Resource Profile 估算与内部框架并发预算；
- SUT/账号/数据 Environment Lease；
- 用户/项目/Batch 成本和业务配额；
- unknown/retry、Evidence、结果聚合和用户语义。

不能把 Kueue 的 Job admitted 等同于“业务 Batch 可在 deadline 内完成”，也不能把 Kubernetes Job Complete 等同于平台 Evidence 已完整。

### 5.5 节点自动扩缩边界

Kubernetes 官方说明节点 Autoscaler 主要依据 Pod `resource requests` 和调度约束，通常不直接考虑 Pod 启动后的真实资源使用。因此自动扩缩链路为：

```text
历史 P90/P95 画像 + 保守余量
    → Resource Profile / Pod requests
    → Shard Plan / Pending Workload
    → Kueue admission
    → Pending Pod
    → 云 Node Autoscaler 增减 Node
```

平台自己的资源估算器不可省略。低估 requests 会导致扩容后仍 OOM/争用，高估会让 Job 无法准入或浪费成本。

缩容要求：

- 节点先 cordon/drain 或等效停止新 Run；
- 运行中测试默认不被成本优化驱逐；
- 节点寿命到期、抢占实例和维护窗口必须计入 deadline 风险；
- 定时大回归可按镜像拉取和节点 ready P95 提前预热暖池。

### 5.6 企业控制面

| 组件 | 扩展方式 | 一致性策略 |
|---|---|---|
| Nginx/企业 Ingress | 多副本/托管入口 | TLS、内网限制和健康检查 |
| FastAPI | 无状态多副本 | 请求幂等、数据库事务、无本地任务队列 |
| Schedule Service | 单 leader + 热备 | 数据库 lease/advisory lock；重复触发幂等 |
| Planner | 水平扩展 | claim work row、不可变输入、结果 digest |
| Admission/Dispatcher | 水平扩展 | `SKIP LOCKED`/CAS、配额事务和 outbox |
| Reconciler | 分区/水平扩展 | 以期望状态和外部观察重复对账 |
| PostgreSQL | 托管 HA + PITR | 唯一业务事实源，Schema migration 可回退 |
| Object Storage | 云托管/公司已有 | versioning/lifecycle/可选不可变保留 |
| OTel/Prometheus/日志 | 集中采集 | 高基数控制和保留策略 |

控制面服务可以物理拆进程，但不要求在初期拆成大量网络微服务。建议以模块化单体 API + 少量独立后台进程起步，共享领域包和数据库契约；只有独立扩展、隔离或发布需求得到指标证明后才拆服务。

### 5.7 企业执行安全

- 控制面与执行节点使用不同节点池，最好不同集群/账户或至少不同宿主安全域。
- 测试 Pod 禁止 privileged、hostPath、hostNetwork、hostPID、hostIPC 和默认 ServiceAccount token。
- `runAsNonRoot`、drop all capabilities、`allowPrivilegeEscalation=false`、只读根文件系统、RuntimeDefault/批准 seccomp、PID/ephemeral storage 限制。
- NetworkPolicy 默认拒绝，并经 Egress Gateway 执行 Target Grant/TTL/fence；仅 NetworkPolicy 不能表达全部域名重定向和业务授权。
- 执行镜像固定 digest并经过扫描/签署政策；Playwright package 与镜像严格匹配。
- 高风险 Pool 使用 gVisor/Kata 等 RuntimeClass；必须以真实 pytest/Playwright 兼容性和性能数据批准。
- Node 基线异常或疑似逃逸时 quarantine，撤销 Worker/节点身份并整机重建，不“清理后继续用”。

### 5.8 企业数据与 Artifact

- PostgreSQL 保存在线业务状态、结构化结果和索引；Case result/event 达到真实查询触发器后按时间分区。
- 对象存储 key：`tenant_scope/project/batch/run/attempt/content-digest`；当前为单团队，仍保留 project scope。
- Evidence Manifest finalize 使用 root digest，完成后不可原位改写；修正产生新版本/补充记录。
- 使用短期预签名 URL 或 workload identity 上传；测试 Pod 不拥有列表/删除权限。
- Object lifecycle 分层删除；关键发布证据可选择 versioning/不可覆盖保留。
- ClickHouse/数据仓库只在长期分析影响 PostgreSQL SLO、扫描量和成本有证据时引入；日 60,000 Case 本身不构成必选理由。

### 5.9 企业观测

推荐组合：

- OpenTelemetry SDK + Collector：控制面 trace/metric/log 统一采集入口；
- Prometheus + Alertmanager：队列、容量、状态、Worker/Job/节点和 SLO；
- Grafana：产品/容量/安全/运维看板；
- Loki 或公司现有日志平台：平台运行日志和有限测试 tail；
- Object Storage：完整测试 stdout、视频、trace 和报告。

`attempt_id`、`run_id`、`case_id` 不作为 Prometheus/Loki 的无界标签。它们作为日志字段、trace attribute（按采样策略）或状态库检索键。

---

## 6. 演进路径与回退

### 6.1 四阶段路径

| 阶段 | 执行形态 | 数据/证据 | 调度 | 主要证明 |
|---|---|---|---|---|
| M0 本地演示 | 单进程/可选 SQLite、假 Worker | 可丢弃 | 手工 | UI/领域原型，不进真实回归 |
| M1 单 ECS MVP | PostgreSQL + 本地 Worker Agent + rootless Docker | 异机对象存储/备份 | 固定 Profile、有界队列 | 正确性、安全闭环、容量基线 |
| E1 静态多 VM | PostgreSQL HA + 2～10 Worker VM Pool | 对象存储 | 资源感知、多 Worker、公平 | 故障隔离、N+1、滚动维护 |
| E2～E4 弹性企业 | 托管 K8s Jobs + Kueue + Node Autoscaler | HA/PITR/生命周期 | deadline、自动准入和有界弹性 | 规模、SLO、自动化、HA/DR |

### 6.2 回退原则

- 自动资源估算异常：回退到批准的保守 Resource Profile。
- 自动扩容异常：回退到固定 Pool + 有界排队，不能放宽 SUT/安全限制。
- Kueue/Autoscaler 异常：暂停新 workload admission；在途 Attempt 按硬时限收敛。
- 新 Worker/Agent 版本异常：停止兼容版本准入，drain/回滚；旧 generation 不能复活。
- 新执行后端异常：新 Batch 切回批准后端；已启动 Attempt 不跨后端热迁移。
- 预测模型漂移：降级置信度和 ETA，进入 Shadow 模式重新校准。

### 6.3 不建议的中间路径

- 不经过 Docker Swarm：它会增加第二套短生命周期编排迁移，最终仍需解决异构配额和弹性。
- 不用 Celery task 代替 Attempt 状态机：ack/retry 不能表达真实副作用、Evidence 和 unknown。
- 不让每个 Worker 自行无限拉取：中央 Admission、全局 Lease 和配额必须先批准。
- 不在每个大容器内部开几十个 pytest/Playwright worker 逃避平台分片：会扩大故障半径并隐藏资源乘法。

---

## 7. 技术方案比较

### 7.1 FastAPI 与 Flask

| 维度 | FastAPI | Flask | 本项目结论 |
|---|---|---|---|
| 协议契约 | 原生 Pydantic/OpenAPI/JSON Schema | 需自行组合扩展 | FastAPI 更适合多领域模型和 Worker 协议 |
| I/O 模型 | ASGI、SSE/WebSocket/async 直接 | WSGI 为主，async 有边界 | FastAPI |
| 成熟/简单 | 结构化、约束更多 | 极简、生态成熟 | 如果已有成熟 Flask 团队也能实现，但无新收益 |
| 测试吞吐 | 不决定 | 不决定 | 测试都在独立执行面，不能用跑分作为理由 |

### 7.2 PostgreSQL 与 SQLite

| 场景 | SQLite | PostgreSQL | 决定 |
|---|---|---|---|
| 可丢弃本地演示 | 极简 | 多一个服务 | SQLite 可选 |
| 正式单 ECS MVP | 单 writer，备份/多进程协调需额外纪律 | 行锁、CAS、队列并发、PITR 路径清晰 | PostgreSQL |
| 多 Worker/HA | 不适合作为多写/多实例事实源 | 符合 | PostgreSQL |
| 迁移成本 | 先快后迁移 | 一开始略重但不换一致性模型 | 正式 MVP 直接 PostgreSQL |

### 7.3 数据库队列、消息队列和工作流

| 方案 | 能解决 | 代价/缺口 | 决定 |
|---|---|---|---|
| PostgreSQL queue + outbox | 持久事实、claim、lease、幂等、恢复 | 极高吞吐广播不擅长 | MVP/E1 核心 |
| Redis/RabbitMQ | 低延迟分发/消息 | 仍需事实库、幂等和对账；多一套运维 | 初期不引入 |
| Celery | Python 后台任务方便 | task ack/retry 与测试副作用/unknown 不等价 | 仅可用于非关键通知，不用于执行 |
| Temporal | 长周期可恢复工作流 | 不是资源调度器，平台认知与运维成本高 | 出现跨系统人工长流程后再评估 |
| Argo Workflows | Kubernetes DAG/fan-out | 多一个状态体系；当前固定流程不需要 | 复杂 DAG 成为产品需求后再评估 |

### 7.4 VM Worker 与 Kubernetes

| 维度 | 静态 VM Worker | Kubernetes Job + Kueue |
|---|---|---|
| 小规模运维 | 简单、透明 | 集群控制器和策略复杂 |
| 多 Pool/异构 | 自己维护调度与镜像 | ResourceFlavor/节点池成熟 |
| 快速弹性 | 需自建 VM 生命周期 | 云节点 autoscaler 有现成能力 |
| 故障/滚动维护 | Worker 协议可实现 | 原生控制器与 drain 生态更强 |
| 资源准入/公平 | 平台自研 | Kueue 提供基础能力，业务 SUT 仍自研 |
| 决定 | 2～10 个稳定节点优先 | 规模/弹性/异构门禁触发后采用 |

---

## 8. 架构决策记录（ADR）

| ADR | 决策 | 状态 | 替代项/触发复审 |
|---|---|---|---|
| ADR-001 | 采用模块化控制面 + 独立后台角色，不从微服务起步 | 建议批准 | 独立扩展/安全/发布需求有数据后拆分 |
| ADR-002 | FastAPI + Uvicorn 作为 HTTP API，重任务不得用 BackgroundTasks | 建议批准 | 团队统一语言/平台发生重大变化时复审 |
| ADR-003 | PostgreSQL 是正式 MVP 和企业唯一业务事实源 | 建议批准 | demo 可 SQLite；正式环境不双数据库兼容 |
| ADR-004 | PostgreSQL queue + lease/CAS + outbox，初期无 MQ | 建议批准 | outbox 延迟/吞吐持续超 SLO 或多外部消费者时复审 |
| ADR-005 | Schedule Service 只创建幂等 Batch | 建议批准 | 不把定时器 JobStore 当测试事实源 |
| ADR-006 | Control API 不持有 Docker/Kubernetes 高权限 | 建议批准 | Execution Coordinator 独立最小权限 |
| ADR-007 | MVP 使用独立 Worker Agent + rootless Docker | 条件批准 | 必须通过 pytest/Playwright 与安全兼容测试 |
| ADR-008 | Worker 主动 mTLS 长轮询，无入站端口 | 建议批准 | 超大 Worker fleet/网络要求变化时复审传输层 |
| ADR-009 | 每 Attempt 全新沙箱，start commit + fencing + TTL | 建议批准 | 不允许后续优化绕过 |
| ADR-010 | Artifact 从 MVP 起进入异机对象存储 | 建议批准 | 本地存储仅 demo/spool |
| ADR-011 | 固定领域状态机优先，不引入通用工作流引擎 | 建议批准 | 复杂 DAG/人工长流程成为批准需求时复审 |
| ADR-012 | E1 先静态 VM Pool，达到门禁后直接 Kubernetes，不经过 Swarm | 建议批准 | 公司已有成熟 K8s 可跳过 E1 |
| ADR-013 | 企业执行使用 Job/Indexed Job + Kueue + 云 Node Autoscaler | 条件批准 | 需公司托管 K8s 能力和真实容量门禁 |
| ADR-014 | OpenTelemetry 字段与资源遥测从 MVP 冻结 | 建议批准 | 后端可替换，语义不变 |
| ADR-015 | 强沙箱 gVisor/Kata 按 Pool 和兼容性启用 | 条件批准 | 不兼容时增加独立 Worker 故障域，不静默降级 |
| ADR-016 | 自动扩缩基于画像/requests/deadline，不使用黑盒 AI | 建议批准 | 未来 ML 仅作可回测估算输入 |

---

## 9. 容量与技术升级门禁

### 9.1 单 ECS → 静态多 Worker

以下任一项持续出现即升级：

- 安全容量内无法满足批准的回归窗口或 interactive SLO；
- 单 ECS 故障/维护中断全部任务不再可接受；
- pytest、Playwright、网络区或安全级别需要独立 Pool；
- 安全不再接受测试与控制面共享宿主内核；
- 纵向扩容成本或 noisy-neighbor 明显高于新增 Worker。

### 9.2 静态 VM → Kubernetes

初始建议复审门槛（不是硬事实）：

- Worker 长期超过约 10 台，人工补丁、镜像、drain 和调度运维成为主要成本；
- 峰谷差显著，节点每日多次扩缩或空闲成本不可接受；
- 同时存在多个异构 Pool、多个安全/网络区和 100+ 活动 Shard；
- 需要按 Pending workload 快速申请节点、跨 Pool 配额和公平共享；
- 公司已有成熟托管 Kubernetes、监控和 on-call 能力，边际运维成本低。

若 2～10 台固定 Worker 已稳定满足 SLO 和成本，继续使用 VM Pool 是合理最优解。

### 9.3 引入消息队列

只有以下证据出现才评估：

- outbox backlog/发布延迟持续超过批准 SLO，优化 PostgreSQL 后仍不满足；
- 需要大量独立消费者、事件保留/回放或跨系统流式集成；
- 队列吞吐压力与业务状态事务明确解耦，且团队具备运维能力。

即使引入 MQ，PostgreSQL 仍是 Batch/Run/Attempt 事实源，事件消费者仍须幂等。

### 9.4 引入分区或分析数据库

- PostgreSQL 热表索引/清理/备份影响控制面 SLO；
- Case result/event 时间窗口扫描经优化后仍超过查询目标；
- 长期趋势分析需要扫描数亿行且影响 OLTP；
- 独立分析团队/报表有明确刷新与一致性需求。

先做保留、归档、索引和 PostgreSQL 时间分区，再评估 ClickHouse/仓库。60,000 Case/day 单独不是更换数据库理由。

### 9.5 引入 Temporal/Argo

- Temporal：跨系统、小时/天级等待、人工审批、补偿和版本化长流程成为核心产品需求。
- Argo：测试流程出现用户定义 DAG、多阶段 fan-out/fan-in 和可复用工作流模板。
- 当前 `Inventory → Plan → Admit → Execute → Aggregate → Publish` 固定流程不满足上述门槛。

---

## 10. 部署、运维与灾备

### 10.1 MVP 部署原则

- 长期控制服务使用 Docker Compose；动态 Attempt 由 Worker Runtime 创建，不写入 Compose。
- Control 和 Worker 使用不同系统用户、文件目录、网络与凭据。
- PostgreSQL 数据、对象 spool 和配置分别挂载，磁盘水位有阻断阈值。
- 宿主只开放内网入口；Worker Agent 不监听远程入站端口。
- 数据库定期基础备份 + 增量/WAL 等效策略到异机位置，并做恢复演练。
- 配置、Schema migration、执行镜像和 Worker Agent 均有版本与回滚路径。

### 10.2 企业部署原则

- API 至少两个副本跨故障域；Schedule/协调角色使用 leader lease 或可并行 claim。
- PostgreSQL 使用托管 HA/PITR 优先，备份与主故障域分离。
- 对象存储启用生命周期、加密、访问日志和必要的版本/不可变保留。
- 控制节点与执行节点隔离；测试 Pod/Worker 无控制面长期凭据。
- Worker/节点使用不可变镜像和滚动重建，部署支持 canary、drain 和回滚。
- 灾备恢复必须验证业务状态与 Artifact 引用对账，而非仅数据库启动成功。

### 10.3 关键告警

- admitted Batch deadline risk 超阈值；
- interactive queue age/p99 超目标；
- unknown Attempt、非法状态迁移或 fence 冲突；
- Worker offline/quarantined、最大故障半径或 N+1 不满足；
- SUT Lease 饱和、Grant 到期/撤销未收敛或出站拒绝异常；
- Artifact finalize backlog、本地 spool 高水位或 digest 冲突；
- PostgreSQL 事务/锁/连接/备份异常；
- Autoscaler 请求到 ready 时长、节点配额或镜像拉取超目标；
- 预测误差漂移、低置信度任务比例或资源 Profile OOM 异常。

---

## 11. 需求到架构追踪

| 架构决策/组件 | MVP 需求 | 企业需求 |
|---|---|---|
| 控制面/执行面分离 | MVP-FR-010～012、SEC-MVP-001～003 | ENT-FR-014～019、SEC-ENT-001～004 |
| Manifest/Plan/Attempt 领域模型 | MVP-FR-003～007、017～019 | ENT-FR-001～009、023～027 |
| PostgreSQL + lease/CAS/outbox | MVP-FR-005、011、019、021～022 | ENT-FR-003、010～015、023～031 |
| Worker mTLS 长轮询与 fencing | MVP-FR-011、019 | ENT-FR-014～018、023～024 |
| Target Grant/Egress/Lease | MVP-FR-013～015 | ENT-FR-020～022、034 |
| 对象存储 + Evidence Manifest | MVP-FR-017～019、023 | ENT-FR-025～026、031 |
| 固定 Profile/DB 队列 | MVP-FR-007～009、024 | E1 基础能力 |
| K8s Job/Kueue/Autoscaler | 不适用，属于非目标 | ENT-FR-010～018、030、034～035 |
| OTel/Prometheus/日志分层 | MVP-FR-016、022、024 | ENT-FR-032～036 |
| HA/PITR/异域恢复 | MVP 异机恢复但无 HA | ENT-FR-030～031、039 |

---

## 12. 架构验证计划

### 12.1 MVP 架构原型必须证明

1. FastAPI 进程和文件系统完全不运行/导入测试代码。
2. Worker Agent 在 rootless Docker 创建 pytest、Playwright Attempt，并强制全套资源/安全限制。
3. 相同 Assignment 的 commit-start 重放只产生一个有效 fence/执行。
4. PostgreSQL queue 多个 Dispatcher 并发 claim 无双重有效分配。
5. Worker/API/PostgreSQL/对象存储故障后状态可解释收敛。
6. 非 root Chromium sandbox、独立 `/dev/shm` 和真实 Playwright Suite 兼容。
7. Egress Gateway 无 Grant/过期/撤销/重定向/直连绕过全部 fail closed。
8. 30,000 Manifest 分片无遗漏、无重复，并在性能目标内完成。

### 12.2 企业架构试验必须证明

1. Indexed Job 同 index 重复 Pod 仍不能形成双活业务 Attempt。
2. Kueue quota/priority/fair share 与平台 SUT/预算准入不会互相绕过。
3. Node Autoscaler 使用资源画像生成的 requests 达到 ready 时间和成本目标。
4. 最大节点故障不影响其他节点 Attempt，未启动 Run 重平衡并满足 N+1。
5. gVisor/Kata 对代表性 pytest/Playwright 的兼容性、性能和 Artifact 完整性。
6. 控制面多副本和 leader 角色故障无重复 Batch/Assignment。
7. PostgreSQL PITR/等效恢复后与对象存储 Evidence 引用一致。
8. 30k 回归与开发峰值并存时满足 deadline、公平、SUT 和成本门禁。

---

## 13. 官方技术依据

- [FastAPI Features](https://fastapi.tiangolo.com/features/)：OpenAPI、JSON Schema、Pydantic 验证、依赖注入和流式能力。
- [FastAPI Background Tasks Caveat](https://fastapi.tiangolo.com/tutorial/background-tasks/#caveat)：重型后台计算应由独立进程/服务器机制处理。
- [SQLite Appropriate Uses](https://www.sqlite.org/whentouse.html)：同一数据库文件单 writer；很多并发写者优先 client/server。
- [PostgreSQL SELECT / SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.html)：适用于多个消费者访问 queue-like table。
- [PostgreSQL Partitioning](https://www.postgresql.org/docs/current/ddl-partitioning.html) 与 [PITR](https://www.postgresql.org/docs/current/continuous-archiving.html)：企业数据生命周期与恢复能力。
- [Docker Resource Constraints](https://docs.docker.com/engine/containers/resource_constraints/)：容器默认无资源约束，必须显式限制。
- [Docker Rootless Mode](https://docs.docker.com/engine/security/rootless/)：daemon 与容器以非 root 用户运行以降低风险。
- [Playwright Docker](https://playwright.dev/docs/docker)：官方镜像默认 root 会关闭 Chromium sandbox，并给出非 root/seccomp 指引。
- [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/)：Indexed Job 和同一程序可能重复启动的语义。
- [Kueue Overview](https://kueue.sigs.k8s.io/docs/overview/)：Job quota、priority、fair sharing、ResourceFlavor 和 admission。
- [Kubernetes Node Autoscaling](https://kubernetes.io/docs/concepts/cluster-administration/node-autoscaling/)：节点扩缩依据 Pod requests/调度约束，不直接以运行后真实使用量推断。
- [OpenTelemetry](https://opentelemetry.io/docs/what-is-opentelemetry/)：供应商中立的 traces、metrics 和 logs 采集框架。
- [gVisor Compatibility](https://gvisor.dev/docs/user_guide/compatibility/)：存在系统调用/子系统兼容性缺口，必须实测。

---

## 14. 架构未决事项

| ID | 未决事项 | 默认处理 | 关闭条件 |
|---|---|---|---|
| OI-ARCH-001 | 公司 OIDC、Secret Manager、对象存储和监控现状 | 优先复用，不自建平行基础设施 | 运维提供可用服务清单 |
| OI-ARCH-002 | ECS OS/CPU/内存/磁盘/网络规格 | 不冻结具体 C_safe | 容量与安全压测 |
| OI-ARCH-003 | rootless Docker 对 Playwright 的兼容与性能 | 条件批准 | 代表性浏览器矩阵验收 |
| OI-ARCH-004 | gVisor/Kata 安全收益与兼容性 | 高风险 Pool 候选 | 安全/性能对比报告 |
| OI-ARCH-005 | SUT egress gateway 的域名/IP/协议需求 | 默认 HTTP(S)+固定目标，其他协议拒绝 | 环境目标矩阵 |
| OI-ARCH-006 | 企业是否已有托管 Kubernetes/Kueue 能力 | 无则先 VM Pool | 平台运维能力评审 |
| OI-ARCH-007 | PostgreSQL 托管还是同 ECS 自建 | MVP 可同机、必须异机备份；企业托管优先 | 成本/RPO/RTO 评审 |
| OI-ARCH-008 | 批准峰值、deadline 和预算 | 自动扩缩保持 Shadow/禁用 | PRD TBD 关闭 |
