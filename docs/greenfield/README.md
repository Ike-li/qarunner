# 测试执行平台绿地设计文档集

> 文档集状态：草稿，待产品、研发、QA、安全、运维联合评审<br>
> 创建日期：2026-07-12<br>
> 适用范围：独立产品定义，不以 qarunner 现有代码、数据库或部署方式为设计前提

## 1. 文档目的

本目录回答十个按顺序展开的问题：

1. 只有一台 ECS 时，最小可用产品必须解决什么问题？
2. 面向 10 万+用例库存和每日约 6 万次执行时，企业目标产品必须具备什么能力？
3. 从单机 MVP 演进到企业目标，系统边界和技术架构应如何设计？
4. 核心领域模型、状态机、协议、调度和安全控制应如何实现？
5. 在上述设计冻结后，现有 qarunner 代码中哪些应复用、改造或替换？
6. 如何按 TDD、迁移门禁和回滚边界分阶段实施，而不形成长期双轨？
7. 如何证明每条需求都有设计、实现阶段、测试和发布证据落点？
8. 在继续完整状态模型实现前，五方必须逐项关闭哪些冲突和不确定性？
9. 八项决议关闭后，状态、命令、聚合、持久化和迁移必须遵守哪份实现契约？
10. 001F 纯领域/Fake 子切片取证后，生产 authority、proof、UoW、API、恢复和授权边界如何关闭？

## 2. 文档顺序与约束

| 顺序 | 文档 | 类型 | 设计输入 |
|---:|---|---|---|
| 0 | [输入基线](00_INPUT_BASELINE.md) | 约束/假设 | 仅使用用户明确给出的业务事实 |
| 1 | [单 ECS MVP 需求规格](01_MVP_REQUIREMENTS.md) | PRD | 输入基线，不读取现有实现 |
| 2 | [企业级测试执行平台需求规格](02_ENTERPRISE_REQUIREMENTS.md) | PRD | 输入基线与已批准的 MVP 边界，不读取现有实现 |
| 3 | [演进式架构设计](03_ARCHITECTURE_DESIGN.md) | Architecture | 两份已冻结需求 |
| 4 | [详细设计](04_DETAILED_DESIGN.md) | Design Reference | 已冻结需求和架构决策 |
| 5 | [现有代码对比与最优解评估](05_CURRENT_STATE_COMPARISON.md) | Assessment | 前四份文档冻结后才读取现有代码 |
| 6 | [实施计划](06_IMPLEMENTATION_PLAN.md) | Execution Plan | 已冻结设计、现状差距和迁移决策 |
| 7 | [需求追踪矩阵](07_REQUIREMENTS_TRACEABILITY.md) | RTM | 需求、设计、实施、测试和发布证据 |
| 8 | [实施状态账本](08_IMPLEMENTATION_STATUS.md) | Execution Ledger | 当前切片、commit、测试证据、风险和下一步 |
| 9 | [M0 状态模型五方决议包](09_STATE_MODEL_DECISION_PACKET.md) | Implementation Decision Reference | PRD/DD/当前实现与测试的冲突、八项 implementation-level 决议、依赖与实施边界；不新增上游需求 |
| 10 | [M0 状态模型实现契约](10_STATE_MODEL_CONTRACT.md) | Implementation Contract Reference | 五方获批的 `B/A+X/B/C/A/B/A/B` 组合、对应实现合同，以及未签的 `STATE-DEC-009/010` 后续决策门 |
| 11 | [001F 生产边界评审与授权包](11_BATCH_PREEXECUTION_PRODUCTION_BOUNDARY_REVIEW.md) | Implementation Boundary Decision Reference | 001F 生产 authority/proof/UoW/API/recovery 的九项已签 `DECIDED` implementation-boundary 决议、Evidence 门禁，以及与 001G/001H 的 fail-closed handoff 边界；签署不等于实施或生产授权 |
| 执行入口 | [长期 Goal Prompt](GOAL_PROMPT.md) | Codex Goal | 读取后持续执行 M0～M8 |

## 3. “独立设计”的含义

- 需求不因现有代码已经实现或尚未实现某项能力而增删。
- 架构不因现有框架、数据库和类名而预设答案。
- 需求阶段只描述业务结果、约束和可验收行为，不把 FastAPI、SQLite、Docker、Kubernetes 等技术写成产品需求。
- 技术选型在架构阶段依据规模、信任模型、运维能力和演进成本决定。
- 现状对比阶段允许得出“保留现有方案”或“替换现有方案”的结论，但必须给出证据、代价和触发条件。

## 4. 决策状态

本目录中的数值分为三类：

- **已确认事实**：来自用户明确输入，不得擅自改变。
- **建议基线**：为使文档可评审而给出的初始目标，需负责人批准。
- **待实测参数**：无法从用例数量推导，必须通过代表性负载基线测量后确定。

任何标为 `TBD-*` 的事项都不是隐含承诺。对应负责人关闭前，相关容量或 SLO 不得宣称已经满足。

## 5. 统一产品定位

> 本产品是单团队内部测试执行与资源编排平台。它把不可直接信任的 pytest、Playwright 及其依赖放入受控的一次性执行环境，在真实被测环境的授权边界内，以可解释的分片和资源调度完成测试，并提供完整、可追溯的结果证据。

它不是测试用例编写工具、通用 CI/CD 平台、生产流量压测平台或面向公网的多租户 SaaS。

## 6. 当前执行摘要

- MVP PRD V0.3.0 已补充 AC-MVP-026～032，并同步八项已批准状态模型语义；7 条“缺少独立
  正式 AC”的结构缺口已关闭。
- 上述新增 AC 与全部上游规范仍为 `DRAFT`；这些 AC 未获得五方批准，也未产生对应实现/验收
  Evidence。
- `STATE-DEC-001`～`STATE-DEC-008` 的五方 implementation-level 决议已关闭，获批组合与实现
  边界见[M0 状态模型实现契约](10_STATE_MODEL_CONTRACT.md)；这不表示 `T-M0-STATE-001`、Schema、
  API、数据库或生产实现已经完成。
- `STATE-DEC-009/010` 是签署后发现的下游歧义，当前均为 `PROPOSED/UNSIGNED`。只有
  `T-M0-STATE-001F` 的 M0 contract 状态升级为 `VERIFIED` 后，才可另建决议包具名签署并进入
  001G；001H 还必须等待 001G immutable Run facts。签署前不得实现其 proposed 答案。
- 001F 纯领域/Fake 子切片状态为 `VERIFIED`，对应
  `EV-M0-BATCH-PREEXECUTION-DOMAIN-001F` Evidence 已记录并可定位；第 11 号文档中的九项
  `STATE-001F-PROD-DEC-001～009` Option A 已由用户明确授权的 `Ike-li` 代表
  PROD/DEV/QA/SEC/OPS 具名签署并全部转为 `DECIDED`，签署记录、UTC 与适用 baseline 见该文档
  §12.2。这只关闭 implementation-boundary 决策，不把 `T-M0-STATE-001F` 升级为 `VERIFIED`。
- 001F 整体仍为 `IN_PROGRESS`：M0 相关七项决议的决策前置已关闭，但
  `GATE-IMP-001/002`、窄实施授权和第 11 号文档 §10.3 的四包 M0 contract Evidence 尚未关闭。
  001F 的五包 M1 PG concurrency/proof integration/fault recovery/migration/capacity Evidence 与
  一包 M2 真实 API/RBAC Evidence 不反向阻塞 M0 contract completion，也尚未形成。
- 状态模型合同 §10 对 001G/001H 的 umbrella 完成门跨越 M0～M2：M0 先关闭 contract slices 并
  基线化接口后才能进入 M1；真实 persistence/API/crash/real-DB/E2E 由 001G/001H 各自的独立
  Evidence 关闭，不能复用 001F 六包。Production activation 仍等待上述全部 Evidence、M8 与独立
  五方 `GO`，当前保持 `NO-GO`。
- 后续阶段、当前验证证据和阻断项只以[实施状态账本](08_IMPLEMENTATION_STATUS.md)为事实入口。
