# 测试执行平台详细设计

> 文档编号：QEP-DES-001<br>
> 版本：V0.1.0<br>
> 状态：草稿，待详细设计评审<br>
> 日期：2026-07-13<br>
> 上游：[MVP PRD](01_MVP_REQUIREMENTS.md)、[企业 PRD](02_ENTERPRISE_REQUIREMENTS.md)、[架构设计](03_ARCHITECTURE_DESIGN.md)<br>
> 设计原则：本文件先于现有代码对比冻结，不以现有类、表或 API 为前提

---

## 1. 设计目标与适用范围

### 1.1 本文件回答的问题

- 领域对象、状态和不变量如何表达？
- PostgreSQL 中哪些数据是业务事实，如何保证幂等、fencing 和可恢复？
- 用户 API、Worker 协议、证据上传和错误码如何定义？
- Manifest、分片、调度、容量计划和公平性如何计算？
- 单 ECS Docker Worker 与企业 Kubernetes 后端如何共享同一业务语义？
- 不可信测试的文件、进程、资源、网络、秘密和证据边界如何落地？
- 组件崩溃、网络分区、重复消息和状态不确定如何收敛？
- 实现时应先写哪些测试，发布证据如何追踪到需求？

### 1.2 设计层级

| 层级 | MVP | 企业目标 |
|---|---|---|
| 控制面 | 模块化单体 API + 独立 Schedule/Planner/Dispatcher/Reconciler 进程 | 同一模块边界，多副本和 leader/claim 扩展 |
| 状态 | PostgreSQL 单实例 + 异机备份 | PostgreSQL HA + PITR/等效恢复 |
| 执行 | 本机 Worker Agent + 专用 rootless Docker | 静态 VM Worker；达到门禁后 Kubernetes Job/Kueue |
| 证据 | 对象存储 + 本地有界 spool | 对象存储 lifecycle/versioning/可选不可变保留 |
| 观测 | JSON logs + OTel SDK + `/metrics` | OTel Collector + Metrics/Alert/Log backend |

### 1.3 核心设计不变量

| 不变量 ID | 约束 |
|---|---|
| INV-001 | 用户测试、收集器、依赖安装和报告生成只能在一次性沙箱中运行。 |
| INV-002 | Batch 输入、Case Manifest 和 Shard Plan 一旦批准执行即不可原位修改。 |
| INV-003 | 一个 Shard Plan 中每个 Manifest item 恰好映射到一个 Run。 |
| INV-004 | Worker 只有得到持久化的 start commit 后才能创建执行环境。 |
| INV-005 | 每个 Run 同一时刻最多有一个当前有效 fence；旧 fence 的事件不能推进状态。 |
| INV-006 | Attempt 完成需要受信退出事实和已校验 Evidence Manifest，测试内容不能自证成功。 |
| INV-007 | 重试创建新 Attempt，不覆盖原 Attempt、CaseResult 或 Artifact。 |
| INV-008 | `attempt_unknown` 默认不自动重试；人工裁决不能抹去 unknown 事实。 |
| INV-009 | 计算、框架内部并发、SUT Lease、账号/数据、配额、预算和安全限制共同准入。 |
| INV-010 | 测试沙箱不拥有平台数据库、执行引擎、云管理或对象存储长期凭据。 |

---

## 2. 控制面模块设计

### 2.1 模块边界

| 设计 ID | 模块 | 职责 | 禁止职责 |
|---|---|---|---|
| DES-MOD-001 | Identity & Authorization | OIDC 主体映射、角色、对象权限、服务身份 | 保存用户密码；把基础设施权限发给用户 |
| DES-MOD-002 | Suite Catalog | Suite/Revision、框架、输入、原子约束、默认 Profile | 执行 collection 或依赖安装 |
| DES-MOD-003 | Batch Command | 幂等创建/取消/重跑、deadline、用户意图 | 直接创建容器/Pod |
| DES-MOD-004 | Schedule | cron/时区/misfire、创建幂等 Batch | 把 scheduler 内存 Job 当事实源；执行测试 |
| DES-MOD-005 | Collection Coordinator | 创建一次性 collection Run，接收 Case Manifest | 在控制面 import 测试包 |
| DES-MOD-006 | Shard Planner | 原子组、历史画像、确定性 Shard Plan、digest | 修改用户测试语义或忽略约束 |
| DES-MOD-007 | Capacity Planner | work/slot/ETA/置信度/成本/deadline risk | 绕过硬配额、SUT 或安全边界 |
| DES-MOD-008 | Admission & Scheduler | 队列、公平、Lease、资源和 Assignment | 抢占已运行 Attempt；把消息 ack 当终态 |
| DES-MOD-009 | Worker Registry | Worker 身份、generation、能力、心跳、drain/quarantine | 接受普通用户指定宿主参数 |
| DES-MOD-010 | Attempt Coordinator | start commit、fence、renew、cancel、event、reconcile | 在 API 进程执行测试 |
| DES-MOD-011 | Target Access | Target Grant、Environment Lease、短期访问声明 | 储存/下发超范围长期凭据 |
| DES-MOD-012 | Evidence & Results | 分块上传、Artifact、Evidence finalize、Case 聚合 | 信任用户生成的“success”字段作为平台事实 |
| DES-MOD-013 | Governance | Quota、Budget、Retention、Audit、人工覆盖 | 允许覆盖安全/SUT 硬限制 |
| DES-MOD-014 | Reconciler | 期望状态与 Worker/Job/Artifact 重复对账 | 对 unknown 进行无依据推断 |
| DES-MOD-015 | Observability | 统一关联、SLO、告警和容量样本 | 将高基数 ID 无界写入 metric label |

### 2.2 进程角色

同一代码仓库可提供多个明确入口，共享领域模型但不共享进程内状态：

```text
control-api       HTTP API / SSE / auth / commands / queries
schedule-service  计算到期 schedule，幂等创建 Batch
planner           collection 收敛、Manifest、Shard/Capacity Plan
dispatcher        Admission、Lease、Assignment、VM Worker claim
reconciler        Worker/Job/Attempt/Upload/Outbox 周期对账
worker-agent      独立部署，只存在于执行主机
```

MVP 可合并 `planner + dispatcher + reconciler` 为一个后台容器以减少运维，但模块、事务和测试必须保持独立；不得合并进 Web request 生命周期。

### 2.3 代码分层约束

```text
domain/          纯领域对象、状态迁移、策略和错误；不依赖 FastAPI/Docker/K8s
application/     use case、事务边界、端口接口、命令/查询
adapters/http/   FastAPI、Pydantic、OIDC、problem details
adapters/db/     PostgreSQL repository、migration、outbox
adapters/worker/ Worker protocol client/server
adapters/docker/ Worker 端 Docker executor
adapters/k8s/    企业 Kubernetes execution backend
adapters/store/  Object storage、secret、egress policy
```

- Domain 不导入 Web、数据库、Docker 或 Kubernetes SDK。
- Application 只依赖抽象端口和领域类型。
- 执行后端不得泄漏原始 Docker/Kubernetes 对象到用户 API。
- 测试 Fake 实现端口契约；集成测试验证真实 PostgreSQL/Executor 的并发与故障语义。

---

## 3. 领域模型与状态

### 3.1 聚合边界

| 聚合 | 根对象 | 一致性边界 |
|---|---|---|
| Suite Aggregate | SuiteRevision | 版本、框架、输入、默认 Profile 和原子约束不可变 |
| Batch Aggregate | Batch | Manifest/Plan 引用、deadline、策略和聚合终态 |
| Run Aggregate | Run | Assignment fence、Attempt 序列、取消和最终分类 |
| Worker Aggregate | WorkerGeneration | 身份、能力、状态、心跳、drain/quarantine |
| Target Aggregate | TargetGrant | 目标范围、TTL、凭据档案和 Environment Lease 上限 |
| Evidence Aggregate | EvidenceManifest | Attempt 平台事实、Case 汇总、Artifact root digest |
| Governance Aggregate | Quota/Budget | 作用域、资源维度、硬/软边界和覆盖记录 |

不同聚合不依赖跨表大事务维持长流程；通过持久状态、outbox 和 Reconciler 收敛。需要同时守住的硬资源/Lease 使用短事务和唯一约束/原子计数。

### 3.2 标识与摘要

- 所有外部 ID 为不透明 128-bit ID；推荐使用时间可排序 UUID，但客户端不得解析其时间语义。
- 数据库使用内部主键和唯一业务 ID，任何用户可见序号只用于展示。
- 内容摘要统一为 `sha256:<64 lowercase hex>`。
- JSON 摘要使用 RFC 8785 JSON Canonicalization Scheme 或等价冻结实现；数组顺序具有业务意义，集合在进入摘要前按稳定键排序。
- ExecutionSpec、Manifest、Shard Plan、Artifact part 和 Evidence Manifest 均存 schema version 与 digest。
- 服务端拒绝“同 ID/同序号、不同 digest”的重复写入，并产生高优先级审计事件。
- 取消使用两层摘要：`qep.cancellation-request.v1` 只绑定调用方提供的 Run、幂等键、来源、
  actor 和 reason，不含服务端 `recorded_at`；`qep.cancellation-intent.v1` 再绑定 request digest
  与服务端记录时间，形成不可变取消事实。
- `CANCELLED_PRESTART` Assignment closure 的 request digest 不含 intent digest，完整 closure
  fact digest 必须绑定 intent digest。expiry/release 既有 closure root 不漂移；不同取消意图也
  不能借 closure replay 覆盖 Run 已保存的 intent。

### 3.3 Batch 状态

```text
draft
  → validating
    → collecting
      → planning
        → awaiting_admission
          → queued
            → running
              → finalizing
                → succeeded | failed | partial | cancelled

任意校验/策略阶段 → rejected
无法收敛证据      → 保持 finalizing + alert
```

规则：

- `succeeded`：所有原始 Case 可解释收敛，且失败数满足 Suite 成功策略；不存在 unknown/证据缺失。
- `failed`：Batch 完整收敛，但测试/平台失败达到失败策略。
- `partial`：存在 cancelled、unknown 或被明确允许的未执行项；不能包装成 succeeded。
- `cancelled`：用户/策略取消且所有 Run 已停止或明确 unknown。
- `rejected`：未产生任何 start commit；验证、授权或硬准入失败。

### 3.4 Run、Assignment 与 Attempt

Run 是最小调度单位，Attempt 是实际执行事实，Assignment 是执行前的短期预留：

```text
Run planned → queued
  → Assignment offered → claimed
      ├─ commit 前过期/释放 → 初次 Run queued；retry Run retry_queued
      │                         （没有 Attempt，可安全重分配；retry 保留 pending RetryIntent）
      └─ commit-start → 创建 Attempt N + fence F
             → provisioning → running → uploading
             → passed | test_failed | infra_failed | cancelled | attempt_unknown
                         └─ policy allows retry → Run retry_queued → new Assignment → Attempt N+1

commit 前 cancel，且早于 expiry
  → Assignment cancelled_prestart + Run cancelled
  → 不创建 Attempt/fence

cancel 在 expiry 边界或之后被观察
  → expiry closure 保持权威 + Run 保存 cancel intent

commit 后 cancel
  → 只保存 intent，Run/Assignment/Attempt/fence 不立即改写
      ├─ 受信 process + SUT-access stop proof → Run/Attempt cancelled
      └─ stop 不可证明 → Attempt attempt_unknown
```

关键规则：

- Assignment 在 commit-start 前没有业务 Attempt；过期/释放可安全重新分配。初次 reservation 回
  `queued`；retry reservation 回 `retry_queued` 并保留 pending RetryIntent。
- commit-start 与创建 Attempt 在同一个数据库事务中完成，返回持久 `attempt_id/fence` 后 Worker 才能创建容器。
- `fence` 对每个 Run 单调递增，任何事件/上传/finalize 都携带 fence。
- `provisioning` 已可能产生执行副作用；其状态丢失不能退回“未启动”。
- Attempt 终态不可修改；重新分类只能新增有审计的 adjudication，不重写原始事实。
- `test_failed` 只表示测试进程完成且断言/用例失败；Worker/容器/上传/证据故障不能伪装为测试失败。
- cancel request 与 cancelled outcome 是不同事实。postcommit request 只记录 intent，不能直接
  宣称 Attempt 已停止；`attempt_unknown` 为吸收终态，迟到 stop proof 不得改写它。
- `retry_queued` 的 prestart cancel 保留历史 Attempt、RetryIntent 和 pending pointer，但取消
  intent 会阻止继续 offer 或消费该 retry。
- cancelled Evidence 与 completed Evidence 采用 first-finalized：任一方向先持久化的 root
  获胜，另一方向只能得到冲突，不能覆盖原始 Attempt Evidence。
- completed Evidence 先于取消证明完成时，当前只保留 immutable Attempt Evidence 与 cancel
  intent；完整 Run/Batch derived terminal 尚未冻结，见 `OI-DES-011`。

### 3.5 Worker 状态

```text
registering → ready ↔ busy → draining → retired
                   ↘ offline
任意非 retired → quarantined → retired/rebuilt(new generation)
```

- `worker_id` 表示逻辑节点，`generation` 每次重建/重新注册递增。
- 证书、请求和 Assignment 同时绑定 `worker_id + generation`。
- `offline` 是健康判断，不能自动等同所有 Attempt `infra_failed`。
- `quarantined` 立即停止新 claim、撤销身份/Grant，保存控制面审计；旧 generation 永不恢复为 ready。

### 3.6 Target Grant 与 Environment Lease

Target Grant 状态：`draft → approved → active → suspended/expired/revoked`。

- Grant 绑定 project、environment、host/IP/CIDR、协议/端口、用途、凭据档案、最大并发/QPS、有效期和批准人。
- Environment Lease 绑定 `attempt_id + fence + target_grant_id + lease_dimension`。
- Lease 使用数据库唯一/原子容量约束；Worker 数增加不增加 Lease 上限。
- Grant/Lease 失效同时影响 Admission、Worker renew 和 Egress Gateway；任何一层失效都 fail closed。

---

## 4. PostgreSQL 数据设计

### 4.1 Schema 原则

- 使用 UTC `timestamptz` 保存业务时间；API 按用户时区展示。
- 金额/配额使用精确数值和明确单位，不使用浮点表达预算。
- 状态使用受控字符串/lookup + 应用迁移；避免数据库 enum 阻碍滚动兼容，除非组织标准另有要求。
- JSONB 只存版本化扩展字段，不把核心查询/约束字段藏入 JSON。
- 所有可变业务表具有 `version bigint`，更新采用 `WHERE id=? AND version=?` CAS。
- 删除优先软删除/retired 状态；审计和 Evidence 引用不可级联误删。
- MVP 先不分区；企业在真实数据触发器满足后对 `case_result`、`attempt_event`、`capacity_sample` 按时间分区。

### 4.2 核心表

| 表 | 关键字段 | 关键约束/索引 |
|---|---|---|
| `project` | `id,name,status` | `name` scope 内唯一 |
| `principal` | `id,issuer,subject,display_name,status` | `unique(issuer,subject)`；不存密码 |
| `role_binding` | `principal_id,role,project_id` | 对象范围唯一 |
| `suite` | `id,project_id,name,status` | `unique(project_id,name)` |
| `suite_revision` | `id,suite_id,revision_no,source_spec_digest,config_digest,framework,profile_id,status` | revision immutable；digest 索引 |
| `test_case` | `id,suite_revision_id,stable_case_id,metadata_json` | `unique(suite_revision_id,stable_case_id)` |
| `schedule` | `id,suite_id,cron,timezone,next_fire_at,misfire_policy,enabled,version` | `next_fire_at` 可 claim 索引 |
| `batch` | `id,project_id,suite_revision_id,request_digest,idempotency_scope/key,status,deadline_at,priority_class,version` | `unique(scope,key)`；状态/创建时间索引 |
| `case_manifest` | `id,batch_id,schema_version,digest,item_count,status` | `unique(batch_id)`；approved 后 immutable |
| `manifest_item` | `manifest_id,item_index,case_id,atomic_group_id,estimated_ms,profile_id,constraints_json` | `unique(manifest_id,item_index)`、`unique(manifest_id,case_id)` |
| `shard_plan` | `id,batch_id,algorithm_version,digest,run_count,total_estimated_ms,status` | `unique(batch_id)`；approved 后 immutable |
| `run` | `id,batch_id,plan_id,shard_index,profile_id,estimated_ms,status,current_fence,attempt_count,version` | `unique(plan_id,shard_index)`；queue composite index |
| `run_manifest_item` | `run_id,manifest_id,item_index` | `unique(manifest_id,item_index)` 保证单归属 |
| `assignment` | `id,run_id,worker_id,generation,offer_token_hash,status,expires_at,attempt_id,fence,version` | 活动 Assignment partial unique per Run |
| `attempt` | `id,run_id,attempt_no,fence,status,spec_digest,worker_id,generation,start/finish timestamps,exit_class` | `unique(run_id,attempt_no)`、`unique(run_id,fence)` |
| `attempt_event` | `attempt_id,fence,event_id,event_seq,event_type,payload_digest,payload_json,occurred_at,received_at` | `unique(attempt_id,event_id)`、`unique(attempt_id,event_seq)` |
| `worker` | `id,host_id,current_generation,status,pool_id,last_seen_at,version` | host/identity 索引 |
| `worker_generation` | `worker_id,generation,cert_serial,agent_version,capabilities_digest,registered/retired_at` | `unique(worker_id,generation)` |
| `worker_capacity` | `worker_id,generation,resource_vector,reserved_vector,observed_at` | current generation only |
| `resource_profile` | `id,name,version,framework,requests_json,limits_json,internal_workers,security_profile_id` | `unique(name,version)`；approved immutable |
| `capacity_plan` | `id,batch_id,model_version,status,work_ms,required_slots,eta,confidence,constraints_json,cost_cap` | 版本化，不覆盖历史 |
| `capacity_sample` | `attempt_id,phase,duration_ms,cpu_ms,peak_memory,pid_peak,disk_bytes,network_bytes` | 时间/目标/Profile 查询索引 |
| `target_grant` | `id,project_id,target_spec,credential_ref,max_concurrency,valid_from/to,status,version` | 目标/状态/有效期索引 |
| `environment_lease` | `id,grant_id,attempt_id,fence,dimension,units,status,expires_at` | 活动单位约束/索引 |
| `test_result` | `attempt_id,manifest_item_index,outcome,duration_ms,error_class,details_ref` | `unique(attempt_id,item_index)` |
| `upload_session` | `id,attempt_id,fence,path,content_class,expected_size,digest,status,expires_at` | `unique(attempt_id,fence,path)` |
| `upload_part` | `session_id,part_no,size,digest,storage_etag` | `unique(session_id,part_no)` |
| `artifact` | `id,attempt_id,path,content_class,size,digest,object_uri,retention_class` | `unique(attempt_id,path)`；object URI 不可用户任意指定 |
| `evidence_manifest` | `id,attempt_id,schema_version,root_digest,object_uri,finalized_at` | `unique(attempt_id)`；finalized immutable |
| `idempotency_record` | `scope,key,request_digest,response_status,response_ref,expires_at` | `unique(scope,key)` |
| `outbox` | `id,aggregate_type/id,event_type,payload_digest,payload,status,available_at,attempts` | pending/available index |
| `audit_event` | `id,actor,action,object_type/id,decision,reason,before/after_digest,occurred_at` | append-only；时间/对象/actor 索引 |

### 4.3 完整性检查

数据库约束保证每个 Manifest item 最多属于一个 Run；Plan approve 事务还必须验证：

```sql
manifest.item_count
= count(manifest_item)
= count(run_manifest_item for plan)
= count(distinct manifest_id, item_index for plan)
```

任何不一致均使 Plan 保持 `invalid`，不能通过“部分成功”继续执行。

Batch finalize 验证：

```text
expected manifest items
= terminal original outcomes
+ explicit cancelled/unknown/not_executed classifications
```

Retry 结果不能增加 expected denominator，也不能覆盖原始 Attempt；聚合策略必须保留 original 与 retry 两个视图。

### 4.4 事务模式

#### 创建 Batch（幂等）

1. 规范化请求并计算 `request_digest`。
2. 插入 `idempotency_record(scope,key,digest)`。
3. 唯一冲突时：相同 digest 返回原响应；不同 digest 返回 `IDEMPOTENCY_CONFLICT`。
4. 同一事务创建 Batch 和 outbox `batch.created`。

#### 领取待规划/调度记录

```sql
SELECT id
FROM work_item
WHERE status = 'pending' AND available_at <= now()
ORDER BY priority DESC, available_at, id
FOR UPDATE SKIP LOCKED
LIMIT :n;
```

同一事务将记录改为 `claimed`、写 owner/deadline/version。`SKIP LOCKED` 只用于 queue-like 领取，不用于需要一致快照的普通查询。

#### Assignment claim

1. 锁定 eligible Run 和 Worker capacity snapshot。
2. 重新校验 Profile、quota、Target Grant、Lease 和资源余量。
3. 原子预留资源/Lease，创建短期 Assignment，Run → `assigned`。
4. 返回 opaque offer token；数据库只存 token hash。
5. commit 前 Assignment 过期/释放时释放预留；初次 reservation 的 Run → `queued`，retry
   reservation 的 Run → `retry_queued` 并保留 pending RetryIntent；不产生 Attempt。

#### Commit-start

1. 校验 mTLS worker/generation、offer token、Assignment 状态/TTL 和 `spec_digest`。
2. `SELECT run FOR UPDATE`，确认没有更高 fence/已运行 Attempt。
3. `run.current_fence += 1`；创建 Attempt `start_committed`；关联 Assignment。
4. 创建/激活 Environment Lease 和短期访问声明。
5. 写审计/outbox，并提交事务。
6. 返回持久 `attempt_id/fence/lease_duration/max_deadline`；响应丢失时相同请求返回相同结果。

#### 事件追加

- 先校验当前 worker/generation/attempt/fence。
- `event_id` 已存在且 digest 相同：返回已接受。
- `event_id` 或 `event_seq` 已存在但 digest 不同：拒绝并 quarantine/告警候选。
- 允许网络乱序写入原始事件，但只有状态机可接受的下一迁移推进 Attempt。
- 迟到旧 fence 事件保留为审计拒绝，不进入业务时间线。

#### Evidence finalize

1. 校验 Attempt 当前 fence、所有 upload session complete、Artifact digest/size/path。
2. 计算 canonical Evidence Manifest/root digest。
3. 条件插入 `evidence_manifest`；相同 digest 重试幂等，不同 digest 冲突。
4. 更新 Attempt 终态和 Run 聚合，写 outbox。
5. Batch 只在所有 Run 完成对账后进入 finalizing/terminal。

取消专用 finalize 必须在同一事务中校验 Run-owned cancellation intent、current
Attempt/fence/Worker authority、`TrustedCancellationStop` 与服务端重建的 Evidence root，随后
原子写入 Attempt `cancelled`、Run `cancelled` 并清除 current Assignment pointer。cancel request
本身不得执行该终态更新；exact replay 优先于 Run/Attempt CAS，异 root 或异 stop proof 冲突。

### 4.5 隔离级别与锁

- 默认事务隔离 `READ COMMITTED`；关键行使用显式 `FOR UPDATE`、CAS version 和唯一约束。
- Quota/Lease 分配使用单行计数或 slot token 表，避免先查后写竞态。
- 只在经过证明的跨行不变量需要时使用 `SERIALIZABLE`，并实现有界重试；不全局开启。
- 外部 API、对象存储、Docker/Kubernetes 调用不放在长数据库事务内；通过 intent/outbox + Reconciler 完成。
- 所有事务设置 statement/lock timeout；超时进入可重试内部错误，不无限占锁。

### 4.6 Migration 与恢复

- Schema migration 使用 expand → deploy compatible code → backfill → contract。
- Worker/API 协议至少支持一个滚动兼容窗口；不能让旧 Worker 写入新状态非法值。
- 大表 backfill 分批并可暂停；不在一个事务重写全部历史数据。
- 每次 migration 有 precheck、postcheck、回退/前滚说明和真实备份恢复验证。
- PITR/等效恢复后，Reconciler 对 Assignment、Attempt、Worker、Lease、Upload 和 Object Storage 全量对账。

---

## 5. 用户与管理 API

### 5.1 协议约定

- Base path：`/api/v1`；兼容破坏通过新 major path 发布。
- 请求/响应 JSON 使用明确 schema；时间为 RFC 3339 UTC，大小/时长字段带单位后缀。
- 创建/变更命令使用 `Idempotency-Key`，服务端绑定主体、路由和请求摘要。
- 错误使用 `application/problem+json`，包含稳定 `code`、`title`、`status`、`request_id`、可选 `details`。
- 列表使用稳定 cursor pagination，不使用大 offset 扫描热表。
- `ETag/version` 支持管理配置的乐观并发；冲突返回 409。
- API 只接受领域参数，不接受 Docker/Kubernetes raw spec、宿主路径或任意命令行覆盖。

### 5.2 主要端点

| 方法与路径 | 权限 | 作用 |
|---|---|---|
| `POST /suites` | maintainer/admin | 创建 Suite |
| `POST /suites/{id}/revisions` | maintainer/admin | 创建不可变 Revision 草稿并校验 |
| `POST /suite-revisions/{id}:activate` | maintainer/admin | 激活经过验证的 Revision |
| `GET /suite-revisions/{id}/cases` | project reader | 分页查询 Case 库存 |
| `POST /batches` | executor | 幂等创建即时 Batch |
| `GET /batches/{id}` | object reader | Batch、计划、状态和聚合 |
| `GET /batches/{id}/manifest` | object reader | Manifest 元数据/分页 item |
| `GET /batches/{id}/plan` | object reader | Shard/Capacity Plan 和限制因素 |
| `POST /batches/{id}:approve-best-effort` | executor/lead | 批准 best-effort 进入队列 |
| `POST /batches/{id}:cancel` | owner/admin | 请求取消并返回收敛状态 |
| `GET /batches/{id}/events` | object reader | SSE 状态/有限日志事件，支持 Last-Event-ID |
| `GET /runs/{id}` | object reader | Run/Attempt 列表、结果和时间线 |
| `POST /runs/{id}:retry` | executor/admin | 按策略创建显式新 Attempt 请求 |
| `POST /attempts/{id}:adjudicate` | admin | unknown 人工裁决；必须给理由 |
| `GET /attempts/{id}/artifacts` | object reader | Artifact 元数据 |
| `POST /artifacts/{id}:download-url` | object reader | 生成短期、单对象下载授权 |
| `POST /schedules` | maintainer | 创建 schedule/时区/misfire |
| `POST /schedules/{id}:pause` / `POST /schedules/{id}:resume` | maintainer | 幂等启停 |
| `POST /targets` | admin/environment owner | 创建目标草稿 |
| `POST /target-grants/{id}:approve` / `POST /target-grants/{id}:revoke` | environment owner/admin | 批准/撤销 Grant |
| `GET /workers` | admin/ops | Worker/Pool/容量/健康 |
| `POST /workers/{id}:drain` / `POST /workers/{id}:quarantine` | admin/ops/security | 维护或安全隔离 |
| `GET /capacity-plans/{id}` | reader/ops | 计划输入、置信度、成本和限制 |
| `GET /audit-events` | auditor/admin | 按对象/主体/时间检索审计 |

### 5.3 稳定错误码

| Code | HTTP | 语义 |
|---|---:|---|
| `VALIDATION_FAILED` | 422 | 输入格式/字段/组合无效 |
| `AUTHENTICATION_REQUIRED` | 401 | 未认证或会话失效 |
| `OBJECT_FORBIDDEN` | 403 | 无对象级权限 |
| `POLICY_DENIED` | 403 | 安全/治理策略拒绝，不能重试绕过 |
| `NOT_FOUND` | 404 | 对象不存在或按防枚举策略隐藏 |
| `IDEMPOTENCY_CONFLICT` | 409 | 相同 key 对应不同请求摘要 |
| `VERSION_CONFLICT` | 409 | 配置被并发修改 |
| `STATE_CONFLICT` | 409 | 当前状态不允许该命令 |
| `TARGET_GRANT_REQUIRED` | 409 | 缺少/过期/范围不匹配的 Grant |
| `ENVIRONMENT_CAPACITY_WAIT` | 409/202 | Environment Lease 满，任务保持等待 |
| `RESOURCE_CAPACITY_WAIT` | 202 | 资源暂不可用，任务已排队 |
| `PLAN_INFEASIBLE` | 422 | 硬约束内无法满足请求 |
| `ATTEMPT_UNKNOWN_REVIEW_REQUIRED` | 409 | unknown 需要授权裁决 |
| `EVIDENCE_NOT_FINALIZED` | 409 | 证据未完成，终态/下载动作不允许 |
| `DEPENDENCY_UNAVAILABLE` | 503 | 身份、状态、输入、证据等依赖不可用 |
| `INTERNAL_ERROR` | 500 | 未分类内部错误；响应不泄露秘密/堆栈 |

### 5.4 SSE 与日志 tail

- SSE 只发送状态、进度、告警和经过限速/截断的日志 tail；完整日志在 Artifact。
- Event ID 为服务端单调 cursor，不等同 Worker `event_seq`。
- 客户端用 `Last-Event-ID` 断线重连；超出在线窗口时返回 snapshot + 新 cursor。
- 每连接、每用户和每 Batch 有并发/带宽上限；慢客户端不能反压执行事件主链路。
- 日志正文编码为纯文本，不解释 ANSI 控制命令或 HTML；可选择安全过滤后显示颜色。

---

## 6. Worker 协议

### 6.1 协议选择

- Worker 主动通过 HTTPS 长轮询连接控制面；Worker 主机不开放远程管理入站端口。
- 双向 TLS 认证 Worker 身份；普通用户 token 不能调用 Worker API。
- HTTP/JSON 领域协议，base path `/internal/worker/v1`；不代理 raw Docker/Kubernetes API。
- Assignment token、fence、schema version 和 canonical digest 提供业务级重放/篡改防护。
- 传输是至少一次；所有命令、事件、上传和 release 必须幂等。
- Worker 本地使用 monotonic clock 管理 lease deadline；服务器时间只用于审计展示。

单 Worker/MVP 不引入 WebSocket、gRPC 或 MQ。长轮询易于经过标准反向代理、证书和审计；Worker fleet 规模或双向流量有实测瓶颈后再复审传输层，但不能改变领域语义。

### 6.2 Worker 身份生命周期

1. 运维为新主机签发一次性 bootstrap token，绑定预期 pool、host fingerprint 和有效期。
2. Worker 生成私钥并提交 CSR/注册信息；控制面验证后分配稳定 `worker_id` 和 `generation`。
3. 证书 SAN/扩展绑定 `worker_id + generation + pool`，私钥不可导出/最小文件权限或硬件保护。
4. Worker 定期轮换证书；新证书生效后旧 serial 进入短兼容/撤销窗口。
5. 主机重建、克隆检测或 quarantine 后 generation 增加，旧 generation 所有 claim/event/upload 均永久拒绝。
6. Worker 注册/轮换/撤销均写高价值审计，普通用户无权限。

Worker 请求必须同时通过：

```text
mTLS certificate valid
AND worker_id/generation matches certificate
AND generation is current and not quarantined/retired
AND agent version is within protocol compatibility window
AND request assignment/fence/token is valid
```

### 6.3 端点目录

| 方法与端点 | 幂等键 | 作用 |
|---|---|---|
| `POST /claims:next` | request ID | 长轮询领取与能力匹配的 Assignment；无任务返回 204 |
| `POST /assignments/{id}:commit-start` | `start_commit_key` | 持久创建 Attempt/fence，返回规范化 ExecutionSpec |
| `POST /assignments/{id}:renew` | `renew_seq` | 续租、资源/进度摘要、接收 cancel/stop 命令 |
| `POST /assignments/{id}/events` | 每个 `event_id` | 批量追加幂等事件 |
| `POST /assignments/{id}/uploads` | `path + content digest` | 创建 Artifact upload session |
| `PUT /uploads/{session_id}/parts/{part_no}` | `part_no + digest` | 上传分块；内容可直传对象存储 |
| `POST /uploads/{session_id}:complete` | `complete_key` | 校验分块、大小和总 digest |
| `POST /assignments/{id}:finalize-evidence` | `root_digest` | 提交 Evidence Manifest，条件进入 Attempt 终态 |
| `POST /assignments/{id}:release` | `release_key` | 报告沙箱和本地工作区已清理，释放 Worker 本地资源 |
| `POST /worker:reconcile` | reconcile request ID | Worker 重启/恢复后报告活动/残留执行，接收处置命令 |

上传大文件时，控制面可返回短期预签名对象存储 URL；Worker API 仍负责创建 session、校验 part digest 和 complete/finalize，不把对象存储列表/删除权交给 Worker。

### 6.4 `claims:next`

请求示例（字段语义，不是最终生成代码）：

```json
{
  "worker_id": "w_...",
  "generation": 7,
  "agent_version": "1.x",
  "protocol_versions": ["1.0"],
  "capabilities_digest": "sha256:...",
  "available": {
    "cpu_millis": 6000,
    "memory_bytes": 12884901888,
    "pid_slots": 1500,
    "ephemeral_bytes": 53687091200,
    "browser_slots": 2
  },
  "active_assignments": [
    {"assignment_id": "asn_...", "attempt_id": "att_...", "fence": 42}
  ],
  "max_wait_ms": 20000,
  "request_id": "req_..."
}
```

成功响应：

```json
{
  "assignment_id": "asn_...",
  "assignment_token": "opaque-secret-returned-once",
  "offer_ttl_ms": 30000,
  "run_id": "run_...",
  "batch_id": "bat_...",
  "task_type": "test_run",
  "framework": "playwright",
  "resource_profile": "browser-small@3",
  "security_profile": "untrusted-standard@2",
  "execution_spec_digest": "sha256:...",
  "required_capabilities_digest": "sha256:..."
}
```

约束：

- Claim 响应只是短期 offer，Worker 不得据此创建容器。
- Assignment token 只在领取时返回，数据库存 hash；日志、指标和错误不得输出 token。
- Worker 能力发生变化时必须重新发布 digest；控制面不把不匹配 Run 强行分配。
- Worker 可以因本地自检失败 release offer；不得私自修改 Profile 后执行。
- 同一 Worker 同时未 commit offer 数有上限，防止“囤任务”。

### 6.5 `commit-start`

请求：

```json
{
  "worker_id": "w_...",
  "generation": 7,
  "assignment_token": "opaque-secret",
  "start_commit_key": "sc_...",
  "execution_spec_digest": "sha256:...",
  "worker_nonce": "random-128-bit"
}
```

响应：

```json
{
  "attempt_id": "att_...",
  "attempt_no": 1,
  "fence": 42,
  "lease_duration_ms": 60000,
  "renew_after_ms": 20000,
  "max_run_duration_ms": 1800000,
  "execution_spec": {
    "schema_version": "1.0",
    "digest": "sha256:...",
    "image_digest": "sha256:...",
    "argv": ["approved-runner-entrypoint", "--manifest", "/input/run.json"],
    "working_directory": "/workspace",
    "resource_limits": {},
    "security_profile": {},
    "input_mounts": [],
    "target_access": [],
    "upload_policy": {}
  },
  "attempt_token": "short-lived-report-only-token"
}
```

`ExecutionSpec` 由控制面根据批准 Profile 生成，用户不能直接提供：

- raw container runtime flags；
- host path/device/capability/security-opt；
- host network/PID/IPC；
- 任意 entrypoint 替换。

用户自定义测试命令被封装为 runner input，由批准 entrypoint 校验/执行；如果产品允许自定义 argv，也必须经过 SuiteRevision 冻结、长度/字符/环境策略校验，不能变成运行时绕过 Profile 的任意宿主命令。

commit-start 的幂等语义：

- 相同 Assignment、token、key、spec digest：始终返回同一 `attempt_id/fence`。
- 相同 key 不同 digest：`WP_IDEMPOTENCY_CONFLICT`。
- Assignment 已过期且未 commit：拒绝，Worker 必须丢弃 offer。
- Assignment 已被更高 fence 替代：拒绝并要求停止任何本地残留。
- 响应超时：Worker只能重试 commit，不能猜测成功后先创建容器。

### 6.6 lease、renew 与本地时间

Worker 收到 commit/renew 响应时，以当前 monotonic time 计算：

```text
local_lease_deadline = monotonic_now + lease_duration - safety_margin
local_hard_deadline  = min(
  commit_monotonic + max_run_duration,
  local grant/secret deadlines
)
```

- 不用 Worker wall clock 与服务器绝对时间比较 lease；NTP 跳变不应延长执行。
- Worker 在 `renew_after` 前续租并带递增 `renew_seq`；重复序号相同摘要幂等。
- renew 重新验证 Worker/generation/fence、Grant、Lease、cancel 和最大 deadline。
- 控制面可返回 `continue`、`cancel`、`stop_policy_revoked`、`stop_grant_expired`、`drain_after_finish`。
- Worker 无法续租时可以完成短暂本地清理，但到 `local_lease_deadline` 必须停止测试进程并失去 Egress 能力。
- renew 不得把 deadline 延长到 Batch/Run 的绝对硬上限之外；人工延长是受审计的新决策。

启动默认参数（仅 bootstrap，需在演练后配置化）：

| 参数 | 初始建议 | 原因 |
|---|---:|---|
| claim long-poll | 20 秒 | 低空转与代理兼容折中 |
| offer TTL | 30 秒 | 防 Worker 囤积；镜像准备必须在 commit 后计入 Attempt |
| run lease | 60 秒 | 允许短网络抖动，同时控制失联影响 |
| renew interval | 20 秒 | 一个 lease 内至少两次重试机会 |
| local safety margin | 5 秒 | 防传输/调度抖动越过服务器意图 |
| cancel soft grace | 10 秒 | 先 TERM/等效优雅停止 |
| cancel hard cap | 120 秒 | 超过后强制终止和 unknown/infra 分类 |

### 6.7 事件模型

事件信封：

```json
{
  "event_id": "evt_...",
  "event_seq": 17,
  "event_type": "test_process_exited",
  "occurred_monotonic_ms": 8839123,
  "occurred_wall_time": "2026-07-12T12:00:00Z",
  "payload": {},
  "payload_digest": "sha256:..."
}
```

允许的主要事件：

| 阶段 | 事件 |
|---|---|
| 准备 | `sandbox_create_started/completed`、`input_materialize_started/completed` |
| 执行 | `test_process_started`、`progress_snapshot`、`resource_sample`、`log_chunk_available` |
| 停止 | `cancel_received`、`test_process_exited`、`lease_expired_local_stop`、`sandbox_runtime_failed` |
| 证据 | `upload_started/completed`、`evidence_ready` |
| 清理 | `sandbox_destroyed`、`workspace_destroyed`、`release_ready` |

规则：

- `event_seq` 在 Assignment/fence 内从 1 单调递增；断线后允许批量补传。
- `occurred_wall_time` 只供诊断；平台顺序以 seq、状态机和 received time 判断。
- `resource_sample/log` 可按批准窗口采样/合并，不能因高频事件阻塞 renew/cancel。
- 测试内容进入 payload 时标记 `untrusted_content`，不得作为状态迁移字段。
- `test_process_exited` 包含受信 PID/exit code/signal/timeout/oom 事实；测试报告的 Case 结果另行解析。

### 6.8 release 与 reconcile

`TrustedCancellationStop` 只证明对应 Attempt 的测试进程已经停止，且该 Attempt 获准的 SUT
access 已停止。它不证明 sandbox/workspace 已销毁、Worker capacity 已释放或完整 runtime
cleanup 已完成；这些事实仍必须由下面的 release/reconcile 契约证明。

`release` 只有在以下条件满足后发送：

- 测试/子进程已结束；
- Egress/secret 已撤销或过期；
- Artifact 上传完成或明确进入可恢复 spool；
- 沙箱和可写工作区已销毁；
- Worker 资源计数已本地释放。

控制面收到 release 不会覆盖尚未 finalize 的 Evidence；它只关闭 Worker 本地资源占用。

Worker 启动或恢复时调用 reconcile，报告：

```text
worker_id/generation/agent_version/capabilities_digest
live sandbox: assignment_id, attempt_id, fence, runtime_id, spec_digest, local_deadlines
completed-but-spooled: attempt_id, fence, pending uploads/digests
orphan runtime/workspace identifiers
```

控制面逐项返回：

- `continue_and_renew`：当前 fence 有效；
- `stop_stale_fence`：旧 fence，立即停止并隔离证据；
- `stop_cancelled/revoked/expired`；
- `resume_upload_only`：不得再访问 SUT，只续传证据；
- `quarantine_do_not_delete`：安全事件，保留受控诊断并撤销 Worker；
- `delete_orphan`：经双向确认的无业务引用残留。

Worker 不得自行把“找不到控制面记录”的容器视为成功，也不得在没有处置命令时无限保留/运行。

### 6.9 Worker 协议错误码

| Code | HTTP | Worker 行为 |
|---|---:|---|
| `WP_AUTH_FAILED` | 401 | 停止 claim，刷新/注册身份；重复失败进入告警 |
| `WP_GENERATION_STALE` | 403 | 立即停止所有旧 generation 活动并等待人工处理 |
| `WP_WORKER_QUARANTINED` | 403 | 停止新执行，保全状态，不自动重新注册 |
| `WP_PROTOCOL_UNSUPPORTED` | 426 | drain 并升级/回滚 agent |
| `WP_ASSIGNMENT_EXPIRED` | 409 | 未 commit 时丢弃 offer；已本地创建环境则安全事件 |
| `WP_ASSIGNMENT_REVOKED` | 409 | 停止对应活动并 release/reconcile |
| `WP_FENCE_STALE` | 409 | 立即停止旧活动；迟到内容单独隔离，不能 finalize |
| `WP_DIGEST_MISMATCH` | 409 | 不执行/不上传，记录输入完整性事件 |
| `WP_IDEMPOTENCY_CONFLICT` | 409 | 停止当前操作并告警，不生成新 key 绕过 |
| `WP_EVENT_CONFLICT` | 409 | 暂停该 Attempt 上报，reconcile；可能 quarantine |
| `WP_GRANT_INVALID` | 409 | 不启动或立即停止目标访问 |
| `WP_LEASE_CAPACITY_LOST` | 409 | 停止/等待，不能本地超发并发 |
| `WP_UPLOAD_POLICY_DENIED` | 403 | 不重命名绕过；记录受限 Artifact |
| `WP_RATE_LIMITED` | 429 | 按 `Retry-After` 有界退避，renew/cancel 走保留通道 |
| `WP_DEPENDENCY_UNAVAILABLE` | 503 | 有界退避；到本地 lease deadline 必须停止 |

---

## 7. Artifact 与 Evidence 设计

### 7.1 内容分类

| Content Class | 示例 | 默认处理 |
|---|---|---|
| `structured_result` | JUnit、平台规范 Case JSON | 小体积、Schema 校验、用于聚合 |
| `log` | stdout/stderr/runner log | 文本/二进制安全展示、截断 tail、完整对象存储 |
| `screenshot` | PNG/JPEG | MIME/魔数/大小校验，按敏感数据授权 |
| `video` | WebM/MP4 | 大文件、短保留、禁止浏览器自动内联执行 |
| `trace` | Playwright trace/zip | 不在服务端自动解压；下载后由受控工具查看 |
| `report` | Allure/HTML bundle | 作为不可信压缩内容；不直接同源托管执行脚本 |
| `diagnostic` | core/har/resource profile | 默认受限，仅管理员/维护者可见 |

大小、数量、单 part、Attempt 总量和本地 spool 都由 `upload_policy` 限制。超限产生明确 `artifact_rejected`，不能拖垮 Worker/控制面；是否使 Attempt `infra_failed/partial` 由 Suite Evidence Policy 决定。

### 7.2 路径与文件安全

- Artifact logical path 必须是 UTF-8、相对 POSIX 路径；禁止绝对路径、`..`、NUL、重复分隔和平台保留名。
- Worker 在打开文件后验证其真实路径仍位于 Attempt workspace；拒绝 symlink、hardlink 到边界外、device、socket、FIFO。
- 不信任扩展名；记录声明 MIME 与检测 MIME，冲突按策略拒绝/隔离。
- 服务端不自动解压用户 zip/tar；需要索引时在独立受限扫描沙箱处理压缩炸弹和路径穿越。
- Artifact 下载使用 `Content-Disposition: attachment`、隔离域名/来源和短期 URL；不在控制面同源执行 HTML/JS。

### 7.3 分块上传

1. Worker 提交 logical path、content class、总大小、SHA-256、part 大小和 fence。
2. 控制面校验政策并创建唯一 Upload Session；相同元数据/digest 重试返回同一 session。
3. 每 part 校验编号、大小和 digest；对象存储 ETag 不能代替平台 SHA-256。
4. `complete` 验证 part 连续性、总大小和最终 digest，生成 Artifact row。
5. session 过期可续建，但不能把不同 digest 写入同一 logical path。

初始建议 part 大小 16 MiB，允许 8～64 MiB 配置；小于阈值的文件单 part。最终值以对象存储、网络和内存压测为准。

### 7.4 Evidence Manifest

Evidence Manifest 是 canonical JSON，至少包含：

```json
{
  "schema_version": "1.0",
  "batch_id": "bat_...",
  "run_id": "run_...",
  "attempt_id": "att_...",
  "attempt_no": 1,
  "assignment_id": "asn_...",
  "fence": 42,
  "worker": {"worker_id": "w_...", "generation": 7},
  "input": {
    "suite_revision_digest": "sha256:...",
    "manifest_digest": "sha256:...",
    "shard_plan_digest": "sha256:...",
    "execution_spec_digest": "sha256:...",
    "image_digest": "sha256:..."
  },
  "target_grants": [],
  "platform_exit": {
    "class": "completed",
    "exit_code": 1,
    "signal": null,
    "oom": false,
    "timeout": false
  },
  "case_summary": {},
  "artifacts": [
    {"path": "junit.xml", "class": "structured_result", "size": 1234, "digest": "sha256:..."}
  ],
  "timing": {},
  "resource_summary": {},
  "root_digest": "sha256:..."
}
```

取消 Evidence 额外绑定：

```json
{
  "cancellation_stop": {
    "proof_digest": "sha256:...",
    "cancellation_intent_digest": "sha256:...",
    "source_event_id": "evt_...",
    "process_stopped_at": "2026-07-12T12:00:01Z",
    "sut_access_stopped_at": "2026-07-12T12:00:02Z",
    "recorded_at": "2026-07-12T12:00:03Z"
  }
}
```

- `root_digest` 对除自身外的 canonical manifest 计算，或使用明确的 envelope 规则，版本化后固定。
- Worker 提议 manifest，控制面基于数据库受信事实重建/校验关键字段；Worker 不能自报任意身份/fence/退出分类。
- finalize 后不可原位修改。后续人工 adjudication 是单独签名/审计记录，引用原 root digest。
- Batch evidence bundle 引用所有原始/重试 Attempt manifests、聚合规则版本和未执行/unknown 清单。
- 非取消 Evidence 继续使用 `qep.m0-attempt-evidence.v1`，既有 root 不漂移；取消 Evidence
  使用 `qep.m0-attempt-evidence.v2`。classification 仍为 `qep.attempt-classification.v1`，
  outcome 映射未变，stop-proof gate 与新增 root 字段由外层 v2 表达。
- 裸 `PlatformExitClass.CANCELLED` 不足以生成 cancelled Evidence；必须同时存在与 Run intent、
  Attempt/fence、Worker generation 和停止时间一致的受信 stop proof。

### 7.5 结果解析

- Framework Adapter 在沙箱内产生平台规范 `case-results.json` 和原生 JUnit/trace。
- 控制面只解析有大小/Schema 限制的规范 JSON；复杂/不可信报告转换在沙箱完成。
- Case ID 必须存在于 Run manifest；额外 Case 标记 `unexpected_case`，不能静默扩 denominator。
- 缺失 Case 根据平台退出事实分类为 `not_reported/infra_failed/cancelled/unknown`，不能默认 passed/skipped。
- Retry 聚合至少保留：original outcome、每次 retry outcome、最终策略 outcome、flaky flag 和策略版本。

---

## 8. Target Grant、Lease、秘密与出站

### 8.1 Target Spec

Target 使用管理员维护的稳定对象，不让用户直接输入任意 URL 作为授权：

```text
target_id
environment (dev/test/staging/...)
allowed DNS names / resolved address policy
allowed CIDRs (如确有需要)
protocols and ports
TLS/SNI/certificate policy
redirect policy
forbidden metadata/control ranges
owner and data classification
```

- 域名在批准和执行时解析；DNS 结果必须落在批准地址范围，TTL 到期重解析并重新校验。
- HTTP 重定向每一跳重新授权，不能借批准域名跳到任意内网/公网。
- 默认拒绝云元数据、loopback、link-local、控制面、数据库、Worker 管理和 RFC1918 未批准范围。
- 非 HTTP(S) 协议必须在 Grant 明确批准并有等效目标校验，不能因代理不支持改为全网放行。

### 8.2 Grant 与 Lease 准入

Grant 是权限上限，Lease 是一次运行的占用：

```text
Grant allows target X, max 20 browser sessions, valid 01:00-06:00
Attempt A requests 2 sessions
Admission atomically reserves 2 units as Environment Lease
Egress Gateway allows only Attempt A/fence until min(lease, grant, secret TTL)
```

- 多个 Worker 的 Lease 使用同一全局事实源/网关计数，不能各节点独立限 20。
- 租约维度可包括 session、account、tenant、test-data namespace、QPS token 和 destructive-operation lane。
- Lease 分配与 start commit 同事务/可补偿 intent；commit 失败释放，Attempt 结束/TTL 到期自动释放并对账。
- Grant suspend/revoke 立即阻止新 commit，并在批准收敛时间内使活动 Egress 凭证失效。

### 8.3 秘密发放

- 数据库只保存 `credential_ref` 和版本，不保存可展示明文。
- commit-start 后由 Worker/sidecar 使用自身受限身份换取 Attempt/target/use-bound 短期秘密。
- 优先以只读 tmpfs 文件或专用本地 socket 注入；需要环境变量的 Suite 必须显式声明其日志/进程泄露风险。
- 秘密不写入 ExecutionSpec digest 正文、Worker 日志、事件、指标、Artifact manifest 或错误响应。
- 日志/Artifact 脱敏只作为补救，不能代替最小秘密和出站限制；二进制截图/trace 不能可靠自动脱敏时采用短保留和更严格授权。
- Attempt 停止、fence 失效、Grant 撤销或 Worker quarantine 时秘密立即失效；不能缓存供后续 Attempt 复用。

### 8.4 Egress Enforcement

MVP：执行 bridge/network namespace + 主机 firewall 默认拒绝，只能到 Egress Gateway、对象上传端点和受控依赖代理。

企业：NetworkPolicy 默认拒绝 + Egress Gateway/代理；高风险 Pool 可独立 VPC/子网。NetworkPolicy 提供网络层边界，Gateway 执行 Attempt/Grant/fence/域名/TTL/审计语义。

Gateway 决策输入：

```text
attempt identity/token
current fence
target grant id/version
environment lease id/units/expiry
destination DNS/IP/port/protocol/SNI
request timestamp and byte/rate budget
```

拒绝原因进入安全审计，但不得记录 Authorization、cookie、request body 或 URL secret query 参数。必要的 HTTP 级策略应在不暴露敏感正文的情况下实现。

---

## 9. Collection、Manifest 与分片

### 9.1 Collection 也是不可信执行

pytest collection、Playwright test listing、配置加载和插件发现都可能执行用户代码。因此 Collection 使用特殊 `task_type=collect` Attempt：

- 全新沙箱、固定只读输入和严格资源/网络/时间上限；
- 默认不允许真实 SUT 凭据和业务写访问；
- 输出平台规范 Case 清单、框架原始清单、警告和输入摘要；
- collection 失败使 Batch 停在 `collecting_failed`，不能用缓存旧清单静默继续；
- 可以使用已校验的相同 revision collection cache，但 cache key 包含 source/dependency/config/runner digest。

### 9.2 Manifest item

每个 item 至少包含：

```text
stable_case_id
framework_locator (nodeid / project+file+title / adapter-specific)
atomic_group_id
serial_group / environment / account / data lease requirements
resource_profile_id
historical duration/resource estimate + confidence
tags/selection metadata
```

框架无法提供稳定 Case ID 时，Adapter 必须生成版本化、可解释的稳定 locator；无法保证时将文件/模块作为原子项，并在 UI 标明粒度，不伪造 Case 级完整性。

### 9.3 原子组

按下列顺序形成不能拆分的 atomic group：

1. 用户/框架显式顺序和 serial marker；
2. session/module fixture 或共享 browser context；
3. 同一不可并发账号、设备、租户、测试数据或环境 Lease；
4. 相同浏览器 project、地域、网络区或运行时要求；
5. Suite 明确声明的事务/cleanup 组。

冲突规则：

- 一个组要求互斥 Profile/Pool 时计划失败，要求维护者修正。
- 单 atomic group 超过 Run 硬时限时明确标记 `oversized_atomic_group`，不得偷偷拆开；需修改测试设计或批准专用 Profile。
- 未声明的隐含依赖不能靠算法猜出；平台通过历史冲突信号提示维护者，但不自动改变语义。

### 9.4 估算值

对每个原子组记录：

```text
duration: P50/P90/P95, sample_count, last_updated, cold/warm class
resource: cpu_ms, peak_memory, pid_peak, disk, network, artifact bytes
context: framework, browser/project, image, profile, SUT environment
confidence: high / medium / low
```

- 正常成功样本用于运行时长分位数；timeout、取消、collection/infra failure 分开建模。
- 新 Case 使用 framework + Profile 的保守默认值；样本不足时不能输出 high confidence。
- 时间衰减和最近版本变化影响置信度；模型版本变更不重写历史 Capacity Plan。
- 资源上限不直接等于历史均值，至少使用批准分位数 + 安全余量。

### 9.5 确定性加权分片

推荐算法：约束过滤后的 Weighted Longest Processing Time（LPT）。

1. 按 Profile、Security Profile、network/Grant compatibility 划分不可混合的 bucket。
2. 计算 bucket 总预计工作量与目标 Shard 数：

   ```text
   base_shards = ceil(total_estimated_work / target_shard_duration)
   desired_shards = clamp(base_shards, min_shards, max_shards)
   ```

3. 对 atomic group 按 `(estimated_duration desc, stable_group_id asc)` 稳定排序。
4. 将下一个组放入当前预计负载最低且约束兼容的 Shard；相同时按 shard index 选择。
5. 超长 atomic group 单独成 Shard并标记。
6. 生成每 Shard item list、估算、Profile、约束和 digest。
7. 验证完整性/唯一性后才批准 Plan。

动态负载均衡依靠 Worker 领取未启动的细粒度 Run，而不是迁移在途 Run。企业阶段初始可让 Shard 数约为可用 slot 的 4～8 倍，以降低长尾；最终因初始化开销、Artifact 成本和测试语义校准。

### 9.6 分片属性测试

对随机 Manifest/约束生成器验证：

- 每个 item 恰好出现一次；
- 原子组不拆分；
- 不兼容 Profile/Grant 不混合；
- 相同输入/算法版本输出完全相同 digest；
- 单 item/空 selection/全部 serial/超长组/未知时长有明确结果；
- item 顺序变化但集合/稳定键相同不会造成非预期计划漂移；
- 估算溢出、负值、极大时长和资源单位错误被拒绝。

---

## 10. 容量、Admission 与调度

### 10.1 Resource Vector

统一资源向量：

```text
cpu_millis
memory_bytes
pid_slots
ephemeral_storage_bytes
disk_iops_class
network_bandwidth_class
browser_slots
internal_test_workers
security_profile
network_zone
special labels
```

`internal_test_workers` 不是额外机器资源，而是并发放大因子，必须同时作用于 CPU/内存/browser/SUT Lease 估算。Profile 包含 `requests`（调度预留）和 `limits`（硬上限）；不能只设 limits 后按零 request 超卖。

### 10.2 初始 Profile 模板

以下仅为容量压测起点，不是批准生产值：

| Profile | 用途 | 初始 requests | 初始 hard limits | 内部并发 |
|---|---|---|---|---:|
| `api-small` | 轻量 pytest 接口 | 500m CPU / 512 MiB | 1 CPU / 1 GiB / PID 256 | 1 |
| `api-large` | 重 fixture/并发接口 | 2 CPU / 2 GiB | 4 CPU / 4 GiB / PID 512 | 1～4，计入总预算 |
| `browser-small` | 单浏览器 Playwright | 2 CPU / 3 GiB / 512 MiB shm | 3 CPU / 4 GiB / PID 512 | 1 |
| `browser-large` | 多浏览器/project | 4 CPU / 6 GiB / 1 GiB shm | 6 CPU / 8 GiB / PID 1024 | 经批准 |

正式值由 API/browser/mixed 压测冻结。普通用户只能选择批准 Profile，不直接覆盖 CPU、memory、PID、shm 或 security settings。

### 10.3 Batch 容量模型

```text
W_batch = Σ atomic_group_estimated_duration

required_slots = ceil(
  W_batch
  / ((deadline - collection - image/setup - artifact/finalize - safety_margin)
     × parallel_efficiency)
)

effective_parallelism = min(
  compute_capacity,
  SUT_capacity,
  account/data/environment_capacity,
  external_dependency_capacity,
  security_pool_capacity,
  quota_and_budget_capacity
)
```

示例仅说明数量级差异：30,000 Case、4 小时窗口、并行效率 70% 时，平均 10 秒约需 30 个 slot，平均 60 秒约需 179 个，平均 180 秒约需 536 个。实际还要按 pytest/API 与 Playwright Profile 分开，不能把它们相加后按统一 slot 除。

### 10.4 Admission 顺序

1. **输入/权限**：主体、SuiteRevision、Manifest/Plan、deadline 合法。
2. **安全**：Security Profile、Worker Pool、Target Grant 和 secret policy 可用。
3. **外部容量**：Environment/账号/数据/QPS Lease 可满足。
4. **治理**：项目/用户/Batch 配额和预算允许。
5. **计算可行性**：ready + 可扩容量、启动时间、N+1 和 deadline。
6. **队列公平性**：priority class、dominant share、aging、max batch share。

硬约束失败返回 `infeasible/denied`；暂时容量不足进入 queued 并给限制因素；无法承诺 deadline 但用户允许时才进入 best-effort。

### 10.5 MVP 调度

MVP 单 Worker 不实现复杂全局 DRF，但保留相同字段：

- 三类队列：interactive、scheduled、background/retry；
- 固定 Resource Profile + 多维预留；
- interactive 初始建议保留 25% 可借用容量，最终基线化；
- 存在其他 eligible Batch 时限制单 Batch 资源份额；
- same class 按 priority、deadline/created_at 和 aging 排序；
- 不抢占 running Attempt，只停止低优先级队列领取新 Run；
- 达到主机高水位时关闭新 Admission，保留 control/finalize/cleanup 容量。

### 10.6 企业公平调度

外层在 project/requester 间使用 Weighted Dominant Resource Fairness：

```text
dominant_share(scope) = max(
  cpu_reserved / cpu_total,
  memory_reserved / memory_total,
  browser_slots_reserved / browser_slots_total,
  other_scarce_resource_share
)

weighted_share = dominant_share / configured_weight
```

优先从 `weighted_share` 较低且有 eligible work 的 scope 选择。内层：

- interactive：等待时间 + aging，使用保障/借用容量；
- scheduled：Earliest Deadline First，结合 deadline risk；
- background/retry：较低权重和独立最大份额；
- 单 Batch：`max_batch_share`，系统空闲时可借用。

调度决策必须记录候选、选择原因、资源/Lease/配额快照和规则版本，便于解释“为什么这个任务先跑”。

### 10.7 Retry 策略

| 原因 | 默认自动重试 | 条件 |
|---|---|---|
| Assignment commit 前过期 | 是，不产生 Attempt | 已确认没有 start commit |
| 输入/镜像拉取失败，测试进程未启动 | 有界重试 | 来源故障可重试、digest 不变、预算允许 |
| 明确 Worker/沙箱基础设施失败 | 有界重试 | 已证明旧执行停止且 Lease/secret 失效 |
| 测试断言失败 | 否 | Suite flaky policy 可显式创建独立 retry Attempt |
| 超时 | 否 | 需区分产品/平台；人工或批准策略 |
| 用户取消/策略撤销 | 否 | 新提交是新用户意图 |
| `attempt_unknown` | 否 | 只有人工批准重复副作用或业务证明幂等 |
| Artifact 可续传失败 | 续传，不重跑测试 | 本地/对象内容 digest 可校验 |

所有 retry 受次数、资源、预算和优先级上限；框架内部 retry 必须在结果中显式报告，不能与平台 retry 混为一次通过。

### 10.8 自动扩缩

Capacity Planner 输出每个 Pool 的：

```text
queued_work_ms
forecast_arrivals_ms
deadline_remaining_ms
efficiency
ready_slots / starting_slots / max_slots
desired_resource_vectors
warm_capacity
cost_cap
```

执行层把 Run 转为资源 requests；Kueue 先做 quota admission，Node Autoscaler 对 Pending Pod 增加节点。缩容只针对空闲/已 drain 节点。扩容失败、云配额、镜像拉取和节点 ready P95 都进入 ETA；不能在请求节点后立即把任务标记为“已有容量”。

---

## 11. 执行后端设计

### 11.1 统一端口

Application 只依赖：

```text
ExecutionBackend.offer_or_launch(run_intent) -> backend_ref
ExecutionBackend.observe(backend_ref) -> observations
ExecutionBackend.cancel(backend_ref, reason)
ExecutionBackend.reconcile(expected_assignments) -> differences
ExecutionBackend.drain(scope)
```

领域层负责 Attempt/fence/Evidence；后端只负责把批准规范映射到运行环境和上报受信观察。

### 11.2 Docker Worker Backend

- Dispatcher 创建 Assignment，由 Worker 长轮询 claim。
- Worker commit-start 后本地构造固定容器规范。
- 容器标签仅含非秘密关联：attempt ID、fence、spec digest、worker generation。
- 每个 Attempt 独立 network、workspace volume/tmpfs、PID namespace、cgroup 和日志 driver 限制。
- Image 必须按 digest pull/verify；tag 只用于显示。
- 依赖缓存若启用，只能是只读内容寻址缓存；测试不能写共享 cache 或影响后续 Attempt。
- Worker 主机重启后用标签列出残留，与控制面 reconcile；未经批准不重启旧容器。

### 11.3 Kubernetes Job Backend

1. Planner 按相同 image/Profile/Security/Grant class 分组 Run。
2. Backend 持久化 Job intent/outbox，再创建 Job/Indexed Job。
3. 每个 completion index 只读取冻结 Shard Plan 中对应 Run。
4. Pod 启动使用一次性 bootstrap token 完成 Attempt/fence 身份确认；Pod UID 记录为 backend fact。
5. Pod `restartPolicy=Never`；业务 retry 由平台创建新 Attempt/Job generation。
6. `backoffLimitPerIndex` 初始设为 0 或等效禁止透明业务重试；基础设施控制器仍可能重复创建 Pod，fence 必须处理。
7. Test Pod 不挂载 Kubernetes API token；只访问 Egress Gateway 和 Artifact/Attempt endpoints。
8. Job status 是观察输入，不直接覆盖平台状态；Reconciler 综合 Pod UID、Attempt events、Evidence 和 fence。
9. Job cleanup TTL 只能在 Evidence/诊断保留条件满足后执行，不能先删掉唯一故障证据。

### 11.4 Backend 切换

- 同一 Batch 可以按 Profile 把不同 Run 放到不同批准后端，但每个 Run 一旦 start commit 就不能热迁移。
- 后端选择进入 Capacity/Shard Plan 和 digest，用户可见且可审计。
- 切换/回退只影响未启动 Run；已启动 Attempt 按原后端收敛。
- Docker 与 Kubernetes 后端必须通过同一黑盒契约测试：commit、duplicate start、cancel、lease expiry、unknown、Artifact 和 reconcile。

---

## 12. 沙箱与安全 Profile

### 12.1 Security Profile

| Profile | 适用 | 最低边界 |
|---|---|---|
| `untrusted-standard` | pytest/API、内部普通依赖 | 独立执行主机优先；rootless/userns、非 root、seccomp/LSM、drop all caps、default-deny egress |
| `untrusted-browser` | Playwright | standard + Chromium sandbox、独立 shm/PID、浏览器子进程和下载限制 |
| `high-risk` | 未知来源、敏感 SUT、主动攻击测试 | 强 RuntimeClass/微虚机或独占可销毁 Worker；更严格网络和证据授权 |
| `collection-only` | Case collection/依赖解析 | 无真实 SUT/业务秘密、短时、只读输入、受控输出 |

普通用户不得选择 `trusted` 以放宽隔离。若管理员定义兼容性豁免 Profile，必须显示风险、限制 Suite/目标/时段并有到期日；不能成为默认。

### 12.2 ExecutionSpec 安全字段

```text
run_as_uid/gid (non-zero)
read_only_rootfs = true
no_new_privileges = true
capabilities_drop = ALL
seccomp/LSM profile version
cpu/memory/pids/ephemeral/shm limits
process/file descriptor ulimits
network policy/target grant references
allowed input mounts (read-only, content-addressed)
writable tmpfs/workspace quotas
max wall time / grace / output bytes
image digest and signature/policy result
```

Worker 使用 allowlist 模板生成 runtime spec，对未知字段 fail closed。任何 Profile 改动版本化、审计并需要安全回归测试。

### 12.3 主机保护

- Control 和 Worker 使用不同 OS principal；Worker 不能读控制数据库凭据、OIDC secret 和 TLS private key。
- 测试容器不能访问 `/var/run/docker.sock`、rootless socket、host `/proc`/`sys`/`dev`、cloud metadata 或主机 SSH agent。
- Worker 管理接口无远程入站；主机 SSH/运维入口与测试网络分离并最小化。
- 主机为 control/finalizer/cleanup 保留资源；高水位触发 stop-admit 而非继续超卖。
- Worker 日志/临时目录有轮转、配额和清理；诊断保留不能把磁盘写满。

### 12.4 供应链

- 代码 revision、依赖 lock、包代理来源、runner image、browser image 和 policy version 全部进入 digest。
- 运行期禁止回退公共 registry/package index；未固定依赖按策略拒绝或标低重现性。
- 执行镜像以最小 base 构建、扫描并按组织策略签署；发现高风险漏洞可撤销 digest。
- 缓存只读且内容寻址，写入由独立构建/扫描流程完成；不把某 Attempt 的可写依赖目录共享给下一次。
- SBOM/漏洞结果属于输入证据；不能证明“无漏洞”，但决定准入和紧急 quarantine。

### 12.5 审计事件

必须审计：

- 登录/强认证失败、角色与对象权限变更；
- SuiteRevision/Resource/Security Profile 激活；
- Target Grant 批准、扩大、暂停、撤销和凭据档案变更；
- Worker 注册、证书轮换、generation、drain、quarantine、retire；
- Batch best-effort 批准、配额/预算覆盖；
- Assignment commit、fence 冲突、unknown、人工重试/adjudication；
- Evidence digest 冲突、下载敏感证据、保留/删除和法律保全；
- 灾备恢复、migration、策略回滚和安全例外。

审计记录包含 actor、认证强度、request ID、对象、动作、决策、原因、before/after digest、时间和来源；不存秘密明文。

---

## 13. 可观测性与 SLO

### 13.1 统一关联字段

```text
request_id, trace_id
project_id, suite_revision_id
batch_id, run_id, attempt_id
assignment_id, fence
worker_id, worker_generation, pool
framework, resource_profile, security_profile
sut_environment, target_grant_id
```

ID 作为结构化日志/trace 字段，不作为 Prometheus 无界 label。Metrics 只用低基数维度：framework、profile、pool、priority_class、outcome_class、environment_class。

### 13.2 指标

| 指标 | 类型 | 低基数标签 |
|---|---|---|
| `qep_batch_total` | counter | priority, terminal_class |
| `qep_batch_deadline_seconds` | histogram/gauge projection | priority, suite_class |
| `qep_queue_age_seconds` | histogram | queue, profile, pool |
| `qep_dispatch_latency_seconds` | histogram | profile, pool |
| `qep_attempt_total` | counter | framework, profile, outcome_class |
| `qep_attempt_phase_seconds` | histogram | phase, framework, profile |
| `qep_infra_failure_ratio` | recording rule | pool, failure_class |
| `qep_attempt_unknown` | gauge/counter | pool, reason_class |
| `qep_worker_state` | gauge | pool, state, version_class |
| `qep_worker_capacity` | gauge | pool, resource, state |
| `qep_target_lease_usage` | gauge | environment_class, dimension |
| `qep_egress_denied_total` | counter | reason, environment_class |
| `qep_artifact_finalize_seconds` | histogram | content_class, size_bucket |
| `qep_spool_bytes` | gauge | worker/pool 用受控映射，避免生命周期无界 |
| `qep_prediction_error_ratio` | histogram | framework, profile, model_version_class |
| `qep_scale_ready_seconds` | histogram | pool, result |

### 13.3 日志与 trace

- 控制面日志为 JSON，字段 schema 版本化；错误日志不输出 token、cookie、secret、完整 URL query 或用户敏感 payload。
- Worker 平台日志与测试 stdout 分开；测试日志进入 Artifact/有限 tail。
- Trace 覆盖 `submit → validate → collect → plan → admit → assign → commit → finalize`，但不采集测试正文。
- 大批量 Run 使用 head/tail/错误采样，不能因全采样让观测系统成为瓶颈。
- 日志后端丢失不改变业务状态；审计是独立持久事实。

### 13.4 告警与处置

| 告警 | 触发 | 第一处置 |
|---|---|---|
| Deadline risk | admitted Batch 预测违约 | 重算限制因素；检查 Pool/SUT/扩容，不盲目提优先级 |
| Interactive queue SLO | P95/P99 超阈值 | 检查保障容量、借用回收和异常长 Run |
| Unknown Attempt | 任一新增/超处置时限 | 撤销访问，保全证据，禁止自动 retry，人工裁决 |
| Fence/event conflict | 同 ID/seq 不同 digest 或旧 fence | 暂停 Assignment，评估 Worker quarantine |
| Worker lost | 心跳/renew 超阈值 | 隔离故障域，重规划未启动 Run，检查 N+1 |
| SUT saturation | Lease/QPS 达上限 | 停止计算扩容误导，通知环境负责人 |
| Artifact backlog/spool high | finalize 延迟/磁盘水位 | 背压新任务、恢复对象存储、禁止删唯一证据 |
| DB health/PITR failure | lock/replication/backup 异常 | stop-admit、保护事实源、按 runbook 恢复 |
| Prediction drift | 误差持续超预算 | 降级保守 Profile，切 Shadow，重新训练/校准 |
| Security policy unavailable | Egress/identity/secret/audit 异常 | fail closed 新执行，收敛现有 Attempt |

---

## 14. 故障恢复与 Reconciliation

### 14.1 Reconciler 原则

- 状态机是期望事实，Worker/Job/Object Storage 是外部观察；任何一个单独都不代表真相完整。
- Reconciler 操作幂等、可重复、分批、带游标和最大工作量，不能一次全表扫描拖垮状态库。
- 不确定性显式升级为 unknown/incident，不用“最终一致”掩盖无法证明的副作用。
- 删除/清理是最后动作，必须先确认没有恢复/调查/证据引用需求。

### 14.2 故障矩阵

| 故障 | 可安全自动动作 | 禁止动作 |
|---|---|---|
| API 响应丢失 | 客户端用幂等键重试/查询 | 换新 key 重复创建 |
| Schedule leader 崩溃 | 新 leader claim due schedule；同触发键幂等 | 用内存“已运行”判断 |
| Planner 崩溃 | 重算未批准 Plan，比较 digest | 修改已批准 Plan |
| Assignment commit 前 Worker 消失 | offer 过期、释放资源、重新分配 | 创建 Attempt/记 infra failure |
| commit 后、sandbox 前 Worker 消失 | Attempt 进入 lost 评估；已证明无执行可 infra_failed | 直接退回 queued、抹去 Attempt |
| running Worker 失联 | TTL 停访问；标 unknown/infra based on proof | 立即盲重跑非幂等测试 |
| postcommit cancel 但 stop 不可证明 | 写入 intent-bound unknown，等待显式裁决 | 直接标 cancelled 或盲重跑 |
| cancelled/completed Evidence 竞争 | 保留先 finalized 的 root，另一方向返回冲突 | 用较晚到达事实覆盖终态 |
| Worker 恢复带旧 fence | 要求停止、隔离迟到证据 | 允许推进当前状态 |
| 容器/Pod 重复启动 | 只有当前 fence/attempt token 生效，其余终止 | 让两个副本共享 Artifact 路径 |
| PostgreSQL 短时不可用 | stop new commit；Worker 到 lease deadline 停止 | 本地无限续租/新启动 |
| PostgreSQL 恢复/PITR | 全量对账 Worker/Job/Lease/Artifact | 默认所有 running 仍有效 |
| Object Storage 不可用 | 有界 spool、续传、背压 | 标记 Evidence 完成或无限写本地 |
| Egress Gateway 不可用 | fail closed；等待/停止真实目标测试 | 直连 SUT 绕过 |
| Secret service 不可用 | 不启动；现有秘密按原 TTL | 下发长期 fallback secret |
| 节点 autoscale 失败 | 更新 ETA/deadline risk、best-effort/拒绝 | 超预算或降级安全 Pool |
| 磁盘高水位 | stop-admit、清理已确认垃圾、告警 | 删除未上传唯一证据 |

### 14.3 Unknown adjudication

管理员页面必须显示：

- 最后有效 fence/renew/event；
- Worker/网关/secret 的最后确认时间和到期时间；
- 是否观察到测试进程启动、SUT 访问和业务副作用；
- 已上传 Artifact 和缺失证据；
- Suite 幂等声明、环境负责人意见和重复执行风险。

可选决策：

- `confirm_stopped_then_retry`：有外部证据证明旧执行停止；创建新 Attempt。
- `accept_duplicate_risk_then_retry`：业务负责人明确接受；高价值审计。
- `mark_infra_failed_no_retry`：保持原事实，不再执行。
- `mark_completed_from_verified_evidence`：仅当完整受信证据能证明终态，不能靠测试自报文本。

Adjudication 是附加记录，不修改原 `attempt_unknown` 事件历史。

### 14.4 Garbage Collection

GC 分级：

1. 已完成且 Evidence finalized、超过保留期的对象按策略删除。
2. 未完成 upload session 超 TTL：确认 Attempt 不活动后 abort，记录审计。
3. Worker orphan runtime/workspace：双向 reconcile 后删除；安全事件可冻结。
4. Kubernetes Job/Pod：保留诊断窗口后 TTL 清理，Artifact/Evidence 已外置。
5. PostgreSQL 在线明细：先归档/对账，再按分区/批次删除；不做无界单事务 delete。

---

## 15. 配置、部署与运行参数

### 15.1 配置分级

| 级别 | 示例 | 变更控制 |
|---|---|---|
| Code default | 协议安全上限、Schema 兼容 | 代码评审和测试 |
| Environment config | DB/object endpoints、日志级别、实例角色 | 配置发布、秘密引用 |
| Versioned policy | Resource/Security Profile、queue share、retention | 管理 API、审批、审计、可回滚 |
| Runtime grant | Target Grant、Lease、Attempt token | 短期、最小权限、自动失效 |

未知配置键启动失败；单位字段显式（ms/bytes/millis），不接受含糊字符串。安全硬默认不可被普通环境变量静默放宽。

### 15.2 MVP 服务与端口

| 服务 | 网络可见性 | 数据/秘密 |
|---|---|---|
| Nginx | 批准内网入口 | TLS cert；无 DB/Worker 权限 |
| UI | 经 Nginx | 无 secret；运行时配置只含 public origin |
| Control API | 仅 Nginx/内部管理 | DB/OIDC/object metadata 权限；无 Docker socket |
| Schedule/Planner/Dispatcher/Reconciler | 内部网络 | 最小 DB 角色；需要的服务身份分开 |
| PostgreSQL | 仅 control network/主机 | 独立持久卷和备份凭据 |
| Worker Agent | 主动出站到 Control；无远程入站 | mTLS key、rootless Docker socket、短期 Attempt tokens |
| Egress Gateway | 仅执行网段入口/批准出口 | Grant/fence 验证身份；无用户管理权限 |

### 15.3 数据库角色

- `qep_api`：用户命令/查询所需表和存储过程；不能改 migration/audit。
- `qep_scheduler`：schedule claim、Batch create intent。
- `qep_dispatcher`：Run/Assignment/Lease/Worker capacity。
- `qep_finalizer`：events/results/artifact/evidence finalize。
- `qep_migrator`：仅发布窗口使用，不能作为运行时凭据。
- `qep_readonly/auditor`：受限查询和审计。

若 MVP 为降低连接数复用一个应用角色，仍须在代码端口和审计中分主体；企业阶段按上述最小权限拆分。

### 15.4 备份与恢复

- PostgreSQL：连续/高频恢复点 + 定期全量备份到异机；加密、保留和恢复测试。
- Object Storage：lifecycle、版本/不可变策略按 Evidence class；数据库备份不等同 Artifact 备份。
- 配置/Policy：版本化导出；Secret 只备份引用和恢复流程，不把明文塞入普通备份。
- 恢复后运行一致性检查：Manifest/Plan、Run/Attempt、Lease、Evidence URI/digest 和 Audit 序列。

---

## 16. TDD 与验证设计

### 16.1 实现顺序

每个切片先写失败测试表达外部可观察契约，再做最小实现；纯文档和部署探索除外。

| 测试层 | 重点 |
|---|---|
| Domain unit/property | 状态迁移、不变量、分片完整性、容量公式、公平/重试策略 |
| PostgreSQL integration | 幂等、CAS、`SKIP LOCKED`、Lease、fence、事件冲突、migration |
| Worker protocol contract | JSON schema、mTLS identity、commit replay、renew/expiry、error matrix |
| Executor conformance | Docker/K8s duplicate start、cancel、resource/security、reconcile |
| Security negative | 越权、恶意容器参数、网络扫描、secret/Artifact/供应链攻击 |
| End-to-end | pytest/Playwright → SUT Grant → result/Evidence → UI/API |
| Load/fault | 100k/30k/60k、Worker/DB/store/egress failures、retry storm |
| Recovery | backup/PITR、Worker restart、region recovery and evidence reconciliation |

### 16.2 关键并发测试

- 100 个并发相同 Idempotency-Key：只创建一个 Batch，其他返回同一结果。
- 多 Dispatcher 对同一队列 claim：每个 Run 同一时刻最多一个活动 Assignment。
- Assignment commit 响应丢失并并发重试：一个 Attempt/fence。
- renew/cancel/lease expiry 竞态：最终状态合法，过期 Worker 不能继续访问。
- cancel 与 commit-start 的双向 CAS：只有一个权威顺序，败者不能补造 Attempt 或取消终态。
- cancel 与 expiry/release 边界：expiry 边界优先，历史 closure 不被 backdated intent 改写。
- cancelled/completed Evidence 双向先后：先 finalized root 不可被另一 outcome 覆盖。
- stop proof 与 unknown 竞争：unknown 一旦持久化即为吸收终态；迟到 proof 只能形成冲突。
- finalized cancel 后迟到 event/Evidence：exact replay 幂等，其余事实不得推进终态。
- 同 `event_id/seq` 相同/不同 digest：分别幂等/冲突。
- Evidence finalize 与迟到 upload/retry 竞态：旧 fence 不覆盖，新 Attempt 路径独立。
- Grant revoke 与 Worker renew/egress request 竞态：在 TTL 上限内 fail closed。
- Worker generation 轮换与迟到旧请求：全部拒绝。

### 16.3 属性与模型测试

- 使用状态机模型生成随机命令/故障序列，验证无非法终态和 fence 倒退。
- 使用随机 Manifest/原子约束验证完整性、确定性和约束保持。
- 使用离散事件模拟真实到达轨迹，验证 queue SLO、deadline、公平、N+1 和扩容策略。
- 所有随机测试保留 seed、输入摘要和最小化反例，能在 CI/容器中重现。

### 16.4 发布证据

每个 Release Evidence Bundle 至少包括：

- requirement/design/test traceability snapshot；
- migration/rollback 与恢复报告；
- API/Worker schema compatibility；
- pytest/Playwright 代表性 E2E Evidence Manifest；
- malicious workload/network/secret/Artifact security report；
- capacity、prediction、deadline/fairness 和故障演练；
- 已知风险、TBD/豁免、批准人和有效期。

---

## 17. 需求—设计—测试追踪

| 设计域 | MVP 需求 | 企业需求 | 测试族 |
|---|---|---|---|
| Domain/DB state | MVP-FR-004～012、017～019 | ENT-FR-003～015、023～027 | T-DOM-STATE、T-DB-CONCURRENCY |
| Manifest/sharding | MVP-FR-004、007 | ENT-FR-004～009 | T-MANIFEST、T-SHARD-PROPERTY |
| Capacity/admission | MVP-FR-008～009、014、024 | ENT-FR-009～013、017、034～035 | T-CAPACITY、T-FAIRNESS、T-SIM |
| Worker protocol | MVP-FR-011～012、019 | ENT-FR-014～018、023～024、039 | T-WP-CONTRACT、T-WP-FAULT |
| Docker/K8s executor | MVP-FR-010～012 | ENT-FR-016～019、030、039 | T-EXEC-CONFORMANCE、T-DUP-START |
| Target/Lease/secret | MVP-FR-013～015 | ENT-FR-020～022、034 | T-EGRESS、T-GRANT、T-SECRET |
| Artifact/Evidence | MVP-FR-016～019、023 | ENT-FR-025～027、031 | T-UPLOAD、T-EVIDENCE、T-RESULT |
| Auth/audit/governance | MVP-FR-020～023 | ENT-FR-028～029、034、040 | T-RBAC、T-AUDIT、T-RETENTION |
| HA/reconcile/recovery | MVP-FR-019、022 | ENT-FR-018、030～033、039 | T-RECONCILE、T-FAULT、T-DR |
| Security Profile | SEC-MVP-001～012 | SEC-ENT-001～014 | T-SANDBOX、T-NETWORK、T-SUPPLY |
| Observability/model | MVP-FR-024 | ENT-FR-032～036 | T-TELEMETRY、T-SLO、T-DRIFT |

---

## 18. 实施切片

### 18.1 MVP 切片

| 顺序 | 切片 | 完成定义 |
|---:|---|---|
| 1 | 领域状态与 PostgreSQL 基础 | 状态/幂等/CAS/审计测试通过，migration 可回退 |
| 2 | Suite/Batch API 与 OIDC/RBAC | OpenAPI 契约、对象授权、idempotency 通过 |
| 3 | Collection/Manifest/Shard Plan | 代表性 pytest/Playwright 清单与属性测试无遗漏 |
| 4 | Worker 协议 Fake | commit/renew/event/reconcile 错误矩阵通过，无 Docker 依赖 |
| 5 | Docker Worker Executor | rootless/资源/安全/取消/清理 conformance 通过 |
| 6 | Target Grant/Egress/Secret | 无授权、撤销、TTL、扫描和直连绕过测试通过 |
| 7 | Artifact/Evidence/Result | 分块、摘要、finalize、Case 聚合和 retry 证据通过 |
| 8 | Schedule/Queue/Capacity | 有界队列、Profile、三类优先级和资源遥测通过 |
| 9 | Reconcile/Backup/Operations | 重启、孤儿、spool、drain 和异机恢复演练通过 |
| 10 | UI/UAT/容量基线 | 10 人流程、100k/30k、API/browser/mixed 门禁通过 |

### 18.2 企业切片

| 顺序 | 切片 | 完成定义 |
|---:|---|---|
| E1 | 远程静态 Worker Pool | mTLS/generation、多 Worker 故障隔离、N+1、drain |
| E2 | 历史画像和公平调度 | P90/P95 模型、LPT、DRF/EDF、Shadow 误差门禁 |
| E3 | 控制面 HA/PostgreSQL HA | 多副本、leader failover、PITR 和 Evidence 对账 |
| E4 | Kubernetes backend shadow | Job/Indexed Job duplicate-start 和 conformance 通过 |
| E5 | Kueue/Node Autoscaler | quota/Pool/ready time/成本/SUT 双重准入通过 |
| E6 | 强沙箱与安全 Pool | gVisor/Kata 兼容、安全和性能批准 |
| E7 | 自动扩缩 GA | Shadow → canary → 自动；可回退固定 Pool |
| E8 | 区域恢复与企业发布 | 30k+peak、最大故障、RPO/RTO 和全部发布门禁通过 |

每个切片不得同时引入领域语义和基础设施大替换；先用 Fake/契约冻结行为，再接真实后端。

---

## 19. 详细设计未决事项

| ID | 未决事项 | 默认/安全处理 | 关闭证据 |
|---|---|---|---|
| OI-DES-001 | ECS 规格与正式 Resource Profile 数值 | 使用低并发保守 Profile，未校准不承诺窗口 | API/browser/mixed 压测 |
| OI-DES-002 | OIDC、Secret Manager、对象存储具体提供方 | 抽象端口，优先公司已有服务 | 运维服务目录与集成 PoC |
| OI-DES-003 | Rootless Docker + Chromium sandbox 兼容 | Playwright 不通过则阻断 browser MVP 或增加执行主机 | 真实浏览器矩阵报告 |
| OI-DES-004 | gVisor/Kata 选择 | high-risk 不启用前不得宣称强恶意代码隔离 | 安全/性能/兼容对比 |
| OI-DES-005 | Target 协议和 DNS/重定向范围 | 默认仅批准 HTTP(S)，其他拒绝 | SUT 目标矩阵 |
| OI-DES-006 | Grant/lease/renew/TTL 正式时长 | 使用保守 bootstrap 值 | 网络分区和取消演练 |
| OI-DES-007 | Evidence 保留、单 Attempt 大小和敏感下载 | 默认 30 天和硬上限，敏感更严 | QA/安全/成本批准 |
| OI-DES-008 | schedule misfire 和 deadline 策略 | 不自动并发补跑多个大回归 | 产品规则签署 |
| OI-DES-009 | 企业 K8s/Kueue 是否已有组织能力 | 无能力则停留静态 VM Pool | 运维 readiness review |
| OI-DES-010 | 预测模型样本阈值/衰减/置信度 | 未达门禁使用保守 class default | Shadow Planning 报告 |
| OI-DES-011 | cancel intent 后 completed Evidence 先 finalize 时的完整 Run/Batch derived terminal | 保留 immutable Attempt Evidence 与 cancel intent，不补造 Run terminal | 完整状态模型、数据库并发与 reconcile 测试 |

---

## 20. 设计评审门禁

- [x] 每个 P0 领域需求有明确模块、状态、数据或协议落点。
- [x] 控制面与测试执行的进程、权限、网络和秘密边界明确。
- [x] Assignment commit 前后故障语义、fence、TTL 和 unknown 明确。
- [x] Manifest、Shard Plan、Attempt、Result 和 Evidence 完整性可机械验证。
- [x] 单 ECS Docker 与企业 Kubernetes 后端共享领域语义，并有切换/回退规则。
- [x] 资源估算、嵌套并发、SUT Lease、公平性和自动扩缩职责明确。
- [x] 安全、故障、恢复、观测和 TDD 验证路径明确。
- [ ] 产品、技术、QA、安全和运维批准全部未决事项或阻断例外。
- [ ] API/Worker/OpenAPI/JSON Schema、数据库 DDL 和沙箱模板完成实现级评审。
- [ ] 现有代码差距分析完成；在此之前不因实现方便修改本设计基线。
