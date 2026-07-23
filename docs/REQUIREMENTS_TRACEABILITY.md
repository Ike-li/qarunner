# qarunner 需求追踪与发布证据矩阵

> 文档类型：Requirements Traceability Matrix（RTM）
> 文档编号：QARUNNER-RTM-001
> 版本：V1.2.1
> 状态：评审中
> 更新日期：2026-07-12

## 0. 目的与边界

本文把产品需求映射到系统、安全、运维、验证和发布证据，回答以下问题：

1. 每条产品需求由哪些可实现契约承接。
2. 哪组冻结的验收用例构成通过或失败的分母。
3. 需要保存什么证据、由谁复核、何时才算关闭。
4. 当前实现差距阻断了哪些需求。

本文不声明任何需求已经实现。没有候选版本证据记录和五方适用签署时，需求最多只能标记为 `mapped`，不得标记为 `verified` 或 `accepted`。Worker 相关需求的协议级设计基线见
[Worker 协议参考规范](WORKER_PROTOCOL.md)。

逻辑验收目录、稳定 family/case ID、适用性和样本套件候选见 [发布验收目录](RELEASE_GATE_CATALOG.md)。

## 1. 两类证据不得混淆

| 证据 | 粒度 | 证明内容 | 不证明内容 |
|---|---|---|---|
| Evidence Manifest | 单个 Run | 平台收集后的运行证据未被无声修改，缺失、截断、删除和损坏状态可识别 | 测试逻辑、断言或测试输出本身真实正确 |
| Release Evidence Bundle | 一个候选发布版本 | 需求、设计、代码、测试、镜像、配置、环境、结果、例外和签署形成可追踪发布依据 | 候选版本之外的未来运行一定安全或正确 |

## 2. 需求状态模型

| 状态 | 定义 | 是否可关闭发布门禁 |
|---|---|---|
| draft | 需求尚未完成基线评审；可以具有待批准的映射草案，但不能视为已确认契约 | 否 |
| mapped | 需求基线已完成适用评审，且设计责任、验证目录和当前阻断项已获确认 | 否 |
| implemented | 代码、配置、迁移和运维材料已完成，但证据尚未复核 | 否 |
| verified | 冻结目录中的全部适用用例通过，证据完整且来自当前候选版本 | 否 |
| accepted | 指定验收角色已签署，且不存在未批准的阻断风险 | 是 |
| blocked | 存在实现差距、失败证据、过期证据或未批准例外 | 否 |

状态按 `draft → mapped → implemented → verified → accepted` 推进；任一新相关变更、证据过期、测试失败或风险失效都必须退回 `implemented` 或 `blocked`。

## 3. 共同验收规则

- 每个候选版本必须冻结 `release_gate_catalog_version`，其中列出 [发布验收目录](RELEASE_GATE_CATALOG.md) 中全部适用 family/case、启用状态和适用原因。
- 分母是冻结目录中对该候选版本适用且 enabled 的全部用例；`skipped`、`not-run`、缺失结果和未批准的 `not-applicable` 均按未通过处理。
- 证据必须来自最后一次影响该需求的变更之后，并绑定候选 commit、控制面/执行镜像 digest、数据库 schema、生产配置 digest、策略/参数基线版本和测试环境标识。
- 同一证据可以支持多条需求，但每条需求必须独立记录适用性、结果和验收人。
- 例外必须记录 risk/exception ID、范围、补偿控制、责任人、到期日和批准人；过期后自动使相关需求变为 `blocked`。
- 逻辑验收目录名称是稳定追踪键，不代表当前仓库已经存在同名测试文件；RC family/case ID 已在发布验收目录登记，五方批准 `1.0.0` 后冻结。`planned-blocking` 不因 OI-022 关闭而自动变为通过。
- SYS-NFR-019 与 OPS-036 适用于全部 PRD；Security Parameter Baseline、Operational Threshold Catalog 或 Release Evidence Bundle 缺失时，相关 P0 均不得标记为 `accepted`。
- GAP-010 是全部 P0/P1 PRD 的全局 blocker，直到 release gate catalog 和逐需求 Release Evidence Bundle 建立；P2 只有在纳入候选发布时适用。为避免重复，不在每一行“当前阻断”列重复书写。

## 4. 单条需求关闭记录

每条 P0/P1 需求至少保存：

- requirement_id 和 requirement_version。
- status。
- design_ref、code_ref、migration_ref 和 runbook_ref（适用时）。
- test_catalog_version、test_case_id 和执行命令。
- candidate commit、build ID、控制面/Worker agent/阶段/executor image digest、schema version 和配置 digest。
- worker_id、Worker identity version、agent_version、host_baseline_digest、assignment/job digest 和 fencing token（执行相关要求适用时）。
- Worker 协议相关要求还必须记录 worker_generation、start_commit_id/task_attempt_id、executor 的 execution_attempt_id、最后接受的 event sequence、reconcile 结果和 upload/Manifest root digest（适用时）。
- 测试环境、Target Environment、Network Policy/Grant/Lease 信息（适用时）。
- result、artifact_ref、日志/报告摘要和执行时间。
- exception/risk_acceptance_ref（适用时）。
- implementer、reviewer、accepted_by 和 accepted_at。

模块级“已有测试”或旧版本通过记录不能替代上述记录。

## 5. PRD 端到端追踪矩阵

PRD-001、003、005、006、010、020、021 的 `design_ref` 必须引用
[WORKER_PROTOCOL.md](WORKER_PROTOCOL.md) 中对应的身份、commit-start、fencing、恢复、上传或主机边界章节；仅引用抽象架构图不能进入 `implemented`。

| PRD | 系统契约 | 安全/运维契约 | 冻结验收目录 | 主验收角色 | 当前阻断 |
|---|---|---|---|---|---|
| PRD-001 | SYS-INV-019～022；SYS-FR-002、019、021、030～035；SYS-NFR-009、017、020～022 | SEC-016～022、068～073、088～095 | `suite-acceptance-matrix`、`source-acquisition-matrix`、`worker-protocol-fault-matrix` | 产品、研发、QA | GAP-001、005、014、021；SOR-GAP-023 |
| PRD-002 | SYS-INV-001、014；SYS-FR-001～006、019；SYS-NFR-003 | SEC-039、049、078～080 | `provenance-matrix` | 研发、QA | GAP-001、012、017 |
| PRD-003 | SYS-INV-012、019～022；SYS-FR-016、017、021、027、030～035；SYS-NFR-011、017～018、020～022 | SEC-010～015、045～057、062～073、087～095；OPS-006～009、031、037～041 | `sandbox-isolation-matrix`、`worker-isolation-matrix`、`worker-protocol-fault-matrix` | 研发、安全、运维 | GAP-012～014、018、021；SOR-GAP-022～023 |
| PRD-004 | SYS-FR-005、014～015、022、025；SYS-NFR-005、010、013 | SEC-023～028、038～044、058～061、074～077、086；OPS-030、032～033 | `approved-target-network-matrix` | 产品、QA、安全、运维 | GAP-003、011、015；SOR-GAP-022 |
| PRD-005 | SYS-INV-002、004、016、019～022；SYS-FR-031、034～035；SYSTEM §3；SYS-NFR-001～002、012～014、020、022 | SEC-041、074～083、088～095；OPS-010～017、032～033、038～041 | `run-state-fault-matrix`、`worker-protocol-fault-matrix` | 研发、QA、运维 | GAP-008、015～016、019、021；SOR-GAP-023 |
| PRD-006 | SYS-INV-005～006、012、017、021；SYS-FR-013、017、024、034；SYS-NFR-006、011、015、020 | SEC-006～009、052～061、078～080、091；OPS-019～020、029、034 | `evidence-access-integrity-matrix`、`worker-protocol-fault-matrix` | QA、安全 | GAP-013、017、021；SOR-GAP-023 |
| PRD-007 | SYS-INV-003、005、017；SYS-FR-006～009、024；SYS-NFR-004、015 | SYSTEM §5 | `comparison-golden-matrix` | 产品、QA | GAP-002、009、017 |
| PRD-008 | SYS-INV-008、013、018；SYS-FR-010～011、018、023、026；SYS-NFR-014、016 | OPS-010～013、030、032 | `schedule-recovery-matrix` | 产品、研发、QA、运维 | GAP-008、016、019 |
| PRD-009 | SYS-INV-006；SYSTEM §9.2 | SEC-001～009、029～037；SECURITY §7 | `owner-scope-audit-matrix` | QA、安全 | GAP-004；SOR-GAP-011 |
| PRD-010 | SYS-INV-019～022；SYS-FR-012、026～027、030～035；SYS-NFR-002、008、016、018、020～022 | SEC-062～067、088～095；OPS-001～009、014～041 | `deployment-recovery-drill`、`worker-rebuild-drill`、`worker-protocol-fault-matrix` | 研发、安全、运维 | GAP-006～007、018～019、021；SOR-GAP-019、023 |
| PRD-011 | SYS-FR-013、028；SYSTEM §3.3、§9.4 | SEC-052～057 | `failure-triage-journey` | 产品、研发、QA | 待逐条候选版本证据 |
| PRD-012 | SYS-INV-006；SYS-FR-029；SYSTEM §9.2 | SEC-001～009、052～056 | `web-api-contract-matrix` | 产品、研发、QA | GAP-020；待冻结 API 契约目录 |
| PRD-013 | SYS-INV-009；SYSTEM §9.3 | SECURITY §6；OPS-017 | `extension-failure-isolation-matrix` | 产品、研发、QA | 待逐条候选版本证据 |
| PRD-014 | SYS-INV-011；SYS-FR-014～015、020；SYS-NFR-010、012 | SEC-038～044；OPS-030、032 | `grant-lifecycle-race-matrix` | 产品、安全、运维 | GAP-011 |
| PRD-015 | SYS-INV-012、014；SYS-FR-016 | SEC-045～051；OPS-001～009 | `dependency-preparation-matrix` | 研发、安全、运维 | GAP-012 |
| PRD-016 | SYS-INV-012；SYS-FR-017、024；SYS-NFR-011、015 | SEC-052～057 | `active-content-security-matrix` | 研发、QA、安全 | GAP-013 |
| PRD-017 | SYS-INV-016；SYS-FR-022、025、028；SYS-NFR-013 | SEC-074～077；OPS-033 | `target-lease-fault-matrix` | QA、安全、运维 | GAP-015 |
| PRD-018 | SYS-INV-015；SYS-FR-023；SYS-NFR-014 | SEC-081 | `idempotency-matrix` | 研发、QA | GAP-016 |
| PRD-019 | SYS-INV-017；SYS-FR-024；SYS-NFR-015 | SEC-078～080；OPS-034 | `manifest-integrity-matrix` | QA、安全、运维 | GAP-017 |
| PRD-020 | SYS-INV-014、019～022；SYS-FR-019、021、030～034；SYS-NFR-017、020～021 | SEC-016～022、068～073、088～095 | `source-acquisition-matrix`、`worker-isolation-matrix` | 研发、安全、运维 | GAP-005、014、021；SOR-GAP-023 |
| PRD-021 | SYS-INV-019～022；SYS-FR-027、030～035；SYS-NFR-018、020～022 | SEC-062～067、088～095；OPS-031、037～041 | `host-security-baseline`、`worker-isolation-matrix`、`worker-rebuild-drill` | 研发、安全、运维 | GAP-018、021；SOR-GAP-019、023 |

当前 21 条 PRD 均为 `draft`，本矩阵只提供待五方批准的 mapping proposal。完成需求基线评审后才能进入 `mapped`；在上述 Gap 关闭并生成候选版本证据前，不得进入 `verified` 或 `accepted`。

## 6. 未决决策与基线门禁

- OI-018 与 OI-021 的架构决策已于 2026-07-12 由 DEC-010、DEC-013～016 关闭；OI-018 关闭不等于发布风险接受，SEC-067/PRD-021 要求的安全与运维签署仍是 accepted/GA 门禁。OI-022 已形成 `release-gate-catalog@0.1.0-rc2` 候选，待五方批准 family/case、适用性、owner 和样本契约并发布 `1.0.0` 后关闭；未实现 case 继续作为发布 blocker。
- OI-001～003、OI-009～017、OI-019～020、OI-023 影响 Phase 1 设计或安全边界，进入对应实现切片前必须有批准决定；未关闭时维持文档定义的 fail-closed 默认行为。
- OI-004～008、OI-024 按各自关闭时点处理，但不得在缺少批准值时把相关需求标记为 `verified`。
- 任何 OI 延期必须记录影响需求、临时默认、责任人和新截止日期，不能只修改日期。

## 7. 五方签署

“五方”表示五类职责视角，不强制由五个不同自然人承担。单团队中同一人兼任多个角色时，Release Evidence Bundle 必须分别记录其签署角色，并显式记录职责集中带来的复核独立性残余风险。

| 角色 | 签署范围 | 当前状态 |
|---|---|---|
| 产品负责人 | 用户价值、范围、优先级、KPI 分母、例外业务影响 | 待评审 |
| 技术负责人 | 领域模型、接口、迁移、失败语义、实现与代码证据 | 待评审 |
| QA 负责人 | 验收目录、用例适用性、执行证据和回归结论 | 待评审 |
| 安全负责人 | 威胁模型、安全目录、风险例外和残余风险 | 待评审 |
| 运维负责人 | 生产配置、阈值、监控、备份恢复、Runbook 和演练 | 待评审 |

只有适用于候选版本的五方签署全部完成，Release Evidence Bundle 才能标记为 approved，相关需求和候选发布才能进入 `accepted`。
