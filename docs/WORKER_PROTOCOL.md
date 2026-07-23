# qarunner Worker 协议参考规范

> 文档类型：Reference / 接口与故障语义参考
> 文档编号：QARUNNER-WPR-001
> 版本：V0.1.0
> 状态：评审中
> 更新日期：2026-07-12
> 适用基线：PRD V7.4.1、SRS V1.4.1、SOR V1.4.1、Release Gate Catalog V0.1.0-RC2

本文是控制面与专用 Worker agent 之间的协议参考规范。它把
[系统需求](SYSTEM_REQUIREMENTS.md)、[安全与运维需求](SECURITY_OPERATIONS_REQUIREMENTS.md)
中的边界、状态和证据要求冻结为可实现、可故障注入、可验收的合同。本文不表示当前仓库已经实现该协议；当前实现差距仍登记为 `GAP-021/SOR-GAP-023`。

目标读者是后端、QA、安全和运维负责人。产品范围和价值以
[产品方向](DIRECTION.md) 与 [产品需求](REQUIREMENTS.md) 为准。

## 0. 规范语言与边界

本文使用以下规范词：

- **必须**：违反即拒绝请求、阻断启动或使相关发布门禁失败。
- **应**：默认实现要求；需要例外时必须记录理由、补偿控制和批准人。
- **可以**：实现选择，不得改变本文定义的外部语义。

V7.4 的固定部署边界是：受信内网、单团队使用、不直接暴露公网；管理员可信，普通用户半可信，测试代码和依赖不可信；一台控制面主机、一台专用加固 Worker 主机，每个 Run 使用全新一次性容器。

本文不定义：

- 多 Worker 调度、弹性扩缩容、自动故障切换或公网 SaaS。
- 通用远程 Docker API、任意 shell/argv、任意宿主路径或任意网络代理。
- 测试代码、断言、SUT 数据或测试输出真实性的证明。
- MQ、WebSocket、gRPC 作为 V7.4 的必要依赖。

## 1. 协议决策

### 1.1 传输

Worker 主动向控制面建立 HTTPS 长轮询连接，使用双向 TLS（mTLS）。Worker 不监听入站端口；控制面不主动连接 Worker。内部 API 使用独立 listener/route、独立 audience 和网络 ACL，拒绝浏览器、Cookie、用户 JWT 和管理员 API token。

V7.4 不引入消息队列。若未来更换传输，应用层的 assignment、fencing、幂等、sequence、digest 和恢复语义必须保持不变。

TLS 要求：

- 使用批准的 TLS 版本和密码套件，禁用 TLS 1.3 0-RTT。
- 每次请求重新校验证书 serial、identity version、worker generation 和吊销状态；旧的长连接不能绕过吊销。
- 请求和响应均带 `correlation_id`；连接断开不代表任务未启动。

### 1.2 启动屏障

控制面与 Worker 之间必须存在幂等 `commit-start` 屏障：

1. Worker claim assignment，校验 job digest 和本地能力。
2. Worker 请求 `commit-start`。
3. 控制面原子提交 `start_commit_id` 和 `task_attempt_id` 后才允许 Worker 创建容器。
4. Worker 持久化 journal，再调用 Docker create/start。

只有控制面持久记录“从未成功提交 start commit”，且 Worker journal 与 Docker 标签核对都证明没有创建容器时，assignment 才能被 fence 后重新排队。任何响应丢失、Docker API 超时、agent 崩溃或资源核对不完整，都必须按 `worker_lost/attempt_unknown` 处理，不得自动启动第二个 executor。

V7.4 的初始 fail-closed 默认是：任何已经 start-committed 且身份不确定的 task 都不自动重试。未来若要允许 source/dependency/report task 自动重试，必须另行批准版本化 Task Retry Policy，证明旧 staging 已隔离或清空、没有外部副作用、不会覆盖不可变 Revision/Snapshot/Manifest；该策略永远不能允许不确定 executor 自动产生第二个 execution attempt。

### 1.3 Playwright 与真实被测环境

Playwright **不必须**访问真实被测环境。默认执行口径仍为 `isolated`，适用于 mock、fixture、静态页面和不需要外部 SUT 的测试。只有测试确实需要真实 SUT 时，管理员才可批准 `approved-target` Run，并同时绑定不可变 Suite Revision、Profile revision、Dependency Snapshot、Target Environment、Network Policy、Target Access Grant 和 Environment Lease。

浏览器页面 URL 可达不等于授权。approved-target 的出站控制必须覆盖 Node、浏览器、request context、service worker、DNS、HTTP(S)、WebSocket、重定向和下载；不可信测试代码不能通过替换 URL、环境变量、DNS 或参数扩大范围。

## 2. 信任边界

| 主体 | 信任级别 | 允许能力 | 明确禁止 |
|---|---|---|---|
| 控制面 | 高价值受信 | 认证、授权、Job/Assignment 元数据、状态 CAS、证据验收和分发 | Docker socket、测试代码、依赖安装、Git 处理、报告生成 |
| Worker agent | 受信执行控制组件 | 领取冻结领域任务、本机调用 allowlisted Docker API、停止和清理资源、上传证据 | 控制面数据库、管理员会话密钥、任意 Docker 参数、任意命令 |
| 一次性容器 | 不可信 | 仅获得本 Run 的 workspace、依赖快照、results 和批准目标网络 | agent、Docker socket、控制面、其他 Run、宿主 namespace/device |
| 普通用户 | 半可信 | 自己资源范围内创建/查看 Run；使用已批准 Profile/Grant | 创建或扩大 Network Policy、Target Environment、Worker 能力或秘密范围 |
| 管理员/运维 | 可信但须审计 | 注册/轮换 Worker、批准策略、drain/quarantine、发布和恢复 | 绕过审计或把未知 attempt 当成未启动 |

Worker 主机共享宿主内核。V7.4 接受该残余风险，并以专用主机、补丁、seccomp/LSM、无 host namespace/device、一次性容器和控制面分离降低影响。公网、多租户、生产 SUT 或高对抗代码应重新评审并升级为每 Run VM/microVM。

## 3. 版本、身份与注册

### 3.1 协议版本

API 前缀固定为 `/internal/worker/v1`。协议版本、Job schema、Event schema、Upload schema 和策略版本分别校验；未知版本返回 `PROTOCOL_VERSION_UNSUPPORTED`，不得静默按旧版本解释。

### 3.2 Worker 身份

Worker 身份由 mTLS 证书和控制面注册记录共同决定，body 中重复声明的身份字段仅用于一致性校验。最小身份记录如下：

| 字段 | 语义 |
|---|---|
| `worker_id` | 逻辑 Worker 稳定 ID |
| `host_id` | 当前专用主机稳定 ID |
| `worker_generation` | 主机重建或身份替换时单调递增；用于 split-brain fencing |
| `identity_version` | 同一主机证书轮换时递增 |
| `cert_serial` | 当前证书 serial |
| `agent_instance_id` / `boot_id` | 当前 agent 进程启动实例 |
| `agent_version` / `agent_image_digest` | agent 可追溯版本 |
| `host_baseline_digest` | 主机安全基线摘要 |
| `capabilities_digest` | 能力清单摘要 |

`worker_generation` 与 `identity_version` 的区别必须保留：同主机证书轮换只递增 `identity_version`；重建或更换主机必须递增 `worker_generation`，并 fence 旧 generation 的全部 assignment、renew、事件和上传。

### 3.3 注册与轮换

1. 管理员预注册 `worker_id/host_id` 和允许的能力范围，生成单次、短期 enrollment token。
2. Worker 使用固定 CA 指纹的 server-auth TLS 消费 token，并本地产生私钥/CSR；token 原子消费。
3. 证书轮换使用新本地私钥和 pending identity；`activate` 成功后控制面原子切换 active identity，立即拒绝旧 serial。
4. 主机重建必须生成新 `worker_generation`，旧证书、token 和 assignment 全部失效；新 Worker 在 host baseline、agent、Docker 和 reconcile 通过后才可 `ready`。
5. assignment TTL 不得超过当前证书剩余有效期减安全余量。

Worker 不能自行修改 `worker_id`、`host_id`、generation、task scope、capabilities、镜像或网络策略。

## 4. 领域模型

### 4.1 Job

Job 是控制面冻结后可执行的领域任务，不包含任意命令。必须包含：

- `job_id`、`job_schema_version`、`task_type`、`subject_type`、`subject_id`。
- `suite_revision_id`、`source_content_digest`（适用时）。
- `dependency_snapshot_id`、`dependency_digest`（适用时）。
- 精确的 stage/executor `image_digest`，禁止 `latest`。
- 固定 runner/profile 参数、mount/network/resource/output policy 的 ID、版本和 digest。
- secret ref ID/version/scope（不含明文）。
- Target Access Grant、Environment Lease、目标和网络策略版本/fence（适用时）。
- `deadline_at`、`timeout_s`、`canonical_job_digest`。

`canonical_job_digest` 使用 RFC 8785/JCS 等批准的规范化编码后计算 SHA-256。纳入执行语义的字段必须全部参与摘要；`assignment_token`、续租时间、传输 request ID 和接收时间不得参与摘要。Worker 必须重算并比对摘要。

支持的 `task_type` 最小集合：

| task type | subject 约束 | `run_id` |
|---|---|---|
| `source_acquisition` | Suite/Revision | 必须缺省 |
| `dependency_prepare` | Suite Revision + Dependency Snapshot | 必须缺省 |
| `executor` | Run + Suite Revision + Snapshot | 必填 |
| `report` | Run + evidence staging | 必填 |

不允许把 `ProcessRunner(cmd, cwd, env)`、shell 字符串或 Docker create JSON 作为远程协议字段。
控制面只发送上述领域字段；Worker 根据本地版本化 allowlist 编译实际 container spec，并上传 `effective_container_spec_digest` 供控制面核对。

### 4.2 Assignment 与 Attempt

Assignment 表示一次控制面分配，Attempt 表示可能已经越过执行启动点的执行尝试。字段：

| 字段 | 语义 |
|---|---|
| `assignment_id` | 分配稳定 ID |
| `job_id` | 冻结 Job |
| `run_id` | executor/report 必填；其他 task 缺省 |
| `worker_id` / `worker_generation` | 领取身份 |
| `assignment_fence` | 单调 fencing 值 |
| `assignment_token` | 256-bit opaque capability；服务端只存 hash |
| `claimed_at` / `expires_at` | 控制面 UTC 时间 |
| `max_task_deadline` | 任务硬截止时间 |
| `start_commit_id` | commit-start 后生成 |
| `task_attempt_id` | 该领域 task 的 attempt 稳定 ID |
| `execution_attempt_id` | 仅 executor task 使用；一个 start-committed executor 对应一个不可复用 ID |
| `container_id` / `effective_spec_digest` | 创建后追加 |
| `last_accepted_event_seq` | 控制面已接收的连续序号 |
| `upload_session_id` | 证据上传作用域 |

Assignment 状态为：

`claimed → start_committed → running → stopping/collecting → uploading → released`

旁路状态为：`expired_prestart`、`fenced`、`attempt_unknown`。`attempt_unknown` 是安全终态，不允许自动转回 queued/running。

assignment token、upload token、mTLS 私钥和控制面凭证只由 Worker agent 持有，不得进入一次性容器、argv、环境变量、挂载、报告或日志。Worker 创建的容器、挂载、volume、network、workspace 和 staging 必须带可核对 label/metadata，至少绑定 worker generation、assignment/fence、start commit、task/execution attempt 和 job digest。

### 4.3 Worker 状态

Worker 生命周期只允许以下状态：

| 状态 | 进入条件 | 新 claim |
|---|---|---|
| `registered` | 已注册但尚未通过 readiness | 否 |
| `ready` | 身份、基线、agent、Docker、镜像、时钟和 reconcile 通过 | 是 |
| `draining` | 管理员/运维维护或容量保护 | 否；在途任务可收敛 |
| `offline` | heartbeat/连接超过阈值 | 否 |
| `quarantined` | 清理失败、残留、身份冲突、重放或基线不合格 | 否 |

`degraded` 不是生命周期状态。磁盘、Docker、上传、时钟或某阶段能力异常通过 `health.degraded=true` 和具体 `degraded_components[]` 表示；任何导致无法证明隔离的异常必须直接进入 `quarantined`。

`blocked-worker` 不是 Worker 状态，只能作为 Run/Schedule 的 `wait_reason`，例如 `worker-unavailable`、`worker-capacity` 或 `worker-quarantined`。

### 4.4 事件

事件字段：

- `event_id`、`batch_id`、`event_seq`（从 1 连续递增）。
- `event_type`、`payload_schema_version`、`payload_digest`、受限 `payload`。
- `occurred_at` 仅用于诊断；控制面 `received_at` 是审计排序时间。
- `assignment_id`、`assignment_fence`、`worker_generation`、`canonical_job_digest`。

大日志和文件不放入事件 payload，必须走 Upload Session。

### 4.5 Upload Session 与 Manifest

Upload Session 绑定 `assignment_id`、fence、job digest、短期 `upload_token`、到期时间、文件数/总字节/分块上限。token 不进入 URL、日志、容器环境变量；请求必须同时通过 mTLS worker identity、generation 和 fence 校验。

文件条目必须包含：规范化相对路径、`file_id`、size、SHA-256、content class、sensitivity 和 `present|truncated|missing` 状态。每个分块包含 `part_no`、长度和 `chunk_sha256`。complete 时控制面重新计算文件 digest 和规范化 Manifest root digest；成功后只读。

Manifest 与 Run 终态在同一持久原子单元收敛：`finalized` 或 `finalize_failed`。Worker 只能报告事实，不能直接指定任意 Run 终态。

## 5. API 契约

### 5.1 通用请求/响应

所有请求必须使用 JSON（分块上传除外），带：

```text
X-Request-ID: <unique request id>
X-Protocol-Version: worker-protocol/v1
```

错误体固定为：

```json
{
  "error_code": "JOB_DIGEST_MISMATCH",
  "message": "safe, non-secret description",
  "correlation_id": "...",
  "retryable": false,
  "details": {"required_action": "reconcile"}
}
```

`details` 不得泄露秘密、宿主路径、Docker socket、目标内部拓扑或其他 Run 内容。

### 5.2 端点

| 方法与路径 | 目的 | 关键规则 |
|---|---|---|
| `POST /internal/worker/v1/enrollments:consume` | 首次注册 | 单次 token；成功后立即失效 |
| `POST /internal/worker/v1/identity:rotate` | 申请新身份 | 旧身份仍可申请，但不能改绑定字段 |
| `POST /internal/worker/v1/identity:activate` | 激活新证书 | 原子切换 identity version，旧 serial 立即拒绝 |
| `POST /internal/worker/v1/workers/self:heartbeat` | 健康/能力报告 | body 身份必须与 mTLS 一致 |
| `POST /internal/worker/v1/claims:next` | 长轮询领取 | 原子 claim；无任务返回 204 |
| `POST /internal/worker/v1/assignments/{id}:commit-start` | 启动屏障 | 同 request 重放返回同一 commit |
| `POST /internal/worker/v1/assignments/{id}:renew` | 续租/下发指令 | 只接受更高 lease version |
| `POST /internal/worker/v1/assignments/{id}/events` | 上传状态事件 | 严格连续 sequence |
| `POST /internal/worker/v1/assignments/{id}/uploads` | 创建上传会话 | 只允许该 assignment 的 manifest 范围 |
| `GET /internal/worker/v1/uploads/{session}` | 查询断点 | 不返回 token 明文 |
| `PUT /internal/worker/v1/uploads/{session}/files/{file_id}/parts/{part_no}` | 上传分块 | 同 digest 重放幂等，异 digest 冲突 |
| `POST /internal/worker/v1/uploads/{session}:complete` | 完成 Manifest | 重算 digest；完成后不可覆盖 |
| `POST /internal/worker/v1/assignments/{id}:release` | 报告事实并释放 | 由控制面推导 Run 终态 |
| `POST /internal/worker/v1/workers/self:reconcile` | agent/控制面重启恢复 | 完成前禁止新 claim |

### 5.3 Claim

请求至少包含 `request_id`、`worker_generation`、`identity_version`、`capabilities_digest`、各 task 可用 slot 和 `long_poll_ms`。控制面只返回与能力、策略和容量匹配的 Job，并生成唯一 assignment fence/token。

同 `request_id` + 同规范化请求返回原结果；同 key 不同 body 返回 `CLAIM_IDEMPOTENCY_CONFLICT`。并发 claim 同一 Job 时最多一个 assignment 有效。

### 5.4 Commit-start

请求：

```json
{
  "request_id": "...",
  "assignment_fence": 17,
  "canonical_job_digest": "sha256:...",
  "effective_container_spec_digest": "sha256:..."
}
```

返回 `start_commit_id`、`task_attempt_id`、条件性的 `execution_attempt_id`、lease version/TTL 和 max deadline。Worker 必须先持久化返回值，再调用 Docker。相同 request 重放返回同一结果；不同 digest、旧 fence 或已进入 terminal 返回冲突。

### 5.5 Renew

请求至少包含 request ID、fence、job digest、当前 lease version、最后接受的 event seq 和 observed phase。响应包含单调新 lease version、TTL 和指令：

`continue | stop_cancelled | stop_expired | stop_policy_revoked | drain_after_current | quarantine`

Worker 用“请求发出时的本地 monotonic 时间 + TTL - safety margin”计算 deadline。迟到响应不得复活已经本地过期的 assignment。

### 5.6 Events

事件必须按 `event_seq` 连续提交。控制面按以下规则处理：

| 情况 | 响应 | 副作用 |
|---|---|---|
| `seq == next` 且 digest 正确 | 200 | 接受并递增 next |
| 相同 seq/id/digest 重放 | 200 duplicate | 不重复副作用 |
| 相同 seq 不同内容 | 409 `EVENT_SEQUENCE_CONFLICT` | 审计并 quarantine |
| `seq > next` | 409 `EVENT_SEQUENCE_GAP` | 返回 expected next，不覆盖状态 |
| `seq < next` 且非已知重放 | 409 `EVENT_SEQUENCE_CONFLICT` | 拒绝 |
| terminal 后事件 | 409 `EVENT_AFTER_TERMINAL` | 拒绝 |

### 5.7 Release

Release 只能报告：`completion_kind`、exit code/signal、observed phase、cleanup status、last event seq、upload session、manifest status/root digest 和受限 failure code。控制面根据 assignment/run 状态机原子提交终态；Worker 不能发送任意 `completed`、`failed` 或 `cancelled`。

## 6. 状态与时间语义

### 6.1 Run 映射

| 事件 | Run 结果 |
|---|---|
| 未 claim 或 Worker 未 ready | `queued`，`wait_reason=worker-unavailable/capacity` |
| claim 成功但未 start commit | 仍为 `queued` 或 `assigned` 内部态，不得冒充 `running` |
| start commit 成功 | `running`；attempt 已存在 |
| preflight/Grant/Lease 失败 | `failed`，稳定策略错误码 |
| cleanup + Manifest finalized | `completed` 或按测试 verdict 展示 `passed/test_failed` |
| timeout/cancel/revocation | 对应终态，必须收敛 Manifest 和 cleanup |
| 启动后身份/资源不确定 | `failed`，`error_code=worker_lost/attempt_unknown` |

`worker_lost/attempt_unknown` 是唯一的 Worker 启动不确定稳定 Run 错误码；不再使用 `worker_lost/unknown`。它表示平台无法证明 executor 是否已启动，不表示测试失败。

### 6.2 失联与恢复

- 短暂断线：只允许同一 `worker_id + host_id + worker_generation + assignment_id + assignment_fence` 在恢复窗口内续报。
- Worker 本地 deadline 到期：停止容器，切断 approved-target 访问，保存本地证据到隔离 staging；不能等待控制面恢复后继续执行。
- 控制面重启：恢复未终态 assignment 和 journal，不立即把 `running` 标为 `failed`，也不生成第二 assignment。
- Worker agent 重启：先 reconcile；无法解释的容器、挂载、volume、network、workspace 或 upload staging 立即 quarantine。
- 超过 recovery window：收敛 `worker_lost/attempt_unknown`，自动 execution attempt 数必须为 0。
- 用户重跑：只能显式创建新 Run，并记录 `rerun_of_run_id`；不能把未知 attempt 覆盖为新结果。

### 6.3 Drain、取消与吊销

Drain 立即停止新 claim；在途 assignment 可在 drain deadline 内续租并收敛。取消、Grant 吊销或超时由控制面发指令，Worker 必须在批准的 stop timeout 内停止容器、撤销目标访问、执行 cleanup 并上传事实。quarantined Worker 拒绝 claim/start/renew/upload，只允许 reconcile 和受控恢复。

### 6.4 时间

- 期限、证书、Grant、Lease、审计时间使用 UTC wall clock。
- timeout、TTL、deadline 和进程耗时使用 monotonic clock。
- clock skew 超过批准基线时，Worker 不得 claim/start；控制面 execution readiness=false。
- 任何参数值来自版本化 Operational Threshold Catalog；本文只定义字段和关系，不擅自给生产默认值。

## 7. 证据交付

证据路径必须是规范化相对路径；拒绝绝对路径、`..`、符号链接、device/FIFO/socket、路径碰撞、超限文件和归档膨胀。日志、报告、截图、录屏、trace、JUnit 和 Allure 均视为不可信内容。

上传流程：

1. Worker 在 staging 中完成收集并生成文件条目。
2. 创建作用域 Upload Session，取得短期 upload capability。
3. 分块上传；网络中断只重试/续传同 session，不重跑 executor。
4. `complete` 时控制面重算 file digest 和 Manifest root digest。
5. 控制面将 `manifest_status` 与 Run 终态原子提交；完成后只读。

以下情况禁止覆盖已接受证据：

- 旧 assignment fence、旧 generation、过期 upload token。
- 同一 part/file 的 digest 不同。
- Manifest root digest 不一致。
- terminal 后迟到事件或上传。

证据完整性只证明平台收集后的内容未被无声修改，不证明测试代码输出真实或断言正确。主动 HTML/SVG/JavaScript 必须在独立无会话 origin 执行，或以 attachment/download 方式提供。

## 8. 稳定错误码与拒绝矩阵

### 8.1 错误码

| HTTP | `error_code` | 典型处理 |
|---|---|---|
| 401 | `WORKER_MTLS_REQUIRED`、`WORKER_IDENTITY_INVALID`、`WORKER_IDENTITY_EXPIRED`、`WORKER_IDENTITY_REVOKED` | 不重试同一身份；重新 enrollment/rotation |
| 403 | `WORKER_BINDING_MISMATCH`、`WORKER_SCOPE_DENIED`、`WORKER_QUARANTINED`、`ASSIGNMENT_TOKEN_INVALID`、`UPLOAD_SCOPE_DENIED`、`TASK_TYPE_DENIED` | 记录审计；不得扩大权限 |
| 409 | `CLAIM_IDEMPOTENCY_CONFLICT`、`ASSIGNMENT_FENCED`、`ASSIGNMENT_EXPIRED`、`WORKER_GENERATION_STALE`、`JOB_DIGEST_MISMATCH`、`START_BARRIER_REQUIRED`、`START_COMMIT_CONFLICT`、`LEASE_VERSION_STALE`、`INVALID_ASSIGNMENT_TRANSITION`、`EVENT_SEQUENCE_GAP`、`EVENT_SEQUENCE_CONFLICT`、`EVENT_AFTER_TERMINAL`、`UPLOAD_PART_CONFLICT`、`UPLOAD_SESSION_EXPIRED`、`UPLOAD_ALREADY_FINALIZED`、`MANIFEST_DIGEST_MISMATCH`、`RECOVERY_WINDOW_EXPIRED`、`ATTEMPT_IDENTITY_UNKNOWN` | 按 details 恢复或终止；不能盲目重跑 |
| 413 | `EVENT_TOO_LARGE`、`UPLOAD_LIMIT_EXCEEDED` | 截断/失败；不改变 attempt |
| 422 | `PROTOCOL_VERSION_UNSUPPORTED`、`JOB_SCHEMA_INVALID`、`SUBJECT_BINDING_INVALID`、`UPLOAD_PATH_INVALID`、`UPLOAD_PART_DIGEST_INVALID` | 修正协议或 Job；不重试原请求 |
| 429 | `WORKER_RATE_LIMITED` | 按 Retry-After 退避 |
| 503 | `CONTROL_NOT_READY`、`UPLOAD_STORAGE_UNAVAILABLE`、`WORKER_POLICY_UNAVAILABLE` | 可重试控制通道；不代表 executor 未启动 |
| 204 | 无任务 | 长轮询继续；不是错误 |

### 8.2 必须拒绝的请求

- 无 mTLS、错误 audience/EKU/SAN、过期/吊销/旧证书或旧 generation。
- body 身份与证书不一致；使用 Cookie、用户 JWT 或管理员 token 调 Worker API。
- draining/offline/quarantined/not-ready Worker claim；未批准的 task scope/capability。
- 旧 fence、错 Worker、错 job digest、过期 token、迟到 lease 响应。
- executor/report 缺 `run_id`，或 source/dependency 携带 `run_id`。
- 未完成 commit-start 就创建/上报容器；start commit 后不确定 attempt 自动重排。
- 任意 Docker image/tag、entrypoint/shell、host path、bind mount、device、privileged、cap_add、host network/namespace、security_opt、restart policy 或 Docker socket。
- 事件 sequence 回退/跳号/同序异体、非法状态跃迁、Worker 试图直接回写 Run 终态。
- 上传路径、类型、大小、part/root digest 异常，或 finalized 后覆盖。
- reconcile 发现无主容器/挂载/临时目录、effective spec digest 或 host baseline 不符。

## 9. 重启、Reconcile 与 Runbook

Worker agent 必须有本地持久 journal、systemd/watchdog 或等价进程守护，以及容器硬超时。仅依赖 agent 进程存活不足以证明容器会被停止；approved-target 的访问撤销还必须由独立网络/namespace 边界执行。

启动 reconcile 顺序：

1. 校验 host baseline、agent identity、generation、clock 和 Docker daemon。
2. 枚举带 qarunner labels 的容器、挂载、volume、network、workspace 和 upload staging。
3. 将本地 journal 与控制面 assignment/attempt 逐项比对。
4. 旧 generation、无主资源、spec digest 不一致或无法解释的资源进入 quarantine staging；不直接删除证据。
5. 对已 fence/terminal assignment 停止并清理；对同 generation 且未过 deadline 的 assignment 恢复续报。
6. 只有 reconciliation 成功且没有残留风险时才变为 `ready`。

运维 Runbook 的最小结束检查：

- 新 claim 已冻结或恢复；
- assignment、lease、容器、挂载、staging 和 upload session 状态可解释；
- `attempt_unknown_total` 已登记；
- `automatic_second_execution_attempt_total` 仍为 0；
- 清理失败已触发 quarantine，不能手工点回 ready。

## 10. 审计、指标与健康

### 10.1 审计事件

至少记录：注册、轮换、吊销、generation 变更、ready/drain/offline/quarantine、claim、commit-start、renew、fence、sequence conflict、digest mismatch、reconcile、容器 create/start/stop/remove、cleanup failure、upload finalize 和 late write。审计主体为 worker identity 或管理员，不使用用户可控文本作为唯一身份。

### 10.2 指标

指标标签禁止使用 `run_id`、`assignment_id` 等高基数字段；高基数细节进入结构化日志和审计。最小指标：

- `worker_ready`、`worker_status`、heartbeat age、证书剩余有效期、host baseline drift。
- queue depth/oldest age（按 wait reason）、active assignment/slot、claim 成功/拒绝。
- stale fence、replay、sequence conflict、digest mismatch、旧 generation 请求。
- container start/stop/remove、orphan resource、reconcile duration/result、offline duration。
- `attempt_unknown_total`、`automatic_second_execution_attempt_total`；后者任何增量均为 Critical。
- upload retry、chunk conflict、late upload、Manifest finalize duration/failure。

单 Worker 离线时：存在 queued/running Run 立即告警；完全空闲时可降级告警。控制面 readiness 不应因 Worker offline 而整体失败，但必须暴露 `execution_ready=false`。

## 11. Phase 1 实现与 TDD 切片

协议文档不替代实现计划。按以下顺序迁移，每片都先写失败测试：

1. **状态与数据契约**：Worker、Job、Assignment、Attempt、Manifest；canonical digest、point-of-no-return、窄字段 CAS。
2. **SQLite 原子语义**：claim race、fence、sequence 幂等、唯一终态和 idempotency key。Schema migration 需按项目规则单独评审。
3. **Worker Gateway**：mTLS/generation/scope 校验、Fake Worker、claim/commit-start/renew/release/reconcile。
4. **恢复语义**：替换控制面启动即把 RUNNING 标 FAILED 的逻辑；证明同 assignment 恢复和自动第二 execution attempt 为 0。
5. **pytest executor**：把 Docker 生命周期迁到 Worker，固定 image digest、labels、journal、deadline、cleanup/quarantine。
6. **证据与报告**：JUnit/Allure 在 Worker 阶段容器运行；上传分块、digest、Manifest finalize/corruption。
7. **Source/Dependency**：clone/fetch 和依赖准备迁 Worker，生成 immutable Revision/Snapshot；控制面不执行 Git/npm。
8. **Playwright approved-target**：Grant、Lease、全进程出站控制、到期本地 fencing 和清理。
9. **收口**：移除控制面 Docker SDK/socket/Subprocess/collector/reporter 装配；新增双主机部署和故障注入门禁。

代码迁移锚点见 [架构](../ARCHITECTURE.md) 与 [发布验收目录](RELEASE_GATE_CATALOG.md)。当前必须重点替换：

- `src/qarunner/api/deps.py` 的 DockerRunner/SubprocessRunner/collector/reporter 装配；
- `src/qarunner/adapters/asyncio_scheduler.py` 与 `sqlite_store.py` 的 dequeue 即 RUNNING 语义；
- `src/qarunner/core/orchestrator.py` 的本地执行、报告和终态混合职责；
- `src/qarunner/api/app.py` 的启动即失败恢复；
- `src/qarunner/api/routes.py` 的控制面 Git、npm 和本地文件读取。

## 12. 验收映射

本文的协议行为映射到 [Release Gate Catalog](RELEASE_GATE_CATALOG.md) 的稳定 family/case：

| 协议主题 | 主要 RCF | 最小验收情景 |
|---|---|---|
| claim、fence、commit-start、续租、sequence | `RCF-003` | claim race、expired-before-start、create ambiguous、same assignment reconnect、sequence conflict |
| Run 状态、控制面重启、未知 attempt | `RCF-008` | control restart、worker lost、rerun-only、唯一终态 |
| agent journal、残留和隔离 | `RCF-006` | agent restart、orphan resource、cross-run residue、cleanup quarantine |
| 调度不可用和恢复 | `RCF-011` | triggered/blocked/missed、Worker offline 不静默 |
| Worker 重建、generation、旧主机 fencing | `RCF-013/014` | identity rotate、split-brain、旧主机拒绝 claim/renew/upload |
| 证据分块、续传和 Manifest | `RCF-009/023` | upload resume、part conflict、late upload、corrupt manifest |
| Playwright 真实目标访问 | `RCF-007/018/021` | approved-target positive/negative、Grant mismatch/revoke、stale Lease/cleanup |
| 控制面无执行能力 | `RCF-024` | 无 socket/SDK/subprocess fallback，Worker offline 无本地回退 |

这些 case 目前大多是 `planned-blocking`；目录身份冻结不等于实现通过。每条证据必须记录候选 commit、控制面/Worker/阶段镜像 digest、schema/config/policy 版本、worker identity/generation、assignment/job digest 和 fencing token（适用时）。

## 13. 当前非符合项与发布门禁

在以下事项完成前，本协议只能作为设计基线，不能宣称 V7.4 生产就绪：

- 控制面仍可构造 DockerRunner、SubprocessRunner、JUnit/Allure reporter 或访问 raw Docker socket；
- Run 仍由本地 scheduler dequeue 后直接变为 RUNNING；
- 没有 Worker/Job/Assignment/Attempt/Manifest 持久模型、mTLS/generation、commit-start 和 reconcile；
- Source Acquisition、依赖安装、报告生成和结果解析仍在控制面；
- Playwright 只有 legacy isolated 路径，没有 approved-target + Grant + Lease 全链路；
- 启动恢复仍把不确定的 RUNNING 直接标 FAILED，或任何路径自动重跑未知 attempt；
- 容器/挂载/临时目录清理失败不会 quarantine；
- 当前测试没有双主机、控制面无 socket、Worker 独占 socket 的等价验收环境。

上述差距对应 `GAP-021`、`SOR-GAP-023` 及相关 RCF planned-blocking。任何例外必须进入
[需求追踪与发布证据矩阵](REQUIREMENTS_TRACEABILITY.md)，并由产品、研发、QA、安全、运维按五方规则签署。

## 附录 A：最小交互序列

### A.1 正常 executor

```text
Control Plane                  Worker agent                 Docker
     |       claims:next             |                        |
     |<------------------------------|                        |
     | assignment + fence/token ---->|                        |
     |<------ commit-start -----------|                        |
     | start_commit + attempt ------>|                        |
     |                                | persist journal        |
     |                                |----------------------->|
     |                                |   create/start          |
     |<----------- events/renew ------|                        |
     |<----------- uploads -----------|                        |
     |<----------- release -----------|                        |
     | atomic Manifest + Run terminal |                        |
```

### A.2 响应丢失

```text
commit-start request -> control plane commits start_commit
response lost        -> Worker retries same request_id
same response        -> Worker persists journal and creates container
cannot prove commit or container state -> attempt_unknown; no automatic retry
```

## 附录 B：相关文档

- [产品方向](DIRECTION.md)
- [产品需求](REQUIREMENTS.md)
- [系统需求](SYSTEM_REQUIREMENTS.md)
- [安全与运维需求](SECURITY_OPERATIONS_REQUIREMENTS.md)
- [需求追踪与发布证据矩阵](REQUIREMENTS_TRACEABILITY.md)
- [发布验收目录](RELEASE_GATE_CATALOG.md)
- [部署说明](deployment.md)
