# qarunner 系统需求规格

> 文档类型：SRS
> 文档编号：QARUNNER-SRS-001
> 版本：V1.4.1
> 状态：评审中
> 更新日期：2026-07-12

## 0. 目的与范围

本文把 [产品需求](REQUIREMENTS.md) 转换为可实现、可测试的系统契约，重点定义：

- Run provenance。
- Profile 和 Suite 版本。
- Dependency Snapshot 与 Target Access Grant。
- Source Acquisition、Environment Lease 与 Evidence Manifest。
- Run 与 Schedule 状态。
- executor 的 isolated / approved-target，以及依赖准备的 approved-registry 网络模式。
- 基线等价与回归分类。
- 数据、接口、性能、不可信内容交付和证据要求。
- 幂等创建、时钟语义和目标环境并发控制。
- 控制面与单专用 Worker 的认证协议、任务 claim/fencing、心跳、drain 和证据上传。
- 每个 Run 的一次性容器创建、销毁、不可复用和 Worker 失联恢复。
- 旧需求 ID 的迁移关系。

控制面与 Worker 的传输、身份、状态、错误和恢复细节见
[Worker 协议参考规范](WORKER_PROTOCOL.md)；安全控制和生产运行要求见
[SECURITY_OPERATIONS_REQUIREMENTS.md](SECURITY_OPERATIONS_REQUIREMENTS.md)；产品需求到实现与验收证据的映射见 [REQUIREMENTS_TRACEABILITY.md](REQUIREMENTS_TRACEABILITY.md)。

## 1. 系统不变量

| ID | 不变量 | 违反时的系统行为 |
|---|---|---|
| SYS-INV-001 | 每个 Run 创建后必须绑定不可变 provenance | provenance 不完整的 Run 不得进入自动回归分析 |
| SYS-INV-002 | 每个 Run 最终只能进入一个终态 | 冲突更新必须原子拒绝并产生错误记录 |
| SYS-INV-003 | 自动基线只能来自满足批准等价策略的历史 Run | 无满足条件的 Run 时返回“无可比基线” |
| SYS-INV-004 | 测试执行状态与测试结果必须分离 | completed 只表示证据成功归档，不代表全部测试通过 |
| SYS-INV-005 | 原始证据优先于派生分析 | 报告、diff、趋势或 AI 故障不得覆盖原始结果 |
| SYS-INV-006 | 权限必须贯穿资源和派生查询 | 无权访问 Run 时，也无权访问其报告、产物、diff、历史或 AI |
| SYS-INV-007 | 网络能力默认拒绝 | 未绑定批准策略时执行器必须使用 isolated |
| SYS-INV-008 | 调度漏跑不得静默 | 错过的触发点必须形成 missed 记录或批准的补偿 Run |
| SYS-INV-009 | 扩展故障不得影响核心链路 | 通知、看板和 AI 故障不能改变 Run 终态 |
| SYS-INV-010 | 当前实现事实与目标要求必须区分 | 未实现要求必须标记 gap，不能在 FEATURES 中宣称已具备 |
| SYS-INV-011 | 网络可达性不构成真实 SUT 执行授权 | approved-target Run 缺少有效 Target Access Grant 时不得创建或启动 |
| SYS-INV-012 | 依赖准备和派生内容均不可信 | 依赖安装、报告生成和主动内容不得进入控制面高权限/会话上下文 |
| SYS-INV-013 | 调度授权失败不得静默 | 触发点因授权、依赖快照或策略无效而失败时必须形成 blocked-policy 记录 |
| SYS-INV-014 | 已创建 Run 的源码与依赖不可变 | pull、prepare 或工作目录变化不得改变 queued/running Run 实际输入 |
| SYS-INV-015 | 手工/API Run 创建幂等 | 相同主体、接口、Idempotency-Key 和规范化请求最多对应一个 Run |
| SYS-INV-016 | 共享目标使用受 fencing 保护的 Lease | 无有效 Environment Lease 的 approved-target executor 不得启动或继续使用目标 |
| SYS-INV-017 | 终态证据可校验且冻结 | Evidence Manifest 完成后内容变化必须标记 corrupt，不得静默继续作为基线 |
| SYS-INV-018 | 时间语义可解释 | 期限和调度使用 UTC，耗时使用 monotonic；clock skew/跳变必须可检测 |
| SYS-INV-019 | 不可信阶段不在控制面执行 | Source Acquisition、依赖准备、测试执行和报告生成只能在 Worker 的受限容器中运行；控制面不得回退本地执行 |
| SYS-INV-020 | 每个 Run 使用全新 executor | 每个 Run/attempt 创建全新容器，终态后销毁；不得复用容器、可写层或上一个 Run 的临时目录 |
| SYS-INV-021 | Worker 协议和证据交付可认证、可防重放 | Worker 任务必须绑定 job digest、claim/fencing token 和作用域；状态/证据上传必须幂等并校验完整性 |
| SYS-INV-022 | 控制面与 Worker 凭证隔离 | Worker agent 不持有控制面数据库凭证、管理员会话密钥或无关业务秘密；控制面不持有 Worker Docker socket |

## 2. 领域对象

### 2.1 Suite

Suite 表示一组可执行测试代码。

必须记录：

- suite_id：稳定唯一 ID，不使用可变名称作为主身份。
- display_name。
- source_type：git 或 restricted-local。
- source_location：脱敏后的仓库或受控目录标识。
- source_revision：Git commit SHA；非 Git 来源使用内容 digest。
- created_by。
- created_at。
- credential_ref：可选，只保存引用。

每次 clone/pull/本地内容批准必须形成不可变 Suite Revision：

- suite_revision_id。
- suite_id。
- source_revision：Git commit SHA；非 Git 来源使用来源标识。
- content_digest。
- 只读 workspace snapshot/ref。
- acquisition_image_digest、source_policy_id/version。
- submodule/LFS/filter/hook policy 和 sanitization result。
- created_at、created_by。

规则：

- Git branch/tag 只是更新来源，不是不可变 revision。
- clone/fetch/checkout/ref 解析必须在 Source Acquisition 沙箱中执行；只允许 approved-source 网络，不能访问 Docker socket、数据库或控制面秘密。
- 每次 pull 后必须解析 commit SHA、计算 content digest 并创建新 Revision，不能原地改写历史 Revision。
- Run 创建时冻结 suite_revision_id；executor 只挂载该 Revision 的只读 workspace，不读取可变 Suite 工作目录。
- restricted-local 只能由管理员或开发部署启用。
- Suite 删除不得让历史 Run 丢失其来源标识。

### 2.2 Profile

Profile 表示可复用执行口径。

必须记录：

- profile_id：稳定 ID。
- revision：每次影响执行结果的修改递增。
- suite_id。
- runner。
- selected_files、selected_markers、extra_args。
- timeout。
- target_environment_id、target_environment_policy_version。
- network_policy_id 和 network_policy_version。
- non_secret_env。
- secret_refs：密钥引用及版本，不含明文。
- notification_ref：可选秘密引用。
- created_by、created_at、updated_at。

Profile 名称、描述等纯展示字段变化可以不增加 execution revision；其他执行字段变化必须产生新 revision。

### 2.3 Target Environment

Target Environment 表示批准的真实被测环境。

必须记录：

- target_environment_id、target_environment_policy_version。
- 名称与用途，例如 staging、preprod。
- environment_class：non-production 或 production；production 在专项决策批准前不可启用。
- data_classification 和允许的测试数据类别。
- mutation_risk：read-only、controlled-write 或 destructive-capable。
- readiness_probe 和失败处理策略。
- concurrency_limit、isolation_mode：shared/exclusive/namespaced。
- namespace_strategy 和 namespace 清理方式。
- cleanup_policy：mandatory 或 best-effort，以及超时。
- 允许的基础地址或服务身份。
- 关联网络策略。
- 允许的 secret ref 范围或账号策略。
- 可选 target_revision 采集方式。
- 管理员状态：enabled/disabled。

普通用户只能选择 enabled 环境，不能创建或修改目标环境。

### 2.4 Environment Lease

Environment Lease 表示某个 Run 在限定时间内使用 Target Environment 容量与数据命名空间的、受 fencing 保护的凭证。

必须记录：

- lease_id、target_environment_id、run_id。
- namespace/partition 标识。
- fencing_token：单调递增，旧 token 不能继续操作。
- acquired_at、expires_at、heartbeat_at、released_at。
- status：active/released/expired/revoked。
- readiness_result、cleanup_status 和 recovery_reason。

规则：

- lease 获取必须原子遵守 target concurrency_limit 和 isolation_mode。
- approved-target executor 启动前必须持有 active lease；heartbeat 或 fencing 失败时终止 Run。isolated Run 不创建 lease。
- fencing 必须由实际网络、namespace 或目标访问边界执行；只在数据库中比较 token 不构成有效隔离。
- 无容量时 Run 保持 queued，wait_reason=target-capacity。
- mandatory cleanup 成功并释放 lease 后才能进入 completed；cleanup 失败时保留测试证据并进入 failed/cleanup_failed。
- crash、timeout、cancel 和 Grant revoke 后必须回收 lease；旧 worker 不能用 stale token 继续写目标。

### 2.5 Network Policy

Network Policy 表示执行器允许访问的网络范围。

类型：

- isolated：无外部网络。
- approved-target：只访问管理员批准的 Docker 网络或目标集合。
- approved-source：只供 Source Acquisition 沙箱访问批准的 Git host/协议/端口。
- approved-registry：只供依赖准备沙箱访问管理员批准的软件源，不得用于测试执行或 SUT 访问。

必须版本化并记录：

- policy_id、version、type。
- 允许目标、端口、协议或 Docker network。
- 明确拒绝目标。
- 创建人、批准人、创建时间和状态。

### 2.6 Target Access Grant

Target Access Grant 表示管理员允许某个不可变执行组合访问真实被测环境。它与 Network Policy 职责不同：Network Policy 描述网络能到哪里，Grant 描述哪份代码、配置、依赖和密钥被允许使用该网络。

必须记录：

- grant_id、version、status：draft/active/revoked/expired。
- suite_id、suite_revision_id、source_revision、content_digest。
- profile_id、profile_revision；无 Profile 的即时运行使用 manual_execution_scope_hash。
- dependency_snapshot_id/digest。
- target_environment_id 和 target_environment_policy_version。
- network_policy_id/version。
- secret_ref IDs/versions。
- allowed_initiators 或 owner scope。
- issued_by、issued_at、expires_at、reason。
- revoked_by、revoked_at、revocation_reason（适用时）。

规则：

- 只有管理员可以创建、激活、续期或吊销 Grant。
- Grant 必须固定不可变组合，不能使用“当前最新版”代替 Suite Revision、Profile/手工请求、dependency 或 secret version。
- 任一绑定项变化后旧 Grant 不匹配；系统不得自动扩权或继承批准。
- Run 创建和 executor 启动前都必须校验 Grant，避免排队期间被吊销后仍执行。
- Run 创建和 executor 启动时必须满足 `expires_at - now >= run_timeout + revocation_stop_timeout + target_cleanup_timeout`；`target_cleanup_timeout` 来自 Target Environment policy，仅在明确不需要目标侧清理时可为 0。任一参数缺失或余量不足时拒绝创建/启动。
- 管理员吊销 Grant 时，匹配的 queued Run 立即失败，running Run 必须在配置的 revocation_stop_timeout 内终止并以 policy_revoked 原因收敛。
- secret、Network Policy、Dependency Snapshot 和 Suite Revision 必须解析到 Grant 记录的精确版本；不得用“当前版本”替代，缺失或吊销时 fail closed。
- Grant 只允许使用批准网络，不得覆盖 Network Policy 的拒绝规则。

### 2.7 Dependency Snapshot

Dependency Snapshot 表示在受限准备沙箱中生成、可供 Run 只读使用的依赖结果。

必须记录：

- dependency_snapshot_id。
- suite_id、suite_revision_id、source_revision、content_digest。
- lockfile/依赖声明类型与 digest，以及解析后的依赖清单 digest。
- preparation_image_digest。
- registry_policy_id/version。
- dependency artifact 或文件树 digest。
- 生命周期脚本策略。
- created_at、created_by、status。
- 可选 SBOM/扫描结果引用。

同一 Snapshot 不能在生成后原地修改。Suite revision、锁文件/依赖声明、解析结果、准备镜像、批准软件源或脚本策略变化时必须生成新 Snapshot。没有外部依赖时必须保存明确的 no-dependencies 标记，不能用缺字段表示。

### 2.8 Run

Run 是一次不可变执行请求及其结果。

Run provenance 至少包含：

| 类别 | 字段 |
|---|---|
| 身份 | run_id、created_by、trigger_type、created_at、client_request_id/idempotency_key_hash |
| Suite | suite_id、suite_revision_id、source_type、source_revision、content_digest |
| Profile | profile_id、profile_revision 或手工请求 revision |
| 执行 | runner、选择项、编译后参数、timeout |
| 环境 | approved-target 时：target_environment_id、target_environment_policy_version；isolated 时显式记录 not_applicable |
| 网络 | network_policy_id、network_policy_version、network_mode |
| 授权 | target_access_grant_id/version（approved-target 时必填；isolated 时为 not_applicable） |
| 依赖 | dependency_snapshot_id、dependency_snapshot_digest |
| 镜像 | executor_image_name、executor_image_digest |
| 平台 | qarunner_version、schema_version |
| 配置 | execution_scope_hash、non_secret_env_fingerprint、secret_ref_versions |

规则：

- provenance 在 Run 创建时冻结。
- Run 实际挂载的 Suite Revision/Dependency Snapshot 必须与 provenance digest 一致，启动前校验失败则不得执行。
- API 不返回密钥明文。
- secret_ref_versions 用于追踪轮换，不保存 secret value。
- Run 记录 run_phase：preflight/execute/collect/cleanup，以及 wait_reason、readiness_result 和 cleanup_status。
- V7 新 Run 必须在创建事务内冻结全部必填 provenance；任一字段无法解析时创建失败且不得 dequeue。
- `provenance_complete=false` 只允许用于 legacy Run、迁移异常或创建后检测到的完整性损坏，不能作为新 Run 的正常降级路径。
- worker_id、agent_version、host_baseline_digest、assignment/container ID、lease_id、fencing_token、namespace、target_revision、readiness_result、cleanup_status、Evidence Manifest 状态/引用和终态时间属于执行/结果元数据，不属于创建时 provenance；它们在对应生命周期阶段追加后分别冻结，不得反向修改已冻结输入。

### 2.9 Evidence Manifest

Evidence Manifest 在 executor 停止（或确认从未启动）且证据收集尝试结束后收敛；Manifest 的 `finalized`/`finalize_failed` 结果与 Run 终态必须在同一持久原子单元提交。所有终态均显式记录证据的 present/truncated/missing/deleted 状态和 cleanup 结果，不能因 cleanup 或 finalize 失败而丢弃已取得证据，也不能把失败伪装成完整 Manifest。

至少记录：

- 收敛记录包含 run_id、manifest_status（finalized/finalize_failed）和 attempted_at；finalize_failed 时必须记录稳定 failure_code 与受限原因。
- 只有 manifest_status=finalized 时才要求 manifest_id、version、finalized_at 和后续完整字段。
- 每个文件的规范化相对路径、size、SHA-256 digest、媒体分类和 sensitivity。
- stdout/stderr、summary、case records、report 与 artifact 的完整/截断/缺失状态。
- collector/reporter 版本、manifest_root_digest。

规则：

- manifest 完成后证据只读；任何 digest 不匹配标记 evidence_corrupt 并禁止作为 strict baseline。
- 测试代码可以伪造自身输出，因此 Manifest 只证明平台收集后未被无声修改，不证明测试逻辑或内容真实。
- 下载、备份与恢复必须可校验 digest；缺失、损坏和受控删除是不同状态。

### 2.10 Worker 与 Execution Assignment

Worker 表示唯一受控执行主机上的 Worker agent，不等同于测试容器。V7 生产环境只允许一个 active Worker；不提供多 Worker 调度或自动切换。

Worker 必须记录：

- worker_id、host_id、worker_generation、agent_version、identity/certificate version、agent_instance_id/boot_id。
- status：registered/ready/draining/offline/quarantined。
- capabilities：支持的 runner、executor image、浏览器、Network Policy 和 ResourceLimitProfile 版本。
- host_baseline_digest、capabilities_digest、last_seen、heartbeat_at、drain_reason、quarantined_reason。
- max_concurrency、active_slots、source/preparation/executor/report 能力状态。
- health.degraded 和 degraded_components；degraded 是健康维度，不是 Worker 生命周期状态，无法证明隔离时必须进入 quarantined。

Execution Assignment 必须记录：

- assignment_id、job_id、worker_id、worker_generation、task_type、subject_type/subject_id、canonical_job_digest；run_id 仅 executor/report task 必填。
- opaque assignment token、单调 assignment fence、claimed_at、expires_at、heartbeat_at、released_at。
- start_commit_id、task_attempt_id、executor task 的 execution_attempt_id、lease_version、container_id/effective spec digest、workspace/snapshot digest、upload session 和状态。

规则：

- Worker 注册、证书/令牌轮换、ready/drain/quarantine 只能由管理员或运维控制；同主机轮换递增 identity version，主机重建/替换递增 worker_generation 并 fence 旧身份和 assignment；Worker 不得自行扩大能力声明。
- Worker 主动通过 mTLS HTTPS 长轮询领取冻结任务，不监听入站端口；控制面不主动连接 Worker，Worker 不直接访问控制面数据库。
- claim、commit-start、renew、release、事件和上传必须同时校验 mTLS identity、worker_generation、assignment token/fence 和 canonical job digest；旧 Worker、旧 fence 或过期 token 的写入必须拒绝并审计。
- Worker 在获得幂等 commit-start 响应并持久化本地 assignment journal 前不得调用 Docker create；claim 成功本身不表示 task/execution attempt 已启动。初始 fail-closed 默认禁止自动重试任何 start-committed 且身份不确定的 task，尤其不得为 executor 创建第二 execution attempt。
- Worker 必须使用本地 monotonic deadline 强制 assignment expiry；控制面失联、续租失败或 token 到期后停止对应容器和 approved-target 访问，不能因无法联系控制面而继续执行。
- Worker 只领取状态有效且 job digest 匹配的任务；任务参数、Suite Revision、Dependency Snapshot 和策略版本变化时必须重新生成 job digest。
- Worker 失联时控制面不得回退到本地执行；只有控制面确认从未 commit-start，且 Worker journal 与 Docker 标签核对证明不存在容器时，任务才可在 fencing 后重新排队。已 commit-start、已启动或无法证明未启动的任务必须以 worker_lost/attempt_unknown 收敛并执行清理。
- Worker agent 可以访问本机 Docker daemon，但测试容器、Source Acquisition、依赖准备和报告容器都不得访问 agent 或 socket。
- 每个 task/attempt 使用全新容器和临时目录；Worker 使用持久 journal 和统一资源 label 在启动时 reconcile。容器、挂载、网络、临时目录或上传 staging 删除失败必须把 Worker 置为 quarantined，不能复用残留状态。

## 3. Run 生命周期

### 3.1 状态

| 状态 | 含义 |
|---|---|
| queued | 已持久化，等待 worker、并发槽或 Target Environment Lease；wait_reason 必须可见 |
| running | 已获得 start commit，存在 execution attempt，处于 preflight/execute/collect/cleanup 之一；仅 claim 不得进入 running |
| completed | 执行、mandatory cleanup 与 Evidence Manifest 均成功完成；用例可以包含 failed/error/skipped |
| failed | 平台、环境 preflight/cleanup、执行器或证据完整性失败，无法形成可信 completed 结果 |
| timeout | 达到批准超时并终止 |
| cancelled | 用户或停机流程取消 |

### 3.2 转换

| 当前状态 | 事件 | 下一状态 | 要求 |
|---|---|---|---|
| 不存在 | create | queued | 幂等、权限、配额、Suite/Profile revision、依赖快照和网络策略通过；仅 approved-target 校验目标环境与 Grant |
| queued | no ready Worker / no Worker slot | queued | wait_reason=worker-unavailable/worker-capacity，不创建本地执行或重复 assignment |
| queued | no target capacity（approved-target） | queued | wait_reason=target-capacity，不创建重复 lease 或 Run |
| queued | assignment claimed | queued | 记录 assignment/fence 和内部 run_phase=assigned；claim 不创建 execution attempt，也不记录 started_at |
| queued | commit-start + applicable lease acquired | running | 原子提交 start_commit_id/task_attempt_id/execution_attempt_id；approved-target 同时确认有效 lease，之后 Worker 才可创建容器并记录 started_at |
| queued | Grant revoked/invalid（approved-target） | failed | 不启动 executor；记录 error_code=policy_revoked/policy_blocked |
| queued | cancel | cancelled | 原子取消，不再允许 worker 领取 |
| running | preflight failed | failed | Snapshot 或适用的 readiness、Grant、Lease/fencing 校验失败；不启动测试，记录明确 error_code |
| running | cleanup + manifest finalized | completed | mandatory cleanup、summary/cases/provenance/manifest 保存完成 |
| running | mandatory cleanup failed | failed | 保留测试结果；cleanup_status=failed，释放/隔离 lease |
| running | evidence digest/finalize failed | failed | 标记 evidence_incomplete/corrupt，不进入比较 |
| running | execution error | failed | 保存受限错误与 finished_at |
| running | timeout | timeout | 终止执行容器并释放资源 |
| running | cancel | cancelled | 终止执行容器并释放资源 |
| running | Grant revoked（approved-target） | cancelled | 在 revocation_stop_timeout 内终止 executor、回收 lease；记录 termination_reason=policy_revoked |
| running | control plane restart / transient Worker disconnect | running | 仅允许同一 worker_id + host_id + worker_generation + assignment/fencing token 的同一 attempt 在恢复窗口内续报；控制面启动不得立即判失败，也不得创建第二 attempt |
| running | Worker recovery timeout / assignment identity uncertain | failed | 标记 worker_lost/attempt_unknown，保留已有证据，回收/隔离 lease；不得自动启动任何第二 attempt，重跑只能由有权限用户显式创建新 Run |
| queued | claim expired before start commit | queued | 仅在控制面确认从未 commit-start，且 Worker journal/Docker 标签核对证明不存在容器后，fencing 旧 claim 并允许重新分配；证据不完整时转 attempt_unknown，不得重排 |
| queued | control plane restart | queued | 恢复队列和未过期 claim；无 ready Worker 时继续显示 wait_reason |

表中任何进入 completed/failed/timeout/cancelled 的转换都必须按 §2.9 完成 Manifest 收敛；事件列描述触发原因，不允许绕过证据收敛直接提交终态。终态不可再次转换。重跑必须创建新 Run 并记录 rerun_of_run_id。

### 3.3 测试判定

Run 状态与测试 verdict 分离：

| verdict | 条件 |
|---|---|
| passed | completed 且 failed=0、error=0 |
| test_failed | completed 且 failed>0 或 error>0 |
| no_tests | completed 且 total=0；是否允许由 Profile 策略决定 |
| unknown | 非 completed 或缺少 summary |

### 3.4 幂等创建

- `POST /runs`、Profile trigger、Schedule manual trigger 和 rerun 必须接受 Idempotency-Key 或生成等价 client_request_id。
- 幂等作用域至少包含 authenticated actor + endpoint + key。
- 同作用域、同规范化请求返回原 Run；同 key 不同请求返回 conflict，不能创建第二个 Run。
- Idempotency record、规范化请求摘要和 Run 创建必须位于同一数据库事务或等价持久原子单元；崩溃后不得出现已创建 Run 但 key 可再次创建，或 key 已占用但无法返回原 Run 的状态。
- `idempotency_retention_s` 必须来自批准且版本化的生产参数基线，并且不小于 client retry window、API timeout 和 restart recovery window 的批准上界；缺失或低于基线时拒绝 readiness。
- cron 使用 schedule_id + scheduled_fire_time 去重；幂等键不能把显式 rerun 合并到原 Run。

## 4. 执行与网络

### 4.1 控制面与 Worker 边界

- 控制面不安装、挂载或远程访问 Docker socket/daemon，不包含 Docker SDK 执行权限，也不得使用 subprocess 作为生产回退。
- Worker 主动通过双向认证连接控制面，领取任务、续租 assignment、上传状态和证据；Worker 不直接访问控制面数据库或文件系统。
- Worker 身份使用可轮换的短期证书/令牌，绑定 worker_id、host_id、worker_generation、audience、允许 task type 和有效期；重放、过期、旧 generation 或能力越界请求必须拒绝并审计。
- 控制面下发的任务必须包含 canonical payload digest、Suite Revision、Dependency Snapshot、镜像 digest、网络/资源策略和适用授权版本；Worker 必须在创建容器前复核。
- claim 与 commit-start 必须幂等；Worker 状态事件和证据上传必须包含 assignment fencing token、严格连续 sequence/idempotency key 和内容 digest；乱序、重放、同序异体或旧 token 不得覆盖新状态。
- Worker agent 是本机唯一允许访问 Docker daemon 的 qarunner 组件；Docker API 不暴露给控制面网络、测试容器或其他主机。
- Worker offline/quarantined 时新任务保持 queued，并显示 `wait_reason=worker-unavailable/worker-quarantined`；控制面不得本地执行或绕过 Worker 基线。

### 4.2 通用隔离

所有 Runner 必须：

- 使用非 root 用户。
- 禁止 privileged。
- drop all capabilities。
- 启用 no-new-privileges。
- 禁止 host PID/IPC/UTS/user/network namespace、host devices 和新增 device mapping。
- 使用批准的 seccomp 与 AppArmor/SELinux 配置；不得以 unconfined 作为生产默认值。
- 使用只读根文件系统和受控临时目录。
- 限制 CPU、内存和 PID。
- 只挂载本 Run 所需 workspace、results 和批准只读目录。
- workspace 必须来自 Run 冻结的不可变 Suite Revision，不得直接挂载可变 Suite 工作目录。
- 不挂载 Docker socket、控制面配置、数据库或其他 Run 目录。
- 对日志、报告和产物设置大小边界。
- 终态后删除 executor；删除失败产生健康/清理事件，不得复用含前一 Run 状态的容器。

所有 Source Acquisition、依赖准备、executor 和报告生成阶段必须绑定版本化 `ResourceLimitProfile`，至少包含：CPU、memory bytes、PID、timeout、download bytes、file count、single/total file bytes、output bytes、disk bytes 和 concurrency。生产环境任一必填边界缺失时拒绝 readiness；每个边界必须有“达到边界”和“超过边界”的自动化验收。

### 4.3 Source Acquisition

源码获取是与控制面分离的受限阶段：

- clone/fetch/checkout/ref 解析和 workspace 物化在不持有 Docker socket、数据库或平台密钥的低权限容器中执行。
- 只允许 approved-source 策略指定的协议、host、端口和 DNS/IP；Git 凭证最小只读且临时注入。
- 使用清洁 Git config/environment，默认禁用 hook、submodule、LFS/smudge/filter 和递归网络行为；启用项必须由管理员策略批准。
- 限制时间、下载量、文件数、单文件/总大小、磁盘、CPU、内存和并发。
- 输出拒绝越界 symlink、device/FIFO/socket、异常权限和路径碰撞，计算 content digest 后形成只读 Suite Revision。
- 获取失败不得留下可被 Run 使用的半成品，也不得回退到高权限控制面执行。

### 4.4 依赖准备

依赖准备是与测试执行分离的受限阶段：

- 必须在不持有 Docker socket、数据库、控制面配置或平台密钥的独立容器中执行。
- 只允许通过版本化 approved-registry 策略访问批准的软件源；不得同时访问真实 SUT。
- 必须限制 CPU、内存、PID、时间、下载量、输出量和磁盘。
- 默认禁止包管理器生命周期脚本；确需启用时必须记录策略并仍在准备沙箱内执行。
- 输入是不可变 Suite/source revision 和锁文件，输出是只读 Dependency Snapshot。
- 准备失败不得污染既有 Snapshot，也不得回退到控制面宿主执行。
- pytest/Playwright executor 只能挂载已批准 Snapshot，不得在运行时临时联网安装依赖。

### 4.5 isolated

- 默认模式。
- 不允许访问真实被测环境或其他外部网络。
- 适用于 pytest、纯计算测试、自包含服务和执行器/产物验证。

### 4.6 approved-target

- V7 只允许 `runner=playwright` 使用；pytest 保持 isolated。未来扩展到其他 Runner 必须重新完成产品、安全和运维评审。
- 只允许管理员创建、修改、启用和停用。
- 普通用户只能选择与 Profile 绑定的已批准策略。
- 允许访问真实被测环境所需的最小网络范围。
- 默认拒绝 qarunner 控制面、Docker daemon、宿主服务、云元数据和未批准私网目标。
- 策略必须在 executor network namespace、出站代理或等价强制边界覆盖 Node runner、浏览器进程、worker/service worker、Playwright request context、DNS、HTTP(S)、WebSocket、重定向和下载流量；不得依赖测试代码、页面 URL 拦截或浏览器内 hook 自我约束。
- DNS、重定向和代理策略必须防止目标在校验后切换到未批准地址。
- 网络策略变化必须产生新 version。
- 每个 Run 必须引用 active 且完全匹配的 Target Access Grant。
- executor 启动前必须通过 readiness 并持有匹配的 active Environment Lease/fencing token。
- Grant 在排队期间被吊销、到期或失配时，executor 必须拒绝启动并形成明确终态/事件。
- Run 必须显示实际使用的目标环境和策略版本。

### 4.7 验收

每种 approved-target 策略必须有自动化正向与反向测试：

- 能访问批准 SUT。
- 不能访问未批准域名/IP/端口。
- 不能访问平台 API、Docker socket 和元数据服务。
- 普通用户不能伪造 policy ID 或绕过 Profile 绑定。
- 普通用户不能将另一 Suite revision、Profile revision、Dependency Snapshot 或 secret ref 替换进已有 Grant。
- Grant 吊销、到期、排队期间变化和并发启动竞态均 fail closed。
- 超过 target concurrency、stale fencing token、readiness 失败和 mandatory cleanup 失败均按状态契约处理。

## 5. Provenance 与比较契约

### 5.1 比较级别

#### Strict Comparison

用于自动标记新增失败、已修复和持续失败。

以下字段必须存在并满足等价策略：

- suite_id。
- source_revision 相同；若不同，必须有稳定的 case_definition_fingerprint 证明被比较用例定义未变化。
- runner。
- execution_scope_hash。
- dependency_snapshot_digest。
- target_environment_id。
- network_policy_id 和 version。
- Target Access Grant 的有效绑定维度；Grant ID 可不同，但 source/profile/dependency/target/network/secret 组合必须等价。
- executor image compatibility group。
- Worker agent/host baseline compatibility group。
- comparison schema version。
- case identity。

target revision 可以不同，这是回归验证的正常对象，但必须完整展示。Suite、executor 或平台版本不满足上述兼容条件时，必须降级为 contextual 或无基线。

#### Contextual Comparison

用于展示两个 Run 的结果差异，但不做强因果回归结论。

适用例子：

- Suite revision 变化。
- executor image 变化。
- 平台版本变化。
- 目标构建版本变化但环境身份相同。

界面必须显示变化字段和“上下文已变化”提示。

#### Not Comparable

任一情况成立时不得自动选择基线：

- provenance 不完整。
- Evidence Manifest 缺失、未完成或校验失败。
- suite_id、runner 或 execution_scope 不兼容。
- target environment 不同。
- network policy 不兼容。
- 用户无权访问候选 Run。
- 候选 Run 不是 completed。

### 5.2 execution_scope_hash

execution_scope_hash 必须由规范化后的非秘密执行配置生成，至少覆盖：

- Profile revision 或手工请求 revision。
- selected_files、selected_markers、extra_args。
- runner。
- 影响用例集合和行为的非秘密环境。
- secret reference ID/version。
- dependency snapshot digest。
- target environment ID/policy version。
- network policy ID/version。
- Target Access Grant 的有效绑定字段，不使用可变状态或展示名称。

哈希算法、字段排序和规范化方式必须版本化。

### 5.3 用例身份

case identity 至少由 framework、suite、test name 构成。若框架和采集器能够提供稳定的 case_definition_fingerprint，系统必须保存它。跨 Suite revision 的 Strict Comparison 只有在版本化 fingerprint 算法存在且 fingerprint 相等时才允许。重试产生重复 identity 时，最终 attempt 决定本 Run verdict，同时保留 attempt 数量或原始证据。

若框架能提供稳定 test ID，系统必须保存并使用稳定 ID 作为主身份，名称仅作为展示；否则使用版本化 fallback identity 算法。

### 5.4 分类语义

| Base | Head | 分类 |
|---|---|---|
| passed | failed/error | new_failure |
| failed/error | passed | fixed |
| failed/error | failed/error | still_failing |
| 任意 | skipped 或 skipped → 任意 | coverage_change |
| 不存在 | 任意 | new_case |
| 任意 | 不存在 | removed_case |
| passed | passed | 无变化 |

规则：

- failed/error → skipped 不得标记 fixed。
- skipped → failed/error 必须同时表示覆盖变化和当前失败，不得伪装为普通历史回归。
- 新增失败用例应显示为 new_case 且 verdict=failed，不与已有用例的 new_failure 混淆。
- 分类算法必须使用表驱动黄金数据集验证。

### 5.5 Flaky

Flaky 历史必须按可比较执行口径隔离，不得只按测试名称跨 Profile、目标环境或网络策略混合。

必须记录：

- 使用的观察窗口。
- 最少观察次数。
- 翻转阈值。
- execution scope。
- 结论生成时间。

Flaky 是提示，不自动豁免失败。

## 6. 调度契约

### 6.1 Schedule

Schedule 必须绑定：

- schedule_id。
- profile_id 和 profile revision policy。
- dependency snapshot selection policy。
- target_access_grant_id/version（approved-target 时）。
- cron expression。
- IANA timezone。
- owner。
- enabled。
- misfire_policy。

### 6.2 misfire_policy

批准的策略必须是以下之一：

- record-only：记录 missed，不自动补跑。
- catch-up-once：恢复后最多补跑一次，并关联原触发点。
- manual：记录 missed，由用户显式补跑。

默认策略在产品评审前不得隐式决定。

### 6.3 触发保证

- 同一 schedule_id + scheduled_fire_time 最多创建一个 Run。
- 每个触发点最终必须对应 triggered、missed、skipped-disabled、blocked-policy 或 failed-to-create 中一个结果。
- 服务启动后必须在批准的 `scheduler_recovery_timeout_s` 内恢复 future Schedule，并为所有未决 fire point 写入唯一结果。
- 手工 trigger 不参与 cron 去重，但记录触发用户。
- 删除 Profile 的 API/UI 必须返回受影响 Schedule 的 ID、数量和确定处理状态，不得只显示通用成功信息。
- Grant 到期、吊销、Suite/Profile/Dependency Snapshot 变化导致不匹配时记录 blocked-policy，并显示具体失配维度；不得静默改用其他 Grant。

## 7. 功能需求迁移

旧需求 ID 保留，以下修订优先于旧文档表述。

| ID/范围 | V7 状态 | 修订 |
|---|---|---|
| SU-1 | 保留 P0 | 列表必须展示 Suite 来源和当前 revision |
| SU-2 | 修订 P0 | Git 接入改为 Source Acquisition 沙箱，增加批准目标、凭证、禁用扩展行为、commit/content digest 和容量要求 |
| SU-3 | 降级 P2 | restricted-local，仅管理员或开发部署 |
| SU-4 | 保留 P1 | pull 后生成新 source revision，不修改历史 Run |
| SU-5 | 提升 P0 | 依赖准备与控制面隔离；批准 Registry、资源边界、脚本策略和 Dependency Snapshot 必须可追溯 |
| SU-6～SU-8 | 保留 | 选择范围进入 execution_scope_hash；路径边界保持 P0 |
| CR-1～CR-6 | 保留并扩展 | Git 凭证由安全规范管理；删除/轮换使用版本化 secret ref |
| PF-1～PF-2 | 修订 P0 | Profile 影响执行字段变更必须产生 revision |
| PF-3 | P2 扩展 | Webhook 改为 notification secret ref |
| RN-1～RN-3 | 修订 P0 | 持久队列、唯一终态、Docker 和网络策略 |
| RN-5 | 修订 P0 | Playwright 必须支持 approved-target 真实环境，并要求不可变 Target Access Grant |
| RN-6 | 保留 P0 | 取消原子化并释放容器/并发槽 |
| RN-7 | 提升 P0 | 重跑是失败处置闭环的一部分 |
| RN-8～RN-11 | 保留 P0 | 配额、超时、查询和 provenance 一并验收 |
| RN-12～RN-14 | 保留 P1 | 锁定、删除、清理必须审计 |
| RN-15～RN-16 | 提升 P0 | 重启和停机不得造成悬挂或证据伪装 |
| RP-1～RP-6 | 保留 | 增加 provenance、Evidence Manifest/digest、证据完整性、脱敏和缺失/损坏标识 |
| DF-1～DF-3 | 重写 P0 | 使用 strict/contextual/not-comparable 契约 |
| DF-4～DF-6 | 保留 P1 | 趋势、历史、Flaky 必须按 execution scope |
| DF-7 | 保留 P2 | 看板不是核心发布门禁 |
| SC-1、SC-3、SC-5 | 提升 P0 | 配置、到点触发和去重属于产品支柱 |
| SC-2、SC-4 | 保留 P1 | 预览和手工触发 |
| NT-1～NT-2 | 保留 P2 | 扩展故障隔离，Webhook 作为秘密 |
| AI-1～AI-5 | 保留 P2 | 默认关闭，独立评测，不进入核心 verdict |
| UI-1、UI-7 | 保留 P0 | 核心旅程与分层健康 |
| UI-2、UI-4～UI-6 | 保留 P1 | 用户管理、可访问性、API 契约 |
| UI-3 | 保留 P2 | 语言与主题 |

## 8. 新增系统需求

| ID | 要求 | 优先级 |
|---|---|---|
| SYS-FR-001 | Run 创建时冻结完整 provenance | P0 |
| SYS-FR-002 | Suite pull 后保存 commit SHA/content digest | P0 |
| SYS-FR-003 | Profile 影响执行的变更产生 revision | P0 |
| SYS-FR-004 | Run 保存 executor image digest 和 qarunner version | P0 |
| SYS-FR-005 | Run 保存 target environment 和 network policy version | P0 |
| SYS-FR-006 | 系统生成版本化 execution_scope_hash | P0 |
| SYS-FR-007 | 基线选择支持 strict/contextual/not-comparable | P0 |
| SYS-FR-008 | diff 增加 coverage_change 语义 | P0 |
| SYS-FR-009 | Flaky 按 execution scope 隔离 | P0 |
| SYS-FR-010 | Schedule 每个触发点持久化结果 | P0 |
| SYS-FR-011 | missed Schedule 支持批准的补偿策略 | P0 |
| SYS-FR-012 | 分层健康返回 DB、queue、scheduler、Worker identity/heartbeat/drain、Worker Docker/image/disk、clock、source/preparation、target lease 状态 | P0 |
| SYS-FR-013 | 证据缺失、截断和脱敏在 API/UI 明确展示 | P1 |
| SYS-FR-014 | 管理员可创建、查询、吊销和续期 Target Access Grant | P0 |
| SYS-FR-015 | approved-target Run 在创建与启动前校验不可变授权组合 | P0 |
| SYS-FR-016 | 依赖在受限准备沙箱中生成不可变 Dependency Snapshot | P0 |
| SYS-FR-017 | 主动报告不在携带控制面会话的同源上下文中执行 | P0 |
| SYS-FR-018 | Schedule 授权/策略失配形成 blocked-policy 结果 | P0 |
| SYS-FR-019 | clone/pull 生成不可变 Suite Revision，Run 只执行冻结快照 | P0 |
| SYS-FR-020 | Grant 吊销使匹配的 queued/running Run 在规定时间内停止并收敛 | P0 |
| SYS-FR-021 | Git 获取在 approved-source Source Acquisition 沙箱完成 | P0 |
| SYS-FR-022 | approved-target Run 原子获取带 fencing 的 Environment Lease | P0 |
| SYS-FR-023 | 所有 Run 创建入口实现持久化幂等契约 | P0 |
| SYS-FR-024 | 终态生成不可变 Evidence Manifest 并在读取/恢复时校验 | P0 |
| SYS-FR-025 | Target Environment 支持 readiness、并发、namespace 和 cleanup policy | P0 |
| SYS-FR-026 | 系统监控 clock skew/jump，期限使用 UTC、耗时使用 monotonic | P0 |
| SYS-FR-027 | 专用 Worker 上的生产 executor 强制批准的 seccomp/LSM、namespace、device 和容器清理策略 | P0 |
| SYS-FR-028 | Run API/UI 展示 run_phase、wait_reason、readiness、lease 和 cleanup 状态 | P1 |
| SYS-FR-029 | 新增或扩展 API 使用版本化错误响应契约，稳定区分认证、授权、资源隐藏、状态/幂等冲突、校验、配额和依赖不可用 | P0 |
| SYS-FR-030 | 管理员可注册、轮换身份、查询、drain、quarantine 和恢复唯一 Worker | P0 |
| SYS-FR-031 | Worker 通过受认证协议原子 claim，并在幂等 commit-start 后才创建 execution attempt；使用 worker_generation、assignment fencing token 和 heartbeat 防止重复执行与旧状态写入 | P0 |
| SYS-FR-032 | Source Acquisition、依赖准备、报告生成和每个 Run/attempt 均创建全新容器和临时目录，终态后销毁且不可复用 | P0 |
| SYS-FR-033 | 控制面不访问 Docker daemon、不执行不可信阶段，Worker 不可用时不得回退 subprocess 或本地 Docker | P0 |
| SYS-FR-034 | Worker 状态和证据使用严格连续且幂等的 sequence、作用域上传会话和 digest 交付，乱序/重放/同序异体/损坏不得覆盖已接受状态 | P0 |
| SYS-FR-035 | Worker 使用持久 journal、统一资源 label 和启动 reconcile；失联、重启、drain 和隔离具有确定恢复语义，同一 Run 不得自动启动第二个不确定 attempt | P0 |

## 9. 数据与接口

### 9.1 数据生命周期

- Run provenance 和核心元数据在 Run 删除前保留。
- 锁定 Run 的元数据和产物不得自动清理。
- 自动清理只处理达到保留期且未锁定的产物。
- 删除 Run 必须原子标记删除意图；文件删除失败时产生可恢复状态，不能静默只删数据库。
- 历史 Run 不依赖当前 Suite/Profile 行才能解释。
- Suite 工作目录和当前指针可删除或更新，但被 Run/Grant/Dependency Snapshot 引用的 Revision 不得提前清理。
- revoked/expired Grant 及其审批记录只要仍被 Run 或审计引用就不得物理覆盖或复用 ID/version。
- Evidence Manifest 与核心 Run 元数据同生命周期；删除证据时保留受控删除状态和审计，而不是伪装 digest 校验成功。
- Idempotency record 至少保留最大客户端重试窗口；active/stale Environment Lease 具备恢复与清理保留期。
- Worker identity、证书版本、capabilities、heartbeat、drain/quarantine、assignment history 和 host baseline digest 必须保留到相关 Run/审计引用结束。
- Worker 临时工作区、容器层、上传 staging 和短期凭证按 task/Run 生命周期清理；Worker 重建不得改变已冻结 Suite/Dependency/Evidence digest。

### 9.2 API

API 必须新增或扩展：

- Suite revision。
- Source Acquisition job/policy/status。
- Profile revision。
- Dependency Snapshot 管理和状态。
- Target Environment 管理。
- Environment Lease 状态、wait reason、fencing-safe 释放与管理员恢复操作。
- Network Policy 管理。
- Target Access Grant 管理、校验结果和吊销。
- Run provenance。
- Run 创建的 Idempotency-Key 契约和 conflict 响应。
- Evidence Manifest、校验状态和单文件 digest。
- comparison level 和 reason。
- Schedule fire/missed 记录。
- 分层 health/readiness。
- Worker internal API 的身份、claim、heartbeat、drain、任务状态和证据上传。
- 审计查询由安全规范定义。
- 内部 Worker API：注册/身份轮换、capabilities、heartbeat/readiness、claim/renew/release、状态事件、drain/quarantine、证据上传会话和完整性确认。

Worker API 不直接暴露给浏览器或普通用户。它必须使用独立 audience 的双向认证和 Worker scope，不接受用户会话 Cookie/JWT，也不能复用管理员 API token。

错误响应至少包含稳定 `error_code`、安全的 `message`、`correlation_id`、`retryable` 和可选结构化 `details`，不得包含秘密或内部堆栈。HTTP 语义至少统一为：401 未认证、403 已认证但无权、404 按资源隐藏策略不可见、409 状态/版本/Idempotency 冲突、422 请求校验失败、429 配额或限速、503 依赖或 readiness 不可用。

API Pagination Profile 是统一生产参数基线的版本化组成，必须为所有列表定义 default/max page size、稳定排序键和 cursor 语义；超过最大值返回 422，同一快照的相邻页不得重复或遗漏对象。所有 ID 访问执行 owner scope。

### 9.3 兼容

- 旧 Run 无 provenance 时标记 legacy，不能自动进入 strict comparison。
- 数据迁移不得伪造缺失的 source revision、network policy 或 image digest。
- 旧 executor_mode 字段可以兼容读取，但不能绕过批准网络和 Docker-only 契约。
- 旧 Run 没有 manifest 时标记 legacy-evidence，不能伪造 digest 或进入需要证据完整性的发布门禁。

### 9.4 不可信证据交付

- HTML/JavaScript 报告必须使用独立无控制面 Cookie 的源并配置版本化 CSP 基线；访问授权必须绑定 user、run、file、HTTP method 和 audience，TTL 不得超过 Security Parameter Baseline 的批准上限；或转换为无主动内容版本/仅下载。
- 控制面不得提供可在自身 origin 直接执行的不可信 HTML、SVG、脚本或浏览器可执行内容。
- 未知、可执行或可嗅探类型默认使用 attachment；不能只信任扩展名或测试代码声明的 MIME。
- 文件枚举、单文件下载和压缩包必须拒绝路径穿越、符号链接逃逸、特殊文件、数量/大小越界和归档膨胀。
- 日志、用例名、错误、附件名和 ANSI 控制序列在 UI 展示前必须按文本处理和转义。
- 报告生成器本身必须在受限环境运行，失败不影响原始结果。
- 文件响应的 path、size 和 digest 必须来自已冻结 Manifest；磁盘实际值不一致时拒绝并标记 corrupt。

## 10. 非功能要求

| ID | 要求 | 目标/验收 |
|---|---|---|
| SYS-NFR-001 | Run 创建持久化可靠性 | API 成功响应前 Run 已持久化 |
| SYS-NFR-002 | 终态收敛 | worker/lease 丢失被检测后，Run 在 `run_recovery_timeout_s` 内由同一 assignment/attempt 恢复续报或进入终态；不得自动启动第二 attempt，超时仍 active 的异常记录数为 0 |
| SYS-NFR-003 | provenance 完整性 | 新基线 Run 100% 完整 |
| SYS-NFR-004 | diff 正确性 | 黄金状态矩阵 100% 通过 |
| SYS-NFR-005 | 网络策略正确性 | 正向和反向策略测试 100% 通过 |
| SYS-NFR-006 | 证据边界 | 日志、报告、case message 和产物列表均绑定 ResourceLimitProfile ID/version，并通过边界值和超限一单位反向测试 |
| SYS-NFR-007 | 平台开销 | 排队、容器启动、归档和 API P95 目标在 Beta 前批准 |
| SYS-NFR-008 | 容量 | 单控制面/单 Worker 的并发、队列、网络上传、磁盘增长和最大 Run 数在 Beta 前通过容量测试 |
| SYS-NFR-009 | 兼容 | `supported-runtime-matrix@version` 中每个适用单元格全部执行并通过；未执行单元格不得宣称支持 |
| SYS-NFR-010 | 授权一致性 | Grant 创建、Run 创建、dequeue 与启动竞态测试 100% fail closed |
| SYS-NFR-011 | 主动内容隔离 | 安全测试证明报告脚本不能读取控制面 DOM、Cookie、存储或调用带会话 API |
| SYS-NFR-012 | 授权吊销收敛 | 吊销成功后不再启动匹配 Run，在途 executor 于 revocation_stop_timeout 内终止 |
| SYS-NFR-013 | Environment Lease 一致性 | 并发/崩溃/网络分区故障注入下不超过上限，stale fencing token 不能继续使用目标 |
| SYS-NFR-014 | Run 创建幂等 | 重试矩阵中同 key 同请求仅一个 Run，同 key 异体请求 100% conflict |
| SYS-NFR-015 | Evidence 完整性 | 新 Run 100% 生成 Manifest；篡改、缺失与恢复损坏 100% 被检测 |
| SYS-NFR-016 | 时钟正确性 | clock skew 超阈值拒绝 readiness；前后跳变不产生重复 Schedule 或延长已过期 Grant/lease |
| SYS-NFR-017 | Source Acquisition 隔离 | Git 正反向网络、hook/filter/submodule、特殊文件和资源边界测试 100% 通过 |
| SYS-NFR-018 | 主机隔离基线 | GA Worker 主机通过专用化、agent/socket、seccomp/LSM/namespace/device/patch level 配置审计；控制面主机证明无 Docker socket/执行路径 |
| SYS-NFR-019 | 发布证据确定性 | 候选版本冻结验收目录、运行时矩阵、参数/策略版本和环境；全部适用用例有非 skipped 结果，且证据来自最后一次相关变更之后 |
| SYS-NFR-020 | Worker 协议安全 | 伪造 Worker、旧 generation/身份、未 commit-start 创建、任务/状态重放、旧 fencing token、sequence gap/conflict 和越权 task type 测试 100% 拒绝 |
| SYS-NFR-021 | 一次性容器隔离 | 连续 Run/阶段间不可观察前一容器、可写层、临时目录、密钥或网络状态；残留容器使 Worker quarantine |
| SYS-NFR-022 | Worker 故障收敛 | 只有从未 commit-start 且 journal/Docker 证明未创建容器的 assignment 可重新分配；同一已启动 assignment 可在恢复窗口续报，超过窗口在 `worker_recovery_timeout_s` 内形成 worker_lost/attempt_unknown，自动重复 execution attempt 数为 0 |

## 11. 验证与追踪

### 11.1 必须的测试模型

- Run 状态转换表驱动测试。
- comparison level 与状态分类黄金数据集。
- Suite/Profile/环境/网络/镜像差异组合测试。
- approved-target 正向/反向网络测试。
- Target Access Grant 绑定、变化失效、吊销、到期和排队竞态矩阵。
- Suite pull 与 queued/dequeue 并发测试，证明 Run 始终执行冻结 Revision。
- Source Acquisition 的 approved-source、凭证、hook/filter/submodule/LFS、symlink/special-file 和资源矩阵。
- Environment Lease 原子获取、fencing、readiness、cleanup、取消/超时/崩溃回收矩阵。
- 所有 Run 创建入口的 Idempotency-Key 重试、冲突、重启与保留期测试。
- Evidence Manifest 生成、下载时校验、归档后篡改、丢失、受控删除和恢复损坏测试。
- clock skew、前跳、后跳、NTP 恢复对 Grant/lease/Schedule/audit 的测试。
- 生产 executor seccomp/LSM、namespace、device、socket 和残留容器配置审计。
- 依赖准备沙箱的 Registry 正向/反向、资源、脚本和快照完整性测试。
- 不可信报告/日志/产物的 XSS、MIME、路径、符号链接、特殊文件和压缩边界测试。
- owner scope 对所有派生资源的矩阵测试。
- restart、shutdown、missed schedule 和补跑测试。
- Worker 注册/证书轮换、mTLS/等价认证、伪造/重放/旧 token、claim 竞态、heartbeat、drain、断线续报和上传幂等测试。
- 每 Run/每阶段全新容器、可写层/临时目录/密钥/网络状态不复用，以及残留容器 quarantine 测试。
- 控制面无 Docker socket、无本地执行回退；Worker 恢复窗口和 worker_lost/attempt_unknown 终态测试。
- legacy Run 迁移和无基线测试。
- 证据删除失败与恢复测试。

### 11.2 证据

每条 P0/P1 要求必须记录：

- requirement_id 和 requirement_version。
- 当前状态及适用的 release_gate_catalog_version。
- design_ref。
- code_ref、migration_ref 和 runbook_ref（适用时）。
- test_case_id。
- 执行命令。
- 候选 commit、build ID、镜像 digest、schema version 和配置/策略版本。
- 测试环境和适用的 Target Environment/Network Policy/Grant/Lease 标识。
- 测试结果。
- artifact_ref 和执行时间。
- exception/risk_acceptance_ref（适用时）。
- 实现人、复核人、验收人和日期。

模块级“已有测试”不能替代逐条证据。

记录格式、状态推进和 PRD 级映射以 [REQUIREMENTS_TRACEABILITY.md](REQUIREMENTS_TRACEABILITY.md) 为准。

## 12. 当前实现差距

当前代码仍以现有 FEATURES、API 和架构文档为准。已知差距包括：

- Run 模型缺少 V7 provenance 字段。
- Git pull 直接 hard reset 可变工作目录，没有可供 queued Run 冻结的 Suite Revision 快照。
- Git clone/fetch/reset 在持有 Docker socket 的控制面执行，没有 Source Acquisition 沙箱。
- 基线只比较部分执行字段。
- Playwright 固定 isolated。
- 没有 Target Access Grant 领域对象和双阶段校验。
- 没有 Target Environment policy、Environment Lease、readiness、fencing 或 cleanup 状态。
- Run 创建入口没有持久化 Idempotency-Key 契约。
- npm 依赖准备仍在控制面进程执行，未形成独立 Dependency Snapshot。
- Allure 报告仍由控制面同源提供并可直接打开。
- 证据没有 Evidence Manifest/digest，归档后篡改或恢复损坏不可检测。
- diff 没有 coverage_change。
- Flaky 聚合未完全按 execution scope。
- Schedule 没有持久化 missed 记录。
- health 只验证数据库。
- health 不检查 clock skew；executor 未强制项目定义的 seccomp/LSM/host namespace/device 基线。
- 没有冻结的 release gate catalog、统一 ResourceLimitProfile 和候选版本 Release Evidence Bundle。
- API 错误响应没有版本化统一 envelope，同类认证、授权、冲突、配额和依赖失败在不同路由间语义不一致。
- approved-target 尚未实现全进程/全协议出站强制，Playwright 浏览器 sandbox 缺少项目级基线和验收证据。
- 当前没有 Worker agent、Worker identity/heartbeat/drain、任务 claim/fencing 或证据上传协议；控制面仍直接创建本地 Docker executor。
- 当前没有控制面与 Worker 的分离部署、Worker 主机基线、Worker 失联恢复或每阶段容器不可复用的端到端门禁。

这些差距必须通过实现和测试关闭后，相关要求才能标记 verified。
