# M0 状态模型实现契约

> 文档编号：QEP-STATE-MODEL-CONTRACT-001<br>
> 版本：V0.1.0<br>
> 文档类型：Implementation Contract Reference<br>
> 状态：`DECIDED`（仅 `STATE-DEC-001`～`008` 已签范围）；本文另隔离记录
> `STATE-DEC-009/010` 的 `PROPOSED/UNSIGNED` 附录；runtime 尚未激活<br>
> 决议输入：`STATE-DEC-001`～`STATE-DEC-008` 五方确认<br>
> 基线 commit：`967c1e8`<br>
> 首次生效版本：`M0-STATE-V1`（契约/兼容 epoch，不是产品发布号）<br>
> 评审角色：产品（PROD）、研发（DEV）、质量（QA）、安全（SEC）、运维（OPS）<br>
> 决议 UTC：`2026-07-14T03:10:02Z`<br>
> 签署代表：Ike-li（用户授权代表，代表 PROD/DEV/QA/SEC/OPS）<br>
> 最后更新：2026-07-14<br>
> 权威边界：本文件关闭八项 implementation-level 设计决议，不新增上游产品需求

---

## 1. 目的、规范用语与边界

### 1.1 目的

本文件把[M0 状态模型五方决议包](09_STATE_MODEL_DECISION_PACKET.md)中获批的
`B / A+X / B / C / A / B / A / B` 固化为一份可在八项已签范围内驱动 TDD、Schema 设计、
迁移和故障测试的实现契约。`001F` 可直接据此实施；`001G/001H` 中依赖
`STATE-DEC-009/010` 的部分在具名签署前只能驱动 RED/schema-validation 计划，不构成
runtime 实现授权。在该边界内，实现者应能仅凭本文件确定：

- Batch 的 canonical 状态、边和命令归属；
- Run phase、执行事实、disposition 与 outcome 的正交关系；
- retry/unknown 的分层与 fail-closed 底线、cancel 与 Evidence 竞态的已签收敛规则；
- Batch 成功策略不可放宽的安全底线和完整聚合真值表；
- Run-local UoW、Batch reconciler、outbox 与 immutable basis 的原子边界；
- 兼容、迁移、回滚和首次激活门禁；
- 必须先形成 RED 的 TDD case matrix。

### 1.2 规范用语

本文中的“必须”“不得”“只能”在八项已签范围内是规范性要求；任何显式标为
`PROPOSED/UNSIGNED`、“建议”或“候选”的内容只是后续决议输入，不构成实现授权。出现冲突时：

1. 已批准的上游需求仍定义业务目标与安全底线；
2. 本文件是八项 `STATE-DEC` 对应 implementation representation 的权威契约；
3. 当前代码、测试和数据库只说明迁移起点，不能反向覆盖本契约；
4. 任何语义变更必须新建决议并提升相应 Schema/compatibility epoch，不能静默复用 `.v1`。

### 1.3 明确不代表什么

本文件是设计与字段级 Schema contract，但：

- 不是可执行的 JSON Schema、OpenAPI、SQL DDL 或 migration 文件；
- 不声明 API、数据库 adapter、Worker 协议、reconciler、outbox publisher 或生产事务已实现；
- 不声明本文所列 TDD case 已编写、运行或通过；
- 不把当前 provisional 枚举、Fake/UoW 或领域测试扩大为生产保证；
- 不关闭 `T-M0-STATE-001`；该 umbrella 任务继续保持 `IN_PROGRESS`；
- 不代表完整 M0、生产 Worker/API、数据库并发或发布门禁已经完成。

## 2. 已批准决议记录

用户已明确确认有权代表 PROD、DEV、QA、SEC、OPS 五方签署下列组合。签署时间、代表和逐项
记录同时保存在[决议包 §9](09_STATE_MODEL_DECISION_PACKET.md#9-五方评审记录)。

| Decision ID | 获批选项 | 本契约中的规范结果 | 主要关闭证据 |
|---|---|---|---|
| `STATE-DEC-001` | B | DD 长路径是唯一持久及 API canonical Batch path | §3 |
| `STATE-DEC-002` | A + X | persisted pre-execution hard failure 统一 `rejected`；所有持久 preterminal phase 接受 versioned cancel intent | §3.2～3.4 |
| `STATE-DEC-003` | B | Run orchestration phase 与独立持久 outcome 分离 | §4、§8.1 |
| `STATE-DEC-004` | C | immutable execution fact、RunDisposition、最终 RunOutcome 分离 | §4.2～4.4 |
| `STATE-DEC-005` | A | completed Evidence 先 finalize 时 completed fact 获胜，cancel intent 只保留为审计事实 | §5 |
| `STATE-DEC-006` | B | versioned Suite success policy + 平台不可覆盖安全底线 | §6 |
| `STATE-DEC-007` | A | Batch cancel intent + 全量安全 fanout + 收敛后分类 | §7 |
| `STATE-DEC-008` | B | immutable Run/Batch basis + Run-local UoW + 幂等 Batch reconciler | §8 |

共同安全不变量继续适用：start commit 后不得无可信事实宣称完成；unknown 不得假成功或默认
retry；cancel intent 不等于 cancelled outcome；Attempt terminal/Evidence root 不得原位改写；旧
generation/fence/Assignment authority 不得借 replay 恢复；retry 必须创建新 Attempt，且不扩大
original denominator。

## 3. Batch canonical 状态与命令契约

### 3.1 canonical vocabulary

`M0-STATE-V1` 的唯一可写 Batch 状态词汇为：

```text
draft
  -> validating
    -> collecting
      -> planning
        -> awaiting_admission
          -> queued
            -> running
              -> finalizing
                -> succeeded | failed | partial | cancelled

validating | collecting | planning | awaiting_admission -> rejected
draft | validating | collecting | planning | awaiting_admission | queued
  -- fact-aware prestart cancel --> cancelled
```

`collecting` 与 `awaiting_admission` 必须作为持久状态和版本化 API canonical 值暴露。产品可提供
只读 coarse phase，但不得产生第二套可写状态机。`collecting_failed` 不是 v1 状态；pre-execution
hard failure 不得写成 `failed`。`failed` 只表示执行已经完整收敛后，由 §6 聚合得到的 Batch
outcome。

`succeeded`、`failed`、`partial`、`cancelled`、`rejected` 都是吸收终态。终态后的异内容命令必须
返回稳定冲突；只有同一权威事实的 exact replay 可以返回原结果。

### 3.2 edge 与 command ownership

| 源状态 | 命令 / 责任组件 | 必须校验的权威事实 | 成功效果 |
|---|---|---|---|
| 无 Batch | `create_batch` / Submission | 请求可规范化，ID/幂等键/摘要合法 | 创建 `draft@v0`；语法上无法形成稳定 aggregate 的请求只返回请求错误，不制造伪 Batch |
| `draft` | `begin_validation` / Validation coordinator | Batch CAS、request digest | `validating` |
| `validating` | `accept_validation` / Validator | validation fact 与输入 digest | `collecting` |
| `collecting` | `accept_collection` / Collector coordinator | trusted collection result、Manifest candidate digest、任务已停止 | `planning` |
| `planning` | `approve_plan` / Planner | Manifest、Shard Plan、coverage 与 denominator proof | `awaiting_admission` |
| `awaiting_admission` | `admit_batch` / Admission controller | policy/grant/quota/capacity snapshot 与版本 | `queued` |
| `queued` | `start_batch` / Scheduler | canonical Run set 已冻结；无有效 Batch cancel intent | `running` |
| `running` | `begin_batch_finalization` / Batch reconciler | Manifest/Plan/canonical Run set 已冻结；全部 Run 已 `closed` 且有 immutable basis；不存在 `review_required`、`retry_queued`、pending RetryIntent 或可能创建新 Attempt 的分支；Batch CAS 与该快照一致 | `finalizing`；从此只等待 Batch-level policy/completeness/basis 收敛，不得创建新 Attempt |
| `finalizing` | `finalize_batch` / Batch reconciler | §6 完整性、policy、truth table 与 §8.2 basis CAS | 四个 execution terminal 之一 |
| `validating/collecting/planning/awaiting_admission` | `reject_preexecution` / 当前 phase owner | §3.3 rejection fact、无 materialized Run/Attempt/start commit、§8.2.1 pre-execution closure basis | `rejected` |
| 任一持久 preterminal phase | `request_batch_cancel` / Cancellation coordinator | §7 versioned intent、Batch CAS | 只记录 intent；不因请求本身改 terminal |
| `draft/validating/collecting/planning/awaiting_admission/queued` | `finalize_unmaterialized_cancel` / Reconciler | 无 materialized Run/Assignment/Attempt/start commit、无未证明停止的任务、§8.2.1 pre-execution closure basis | `cancelled` |
| 任一存在 materialized Run 的 preterminal phase | `finalize_materialized_cancel` / Batch reconciler | §7 fanout 全部收敛；每个已物化 Run 有 prestart/terminal item resolution，未物化 item 有 `not_executed` fact；§6 truth table 与 §8.2.2 execution basis | 只有 `C+N=denominator`、`P=T=I=U=0` 时直接 `cancelled`；否则不得直达 terminal，必须先修复为 `running`，再按 `running -> finalizing -> execution terminal` 收敛 |

以下边不得由 generic `transition(target)` 暴露：任何 `-> rejected`、任何 `-> cancelled`、
`finalizing -> terminal`。这些边必须由上表 fact-aware command 独占。`queued -> cancelled` 的现有
generic edge 是迁移起点，不是 v1 契约。

### 3.3 pre-execution rejection fact

`qep.batch-rejection.v1` 的字段级契约如下；实现机器 Schema 时不得减少规范字段：

| 字段 | 约束 |
|---|---|
| `schema_version` | 固定 `qep.batch-rejection.v1` |
| `rejection_id` | opaque、非空、不可复用 |
| `batch_id` | 必须绑定被拒绝 Batch |
| `source_batch_version` | 非 bool、非负整数；与 command CAS 相同 |
| `stage` | `validation`、`collection`、`planning`、`admission` 之一 |
| `reason_class` | `invalid_input`、`authorization_denied`、`policy_denied`、`source_failure`、`integrity_failure`、`planning_failure`、`capacity_rejected` 之一 |
| `reason_code` | 稳定机器码；不得只保存自由文本或异常类名 |
| `input_digest` | 触发该 phase 的规范输入摘要 |
| `authority_digest` | 授权/策略/准入拒绝时必填；其他 stage 可为空 |
| `recorded_at` | 服务端 UTC metadata；调用方不得提供 |

`stage + reason_class + reason_code` 必须足以让 API、审计和指标区分“没有执行测试”与“执行后
失败”。内部堆栈、路径、秘密或未经清洗的外部错误不得进入稳定 reason 字段。新增 stage 或
reason class 需要 Schema/兼容评审，不能在 `.v1` 中把未知字符串当作默认分支。

### 3.4 prestart cancel 收敛

所有已持久的 `draft/validating/collecting/planning/awaiting_admission/queued` 都必须接受幂等、
versioned cancel intent。接受 intent 后：

1. 后续 phase advancement、collection task start、Plan approve、Run materialize、admit、offer、
   claim、commit-start 和 retry consumption 必须在各自 authoritative UoW 中重新检查 intent；
2. 没有 materialized Run/Assignment/Attempt/start commit，且不存在未证明停止的任务时，才可由
   `finalize_unmaterialized_cancel` 使用 pre-execution basis 关闭；
3. collection/planning task 已启动时，必须先取得可信 task-stop fact；无法证明停止时保持当前
   phase 并告警，不得伪装为 cancelled；
4. 一旦存在 materialized Run、Assignment、RetryIntent 或 Attempt，即使从未 start commit，也必须
   执行 §7 全量 fanout；已物化 Run 逐 item 形成 prestart/terminal resolution，只有未物化
   item 才写 `not_executed`，Batch 最终使用 §8.2.2 execution basis；
5. cancel intent、stage task stop fact、零-Run closure 的 pre-execution scope-item fact、execution
   fanout 的 not-executed fact 和最终 terminal 必须各自不可变、可重放；两种 item fact 不得混用。

## 4. Run phase、execution fact、disposition 与 outcome

### 4.1 四层词汇

| 层 | v1 vocabulary | 含义 |
|---|---|---|
| `RunPhase` | `planned`、`queued`、`assigned`、`running`、`retry_queued`、`closed` | 只表示编排生命周期 |
| `AttemptExecutionFact` | `passed`、`test_failed`、`infra_failed`、`cancelled`、`attempt_unknown` | 已提交真实执行的不可变事实；来自 Attempt |
| `RunDisposition` | `review_required`、`retry_queued`、`closed_no_retry` | latest terminal Attempt 或 prestart cancel 接下来如何处理 |
| `RunOutcome` | `passed`、`test_failed`、`infra_failed`、`cancelled` | Run 关闭时的持久 policy outcome |

活跃 Run 可以没有 disposition；terminal Attempt 已存在但 policy command 尚未原子提交时，Run
仍不得关闭。`RunOutcome` 只能在 `phase=closed` 时存在；`phase=closed` 必须同时满足
`disposition=closed_no_retry` 并引用 `qep.run-finalization-basis.v1`。反向同样成立。

当前 `RunState.CANCELLED` 不再是 v1 orchestration phase。它必须迁移为
`phase=closed + outcome=cancelled + disposition=closed_no_retry`，并且只有能重建可信 basis 的
历史记录可以自动迁移。

### 4.2 phase edge

```text
planned -> queued -> assigned -> running
                  assigned -- precommit expiry/release --> queued

running -- approved retry intent --> retry_queued -> assigned -> running
assigned -- retry precommit expiry/release --> retry_queued

planned | queued | initial assigned -- proven prestart cancel, no Attempt history --> closed
retry_queued | retry assigned -- cancel pending retry + latest completed fact --> closed
retry_queued | retry assigned -- cancel pending retry + latest unknown --> running/review_required
running -- closed_no_retry + finalization basis --> closed
closed --X--> any other phase
```

Assignment commit-start 继续是创建 Attempt 和递增 fence 的唯一入口。`retry_queued` 只表示
RetryIntent 已持久、尚未 commit 新 Attempt；它不是 outcome。`closed` 不得为 retry 重新打开；若
未来选择“retry 创建新 Run”，必须另开决议，不能复用本契约。

### 4.3 execution fact、disposition 与 outcome matrix

| 当前权威事实 | 必需 gate | disposition / phase | 允许的最终 outcome |
|---|---|---|---|
| initial prestart cancel；无 Attempt/fence/history | cancel intent + Assignment/task closure proof | `closed_no_retry / closed` | `cancelled` |
| latest Attempt `passed` | verified completed Evidence | `closed_no_retry / closed` | `passed` |
| latest Attempt `test_failed` | pinned `qep.suite-retry-policy.v1` + Run retry decision | retry 获批：`retry_queued / retry_queued`；否则 `closed_no_retry / closed` | no-retry 时 `test_failed` |
| latest Attempt `infra_failed` | pinned platform retry policy + 旧执行停止证明 | retry 获批：`retry_queued / retry_queued`；否则 `closed_no_retry / closed` | no-retry 时 `infra_failed` |
| latest Attempt `cancelled` | cancel intent + trusted stop proof + cancelled Evidence | `closed_no_retry / closed` | `cancelled` |
| latest Attempt `attempt_unknown`，无权威 adjudication | immutable observation | `review_required / running` | 无；不得关闭或自动 retry |
| unknown + `confirm_stopped_then_retry` | chain-tail adjudication + stop proof | `retry_queued / retry_queued` | 无；commit-start 创建 N+1 |
| unknown + `accept_duplicate_risk_then_retry` | chain-tail adjudication + risk approver/acceptance digest | `retry_queued / retry_queued` | 无；commit-start 创建 N+1 |
| unknown + `mark_infra_failed_no_retry` | chain-tail adjudication | `closed_no_retry / closed` | `infra_failed`；原 unknown 不改写 |
| unknown + `mark_completed_from_verified_evidence` | chain-tail adjudication + 完整受信 late-Evidence root/classification | `closed_no_retry / closed` | 由受信 Evidence 得到；原 unknown 不改写 |
| pending retry cancelled；latest fact 为 `test_failed/infra_failed` | cancel intent + RetryIntent/当前 retry Assignment closure + original completed basis | `closed_no_retry / closed` | 保留 latest completed outcome；cancel 只作为未执行 retry 的审计事实 |
| pending retry cancelled；latest fact 为 `attempt_unknown` | cancel intent + RetryIntent/当前 retry Assignment closure + chain-tail authority | `review_required / running` | 无；清空 active retry pointer，保留 unknown 与 immutable RetryIntent history，等待显式 adjudication |

`mark_completed_from_verified_evidence` 在 verified late-Evidence identity/path 实现前只能作为保留
Schema decision，不能仅凭 adjudication 字符串关闭 Run。review SLA 到期只能告警和升级，不能
自动把 unknown 变成 success、cancelled、infra failure 或 retry authority。

### 4.4 cross-field invariants

- `disposition=review_required` 时，latest Attempt 必须为 `attempt_unknown`，outcome 必须为空。
- `disposition=retry_queued` 时，phase 必须为 `retry_queued`，outcome 必须为空，pending
  RetryIntent 必须引用 chain-tail adjudication 和 latest Attempt。
- `disposition=closed_no_retry` 时，phase 必须为 `closed`，outcome 与 finalization basis 必填，
  current Assignment/pending RetryIntent pointer 必须为空。
- Attempt history、fence、Evidence root、UnknownObservation、adjudication 和 RetryIntent 均只追加；
  RunOutcome 不得回写这些原始事实。
- 已撤销/superseded authority、current fence 和 chain tail 必须在 exact replay 判断前拒绝，不能
  借相同 payload 恢复；authority 仍有效时，stored `source_run_version + basis_digest` 用于确认
  exact replay，replay 可按具体 command contract 先于 aggregate expected-version CAS 返回原结果。

### 4.5 retry policy、decision 与 unknown 审批 authority

`test_failed` 与 `infra_failed` 使用不同 policy family，禁止一个 generic retry flag 跨越两类权限：

本节冻结“必须显式版本化、分权且 fail closed”的 Schema envelope；具体 retry scope、per-item
effective-result 规则、角色集、次数/预算值与 review SLA 尚未包含在八项签署中，分别受
`STATE-DEC-009/010` 阻断。在两项新决议关闭前，下表只能用于写 RED/schema-validation 计划，不能
激活任何 retry 或 duplicate-risk 权限。

| Schema | 规范字段 | 最低 authority |
|---|---|---|
| `qep.suite-retry-policy.v1` | `policy_id/version`、`project_id/suite_revision_id`、`retryable_case_selector_digest`、`retryable_reason_class_set_digest`、`max_retries`、`retry_scope_policy`、`resource_budget_digest`、`effective_from/to`、`approver_role_set_digest`、`approval_record_digest`、`policy_digest` | 只作用于 `test_failed`；具体次数、批准角色与 scope 由 DEC-009/010 决定 |
| `qep.platform-retry-policy.v1` | `policy_id/version`、`project_id`、`runner_id/version`、`retryable_reason_class_set_digest`、`environment_selector_digest`、`resource_profile_selector_digest`、`stop_proof_scope_digest`、`max_retries`、`retry_scope_policy`、`resource_budget_digest`、`require_prior_execution_stop=true`、`effective_from/to`、`approver_role_set_digest`、`approval_record_digest`、`policy_digest` | 只作用于 `infra_failed`，不得放宽 stop proof；具体次数、批准角色与 scope 由 DEC-009/010 决定 |
| `qep.run-retry-decision.v1` | `run_id/source_run_version`、`source_attempt_id/no/fence/outcome`、`source_item_resolution_set_digest`、`source_item_set_digest`、`target_item_set_digest`、`retry_scope`、`decision=retry/closed_no_retry`、`decision_source_kind=suite_policy/platform_policy/unknown_adjudication`、`reason_class/code`、`retry_intent_digest`（retry 时必填）、`authority_schema/id/version/digest`、`adjudication_digest`（按 kind 可空）、`prior_execution_stop_digest`（按 kind 可空）、`duplicate_risk_acceptance_digest`（按 kind 可空）、`attempt_budget_snapshot`、`decided_at`、`decision_digest` | authority scope、Attempt/fence、item scope、预算和时间窗口全部匹配；一个 Attempt/authority version 只有一个 decision |

`max_retries` 表示原始 Attempt 之后允许的最大新 Attempt 数；该值、预算和时间不得取隐式
默认值。未配置、过期、scope 不匹配、审批记录缺失或未知字段
都产生 `closed_no_retry` 的 fail-closed decision（unknown 除外，unknown 仍必须人工裁决），不能自动
retry。Suite policy 不能批准 infra retry；platform policy 不能把测试断言失败改写为 infra failure。

unknown 使用 `qep.unknown-review-policy.v1` 与既有 append-only adjudication record：review policy
必须绑定 `project_id/suite_revision_id`、policy version/digest、review SLA、升级 owner/route、允许的
`adjudicator_role_set_digest` 和 approval record。具体 role set 与 SLA 是 DEC-010 输入；没有已签 policy 时
unknown 仍保持 `review_required`。SLA 只能产生告警/升级，不产生 disposition。

早期 PRD/DD 草稿曾把项目 admin 与业务负责人作为 adjudication/duplicate-risk 候选角色；
当前 PRD/DD 已把它们降为 DEC-010 决议输入，不是已批准 runtime 权限。选择
`accept_duplicate_risk_then_retry` 时必须引用 `qep.duplicate-risk-acceptance.v1`，至少绑定
`schema_version`、`acceptance_id/version`、`project/suite/SUT/run/attempt/fence`、`target_grant_digest`、
`target_item_set_digest`、UnknownObservation 与 adjudication chain tail、side-effect scope/risk 说明摘要、
接受人 principal + 当前 authority-grant/role-binding digest、`valid_from/until`、`single_use=true`、
`consumed_by_retry_decision_digest`（消费前为 `null`）、记录时间与 acceptance digest。过期、跨 scope、
角色已撤销或与当前 chain tail 不匹配时先于 replay 拒绝。消费必须与 retry decision 在同一
authoritative UoW 中唯一提交；该 acceptance 不得成为后续 Run、其他 item set 或其他 SUT 的通配授权。

## 5. cancel intent 与 Evidence 竞态

### 5.1 first-finalized 原则

cancel intent 只表达“要求停止”，不预留或抢占 outcome。completed/cancelled Evidence 之间由第一个
成功持久化的 verified root 决定 Attempt execution fact；随后到达的异 root 必须冲突。获批的
`STATE-DEC-005/A` 进一步规定：completed root 先完成时，RunOutcome 必须按 completed fact 计算，
cancel intent 以 `cancel_requested_before_completion=true` 的独立审计投影保留，Batch 按普通完成
聚合。

### 5.2 规范竞态矩阵

| 首个持久事实 / 竞态 | Run/Attempt 结果 | 后到事实 | Batch 输入 |
|---|---|---|---|
| cancel intent 后 completed Evidence root 先 finalize | Attempt `passed/test_failed/infra_failed`；按 §4 disposition 收敛 | cancelled Evidence/stop proof 不得覆盖；保留 intent 审计 | completed outcome；intent 本身不产生 cancelled/partial |
| cancelled Evidence root 先 finalize | Attempt fact `cancelled`；Run `closed/cancelled` | completed Evidence 异 root 冲突 | `cancelled` item classification |
| completed Evidence 已 finalize，随后首次 cancel | terminal Run 不新增 cancel intent；返回稳定 terminal conflict | 无状态变化 | 原 completed outcome |
| `attempt_unknown` 先持久，随后 stop proof | unknown 保持吸收；proof 只能成为新 adjudication 的依据 | 不得原位改 cancelled | unknown lineage；未 adjudicate 前不可完成 Batch basis |
| 同一 cancel request exact replay | 返回同一 intent/result | 不增 version、不重复 fanout | 无新增分类 |
| 同幂等键异 digest或同 Run 第二个异 intent | `IDEMPOTENCY_CONFLICT` / cancellation conflict | 不改变旧 intent | 无变化 |
| cancel intent CAS 与 completed finalize CAS 并发 | 两者都可成功；intent 不阻断可信 completed root | completed fact 获胜，审计标记 intent 时序 | completed outcome |
| 初次 precommit cancel 与 commit-start 并发，cancel UoW 先提交 | Assignment `cancelled_prestart`，Run `closed/cancelled`；无 Attempt/fence | commit-start authority 失效 | cancelled |
| precommit cancel 与 commit-start 并发，commit UoW 先提交 | 创建 Attempt/fence；cancel 作为 postcommit intent | 必须 stop proof 或 unknown 收敛 | 最终 verified fact |
| pending retry cancel 与 retry commit-start 并发，cancel UoW 先提交 | 关闭 retry authority；completed latest fact 按原 outcome 关闭，unknown 则回到 `running/review_required` | commit-start authority 失效；不创建 N+1 | completed item facts 或 unknown lineage；intent 不强制 cancelled |

所有双向顺序都必须覆盖：authority stale、expected-version stale、exact replay、异 digest、进程在
commit 后崩溃以及 outbox 重投。API/UI 必须同时展示 completed outcome 和未达成的 cancel request，
不得把后者翻译为“已取消”。

## 6. Batch success policy 与 outcome truth table

### 6.1 original denominator 与分类输入

Batch finalizer 必须从冻结 Manifest/Shard Plan 证明：

```text
original_denominator
= passed
 + test_failed
 + infra_failed
 + cancelled
 + not_executed
 + unknown_lineage
```

这里每个 Manifest item 恰好出现一次。retry Attempt 不增加 denominator；Run basis 必须同时保留
per-item original view 与 authorized retry 后的 effective view。单一 `RunOutcome` 不能乘到该 Run
拥有的所有 item 上，也不能作为 Batch 计数来源。

每个 closed Run 必须持久 `qep.run-item-resolution-set.v1`：

| 字段组 | 规范字段 |
|---|---|
| envelope | `schema_version=qep.run-item-resolution-set.v1`、`batch_id`、`run_id`、`source_run_version`、`manifest_digest`、`shard_plan_digest`、`run_item_set_digest` |
| immutable chains | `attempt_chain_digest`、`retry_chain_digest`（可空）、`adjudication_chain_digest`（可空） |
| ordered entry identity | 按 `(manifest_id, item_index)` 排序的 `item_key`；key 必须是 frozen Run item set 的原始 stable key |
| entry.original | `source_kind=attempt_result/attempt_terminal_fallback/prestart_cancel`、`attempt_id/no/fence`（按 kind 可空）、`attempt_item_set_digest`（按 kind 可空）、`execution_fact`、`fact_schema/version/digest`、`evidence_root_digest`（按 kind 可空）、`result_mapping_schema/version/digest`（按 kind 可空）、`prestart_closure_digest`（按 kind 可空） |
| entry.effective | `source_kind=original/authorized_retry/unknown_adjudication`、`attempt_id/no/fence`（按 kind 可空）、`attempt_item_set_digest`（按 kind 可空）、`outcome`、`fact_schema/version/digest`、`evidence_root_digest`（按 kind 可空）、`result_mapping_schema/version/digest`（按 kind 可空）、`retry_intent_digest`（按 kind 可空）、`retry_decision_digest`（按 kind 可空）、`retry_authority_digest`（按 kind 可空）、`adjudication_digest`（按 kind 可空） |
| entry lineage/result | `unknown_lineage_digest`（独立 sticky 维度，可空）、`aggregation_class`、`item_resolution_digest` |
| result | `item_count`、`original_resolution_set_digest`、`effective_resolution_set_digest`、`resolution_set_digest` |

`original.execution_fact` 只能使用
`passed/test_failed/infra_failed/cancelled/attempt_unknown`；`effective.outcome` 只能使用
`passed/test_failed/infra_failed/cancelled`。`fact_schema/version/digest` 必须指向版本化的 immutable
Case result、platform terminal、cancellation/unknown fact 或 adjudication-supported resolution，不得只存
自由文本。`aggregation_class` 只能使用
`passed/test_failed/infra_failed/cancelled/unknown_lineage`，且是由 effective outcome 与独立 lineage
确定性计算的可校验投影，不是第三个可任意写的结果字段。

`source_kind` 的 required/forbidden 矩阵如下。矩阵中的“空”必须在 canonical payload 中显式为
`null`；`result_mapping_schema/version/digest` 三者必须全空或全非空，不得只存 digest：

| view / source_kind | 必填 | 必须为空 | 额外约束 |
|---|---|---|---|
| original / `attempt_result` | Attempt identity/fence/item-set、fact identity、Evidence root；存在 raw Case outcome 时还须完整 mapping identity | `prestart_closure_digest` | `execution_fact=passed/test_failed`；item 必须出现在 Attempt item set 和 Evidence index |
| original / `attempt_terminal_fallback` | Attempt identity/fence/item-set、fact identity；该 fact Schema 要求 Evidence 时 Evidence root 也必填 | `prestart_closure_digest`；无 raw outcome 时 mapping identity 为空 | 只能得到 `infra_failed/cancelled/attempt_unknown`；terminal fact 必须证明其 item coverage |
| original / `prestart_cancel` | `execution_fact=cancelled`、fact identity、`prestart_closure_digest` | Attempt identity/fence/item-set、Evidence root、mapping identity | closure 必须绑定同一 Run/item 且证明没有 start commit/Attempt |
| effective / `original` | 与 original 相同的 fact、Evidence 与 result-mapping identity；非 prestart 时还须相同 Attempt/item-set | retry intent/decision/authority、adjudication | 必须是 original resolution 的逐字节可验证投影；original 为 `attempt_unknown` 时不能用本 kind 关闭 Run；`outcome` 与 original concrete fact 相同 |
| effective / `authorized_retry` | 新 Attempt identity/fence/item-set、fact identity、retry intent/decision/authority；按 raw fact 要求完整 Evidence/mapping | 无 unknown authority 时 `adjudication_digest` 为空 | 新 Attempt 必须覆盖该 item 并位于连续授权 chain；unknown 授权 retry 时 adjudication 必填 |
| effective / `unknown_adjudication` | 源 unknown Attempt identity/fence/item-set、fact identity、`adjudication_digest` | retry intent/decision/authority | `mark_infra_failed_no_retry` 时 outcome 只能为 `infra_failed`；`mark_completed_from_verified_evidence` 时 Evidence root 及按 raw fact 要求的 mapping identity 必填 |

三个 set digest 的输入边界固定为：

- `original_resolution_set_digest` 覆盖 Schema/envelope identity、`run_item_set_digest` 与有序
  `item_key + original` 投影；
- `effective_resolution_set_digest` 覆盖同一 Schema/envelope identity、`run_item_set_digest` 与有序
  `item_key + effective` 投影；
- `resolution_set_digest` 覆盖 envelope、immutable chains、每个完整 entry（含独立
  `unknown_lineage_digest`、`aggregation_class` 与 `item_resolution_digest`）及前两个 set digest，
  不包含自身。

交叉不变量如下：

- original/effective 的 key set 必须各自与 frozen Run item set 精确相等，无缺失、重复或跨
  Run item；三个 set digest 任一无法重算就不能关闭 Run；
- verified Case result 按 item 形成 `passed/test_failed`；Run-level platform failure 或可信 cancel 只能
  依据受信 exit/stop/Evidence facts 给尚无完成结果的 item 分类，不能覆盖已 finalized 的 item fact；
- `skipped/not_reported` 或框架专用 raw outcome 必须由钉住版本的 result-mapping policy 显式映射；
  未识别、缺映射或缺 Case result 的 item 保持 unresolved，不能默认 passed/skipped；
- 除 `prestart_cancel` 外，original/effective 引用的 Attempt 必须在其
  `attempt_item_set_digest` 中覆盖该 item；prestart entry 的 Attempt/Evidence/item-set 字段必须
  为 `null`，并引用可信 `prestart_closure_digest`；
- 每个 retry 必须显式保存 source/target item-set digest 与 `retry_scope`；完整 Run 重跑、仅失败 item
  重跑或其他 immutable subset 的选择受 `STATE-DEC-009` 阻断。在该决议关闭前不得创建能改变
  effective outcome 的 retry；
- retry 只能追加新 Attempt 和 effective fact；original outcome/Attempt/fence 永不改写，original
  denominator 不变。effective outcome 必须由获签的 scope/precedence 规则从完整收敛的 Attempt
  选择，并引用对应 RetryIntent、retry decision 与 authority；effective Attempt 必须包含该
  item 且位于连续、获授权的 retry chain；不得默认“latest wins”或拼接多个 Attempt 的
  “最佳结果”；
- 任一 item 的 authority chain 含 `attempt_unknown` 时，仍保留后来真实的 effective outcome，但
  `aggregation_class` 固定为 `unknown_lineage` 并引用 lineage digest；不得丢失实际 retry 结果，也
  不得让它进入 passed/failed 计数；
- `PROPOSED/UNSIGNED — STATE-DEC-009`：候选安全默认是，只有可信 per-item
  execution-boundary proof 才能缩小 unknown 影响范围，否则标记该 Attempt 可能覆盖的
  全部 item。该规则仅可写 RED/schema-validation 计划；决议签署前 runtime 必须保持
  unresolved，不得用此候选默认关闭 Run/Batch；
- `not_executed` 只来自没有 materialize Run 的 §7 scope-item fact，不得出现在 Run resolution set；
- RunOutcome 是 Run 级 policy/audit 结果，必须与同一 basis 中的 resolution set 相容，但 Batch
  `P/T/I/C/U` 只能逐 entry 统计，禁止按 RunOutcome × item_count 推导。mixed per-item set 如何映射
  scalar RunOutcome 也是 `STATE-DEC-009` 的显式输入，决议前不得用隐式 worst/latest/majority 规则
  关闭 Run。

Batch 还必须持久 `qep.batch-item-resolution-set.v1`，使六类计数能从单一 canonical
entry set 逐字节重放：

| 字段组 | 规范字段 |
|---|---|
| envelope | `schema_version=qep.batch-item-resolution-set.v1`、`batch_id`、`source_batch_version`、`manifest_id/digest`、`shard_plan_id/version/digest`、`canonical_run_set_digest` |
| ordered entry identity | 按 `(manifest_id, item_index)` 排序的 `item_key` |
| entry source | `source_kind=run_resolution/not_executed`、`source_run_id/version`（按 kind 可空）、`source_run_basis_digest`（按 kind 可空）、`source_run_item_resolution_set_digest`（按 kind 可空）、`source_item_resolution_digest`（按 kind 可空）、`not_executed_fact_schema/digest`（按 kind 可空）、`cancellation_scope_item_digest`（按 kind 可空） |
| entry result | `classification=passed/test_failed/infra_failed/cancelled/unknown_lineage/not_executed`、`batch_item_resolution_digest` |
| result | `item_count`、`passed_count`、`test_failed_count`、`infra_failed_count`、`cancelled_count`、`unknown_lineage_count`、`not_executed_count`、`resolution_set_digest` |

每个 Manifest item 必须恰好一个 Batch entry。`run_resolution` entry 必须逐 item 引用已关闭
Run basis 中唯一的 `item_resolution_digest`，且全部 `not_executed` 字段为 `null`；
`not_executed` entry 必须引用同一 Batch cancel scope 下的 immutable fact，且全部 Run
字段为 `null`。有 Run 的分类顺序固定为：若被引用的 item entry 含非空
`unknown_lineage_digest`，则为 `unknown_lineage`；否则精确等于其 `effective.outcome`。没有
Run 的只能为 `not_executed`。六个 count 必须从 ordered entries 重算，与
`item_count=original_denominator` 及 `resolution_set_digest` 互相校验；任一 source digest、
Evidence/mapping/authority 或 item coverage 无法验证时，该 item 保持 unresolved，禁止写
Batch terminal。

尚处于 `review_required`、缺 required Evidence/resolution entry 或没有 terminal basis 的 Run 是
`unresolved`，Batch 必须保持 `running`，不得冻结后续 retry 所需的 commit-start。

### 6.2 `qep.batch-success-policy.v1`

v1 是最小 Suite policy。字段级契约如下：

| 字段 | 约束 |
|---|---|
| `schema_version` | 固定 `qep.batch-success-policy.v1` |
| `policy_id` | opaque、非空 |
| `policy_version` | 非 bool 的正整数；同 policy 单调递增 |
| `suite_id` | 必须与 Batch 的 frozen Suite identity 一致 |
| `max_test_failed_items` | 非 bool、非负整数；只作用于 final effective `test_failed` |
| `allow_authorized_retry_pass` | bool；只允许完整、连续、非 unknown 的授权 retry chain 按 DEC-009 将来获签的 effective-selection 规则满足 item；决议前不得激活 |
| `allowed_test_failure_selector_digest` | 可空；非空时绑定版本化 per-item allow-list，不接受运行时自由表达式 |
| `result_mapping_schema/version/digest` | 必填；把受支持的 raw Case outcome（含显式 skipped 规则）映射为 v1 item resolution；未知值 fail closed |
| `approval_record_digest` | 必填；绑定获授权的 policy 审批记录 |
| `policy_digest` | 对上述 identity 字段 canonical digest；不得包含自身 |

Suite policy 无权配置 unknown、Evidence 缺失、denominator 漂移、cancelled、not-executed 或未获
platform authority 的 infra failure 为 success。未来若需要允许新的平台结果，必须由单独的
platform policy/authority fact 和新 Schema 版本表达，不能给本 Schema 增加“ignore all”默认值。

### 6.3 fail-closed preconditions

出现任一情况时不得写任何 Batch terminal：

- Manifest/Shard Plan/canonical Run set 未冻结或摘要不一致；
- denominator 为零且没有已批准的 empty-Suite 规则；
- item 缺失、重复覆盖、跨 Run 重叠或计数不等；
- required Evidence root/index 缺失、摘要冲突或 authority/fence 不匹配；
- 任一 Run 为 `review_required`、非 `closed` 或没有 immutable terminal basis；
- policy 缺失、未审批、Suite 不匹配、版本/digest 不一致或字段未知；
- cancel/not-executed fact 没有匹配的 Batch intent/Plan/item identity；
- 对新 mutation 而言 candidate basis 的 source version 已过期；已存 terminal basis 的 exact replay
  必须先按 §8.3 identity 判定，不能被 expected-version CAS 误拒绝。

在 `begin_batch_finalization` 前发现任一 Run 为 `review_required`、非 `closed` 或缺 terminal basis
时，Batch 必须保持 `running`。已经进入 `finalizing` 后再观察到该组合属于 stale/corrupt snapshot
不变量冲突，必须 fail closed、告警并前向修复，不能重开 Run 或创建 retry。其余 Batch-level 输入
未收敛时保持 `finalizing`，由 reconciler 重试或升级；不得默认选择最接近的 terminal。

### 6.4 完整 truth table

以下按自上而下优先级匹配。除 `N` 外均从 §6.1 ordered per-item entries 逐项计数：`C` 为
cancelled 数，`N` 为 not-executed 数，`U` 为 unknown-lineage 数，`I` 为 final infra-failed 数，
`T` 为 policy 计算后的 test-failed 数，`P` 为 passed 数。

| 前置条件（均已通过 §6.3） | Batch cancel intent | 分类条件 | terminal |
|---|---:|---|---|
| 全部 item 都由 cancel 收敛 | 有 | `C + N = denominator` 且 `C + N > 0`，`P=T=I=U=0` | `cancelled` |
| 有取消、未执行或 unknown lineage 的 mixed result | 任意；`C/N` 必须有 intent | `C + N + U > 0`，且不满足上一行 | `partial` |
| 无 partial 类；存在未恢复平台失败 | 任意 | `C=N=U=0` 且 `I > 0` | `failed` |
| 无 partial/infra 类；测试失败超出 policy | 任意 | `C=N=U=I=0` 且 `T > max_test_failed_items` | `failed` |
| 完整收敛且满足 Suite policy | 任意 | `C=N=U=I=0` 且 `T <= max_test_failed_items`，`P+T=denominator` | `succeeded` |

补充约束：

- cancel intent 存在但所有 Run 都在 completed Evidence 先 finalize 时，按最后两行正常得到
  `succeeded/failed`；只额外输出 cancel-requested 审计投影。
- 没有 Batch cancel intent 的 `C/N` 组合违反 authority，不得借 `partial` 掩盖，必须停在
  `finalizing`。
- unknown lineage 永远不能被 Suite policy 变为 `succeeded`；有效 adjudication 选择 no-retry，或
  授权 retry 后新 Attempt 已使 Run 关闭时，均可收敛为 `partial`；未 adjudicate 或 Run 未关闭时
  属于 §6.3 unresolved，并阻止 Batch 进入 `finalizing`。
- `rejected` 不由该 truth table 产生；它只来自 §3 pre-execution command。
- truth table 输入、policy 版本和 digest 相同，结果必须逐字节确定；未匹配组合 fail closed。

## 7. Batch cancel intent、fanout 与启动竞态

### 7.1 intent 与 frozen scope

`request_batch_cancel` 必须在一个短 UoW 中 CAS Batch、写不可变
`qep.batch-cancellation-intent.v1`、审计和 outbox。若 Manifest/Shard Plan 已冻结，intent 必须绑定
其 digest/version 及 canonical Run-set digest；若尚未冻结，intent 必须冻结“不得继续
collect/plan/materialize”的 pre-plan scope。`recorded_at` 是服务端 metadata，不能进入调用方
idempotency identity。

`qep.batch-cancellation-intent.v1` 的字段级契约如下：

| 字段 | 约束 |
|---|---|
| `schema_version` | 固定 `qep.batch-cancellation-intent.v1` |
| `batch_id/project_id/suite_revision_id` | 必须与 Batch 的不可变 ownership 一致；禁止跨项目引用 |
| `source_batch_version` | 接受首次命令时的 authoritative version；非 bool、非负整数 |
| `idempotency_key` | 在 `batch:{batch_id}:cancel` scope 内非空且不可复用于异内容 |
| `source` | `user_request`、`deadline_exceeded`、`policy_enforcement` 之一 |
| `actor_id` | 已认证 principal 或受信系统 actor；非空 |
| `reason` | 非空、已清洗的稳定原因；不得含秘密、原始堆栈或未清洗外部错误 |
| `authorization_digest` | 绑定 actor、role binding、project、Batch 与授权决策版本；重放时仍须重新校验当前调用权限 |
| `request_digest` | 只对 `batch_id`、`source_batch_version`、幂等键、source、actor 和 reason canonical digest；不含服务端 authorization/scope/`recorded_at` |
| `scope_kind` | `pre_plan` 或 `frozen_plan` |
| `preplan_scope_digest` | `pre_plan` 必填；绑定 submission/input 与“禁止继续 collect/plan/materialize”的边界 |
| `manifest_digest/shard_plan_version/shard_plan_digest/canonical_run_set_digest` | `frozen_plan` 全部必填；`pre_plan` 全部显式为 `null` |
| `recorded_at` | 服务端 UTC metadata；调用方不得提供 |
| `intent_digest` | 对 request digest、`authorization_digest`、scope identity 与 `recorded_at` canonical digest；不得包含自身 |

同一 key + request digest 的重放在调用权限与 Batch authority 仍有效时返回首次保存的 intent，忽略
新 server time 与 stale expected version；同 key 异 request digest 返回 `IDEMPOTENCY_CONFLICT`，
第二个异 key 不得替换首个 intent。scope 由持锁 UoW 的 authoritative Batch snapshot 生成，调用方
不能自选 `pre_plan/frozen_plan` 或提供 Plan/Run-set digest。

计划已冻结时，每个 scope item 还必须形成 `qep.batch-cancellation-scope-item.v1`：

| 字段组 | 规范字段 |
|---|---|
| envelope | `schema_version=qep.batch-cancellation-scope-item.v1`、`batch_id`、`batch_cancellation_intent_digest`、`manifest_id/digest`、`manifest_item_key` |
| plan/run binding | `shard_plan_version/digest`、`resolution_kind=not_executed/run_fanout`、`run_id/source_run_version`（按 kind 可空） |
| delivery identity | `delivery_key=(intent_digest,item_key)`、`recorded_at`、`scope_item_digest` |

`not_executed` 要求该 item 从未 materialize 为 Run，`run_id/source_run_version` 必须为 `null`；
`run_fanout` 要求 Run 当前仍属于同一 frozen Plan 和 item set。每个 Manifest item 必须恰好一个
scope-item fact；重复 key 同 digest 是重放，异 digest 冲突。该完整集合的有序 digest 进入 §8.2
Batch basis 和 completeness proof。

intent 提交后，所有可能扩大执行面的命令都必须 fail closed。仅依赖异步 fanout 而不在
materialize/admit/offer/commit UoW 中检查 Batch intent，不符合本契约。

### 7.2 全量安全 fanout

| scope item / Run 状态 | 必须动作 | 明确禁止 |
|---|---|---|
| 尚未 materialize 的 Manifest item | 写绑定 intent + Plan + item 的 `not_executed` fact | 创建占位 Run 或伪 Attempt/Evidence |
| 初次 `planned/queued`；无 Attempt history | 幂等 Run cancel；按 prestart basis 关闭为 `cancelled` | 继续 offer 或伪造 Attempt/Evidence |
| 初次 `assigned` 且无 start commit | 与 commit-start 竞争；cancel 赢时关闭 Assignment、释放 reservation、无 Attempt/fence | 先标 cancelled 再异步检查是否已 commit |
| `retry_queued` 或 retry Assignment 尚未 commit | 原子关闭 pending retry/Assignment 并清 active pointer；latest completed fact 按原 outcome 关闭，latest unknown 保持 `running/review_required` | 把历史 completed/unknown 改写为 cancelled；删除 RetryIntent history；继续 consume retry |
| `running` / 已 commit | 写 Run cancel intent，请求 runtime stop；等待 trusted stop proof + Evidence 或 unknown | 仅凭 Batch/Run intent 改 outcome |
| `review_required` unknown | 保留 unknown 与 adjudication gate；阻止新 retry | 自动改 cancelled 或自动 retry |
| 已 `closed` | 不改 terminal；记录 fanout observed/exact replay | 重开 Run 或覆盖 completed outcome |

fanout 至少一次投递，消费者按 `(batch_cancel_intent_digest, run_id/item_id)` 幂等。所有 scope item
形成明确的 terminal basis、not-executed fact 或 unresolved 告警前，Batch 不得完成 finalize。

### 7.3 关键竞态

| 竞态 | cancel 先提交 | 另一命令先提交 |
|---|---|---|
| cancel vs `reject_preexecution` | intent 的 Batch CAS 先赢；rejection mutation 以 stale CAS 拒绝，已观察 failure 只能追加非终态审计；停止在途任务后按 cancel closure 收敛 | rejection fact + closure basis + `rejected` 原子先赢；cancel 返回稳定 terminal conflict，不新增 intent |
| cancel vs collection/Plan approve | 后续 phase command 读到 intent 并拒绝；停止在途 task 后收敛 | intent 绑定新 frozen digest，随后 fanout |
| cancel vs Run materialize | 不创建 Run；item -> not-executed | 新 Run 必须进入 frozen set并收到 fanout |
| cancel vs Assignment offer/claim | 不创建/消费新 offer；既有未 commit offer 被关闭 | offer 可存在，但 cancel 可在 commit 前撤销 |
| cancel vs commit-start | 无 Attempt/fence；Assignment prestart close | Attempt/fence 成为事实；转 postcommit cancel |
| cancel vs RetryIntent consume | pending intent 保留历史但不得消费 | 新 Attempt 已 commit 时转 postcommit cancel |
| cancel vs completed Evidence | intent 不阻止 completed finalize | terminal completed 后 cancel 返回 terminal conflict |
| cancel vs Batch finalization CAS | candidate basis 必须重建并包含 intent/fanout facts | Batch terminal 后 cancel 返回 terminal conflict |

这些竞态必须共享 authoritative row lock/CAS 或等价单写者序列化点；“先查询无 cancel，再在另一
事务启动”的 check-then-act 实现不符合契约。cancel-vs-reject 使用 first authoritative UoW commit
规则：赢家 exact replay 返回原结果；败者重读后不得覆盖赢家，异内容只返回对应 stale/terminal
conflict。进程在 commit 后崩溃和 outbox 重投不得产生第二个 terminal 或第二个 intent。

## 8. immutable finalization basis、UoW、reconciler 与 outbox

### 8.1 `qep.run-finalization-basis.v1`

Run 关闭必须持久以下字段。`basis_id`、`created_at`、数据库主键等存储 metadata 不参与内容摘要。

| 字段组 | 规范字段 |
|---|---|
| envelope | `schema_version=qep.run-finalization-basis.v1`、`run_id`、`batch_id`、`source_run_version` |
| frozen plan identity | `manifest_digest`、`shard_plan_digest`、`run_item_set_digest`、`execution_spec_digest` |
| Attempt authority | `original_attempt_id/fence`、`final_attempt_id/attempt_no/fence/version/state`、`worker_id/generation` |
| immutable chains | `attempt_chain_digest`、`evidence_root_digest`（可空但须与 input kind 一致）、`unknown_observation_digest`（可空）、`adjudication_chain_digest`（可空）、`retry_chain_digest`（可空） |
| item resolution | `item_resolution_schema/version`、`item_resolution_set_digest`、`original_resolution_set_digest`、`effective_resolution_set_digest`、`item_count`；必须引用 §6.1 的 immutable ordered set |
| cancel authority | `cancellation_intent_digest`（可空）、`cancellation_stop_digest`（可空）、`prestart_closure_digest`（可空） |
| decision/authority chain | `terminal_rule_schema/version/digest`、`decision_chain_digest`、按 `sequence` 排序的 `sequence/decision_kind=contract_rule/suite_retry/platform_retry/unknown_adjudication/duplicate_risk_acceptance`、`decision_schema/id/version/digest`、`source_attempt_id/no/fence`、`source_item_set_digest`、`decision_result` refs |
| disposition/result | `disposition`、`outcome`、`terminal_input_kind` |
| result | `basis_digest` |

`terminal_input_kind` 只能是 `verified_evidence`、`verified_cancellation_evidence`、
`prestart_cancel` 或 `unknown_adjudication`。nullable 字段不得随意省略：canonical payload 必须显式
使用 `null`，并由 input kind 的 cross-field invariant 约束。只有 `prestart_cancel` 可令全部
Attempt/worker/Evidence 字段为 `null`；其他 input kind 必须绑定 final Attempt authority。固定的
passed/prestart-cancel 处置也必须绑定适用的 contract-rule version/digest，不能用“没有 policy”
形成不可重放的隐式默认值。

`decision/authority chain` 必须覆盖从 original Attempt 到 terminal decision 的全部实际步骤，不能只
保存最后一个 policy：无 retry 的 passed/prestart path 至少引用一个 `contract_rule`；test/infra
retry 逐次引用相应 suite/platform decision；unknown path 必须引用对应 adjudication，若接受重复
风险还必须在同一 sequence 中引用 single-use acceptance。序号缺失/重复、source Attempt/fence/item-set
不连续、policy family 与 fact 不匹配或任一 digest 不可重放时，不得关闭 Run。

### 8.2 Batch terminal basis families

每个 Batch terminal 必须且只能引用一个 immutable basis family。`rejected` 与零 materialized Run 的
pre-execution `cancelled` 使用 §8.2.1；一旦存在任一 materialized Run，即使全部从未 start
commit，也必须使用 Run prestart/terminal item resolutions + 未物化 item 的 `not_executed`
facts 构建 §8.2.2 execution basis。不得为 pre-plan closure 伪造 Manifest、Plan、Run 或零
denominator 来满足 execution basis，也不得在已有 Run 时伪造“全量 not-executed”。

#### 8.2.1 `qep.batch-preexecution-closure-basis.v1`

| 字段组 | 规范字段 |
|---|---|
| envelope | `schema_version=qep.batch-preexecution-closure-basis.v1`、`batch_id`、`source_batch_version`、`source_phase`、`terminal_kind` |
| command fact | `rejection_fact_digest`（可空）、`batch_cancellation_intent_digest`（可空）；二者严格二选一 |
| scope | `scope_kind=pre_plan/planned_unmaterialized`、`submission_digest`、`preplan_scope_digest`（可空）、`manifest_digest`（可空）、`shard_plan_digest/version`（可空）、`canonical_run_set_digest`（可空） |
| no-execution proof | `materialized_run_absence_digest`、`execution_absence_snapshot_digest`、按稳定键排序的 `task_stop_fact_digest` 数组、按 item key 排序的 `preexecution_scope_item_fact_digest` 数组、`item_coverage_proof_digest`（可空） |
| result | `batch_outcome=rejected/cancelled`、`basis_digest` |

计划已冻结但仍为零 materialized Run 时，每个 item 使用中性
`qep.batch-preexecution-scope-item.v1`：

| 字段组 | 规范字段 |
|---|---|
| envelope | `schema_version=qep.batch-preexecution-scope-item.v1`、`batch_id`、`source_batch_version`、`terminal_kind=rejection/prestart_cancel` |
| command binding | `rejection_fact_digest`（按 kind 可空）、`batch_cancellation_intent_digest`（按 kind 可空）；严格二选一 |
| item binding | `manifest_id/digest`、`manifest_item_key`、`shard_plan_id/version/digest`、`materialized_run_absence_digest`、`resolution=not_started` |
| result | `scope_item_digest` |

该 fact 只证明 pre-execution terminal 时 item 从未 materialize/start，不是 §7 的 cancellation-only
`not_executed` fact，不得进入 §6 的 `N` 计数或 §8.2.2 execution basis。它属于 `001F`，
不依赖 `001H` 的 fanout Schema。

交叉不变量：

- `terminal_kind=rejection` 只允许 `source_phase=validating/collecting/planning/awaiting_admission`，
  必须引用匹配 stage/source version 的 §3.3 fact，cancellation intent 必须为空，所有已计划
  scope-item facts 也必须引用同一 rejection fact；
- `terminal_kind=prestart_cancel` 必须引用 §7.1 intent；若存在在途 collection/planning task，还必须
  覆盖每个任务的可信 stop fact；rejection fact 必须为空，所有已计划 scope-item facts
  也必须引用同一 cancellation intent；
- `scope_kind=pre_plan` 时尚未形成的 Manifest/Plan/Run-set 字段必须显式为 `null`，并由
  `submission_digest + preplan_scope_digest + execution_absence_snapshot_digest` 冻结已知范围；
- `scope_kind=planned_unmaterialized` 时 Manifest/Plan 与 item coverage 必填，canonical Run set 必须是
  可重放的空集摘要，每个 item 的 `qep.batch-preexecution-scope-item.v1` ref 必填并
  互相一致；
- 两种 scope 都必须在同一 Batch-local UoW 证明不存在 materialized Run、Assignment、
  Run start commit、Attempt 或 fence；一旦任一 Run 已物化，不得使用本 basis，必须转入
  Run fanout 并用 §8.2.2 execution basis；
- `source_batch_version`、command fact、scope 与 no-execution snapshot 相同才是 exact replay；任一
  digest 不同均冲突。digest canonicalization 继续服从 §8.3。

#### 8.2.2 `qep.batch-finalization-basis.v1`

| 字段组 | 规范字段 |
|---|---|
| envelope | `schema_version=qep.batch-finalization-basis.v1`、`batch_id`、`source_batch_version` |
| frozen scope | `manifest_id/digest/item_count`、`shard_plan_id/version/digest`、`canonical_run_set_digest` |
| terminal Run refs | 按 `run_id` 排序的 `run_id/source_run_version/run_basis_digest/run_outcome/run_item_set_digest/original_resolution_set_digest/effective_resolution_set_digest/item_resolution_set_digest` 数组 |
| non-Run refs | 按 item stable key 排序的 `not_executed_fact_schema/digest` 数组 |
| policy | `success_policy_id/version/digest` |
| reconciliation | `batch_item_resolution_schema/version=qep.batch-item-resolution-set.v1`、`batch_item_resolution_set_digest`、`original_denominator`、六类 per-item count、`completeness_proof_digest` |
| cancel/unknown | `batch_cancellation_intent_digest`（可空）、排序后的 unknown lineage/adjudication digest refs |
| result | `batch_outcome`、`basis_digest` |

只绑定聚合计数不充分；basis 必须引用 §6.1 定义的完整
`qep.batch-item-resolution-set.v1`，并能从每个 Manifest item 追到唯一 Run
item-resolution entry 或 not-executed fact。Run refs 是 immutable basis refs，不是可变
`Run.state` 的读取快照；同一 Run 的单一 outcome 不得替代逐 item refs。

### 8.3 digest identity 与 replay

- 使用 RFC 8785 JSON Canonicalization Scheme 或项目已冻结的等价 canonical 实现；所有字符串按
  UTF-8，整数不得接受 bool/float 等值穿透。
- 集合先按文中 stable key 排序后进入数组；数组顺序成为摘要输入。未知字段、重复 key、NaN、
  Infinity 和未规范化时间必须拒绝。
- digest payload 必须包含 `schema_version` 和所有语义字段，排除 `basis_digest` 自身、随机
  persistence ID、`created_at`、outbox delivery metadata 和数据库位置。
- identity tuple 是 `(schema_version, aggregate_id, source_version, basis_digest)`。同 aggregate /
  source version / digest 是 exact replay；同 aggregate/source version 异 digest 是高优先级冲突。
- 新 source version 只有在 aggregate 尚未 terminal 时可重算；terminal basis 不得原位替换。

### 8.4 Run-local UoW

Run finalize 必须在一个本地数据库 UoW 中完成：

1. 锁定并读取 Run、current Assignment/Attempt、所需 immutable Evidence index 及已存 finalization
   basis；此时不得先用 expected-version CAS 拒绝可能的响应丢失重放；
2. 校验命令调用权限、worker generation、Assignment authority、current fence 与 adjudication/retry
   chain tail；已撤销 authority 必须先于 replay 拒绝；
3. 按 `(schema_version, run_id, source_run_version, basis_digest)` 比对已存 identity：完全相同则返回
   原 basis/outcome，不重复写入；同 identity scope 异 digest 立即冲突；
4. 只有新 mutation 才校验 Attempt/Run expected version、policy/adjudication source version 与 Evidence
   binding。只引用事务前已完成校验的 Evidence index/root，不在持锁时访问对象存储、Worker 或 runtime；
5. 原子写 Attempt terminal（若尚未存在）、RunDisposition、RunOutcome/phase closed、immutable
   Run basis、current pointer cleanup、审计和 `run.closed.v1` outbox；
6. retry 分支原子写 disposition、RetryIntent、phase `retry_queued` 和 outbox，但不写 terminal Run
   basis/outcome；
7. 任一写入失败全部回滚。响应丢失后 exact replay 返回相同 basis/outcome，不重复 outbox 语义。

### 8.5 Batch reconciler 与 Batch UoW

Batch reconciler 由 `run.closed.v1`、Batch cancel/Plan-sealed outbox 和周期性 repair scan 触发：

1. 在事务外读取 frozen Manifest/Plan/policy 及 immutable Run basis refs，构建 candidate；
2. 验证 §6 denominator、item coverage、truth table、cancel/unknown facts并计算 candidate digest；
3. 开启短事务，锁定 Batch 并读取已存 terminal basis、source version、policy digest、Run-basis
   refs 和 cancel intent；先按 §8.3 校验 stored identity，exact replay 直接返回，同 scope 异 digest
   冲突；
4. 只有新 mutation 才执行 Batch expected-version CAS，并重新校验 candidate 的全部 source refs；
   任一漂移则丢弃 candidate 并有界重试；
5. 同一事务写 immutable Batch basis、Batch terminal、审计和 `batch.finalized.v1` outbox；
6. Run 尚未关闭时不执行 `begin_batch_finalization`，Batch 保持 `running`；已进入 `finalizing` 后
   缺 Batch-level 输入时保持 `finalizing`，记录可恢复 reason 与下一次调度；不可解释冲突告警，
   不能选默认 terminal。

跨 Run 一致性由 immutable basis refs + CAS completeness proof 提供，不要求锁住所有 Run 的长
事务。outbox 是至少一次交付；consumer 必须按 event ID + payload digest 幂等，同 ID 异 digest
冲突。publisher 状态、投递次数和时间不进入领域 basis。

### 8.6 pre-execution closure UoW

`reject_preexecution` 与 `finalize_unmaterialized_cancel` 必须在一个短 Batch-local UoW 中：锁定 Batch，
先校验 phase authority，再按已存 command/basis identity 判定 exact replay 或异 digest 冲突；只有
新 mutation 才校验 expected version、phase owner、任务停止、materialized-Run absence 与 execution
absence snapshot，并原子
写 command fact、§8.2.1 basis、terminal、审计和 outbox。不得在持锁时调用 collection runtime、
Worker、对象存储或其他外部系统。任一写入失败全部回滚；响应丢失后的 exact replay 返回同一
basis/terminal，不重复 outbox 语义。若持锁快照已有 Run，本 UoW 必须 fail closed 并转交
§7 fanout/§8.2.2 reconciler，不得将 Run item 改写为 `not_executed`。

## 9. compatibility、migration、rollback 与激活

### 9.1 首次生效版本

本设计契约从 V0.1.0 起生效；首个运行时 compatibility epoch 固定为 `M0-STATE-V1`，对应
`state_model_version=1` 以及本文全部规范 `.v1` Schema family，不以一个会随契约补全而失真的
固定数量描述。项目当前没有已批准的产品发布号，因此不得编造 SemVer 或上线日期。只有 §9.3
全部门禁通过的首个发布才可声明“runtime activated for M0-STATE-V1”。

| 子契约 | 必须同 epoch 激活的 Schema family |
|---|---|
| `001F` | `qep.batch-rejection.v1`、`qep.batch-cancellation-intent.v1`、`qep.batch-preexecution-scope-item.v1`、`qep.batch-preexecution-closure-basis.v1` |
| `001G` | `qep.suite-retry-policy.v1`、`qep.platform-retry-policy.v1`、`qep.run-retry-decision.v1`、`qep.unknown-review-policy.v1`、`qep.duplicate-risk-acceptance.v1`、`qep.run-item-resolution-set.v1`、`qep.run-finalization-basis.v1` |
| `001H` | `qep.batch-cancellation-scope-item.v1`、`qep.batch-success-policy.v1`、`qep.batch-item-resolution-set.v1`、`qep.batch-finalization-basis.v1` |

任何 family 尚无可执行机器 Schema、reader/writer conformance 和 migration Evidence 时，对应子契约
仍为 `DRAFT`；文中字段表不能冒充 runtime 已激活。

### 9.2 兼容承诺

- Batch API v1 必须接受并返回 canonical long-state vocabulary；coarse phase 只能是只读附加字段。
- Run API 必须分别返回 `phase`、nullable `disposition`、nullable `outcome` 和 terminal-basis
  reference；不得继续让一个 `state` 同时表达四层语义。
- 兼容窗口内可把 legacy `RunState.CANCELLED` 只读投影为 `closed/cancelled`，但新写入只能写 v1
  facts；不得长期 dual-write 两套状态机。
- 旧客户端遇到新 Batch long state 的行为必须通过 API version/capability 明确；不得把
  `collecting/awaiting_admission` 静默映射成错误 terminal。
- Worker 不获得 Run/Batch outcome 写权限；Worker payload 的旧/new 版本都只能提交可验证事实。

### 9.3 expand → backfill → activate → contract

| 阶段 | 必须完成 | 禁止激活条件 |
|---|---|---|
| Expand | 增加 nullable v1 facts/basis/outbox 字段或表；reader 可读 legacy + v1；写路径受 feature gate 保护 | migration precheck 或备份恢复未验证 |
| Backfill | 仅从 immutable Evidence、cancel/unknown/adjudication/retry/Plan facts重建 basis；保存逐行证据与冲突清单 | 依据 legacy state 名称猜测 outcome；缺 policy/basis 时默认为 success |
| Shadow | 用同一 frozen input 影子计算 Run/Batch result，比较 digest/truth table，不改变用户可见 terminal | 存在 unexplained mismatch、denominator drift 或 stale authority |
| Activate | 先停新 commit-start 的不兼容写入，再开启 v1 Run-local UoW、Batch reconciler 和 API projection | 001F/001G/001H 或数据库故障/迁移门禁未通过 |
| Contract | 一个完整 rollback window 后移除 legacy writer/字段；保留审计读取与 immutable history | 仍有 legacy-only active aggregate 或旧 Worker/API writer |

历史 `RunState.CANCELLED` 只有在 cancellation intent + prestart closure 或 trusted stop + cancelled
Evidence 可重建时，才可 backfill 为 closed/cancelled。历史 completed Attempt 对应的 `running` Run
只有在 Evidence、pinned policy、retry/adjudication chain 和 Manifest ownership完整时，才可关闭。
其他记录进入显式 migration review/quarantine，不能靠当前枚举猜测。

历史 `RunState.RETRY_QUEUED` 必须逐 Run 盘点 Attempt/Assignment/RetryIntent/adjudication 顺序：只有
存在唯一 chain-tail pending RetryIntent、没有未登记 commit-start，且 pointer、source Attempt/fence
与 policy/adjudication authority 全部一致时，才能 backfill 为
`phase=retry_queued + disposition=retry_queued`。若 cancel 已关闭 pending retry，必须保留 immutable
RetryIntent history 并清空 active pointer，再按 §4.3 的 completed/unknown 分支迁移；分叉、孤儿
pointer、历史 Attempt 缺失或 authority 不可证明时进入 quarantine，禁止自动 retry 或猜测 outcome。

历史 Batch `rejected` 或从未 start 的 `cancelled` 必须能重建 §8.2.1 closure basis；执行型 terminal
必须能重建逐 item resolution set 与 §8.2.2 basis。只有 scalar Run outcome、缺 per-item Case result、
缺 result-mapping policy 或 denominator 对不上的记录进入 quarantine，不得把一个 Run outcome 乘到
所有 item 上完成 backfill。

### 9.4 rollback

- Activate 前可回滚应用代码和未使用的 expand Schema；不得删除 backfill 证据或审计。
- Activate 后若出现问题，feature gate 必须停止新 materialize/commit-start/finalize，暂停
  reconciler/publisher，并保留所有已提交 v1 facts；可以回退只读 projection，但不得反向改写
  terminal basis/outcome。
- 已写入 `.v1` immutable facts 后不允许降级为 legacy writer；恢复路径是 forward fix/replay。
- Schema contract/drop 只能在 rollback window、备份恢复、旧 writer 清零和审计确认后执行。
- rollback 演练必须覆盖 UoW 每个 crash point、outbox 未投递/重复投递、stale candidate、部分
  backfill 与旧客户端读取。

## 10. TDD case matrix

本节是实现待办，不是测试 Evidence。三条 umbrella 子契约全部为 `PLANNED/DRAFT`，必须按
RED → 最小 GREEN → 重构推进；不得在实现前或仅凭本文标记为 `VERIFIED`。

| Test contract | 状态 | Decision coverage | 首批 RED case family | 完成门禁 |
|---|---|---|---|---|
| `T-M0-STATE-001F` | `PLANNED/DRAFT` | DEC-001/002 | canonical Batch long path；非法/吸收边；逐 phase rejection；reason Schema；六个 preterminal phase cancel intent 与零 materialized-Run closure；in-flight task stop；cancel-vs-phase CAS；legacy generic cancel 禁止 | domain + API contract + migration tests；100% 行/分支门禁；Evidence 文档 |
| `T-M0-STATE-001G` | `PLANNED/DRAFT` | DEC-003/004/005；DEC-009/010 待签 | Run phase/outcome/disposition cross-product；passed/test/infra retry gate；四种 unknown adjudication；Run basis；completed/cancel/unknown 双向竞态；exact replay/stale authority；legacy cancelled backfill | DEC-009/010 先关闭；domain/model + persistence integration + API projection + crash tests；100% 行/分支门禁；Evidence 文档 |
| `T-M0-STATE-001H` | `PLANNED/DRAFT` | DEC-006/007/008；DEC-009/010 待签 | policy value/digest；exhaustive per-item truth table；denominator/property tests；cancel fanout/commit races；Batch basis canonicalization；stale snapshot；UoW crash/outbox/reconcile；migration/rollback | DEC-009/010 先关闭；property/model + real DB concurrency/fault injection + API/E2E；100% 行/分支门禁；Evidence 文档 |

### 10.1 必测边界清单

| 层 | 最低 case |
|---|---|
| 值域 | raw string enum、bool-as-int、负 version、未知 Schema/field、空/重复 ID、乱序 set、digest 自引用 |
| 状态 | 每条合法 edge；每个非法跨越/倒退；每个 terminal 吸收；fact-aware command 不能被 generic transition 绕过 |
| command | authority-before-replay、expected-version precedence、exact replay/异 digest、双重提交、响应丢失 |
| retry/unknown | latest Attempt 四类；每个 retry/no-retry 分支；chain-tail/superseded adjudication；risk approval；review SLA 只告警 |
| cancel/Evidence | §5 全部双向顺序，另加 crash-before/after commit 与 outbox 重投 |
| Batch policy | §6 每行、边界 budget、unknown lineage、authorized retry pass、missing Evidence、zero/漂移 denominator、未匹配 fail closed |
| UoW/reconcile | 每个原子写点故障；stale Run/Batch CAS；外部 I/O 不在事务；duplicate/out-of-order outbox；repair scan 有界 |
| migration | 可证明 backfill、不可证明 quarantine、mixed legacy/v1 reader、activation gate、post-activation forward-only rollback |

项目测试必须在 `docker-compose.dev.yml` 的容器中运行。本文 docs-only 切片没有运行任何测试；
后续 Evidence 必须记录实际命令、返回码、通过数、覆盖率、JUnit 与 SHA-256，不能用口头结论代替。

## 11. 实施解锁与剩余风险

八项决议已关闭，只解除设计选择 blocker。实现仍必须按依赖推进：

1. `001F` 先形成 Batch vocabulary、rejection/cancel command 的 RED；
2. `001G` 开工前先关闭下表 `STATE-DEC-009/010`，再在 canonical vocabulary 上实现 Run 分层
   模型、per-item resolution 与竞态；
3. `001H` 依赖前两者的 immutable facts 及 `STATE-DEC-009/010`，再完成 Batch policy、fanout、
   basis 和生产 UoW；
4. 每条子契约独立获得 Evidence 后，才能重新评估 `T-M0-STATE-001` 是否完整；
5. API、DB、Worker、migration 和生产并发仍各自需要对应实施/发布门禁。

### 11.1 新发现且未签署的后续决议

下列两项是在八项签署后通过对抗复核发现的下游歧义，不追溯修改 `STATE-DEC-001`～`008` 的
签署结果，也不属于本文件 `DECIDED` 状态的已批准范围：

| Decision ID | 状态 | 必须回答 | 当前 proposed baseline（未批准） | 阻断 |
|---|---|---|---|---|
| `STATE-DEC-009` | `PROPOSED/UNSIGNED` | 多 item Run 是整 Run retry、失败 item/atomic-group retry，还是由 Suite policy 选择；original/effective precedence、unknown 污染范围与 mixed RunOutcome 如何定义 | A：完整 immutable Run item set 重跑；每 item original 永不改写，effective 取最后一个获授权完整 Attempt；unknown lineage sticky；RunOutcome 不参与 Batch 计数 | `001G`、`001H` |
| `STATE-DEC-010` | `PROPOSED/UNSIGNED` | test/infra retry 的次数、reason/scope、预算和批准角色；unknown review SLA；duplicate-risk authority、scope、有效期、single-use 与职责分离 | A：Suite/platform policy 分离、默认 no-retry、最小权限；unknown 永不自动 retry；duplicate-risk 精确绑定单次 Run/Attempt/fence/item-set/SUT | `001G`、`001H` |

§4.5 的具体 policy/role 字段及 §6.1 的 retry scope/effective selection 只是上述 proposed baseline 的
字段完整性草案；不得因它们出现在本契约中就视为签署。`001F` 不依赖这两项，可在既有授权边界
内继续；任何 001G/001H 实现必须先新建决议包、具名签署并记录 UTC。

### 11.2 其他剩余风险

当前主要剩余风险是：现有 Run 领域模型把 cancelled 混在 phase；生产数据库 UoW 尚未证明；
late-Evidence completion path 尚未实现；历史 policy/basis 可能无法自动 backfill；Batch cancel fanout
和跨 Run reconciler 仍只有契约。任何一项未验证都不得以“八项已 DECIDED”为由宣称生产就绪。
