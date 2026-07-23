# qarunner 产品需求文档

> 文档类型：PRD
> 文档编号：QARUNNER-PRD-001
> 版本：V7.4.1
> 状态：评审中
> 密级：内部
> 更新日期：2026-07-12

---

## 0. 文档控制

### 0.1 文档职责

本文定义 qarunner 为什么存在、为谁服务、必须交付什么用户结果、如何判断产品成功，以及哪些能力属于核心、扩展或明确范围外。

本文不重复详细状态机、数据库字段、网络实现和生产控制。对应规范见：

- [产品方向](DIRECTION.md)
- [系统需求规格](SYSTEM_REQUIREMENTS.md)
- [安全与运维需求](SECURITY_OPERATIONS_REQUIREMENTS.md)
- [需求追踪与发布证据矩阵](REQUIREMENTS_TRACEABILITY.md)
- [发布验收目录](RELEASE_GATE_CATALOG.md)
- [当前功能总览](FEATURES.md)
- [当前 API 参考](API_REFERENCE.md)
- [当前架构](../ARCHITECTURE.md)

### 0.2 版本与基线

| 版本 | 状态 | 说明 |
|---|---|---|
| V5.0 | 历史基线 | 按现有能力组织目标、功能和验收 |
| V6.0.0 | 未基线化 | 按企业模板扩展为单体 PRD/SRS；结构完整但混合产品、实现与治理 |
| V7.0.0 | 评审草案 | 从第一性原理重建产品目标、信任模型、成功指标与核心需求；详细系统和安全运维规范拆分 |
| V7.1.0 | 评审草案 | 补齐真实 SUT 的不可变访问授权、依赖准备沙箱和不可信报告/产物隔离 |
| V7.2.0 | 评审中 | 增加源码获取沙箱、环境租约、幂等触发、证据清单、时钟与 Docker 隔离适用边界 |
| V7.3.0 | 评审中 | 增加端到端需求追踪、冻结验收分母、证据新鲜度、发布证据包和参数基线关闭规则 |
| V7.4.0 | 评审中 | 确认单控制面 + 单专用 Worker 架构；每 Run 使用全新一次性容器，控制面不持有 Docker socket |
| V7.4.1 | 评审中 | 增加 OI-022 发布验收目录 RC，冻结候选 family/case 身份、适用性、样本和现有证据边界 |

V7.4.1 只有在产品、研发、QA、安全和运维完成评审后才能建立新基线。

### 0.3 已确认决策

| 决策 ID | 决策 |
|---|---|
| DEC-001 | 部署限定为受信内网、单团队使用，不直接暴露公网 |
| DEC-002 | 管理员和运维可信；普通用户半可信；测试代码及依赖不可信 |
| DEC-003 | pytest 和 Playwright 均在 Docker 执行环境运行 |
| DEC-004 | Playwright 必须支持真实被测环境，但只能通过管理员批准的网络策略访问 |
| DEC-005 | 默认网络模式为 isolated；真实 E2E 使用 approved-target |
| DEC-006 | 可信回归而非单纯执行测试，是产品差异化内核 |
| DEC-007 | AI、通知、本地目录接入和主题等扩展不得成为核心链路依赖 |
| DEC-008 | Network Policy 只定义可达范围，不构成真实 SUT 执行授权；访问必须另有管理员签发的 Target Access Grant |
| DEC-009 | 依赖准备过程、测试报告、日志、trace 和其他产物均按不可信内容处理 |
| DEC-010 | 每 Run 容器共享 Worker 主机内核，不视为高保证恶意代码隔离；V7 接受专用加固 Worker 主机上的残余风险 |
| DEC-011 | 真实 SUT 运行必须获得原子 Environment Lease，并遵守并发、命名空间、readiness 和 cleanup 策略 |
| DEC-012 | 平台保证执行来源与证据完整性，不保证测试逻辑或测试作者结论本身正确 |
| DEC-013 | V7 生产拓扑固定为一个控制面实例和一台独立的专用加固 Worker 主机；控制面不挂载或远程访问 Docker socket/daemon |
| DEC-014 | Source Acquisition、依赖准备、测试执行和报告生成均由 Worker 在各自受限容器中完成；每个 Run 创建全新 executor 容器且不得复用 |
| DEC-015 | Worker agent 是受信执行控制组件，只通过受认证协议领取冻结任务和上传证据；测试容器不能访问 Worker agent、Docker socket、控制面或其他 Run |
| DEC-016 | V7 不提供多 Worker、自动扩缩容或故障自动切换；公网、多租户、生产 SUT 或高对抗代码需要每 Run VM/microVM 并重新评审 |

### 0.4 评审

| 角色 | 必须确认的内容 | 状态 |
|---|---|---|
| 产品负责人 | 用户、价值、范围、优先级、成功指标 | 待评审 |
| 技术负责人 | provenance、Grant、依赖快照、网络模式、状态和接口可实现性 | 待评审 |
| QA 负责人 | 比较口径、验收模型、测试证据与发布门禁 | 待评审 |
| 安全负责人 | 信任边界、目标授权、供应链、主动内容、密钥、审计和残余风险 | 待评审 |
| 运维负责人 | 配置、健康、调度、备份恢复、容量和支持 | 待评审 |

---

## 1. Executive Summary

### 1.1 Problem Statement

目标团队已经拥有 pytest 或 Playwright 自动化套件，但执行入口、配置和证据分散，定时回归依赖零散脚本，历史运行又缺少足够的来源信息，导致团队无法可靠回答“这次相对上次到底发生了什么变化”。

测试代码和第三方依赖具有执行任意代码的能力；真实 Playwright E2E 又必须访问被测环境。仅配置目标白名单仍不足以阻止普通用户把另一份代码、另一版 Profile 或另一组密钥带入该网络范围；测试产生的 HTML 报告和 trace 也可能成为第二条主动内容攻击路径。团队需要的是一个同时满足可复现、可比较、可解释、显式执行授权和受控内容交付的回归运行服务，而不是另一个只会启动测试进程的界面。

### 1.2 Proposed Solution

qarunner 提供一个面向受信内网单团队的自托管控制面和一台独立的专用加固 Worker 主机：

1. 控制面负责认证、授权、持久化、调度和证据索引，不挂载 Docker socket，也不执行测试代码、Git 处理、依赖安装或报告生成。
2. Worker agent 通过受认证协议领取冻结任务，并在 Worker 本机的独立 Source Acquisition 容器获取代码，形成不可变 Suite Revision。
3. Worker 在独立依赖准备容器生成不可变 Dependency Snapshot。
4. 每个 Run 创建全新的 executor 容器，按 isolated 或 approved-target 网络策略执行，终态后销毁且不得复用。
5. 真实 SUT 访问必须同时满足 Target Access Grant，并原子获得符合并发、命名空间和清理策略的 Environment Lease。
6. Worker 在独立受限环境收集/生成结果和报告，通过可校验上传协议交付控制面；控制面冻结 Evidence Manifest 并隔离主动内容。
7. 只有在来源、配置、Worker、环境和网络策略满足等价规则时才生成回归 diff。
8. 将 Worker 不可用、任务认领冲突、失败、漏跑、授权/环境阻断和证据损坏明确暴露给用户。

### 1.3 Success Criteria

以下指标用于判断 qarunner 本身是否成功，不用于评价被测系统质量。

发布指标共同遵循 [需求追踪与发布证据矩阵](REQUIREMENTS_TRACEABILITY.md) 的冻结分母和证据规则：候选版本必须固定 `release_gate_catalog_version`；所有适用且 enabled 的用例构成分母；`skipped`、`not-run`、缺失结果和未批准例外均按未通过处理；证据必须来自最后一次相关变更后的候选 commit、镜像、配置和标准环境。

| KPI ID | 指标 | 目标 | 计算与证据 | Owner |
|---|---|---:|---|---|
| KPI-001 | 核心旅程发布通过率 | 100% | `core-journey-catalog@version` 中通过的适用用例数 / 全部适用用例数；覆盖源码获取、依赖准备、pytest、Grant/Lease 与真实 Playwright、幂等调度、证据、diff、权限和恢复 | QA |
| KPI-002 | Run provenance 完整率 | 100% | 分母为候选版本验证窗口内所有成功创建、`schema_version >= V7` 的非 legacy Run；分子为全部必填 provenance 在创建事务中冻结且启动前 digest/version 校验通过的 Run | 研发、QA |
| KPI-003 | 回归分类正确率 | 100% | `comparison-golden-matrix@version` 中基线选择、comparison level 和分类均符合预期的适用用例数 / 全部适用用例数 | QA |
| KPI-004 | 安全边界验收通过率 | 100% | `security-boundary-catalog@version` 中通过的适用用例数 / 全部适用用例数；正向目标必须可达，未批准网络、失配授权、他人资源、宿主控制面、会话凭证和敏感数据访问必须被拒绝 | 安全、QA |
| KPI-005 | 信号消费与处置率 | Beta 建立基线后批准目标 | 分别记录滚动 7 天周活跃触发人数、失败后 24 小时内重跑/处置率和失败证据打开率；不得合并成单一百分比 | 产品 |
| KPI-006 | Evidence Manifest 完整与校验率 | 三项均为 100% | 终态 Manifest 生成率＝已 finalized Manifest 的终态 Run / 全部终态 Run；证据状态完整率＝每类证据均记录 present/truncated/missing/deleted 的 Run / 全部终态 Run；校验检测率＝正确识别完整/篡改/缺失/恢复损坏的适用用例 / 全部适用用例 | QA、安全 |

以下指标只作为测试资产健康度观察，不作为 qarunner 产品成功 KPI：

- 被测套件通过率。
- Flaky 用例绝对数量。
- 用户测试本身的总运行时长。
- 代码覆盖率。

平台自身的排队、启动、结果归档和 API 延迟目标由系统需求定义。

---

## 2. User Experience & Functionality

### 2.1 User Personas

#### 主要用户：质量负责人 / 测试开发

- 已维护 pytest 或 Playwright 套件。
- 需要每日或按需回归。
- 核心任务是判断失败是否新增、持续、已修复或不可比较。
- 对日志、报告、截图、trace 和历史结果有强依赖。

#### 次要用户：开发人员

- 在提交、发布或排障前消费回归信号。
- 需要快速触发既有口径、查看证据和重跑。
- 不应被要求理解平台内部 Docker、数据库或调度实现。

#### 管理用户：管理员 / 运维

- 管理账号、密钥、Target Access Grant、网络/Registry 策略、被测环境、执行镜像和平台配置。
- 负责备份、恢复、容量、健康与事件处置。
- 对全局资源拥有高权限并承担审计责任。

### 2.2 Jobs to Be Done

1. 当我准备提交或发布改动时，我希望运行一个已知口径的回归，并得到来源明确、证据完整的结果。
2. 当每日回归到点时，我希望系统可靠触发；如果没有触发，我希望明确知道原因和影响。
3. 当测试失败时，我希望判断它是新增问题、持续问题、已修复问题、覆盖变化还是不可比较，而不是手工翻阅多个报告。
4. 当 Playwright 访问真实被测环境时，我希望它只能访问批准目标，不能把测试代码变成任意内网探测工具。
5. 当平台或外部依赖故障时，我希望 Run 收敛到明确状态，历史证据不被伪装成成功。
6. 当套件代码、依赖、Profile 或密钥发生变化时，我希望真实环境访问授权自动失效，而不是让新内容继承旧批准。
7. 当 Worker 不可用、失联或任务执行中断时，我希望控制面不在本机降级执行测试，而是保留任务来源并形成明确等待或终态。

### 2.3 User Stories and Acceptance Criteria

#### US-001：接入并首次运行

作为质量负责人，我希望接入现有 Git 套件并获得首次结果，以便确认平台可以承载该套件。

验收：

- 不修改 qarunner 代码即可完成接入。
- 私有仓库使用密钥引用，不在 URL、日志或响应中出现明文。
- clone/fetch、ref 解析和工作区物化在不持有 Docker socket/控制面秘密的 Source Acquisition 沙箱中执行。
- Source Acquisition、依赖准备和测试执行均发生在专用 Worker 主机；控制面不执行或回退执行这些步骤。
- 未批准的 Git host、submodule、LFS/filter、hook 或特殊文件策略被拒绝。
- 依赖准备不在持有 Docker socket 或控制面秘密的进程中执行。
- 依赖准备只访问批准的软件源并产出与 Suite revision 绑定的依赖快照。
- Run 执行不可变 Suite Revision 快照；排队期间 pull 或工作目录变化不能改变已创建 Run 的代码。
- Run 保存套件来源版本和执行配置版本。
- 失败时明确区分接入失败、依赖准备失败、执行失败和测试失败。

#### US-002：运行真实 Playwright 回归

作为测试开发，我希望 Playwright 访问管理员批准的真实被测环境，以便验证业务端到端流程。

验收：

- 默认 isolated 模式不能访问任何外部目标。
- approved-target 模式只能由管理员批准和分配。
- Network Policy 本身不能授权运行；Run 必须引用状态有效的 Target Access Grant。
- Grant 至少绑定 Suite Revision/content digest、Profile revision 或手工请求口径、依赖快照、Target Environment、Network Policy version 和 secret ref version。
- 任一绑定项变化、Grant 到期或被吊销后，旧 Grant 不得继续创建或启动 Run。
- 允许的被测环境可以访问，未批准目标、平台控制面和元数据地址不可访问。
- executor 启动前原子获得 Environment Lease；超过目标并发、readiness 失败或无法分配命名空间时不得执行测试。
- Run 保存目标环境标识和网络策略版本。
- 定时触发因授权无效而未创建 Run 时，必须形成可查询的 blocked-policy 记录。
- 网络策略不同的 Run 默认不可比较。

#### US-003：获得可信回归判断

作为质量负责人，我希望系统只比较真正等价的两次运行，以便相信新增失败和已修复结论。

验收：

- 缺少 provenance 的 Run 不进入自动基线选择。
- 套件版本、Profile 版本、依赖快照、secret ref version、选择参数、目标环境、网络策略或执行镜像不满足等价规则时显示“无可比基线”。
- failed/error → passed 才能标记为已修复。
- failed/error → skipped 不能标记为已修复。
- 跳过状态变化必须单独显示为覆盖变化。

#### US-004：按时运行并发现漏跑

作为测试负责人，我希望每日调度可靠执行，并能发现停机或故障造成的漏跑。

验收：

- 同一 Schedule 同一触发点最多创建一个 Run。
- 每个 enabled fire point 必须在批准的 `schedule_fire_lag_s` 内创建 Run 或写入唯一明确结果；超过该阈值即记录 missed，阈值缺失时 scheduler readiness 失败。
- 同一用户、接口和 Idempotency-Key 的相同请求只能创建一个 Run；同 key 不同请求必须冲突拒绝。
- 服务不可用期间的错过触发点必须产生可查询的 missed 记录或批准的补跑行为。
- 授权或策略不匹配时产生 blocked-policy，并显示具体原因。
- 调度器、队列或 Docker 不可用时健康状态和告警可见。
- 手工补跑保留原错过触发点的关联。

#### US-005：排查失败并重跑

作为开发人员，我希望从失败进入日志、报告、产物、diff 和历史，再以相同口径重跑。

验收：

- 证据只能由所有者或管理员访问。
- 重跑创建新 Run，原 Run 和 provenance 不变。
- 报告或可选 AI 故障不覆盖原始结果。
- 任何脱敏、截断或证据缺失都有明确标识。
- Run 终态冻结 Evidence Manifest；后续读取、下载和恢复发现 digest 不匹配时标记 evidence_corrupt，不能继续作为可信基线。
- HTML 报告和其他主动内容不能在携带控制面会话的同源安全上下文中执行。
- 未知或可执行类型默认下载，不允许借助文件名、MIME 或压缩内容绕过边界。

#### US-006：安全运营共享服务

作为管理员，我希望普通用户只能操作自有资源，所有高风险变化可追踪，以便在单团队内安全共享平台。

验收：

- owner scope 覆盖 Suite、Credential、Profile、Run、Schedule、Artifact 和回归查询。
- 网络策略、Target Access Grant、角色、密钥、删除和清理操作产生最小安全审计记录。
- 不能删除当前账号或最后一名管理员。
- 生产配置缺失安全关键项时拒绝启动或拒绝就绪。
- 生产主机必须专用于 qarunner，并启用批准的 seccomp、AppArmor/SELinux、无 host namespace/device/socket 的 executor 策略；无法满足时不得就绪。

#### US-007：批准与吊销真实环境访问

作为管理员，我希望在看到完整不可变执行组合和目标风险后签发或吊销 Target Access Grant，以便普通用户可以重复运行已批准口径，而不能把授权转移给其他代码或密钥。

验收：

- 审批页展示 Suite Revision/content digest、Profile/手工请求口径、Dependency Snapshot、secret ref metadata、Target Environment、Network Policy、允许发起人和有效期。
- 任何字段缺失、目标被禁用、生产目标未专项批准或安全策略校验失败时不能激活 Grant。
- 管理员可以看到 Grant 与前一版本的变化并填写批准原因。
- 吊销后不再创建或启动匹配 Run，在途 Run 于批准的 revocation_stop_timeout 内终止。
- 普通用户可以看到授权是否有效及失效原因，但不能查看秘密值或修改授权。

#### US-008：在共享真实环境中稳定运行

作为测试负责人，我希望真实环境运行获得独立命名空间或排他租约，并在结束后按策略清理，以便并发回归不会互相污染或把残留数据误判为产品回归。

验收：

- Target Environment 声明 readiness probe、并发上限、isolation mode、namespace strategy、cleanup policy 和数据分类。
- Environment Lease 原子获取并带 fencing token；旧 worker、过期 lease 或重复请求不能继续使用目标。
- 无可用 lease 时 Run 保持 queued 并显示 wait_reason=target-capacity，而不是静默失败或绕过上限。
- mandatory cleanup 失败时 Run 不能进入 completed；已有测试结果仍保留并标记 cleanup_failed。
- 取消、超时、崩溃和 Grant 吊销均释放或回收 lease，且 stale lease 恢复有审计记录。
- Run 记录 lease、namespace、readiness 结果、cleanup 结果和可获取的 target revision。

### 2.4 Product Requirements

下表“成功定义”必须与 [需求追踪矩阵](REQUIREMENTS_TRACEABILITY.md) 的冻结验收目录共同解释；“全部通过”表示全部适用且 enabled 用例通过，`skipped`、`not-run`、缺失结果和未批准例外均不能计为通过。

| PRD ID | 产品需求 | 优先级 | 成功定义 |
|---|---|---|---|
| PRD-001 | 用户可以接入并维护 pytest/Playwright 套件 | P0 | US-001 在批准样本套件上 100% 通过 |
| PRD-002 | 每个 Run 具有不可变、完整的 provenance | P0 | KPI-002 达到 100% |
| PRD-003 | 测试代码、源码获取、依赖准备和报告生成只在专用 Worker 的对应受控容器运行 | P0 | 控制面无 Docker socket且不执行不可信阶段；容器无法访问 Worker agent、Docker socket、控制面秘密和未授权挂载 |
| PRD-004 | Playwright 可访问批准的真实被测环境 | P0 | approved-target 正向与反向网络验收全部通过 |
| PRD-005 | Run 异步、持久化并最终收敛到唯一终态 | P0 | 创建、排队、取消、超时、重启和停机状态测试通过 |
| PRD-006 | Run 保存完整、受权限保护的结果证据 | P0 | 用例、日志、报告和产物完整性验收通过 |
| PRD-007 | 回归比较建立在明确的等价契约上 | P0 | KPI-003 达到 100% |
| PRD-008 | 即时与 cron 调度均可靠且漏跑/阻断可见 | P0 | 重复触发为 0；每个触发点均有 triggered、missed、skipped-disabled、blocked-policy 或 failed-to-create 结果 |
| PRD-009 | 普通用户只访问自有资源，管理员操作可审计 | P0 | KPI-004 达到 100% |
| PRD-010 | 单控制面 + 单专用 Worker 部署可配置、可诊断、可备份、可重建和可恢复 | P0 | 控制面/Worker 独立健康、drain、恢复和安全运维发布门禁全部通过 |
| PRD-011 | 核心失败信号可以进入证据和重跑闭环 | P1 | US-005 通过，Beta 可测量 KPI-005 |
| PRD-012 | Web 与 API 使用同一业务和权限契约 | P1 | 关键主流程行为一致 |
| PRD-013 | 通知、趋势、主题和 AI 等扩展与核心链路解耦 | P2 | 扩展关闭或故障时 P0/P1 全部正常 |
| PRD-014 | 真实 SUT 访问使用不可变 Target Access Grant | P0 | US-007 通过，且手工/定时触发、变更失效、吊销、到期和并发校验矩阵 100% 通过 |
| PRD-015 | 不可信依赖在受限沙箱中准备并形成依赖快照 | P0 | 控制面隔离、批准软件源、资源边界和快照完整性验收全部通过 |
| PRD-016 | 不可信报告与产物不共享控制面会话安全上下文 | P0 | 存储型 XSS、路径、符号链接、MIME 和压缩边界验收全部通过 |
| PRD-017 | 真实 SUT 运行使用 Environment Lease 和测试数据隔离策略 | P0 | US-008、并发获取、fencing、回收与 cleanup 故障矩阵 100% 通过 |
| PRD-018 | Run 创建支持端到端幂等 | P0 | 网络重试、双击、超时重试和同 key 异体请求测试全部符合契约 |
| PRD-019 | Run 证据冻结为可校验 Evidence Manifest | P0 | KPI-006 达到 100%，篡改、丢失和恢复损坏均被识别 |
| PRD-020 | Git 源码获取与快照物化在受限沙箱执行 | P0 | Git 出站、凭证、hook/filter/submodule、特殊文件和资源边界验收通过 |
| PRD-021 | 专用 Worker 主机满足身份、通信、Docker、每 Run 一次性容器和运行时加固基线 | P0 | 控制面无 Docker socket；Worker/容器边界审计通过，并由安全/运维签署 Worker 共享内核残余风险 |

### 2.5 Release Scope

#### P0：不可缺少

- Git Source Acquisition 沙箱、Suite Revision 和来源版本。
- 隔离依赖准备、批准软件源和依赖快照。
- pytest 与受控网络 Playwright。
- Target Access Grant 及其变更失效、吊销和到期语义。
- Target Environment readiness、Environment Lease、测试数据命名空间和 cleanup。
- Run provenance、持久队列、终态和证据。
- Idempotency-Key、Evidence Manifest 和证据校验。
- 不可信报告、日志、trace 和产物的安全交付。
- 基于等价契约的 diff。
- cron 调度、漏跑可见性和执行健康。
- 身份认证、owner scope、密钥引用、最小审计。
- 单控制面到单 Worker 的身份、任务认领、心跳、drain、fencing 和证据上传协议。
- 控制面/Worker 分离生产配置、镜像、备份恢复、Worker 重建和容量保护。
- 专用加固 Worker、每 Run 一次性容器、运行时策略和共享内核残余风险接受。

#### P1：完整产品

- Profile 管理、重跑、取消、锁定和受控清理。
- 用例历史和通过率趋势。
- Web 控制台与开放 API 的核心能力一致。
- 管理员用户管理。
- 键盘可访问的关键流程。

#### P2：扩展

- 飞书通知适配器。
- 质量看板。
- 中英文与明暗主题。
- 仅管理员/开发环境可用的本地目录接入。
- AI 失败诊断。

### 2.6 Non-Goals

- VCS 事件触发、多阶段构建、制品发布和部署流水线。
- 人工测试用例库、测试计划、需求关联和缺陷管理。
- 多租户、组织/项目空间、计费和公网注册。
- 多控制面、多 Worker、高可用、自动扩缩容和跨 Worker 调度。
- 任意命令、任意容器或任意网络访问。
- 仅凭 Network Policy、目标 URL 或普通用户持有的 Profile 即获得真实 SUT 访问权。
- 未经单独风险批准的生产环境测试访问；在该决策关闭前必须 fail closed。
- 证明测试逻辑、断言、测试数据或测试作者结论本身正确；平台只证明已记录输入与证据的完整性。
- 每 Run VM/microVM、恶意多租户代码或公网代码执行所需的高保证独立内核隔离；该需求必须触发架构重审。
- AI 自动修改代码、自动批准发布或自动执行高风险动作。
- 用 qarunner 的被测套件通过率评价 qarunner 产品是否成功。

---

## 3. AI System Requirements

AI 失败诊断是 P2 可选扩展，不是核心发布门禁。

### 3.1 Capability

- 只读取用户有权访问的失败证据。
- 输出根因类别、置信度、引用证据和建议。
- 不修改代码、不调用部署工具、不触发重跑。
- 无失败、无证据或供应商不可用时明确拒绝或降级。

### 3.2 Tool and Data Requirements

- 模型服务由管理员配置，可使用批准的外部供应商或自建网关。
- 输入必须经过大小限制、数据分类和已知密钥脱敏。
- 日志、用例名、报告和附件内容按潜在 prompt injection 数据处理，不能覆盖系统规则或扩大可读取对象范围。
- 模型、提示版本、供应商和生成时间必须记录。
- AI 输出不能替代原始日志、报告、diff 或人工结论。
- AI 不获得部署、代码修改、重跑、网络或秘密读取工具。

### 3.3 Evaluation

AI 进入正式启用前必须建立脱敏评测集并至少验证：

- 无失败虚构率为 0。
- 证据引用必须能在原始 Run 中定位。
- 解析失败和供应商故障不产生核心服务错误。
- 根因分类准确率、证据一致性和建议可执行性目标由产品、QA、安全共同批准。

---

## 4. Technical Specifications

### 4.1 Architecture Overview

qarunner 由五个逻辑面组成：

1. 控制面：认证、Suite、Profile、Run、Schedule、权限、队列和 API；不访问 Docker daemon。
2. Worker 执行控制组件：单个受信 Worker agent，负责身份、心跳、任务认领、drain、容器生命周期和证据上传。
3. 执行面：Worker 上每阶段/每 Run 独立容器、网络策略、资源限制和进程生命周期。
4. 证据面：用例结果、日志、报告、产物、provenance 和上传完整性。
5. 分析面：基线选择、diff、趋势、Flaky 和可选 AI。

```text
用户 / API 客户端
        |
        v
控制面主机
  API + Auth + SQLite + Scheduler + Evidence Index
  无 Docker socket，不执行测试/Git/npm/报告生成
        |
        | 受认证 Worker 协议：job digest + claim/fencing + heartbeat + evidence upload
        v
专用加固 Worker 主机
  Worker agent（唯一 Docker API 使用者）
        |
        +-- Source Acquisition 一次性容器
        +-- Dependency Preparation 一次性容器
        +-- 每 Run 全新 Executor 容器
        `-- Report Generator 一次性容器
```

详细规范见 [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md)。

### 4.2 Integration Points

| 集成 | 产品要求 |
|---|---|
| Git | Source Acquisition 沙箱只访问批准仓库；私有凭证临时注入；禁用未批准 hook/filter/submodule/LFS 并输出不可变快照 |
| Worker/Docker | 控制面只调用受认证 Worker 协议；Worker agent 独占本机 Docker API；每 Run 新建容器，测试代码不可接触 agent/socket，生产记录 Worker 共享内核残余风险 |
| 软件源/Registry | 仅依赖准备沙箱可按批准策略访问；生成依赖快照，执行阶段不得借此获得额外出站 |
| 真实被测环境 | 同时满足 approved-target、有效 Target Access Grant、readiness 和 Environment Lease 才可访问 |
| 报告/产物 | 生成失败隔离；Evidence Manifest 冻结 digest；主动内容使用独立无会话安全上下文或仅下载 |
| 飞书 | P2 适配器；Webhook 作为秘密保存 |
| AI | P2 扩展；默认关闭；数据出站需批准 |

### 4.3 Security and Privacy

信任模型和生产控制以 [SECURITY_OPERATIONS_REQUIREMENTS.md](SECURITY_OPERATIONS_REQUIREMENTS.md) 为准。任何与该规范冲突的功能需求不得发布。

### 4.4 Requirement Hierarchy

| 层级 | 权威内容 |
|---|---|
| DIRECTION | 为什么做、为谁做、信任模型和边界 |
| PRD | 用户结果、产品需求、优先级、成功指标和路线图 |
| SYSTEM REQUIREMENTS | 状态、数据、比较、网络和接口的规范行为 |
| SECURITY/OPERATIONS | 安全控制、部署、健康、调度、备份恢复和发布门禁 |
| REQUIREMENTS TRACEABILITY | 跨文档映射、冻结验收分母、需求状态和候选发布证据 |
| FEATURES/API/ARCHITECTURE | 当前实现事实 |
| TEST PLANS/REPORTS | 验收证据 |

### 4.5 Current Capability Gaps

以下是 V7 基线要求与当前实现之间的已知差距，不代表完整缺陷清单：

| Gap ID | 差距 | 影响 |
|---|---|---|
| GAP-001 | Run 未绑定不可变 Suite Revision/commit/content digest，Profile、网络策略和执行镜像也缺少完整版本 | 排队期间 Suite pull 可使记录来源与实际执行内容分离，也无法证明两次 Run 等价 |
| GAP-002 | 当前基线选择忽略环境和来源版本 | 回归分类可能误导 |
| GAP-003 | Playwright executor 当前固定断网，尚无覆盖 Node/浏览器/worker/request context/重定向等全部流量的 approved-target 强制边界 | 无法承载通用真实 Web E2E，也不能证明测试代码无法绕过目标限制 |
| GAP-004 | Profile/Run env 和 Webhook 不是安全密钥引用 | 测试密钥可能明文存储和回显 |
| GAP-005 | Git URL 只限制协议，未完成目标主机和 DNS 出站控制 | 普通用户可能诱导控制面访问未批准地址 |
| GAP-006 | 标准生产 Compose 未传入全部文档化配置 | Secure Cookie、通知和生产镜像策略可能不生效 |
| GAP-007 | 健康检查只验证数据库 | Docker、队列、调度器、磁盘或镜像故障可能仍显示健康 |
| GAP-008 | 调度停机漏跑没有持久化 missed 语义 | 每日回归可能静默缺失 |
| GAP-009 | failed → skipped 可被视为已修复，Flaky 统计可混合不同口径 | 核心回归信号不可信 |
| GAP-010 | 已建立目标追踪矩阵，但当前测试证据仍主要停留在模块级，尚无冻结 release gate catalog 和逐需求候选版本证据包 | 无法证明每条 P0/P1 已由当前 commit、镜像、配置和环境完成验收 |
| GAP-011 | approved-target 尚无绑定 Suite/Profile/依赖/密钥版本的 Target Access Grant | 普通用户可让变化后的不可信内容继承旧网络批准 |
| GAP-012 | 依赖准备由控制面执行 `npm ci --ignore-scripts`，没有独立构建沙箱、批准 Registry 和依赖快照 | 包管理器或供应链风险仍处于高权限边界内，运行不可完全复现 |
| GAP-013 | Allure HTML 由控制面同源提供，且存在直接新窗口打开路径 | 不可信主动内容可能接触控制面会话或形成存储型 XSS |
| GAP-014 | Git clone/fetch/reset 由持有 Docker socket 的控制面执行，未隔离 hook/filter/submodule/LFS 和工作区物化 | 恶意仓库或 Git 客户端漏洞可能突破控制面边界 |
| GAP-015 | 没有 Target Environment readiness、Environment Lease、命名空间和 cleanup 状态 | 并发 Run 可互相污染，崩溃后残留状态可能制造假回归 |
| GAP-016 | 手工/Profile trigger API 没有 Idempotency-Key 契约 | 网络重试或双击可能重复执行破坏性 E2E |
| GAP-017 | 证据没有冻结 manifest 与 digest 校验 | 产物丢失、覆盖或恢复损坏不能被可靠识别 |
| GAP-018 | executor 与控制面仍位于同一宿主信任边界，控制面直连 Docker socket，尚无独立专用 Worker 与完整 host/seccomp/LSM 基线 | 容器逃逸或 Docker API 失陷可直接升级为控制面、数据库和平台密钥失陷 |
| GAP-019 | 授权、租约、调度和审计依赖系统时钟，但没有时钟偏差健康与跳变处置 | 到期、去重和事件顺序可能错误 |
| GAP-020 | 当前 API 错误主要是不同路由自行返回 status/detail，缺少版本化 error_code、correlation_id、retryable 和统一冲突/配额/依赖不可用语义 | Web、API 客户端和自动重试可能对同类失败采取不一致或危险行为 |
| GAP-021 | 当前没有 Worker 领域对象、受认证 Worker 协议、任务 claim/fencing、心跳/drain、远程证据上传和 Worker 丢失恢复；Run 仍由控制面本地启动 Docker | 已确认的“控制面 + 专用 Worker + 每 Run 一次性容器”目标架构尚未实现 |

---

## 5. Risks & Roadmap

### 5.1 Phased Roadmap

#### Phase 0：需求基线

- 完成 DIRECTION、PRD、系统需求和安全运维需求评审。
- 建立旧需求 ID 的保留、修订、降级和新增映射。
- 建立 [需求追踪与发布证据矩阵](REQUIREMENTS_TRACEABILITY.md)，为每条 P0/P1 指定验收目录、责任人和证据结构。

退出条件：五方完成需求基线签署；OI-018、OI-021 已按 DEC-010、DEC-013～016 关闭，OI-022 关闭；其他未决事项均有负责人、关闭时点和未关闭期间的 fail-closed 默认，且不存在会改变产品范围、信任边界或 P0 验收分母的未登记问题。

#### Phase 1：可信运行基础

- 单控制面到单专用 Worker 的身份注册、mTLS/等价认证、任务 claim/fencing、心跳、drain 和恢复。
- Worker agent 本地管理 Docker；控制面移除 Docker socket/daemon 访问。
- Source Acquisition、依赖准备、每 Run executor 和报告生成的一次性容器生命周期。
- Run provenance。
- Source Acquisition sandbox 和 immutable Suite Revision。
- Profile revision。
- 套件 commit/content digest。
- executor image digest。
- 密钥引用。
- Target Access Grant。
- Target Environment readiness、Environment Lease 和 cleanup。
- 隔离依赖准备和 dependency snapshot。
- isolated/approved-target 网络策略。
- Git 出站控制。
- 不可信报告与产物隔离交付。
- Idempotency-Key、Evidence Manifest、时钟健康和专用主机基线。
- 生产配置和健康检查。

退出条件：KPI-002、KPI-004、KPI-006 达到 100%；追踪矩阵中映射到 Phase 1 的全部 P0 达到 `accepted`；不存在未批准的 Critical/High 风险。

#### Phase 2：可信回归与调度

- 等价契约与新基线选择。
- 跳过/覆盖变化语义。
- Flaky 按执行口径隔离。
- missed Schedule 记录和补跑策略。
- 需求到测试的逐条追踪。

退出条件：KPI-001、KPI-003 达到 100%；比较与调度相关 P0 达到 `accepted`，并生成当前候选版本的 Release Evidence Bundle。

#### Phase 3：扩展与产品验证

- 通知、看板、主题、语言和 AI 作为独立扩展验证。
- Beta 采集 KPI-005。
- 根据真实使用数据决定保留、调整或删除扩展。

### 5.2 Principal Risks

| 风险 ID | 风险 | 处理原则 |
|---|---|---|
| RSK-001 | provenance 不完整导致错误比较 | 缺字段即无基线，不做猜测 |
| RSK-002 | approved-target 被滥用为内网探测 | 管理员批准、默认拒绝、出站控制、审计 |
| RSK-003 | Worker agent 或 Worker Docker daemon 失陷 | Worker 专用化、最小 Worker 身份、agent/socket 仅本机可达、控制面与 Worker 分离 |
| RSK-004 | 测试密钥进入 env、日志、产物或 AI | 密钥引用、脱敏、访问控制、保留与轮换 |
| RSK-005 | 单控制面或单 Worker 停机造成调度漏跑或执行停摆 | missed/worker-unavailable 可见、drain、告警、补跑和 Worker 重建演练 |
| RSK-006 | 单体文档再次与实现漂移 | 分层 SSOT、逐条追踪和发布核对 |
| RSK-007 | AI 占用核心资源但没有可证明价值 | P2、默认关闭、独立评测和成本边界 |
| RSK-008 | 网络目标虽获批准，但恶意、变化后或排队期间被替换的 Suite 借授权攻击 SUT | Run 挂载不可变 Suite Revision；Grant 固定组合，变化即失效，执行前再次校验 |
| RSK-009 | 依赖准备或主动报告绕过 executor 隔离 | 独立准备沙箱、批准 Registry、无会话报告域和下载优先 |
| RSK-010 | 多个 Run 共享 SUT 状态导致互相干扰或清理失败 | readiness、原子 lease、命名空间/fencing、并发上限和 cleanup 状态 |
| RSK-011 | 测试伪造结果或证据在归档后损坏 | 明确语义边界；平台冻结 Evidence Manifest，但不为测试逻辑正确性背书 |
| RSK-012 | Docker/内核漏洞导致 executor 逃逸并接管 Worker 主机 | 专用 Worker、seccomp/LSM、无 host namespace/device/socket、快速修补；控制面分离限制爆炸半径 |
| RSK-013 | 时钟跳变导致 Grant/lease 过期、Schedule 去重和审计顺序错误 | UTC + monotonic、NTP/偏差健康、跳变告警和恢复测试 |
| RSK-014 | 伪造 Worker、任务重放、claim 竞态或证据上传篡改 | Worker 双向认证、短期作用域凭证、任务 digest、claim fencing、幂等状态和上传 digest 校验 |

### 5.3 Release Gate

正式发布必须满足：

- 所有 P0 产品需求通过验收。
- SYSTEM 和 SECURITY/OPERATIONS 中的 P0 要求全部有测试或演练证据。
- 不存在未关闭的 Critical/High 权限、网络、密钥、数据完整性、错误比较或宿主隔离风险；例外必须记录补偿控制、责任人、到期日，并由产品、安全和运维共同批准。
- 所有证据来自最后一次相关变更后的候选 build；旧 commit、旧镜像、旧配置或不等价环境的通过记录不能复用。
- 标准生产部署实际应用安全配置，并通过 executor、调度、磁盘和数据库健康检查。
- 控制面主机不安装/挂载 Worker Docker socket，且无法直接启动测试容器；Worker agent 身份、任务 claim/fencing、心跳、drain 和证据上传验收通过。
- 每个 Run 使用全新 executor 容器，终态后销毁且不可复用；Worker 失联时控制面不得回退到本地执行。
- 真实 SUT Run 同时通过 Network Policy 与 Target Access Grant 校验，授权变化后旧 Run 请求不能启动。
- 真实 SUT Run 原子获得 Environment Lease，测试数据/并发/cleanup 策略通过故障注入。
- Run 创建幂等，Evidence Manifest 可检测篡改、缺失和恢复损坏。
- 依赖准备和报告生成均不在持有 Docker socket、控制面秘密或控制面会话的安全上下文中执行。
- Git 获取、依赖准备、测试执行和报告生成均不在控制面处理；专用 Worker 主机通过身份、通信、Docker、容器生命周期与运行时加固审计。
- 时钟同步和偏差健康满足 Grant、lease、调度与审计要求。
- 备份恢复演练包含数据库、产物、套件、配置和加密密钥。
- 每个 P0/P1 需求在 [需求追踪与发布证据矩阵](REQUIREMENTS_TRACEABILITY.md) 中达到 `accepted`，并可追溯到设计、代码、迁移、测试、配置、环境和发布证据。
- 生成候选版本 Release Evidence Bundle，记录冻结验收目录、commit、镜像 digest、schema/config/policy 版本、测试结果、例外和产品/研发/QA/安全/运维签署。
- 扩展关闭时核心旅程仍全部通过。

---

## 附录 A：旧需求 ID 迁移

旧 ID 保留用于历史追踪，详细修订见系统和安全运维规范。

| 旧需求组 | V7 处理 |
|---|---|
| SU-1～SU-8 | 保留；Git 接入增加 provenance/出站要求；本地目录降为受限扩展 |
| CR-1～CR-6 | 保留 Git 凭证；扩展为通用密钥引用与轮换要求 |
| PF-1～PF-3 | 保留；Profile 必须版本化；Webhook 从普通字段改为秘密引用 |
| RN-1～RN-16 | 保留并修订；增加 provenance、网络策略和生产生命周期要求 |
| RP-1～RP-6 | 保留；增加证据完整性、脱敏和 provenance 展示 |
| DF-1～DF-7 | 核心重写；比较必须使用等价契约，跳过变化不能算已修复 |
| SC-1～SC-5 | 核心提升；增加 missed 记录、告警和补跑关联 |
| NT-1～NT-2 | P2 扩展，不能阻塞或改变核心 Run |
| AI-1～AI-5 | P2 扩展，独立评测，默认关闭 |
| UI-1～UI-7 | 保留核心流程、权限和健康；主题/语言保持 P2 |
| NF-1～NF-19 | 按系统、安全和运维职责重新编号并保留追踪 |
| V1～V8 | 被 V7 KPI、用户故事验收和发布门禁取代 |

### 旧 ID 完整注册表

以下 ID 均保留为历史追踪键；新规范没有逐条复述时，以本表和上方迁移说明为准。

| 类别 | 历史 ID |
|---|---|
| 产品目标 | GO-1、GO-2、GO-3、GO-4、GO-5、GO-6、GO-7、GO-8 |
| Suite | SU-1、SU-2、SU-3、SU-4、SU-5、SU-6、SU-7、SU-8 |
| Credential | CR-1、CR-2、CR-3、CR-4、CR-5、CR-6 |
| Profile | PF-1、PF-2、PF-3 |
| Run | RN-1、RN-2、RN-3、RN-5、RN-6、RN-7、RN-8、RN-9、RN-10、RN-11、RN-12、RN-13、RN-14、RN-15、RN-16 |
| Result | RP-1、RP-2、RP-3、RP-4、RP-5、RP-6 |
| Diff | DF-1、DF-2、DF-3、DF-4、DF-5、DF-6、DF-7 |
| Schedule | SC-1、SC-2、SC-3、SC-4、SC-5 |
| Notification | NT-1、NT-2 |
| AI | AI-1、AI-2、AI-3、AI-4、AI-5 |
| UI/API | UI-1、UI-2、UI-3、UI-4、UI-5、UI-6、UI-7 |
| Non-functional | NF-1、NF-2、NF-3、NF-4、NF-5、NF-6、NF-7、NF-8、NF-9、NF-10、NF-11、NF-12、NF-13、NF-14、NF-15、NF-16、NF-17、NF-18、NF-19 |
| 约束/假设 | AS-1、AS-2、AS-3、AS-4、AS-5、AS-6、AS-7、AS-8 |
| 风险 | RK-1、RK-2、RK-3、RK-4、RK-5、RK-6、RK-7、RK-8 |
| 旧验收 | V1、V2、V3、V4、V5、V6、V7、V8 |
| 旧待办 | OP-1、OP-2、OP-3、OP-4、OP-5、OP-6 |

## 附录 B：决策与未决事项

| ID | 事项 | 负责人 | 关闭时点 |
|---|---|---|---|
| OI-001 | approved-target 的具体实现选择：Docker 网络、出站代理或主机防火墙 | 技术/安全/运维 | Phase 1 设计前 |
| OI-002 | 非秘密环境指纹包含哪些字段 | 技术/QA/安全 | provenance 设计前 |
| OI-003 | 密钥存储采用内置加密、Docker secret 或外部 Secret Manager | 安全/运维 | Phase 1 设计前 |
| OI-004 | Schedule missed 后采用仅记录、自动补跑或人工补跑 | 产品/QA/运维 | Phase 2 设计前 |
| OI-005 | 平台排队、启动、归档和 API 性能目标 | 技术/QA | Beta 前 |
| OI-006 | RTO、RPO、备份频率和保留期 | 产品/运维/安全 | GA 前 |
| OI-007 | KPI-005 的 Beta 基线和目标 | 产品/QA | Beta 运行两周后 |
| OI-008 | Suite、executor 和平台版本的 strict comparison 兼容规则 | 产品/技术/QA | Phase 2 设计前 |
| OI-009 | 是否允许生产环境成为 Target Environment；关闭前默认禁止 | 产品/安全/运维 | Phase 1 设计前 |
| OI-010 | Target Access Grant 的默认有效期、紧急吊销和批量续期流程 | 产品/安全/运维 | Phase 1 设计前 |
| OI-011 | 依赖快照采用目录 digest、OCI 镜像还是只读制品 | 技术/安全/运维 | Phase 1 设计前 |
| OI-012 | 主动报告采用独立域、静态净化版本或仅下载模式 | 技术/安全/产品 | Phase 1 设计前 |
| OI-013 | Grant 吊销后在途 executor 的默认/最大 revocation_stop_timeout | 安全/技术/运维 | Phase 1 设计前 |
| OI-014 | Phase 1～3 的负责人、目标日期、主机/存储预算和支持投入 | 产品/技术/运维 | 进入排期前 |
| OI-015 | Target Environment 默认 concurrency/isolation/namespace/cleanup 策略 | 产品/QA/安全/运维 | Phase 1 设计前 |
| OI-016 | Evidence Manifest hash、签名/非签名范围和校验时点 | 技术/安全/QA | Phase 1 设计前 |
| OI-017 | Source Acquisition 沙箱对 submodule、LFS、filter 和大仓库的支持范围 | 产品/技术/安全 | Phase 1 设计前 |
| OI-018 | 已关闭（架构决策）：V7 选择专用加固 Worker + 每 Run 一次性容器并承认共享内核残余风险；本项关闭不等于发布风险接受，SEC-067/PRD-021 的安全与运维签署仍是 accepted/GA 门禁；公网、多租户、生产 SUT 或高对抗代码升级为每 Run VM/microVM | 产品/安全/运维 | 2026-07-12 |
| OI-019 | NTP 来源、最大允许 clock skew 与时钟跳变恢复策略 | 技术/运维/安全 | Phase 1 设计前 |
| OI-020 | Environment Lease fencing 由出站代理、Docker 网络、namespace 服务或 SUT 侧何处强制执行 | 技术/安全/运维 | Phase 1 设计前 |
| OI-021 | 已关闭：控制面不访问 Docker socket/daemon，只使用受认证 Worker 协议；Worker agent 独占本机 Docker API，测试容器不能访问 agent/socket | 技术/安全/运维 | 2026-07-12 |
| OI-022 | 已建立 [发布验收目录 RC](RELEASE_GATE_CATALOG.md)：登记 24 个 family、稳定 case ID、适用性、命令和样本契约；待五方批准并发布 `release-gate-catalog@1.0.0` 后关闭。关闭仅表示治理决策冻结，不表示 planned-blocking 已通过 | 产品/研发/QA/安全/运维 | V7.4.1 建立基线前 |
| OI-023 | 批准统一生产参数基线：资源边界、认证限速、补丁 SLA、各类 timeout/TTL、分页、健康/告警、磁盘阈值和 Runbook 演练周期 | 技术/安全/运维/QA | Phase 1 设计前 |
| OI-024 | 批准 supported runtime matrix、单控制面/单 Worker 容量模型及 Beta 性能/容量验收目录 | 产品/技术/QA/运维 | Beta 前 |

## 附录 C：变更管理

任何影响产品方向、信任模型、P0 范围、网络能力或比较等价性的变更，必须按以下流程执行：

提出变更 → 影响分析 → 产品/研发/QA/安全/运维评审 → 更新规范与追踪 → 实现与验证 → 建立新基线。

## 附录 D：企业模板覆盖矩阵

本需求基线以工作区提供的《企业级需求文档模板》作为结构检查表，但模板本身不属于本需求基线。为避免产品、系统和安全运维规则互相覆盖，本文采用分层文档，而不是继续扩张单体文档。

| 模板章节 | 权威文档 |
|---|---|
| 0 文档控制、1 执行摘要 | 本文 0～1 |
| 2 背景与目标、3 利益相关方、4 范围 | [DIRECTION.md](DIRECTION.md) 与本文 1～2 |
| 5 业务流程与规则、6 功能需求、7 用户故事 | 本文 2、[SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md) 1～8 |
| 8 数据、9 接口、10 非功能需求 | [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md) 2、9～11 |
| 11 安全、隐私与合规 | [SECURITY_OPERATIONS_REQUIREMENTS.md](SECURITY_OPERATIONS_REQUIREMENTS.md) |
| 12 AI/算法 | 本文 3 与安全运维规范的 AI 章节 |
| 13 交互与界面 | 本文用户故事、[FEATURES.md](FEATURES.md) 与专项 UI/可访问性规格 |
| 14 验收与测试策略 | 本文 5.3、系统需求 11、安全运维发布门禁与[需求追踪矩阵](REQUIREMENTS_TRACEABILITY.md) |
| 15 发布迁移运营、16 里程碑资源成本 | 本文路线图、安全运维规范；未量化资源与成本保留为评审输入 |
| 17 风险问题决策、18 追踪矩阵 | 本文 0.3、5.2、附录 A/B、[需求追踪矩阵](REQUIREMENTS_TRACEABILITY.md) 与系统需求 11 |
| 19 变更管理、20 附录 | 本文附录 A～D |
