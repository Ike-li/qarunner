# qarunner 发布验收目录

> 文档类型：Release Gate Catalog
> 文档编号：QARUNNER-RGC-001
> 版本：V0.1.0-RC2
> 状态：评审候选
> 更新日期：2026-07-12

## 0. 目的与边界

本文是 OI-022 的目录治理关闭候选，冻结发布验收 family、稳定用例身份、适用性规则、执行入口、样本套件身份和责任人。它不声明当前代码已经满足 V7.4，也不替代 [需求追踪与发布证据矩阵](REQUIREMENTS_TRACEABILITY.md) 的逐条实现、验证和签署。

当前仓库已有测试主要验证 legacy 单宿主实现。`legacy-only` 只能作为迁移前事实，在被 V7.4 证据替代前仍阻断验收；`candidate-reusable` 表示测试语义可复用，但不自动代表整个 family 已通过，仍需在候选版本和批准环境重新执行；`planned-blocking` 表示当前没有合格证据，阻断相关需求进入 `verified/accepted`。

RC2 在 RC1 的 48 个最小 case 上新增 8 个 Worker 协议故障 case，覆盖 commit-start、控制面重启、同 assignment 重连、sequence 冲突、启动 reconcile、split-brain 和上传续传。当前最小集合为 56 个 case；本次只增加验收约束，没有把任何 planned-blocking 标记为通过。

## 1. OI-022 关闭规则

OI-022 是“验收目录如何治理”的决策，不是“所有验收已经通过”。它在以下条件全部满足后可以关闭：

1. 产品、研发、QA、安全和运维批准 `RCF-001～024` family、owner 和聚合目录归属。
2. 用例 ID、适用性、`skipped/not-run`、退役和版本升级规则获批。
3. 样本套件 ID、摘要算法、已有摘要和必须新增的样本契约获批。
4. `release-gate-catalog@1.0.0` 从本 RC 版本建立不可变基线。

OI-022 关闭后，`planned-blocking` 仍继续阻断发布；只有候选版本证据和五方适用签署完成，需求才能进入 `verified/accepted`。目录语义变化必须发布新 SemVer，不能原地修改已冻结版本。

## 2. 目录包与稳定身份

### 2.1 聚合目录

| 目录键 | RC 版本 | 负责角色 | 内容 |
|---|---|---|---|
| `release-gate-catalog` | `0.1.0-rc2` | QA | 全部 enabled 且适用的 family/case 分母 |
| `core-journey-catalog` | `0.1.0-rc2` | 产品、QA | 核心接入、运行、证据、比较、权限、调度和恢复旅程 |
| `comparison-golden-matrix` | `0.1.0-rc2` | QA、研发 | `RCF-010` 全部分类与基线语义 |
| `security-boundary-catalog` | `0.1.0-rc2` | 安全、QA | 身份、网络、秘密、内容、Worker 和主机边界 |
| `sample-suite-catalog` | `0.1.0-rc2` | QA、研发 | 样本套件 ID、用途、摘要和适用范围 |

### 2.2 Family 与 case ID

- Family ID 固定为 `RCF-001～RCF-024`，与 RTM 使用的 24 个逻辑验收目录一一对应。
- Case ID 使用 `<family_id>.<SCENARIO>`，例如 `RCF-010.DIFF.<TRANSITION>`。
- pytest node ID、Playwright 标题和源码路径只作为 `executor_locator`，不能作为稳定身份。
- 参数化测试必须显式使用稳定 `ids`；参数顺序或展示值变化不得改变 case ID。
- 文件移动或文案修改不更换 ID；验收语义、预期结果、分母或信任假设变化时必须创建新 ID。
- 退役 ID 永不复用，记录 `retired_at`、替代 ID 和理由。

### 2.3 适用性与分母

每条 case 必须记录：

- `family_id`、`case_id`、`catalog_version`、`owner`、`requirement_refs`。
- `enabled` 和 `applicability`：`always`、`isolated-only`、`approved-target-only`、`worker-only` 或明确环境条件。
- `command_ref`、`executor_locator`、`sample_suite_id`、预期结果和证据类型。
- 不适用时的 `not_applicable_reason`、批准角色和证据。

冻结目录中 `enabled=true` 且适用的全部 case 构成分母。`skipped`、`not-run`、缺失结果和未经批准的 `not-applicable` 均按未通过处理。动态 `test.skip` 不能自动移出分母；环境缺失必须在执行前 fail closed 或形成批准的不适用记录。

### 2.4 证据记录

每条执行记录至少保存：

- candidate commit/build、控制面/Worker/阶段/executor 镜像 digest、schema/config/policy 版本和 environment ID。
- 适用的 worker ID、assignment/job digest、fencing token、Grant、Lease 和 target revision。
- started/finished time、result、attempt 数量、artifact ref 和 Manifest 状态/引用。
- implementer、reviewer、accepted_by、例外 ID 和到期日。

不确定 Worker attempt 不能被重跑结果覆盖；必须先形成 `worker_lost/attempt_unknown`，再由有权限用户显式创建新 Run。

## 3. Family 注册表

| Family ID | 逻辑目录键 | 主 owner | 聚合目录 | 当前状态 | 当前证据或缺口 |
|---|---|---|---|---|---|
| `RCF-001` | `suite-acceptance-matrix` | 产品、QA | core | legacy-only | 现有 J5 允许 link 失败后回退，不能作为门禁 |
| `RCF-002` | `source-acquisition-matrix` | 研发、安全 | core、security | planned-blocking | 无 Worker Source Acquisition、不可变 Revision 和完整出站矩阵 |
| `RCF-003` | `worker-protocol-fault-matrix` | 研发、运维 | core、security | planned-blocking | 无 identity/generation、claim/commit-start/fencing、heartbeat、drain、sequence 和上传协议 |
| `RCF-004` | `provenance-matrix` | 研发、QA | core、comparison | planned-blocking | 当前 Run 缺少 V7 provenance |
| `RCF-005` | `sandbox-isolation-matrix` | 安全、QA | security | legacy-only | Docker inspect/no-network 只证明当前同宿主实现 |
| `RCF-006` | `worker-isolation-matrix` | 安全、运维 | security | planned-blocking | 无远程 Worker、阶段容器、journal/reconcile 和残留隔离证据 |
| `RCF-007` | `approved-target-network-matrix` | 安全、QA | core、security | planned-blocking | 当前 Playwright 固定 isolated，无真实 SUT 正反向矩阵 |
| `RCF-008` | `run-state-fault-matrix` | 研发、QA | core | candidate-reusable | 当前状态/取消/超时测试较多，Worker 故障路径缺失 |
| `RCF-009` | `evidence-access-integrity-matrix` | QA、安全 | core、security | legacy-only | 已有访问/路径测试，无 Manifest/digest 完整性 |
| `RCF-010` | `comparison-golden-matrix` | QA、研发 | core、comparison | candidate-reusable | 现有 regression 24 case + flaky 8 case，是最成熟候选 |
| `RCF-011` | `schedule-recovery-matrix` | 研发、运维 | core | candidate-reusable | 已有 cron/heartbeat/去重，missed 和 Worker 阻断仍缺 |
| `RCF-012` | `owner-scope-audit-matrix` | 安全、QA | core、security | candidate-reusable | 后端与 role API 已覆盖主要 owner/admin 边界 |
| `RCF-013` | `deployment-recovery-drill` | 运维、安全 | core | planned-blocking | 无双主机生产部署和恢复演练 |
| `RCF-014` | `worker-rebuild-drill` | 运维、安全 | core、security | planned-blocking | 无 Worker 重建、generation/身份轮换和旧主机 split-brain fencing |
| `RCF-015` | `failure-triage-journey` | 产品、QA | core | legacy-only | J3/J2 可复用，但存在未断言和 legacy executor |
| `RCF-016` | `web-api-contract-matrix` | 产品、研发 | core | candidate-reusable | 现有 UI/API 权限和主流程可作为候选 |
| `RCF-017` | `extension-failure-isolation-matrix` | 产品、QA | core | candidate-reusable | AI/通知关闭与降级已有部分测试，需绑定核心目录 |
| `RCF-018` | `grant-lifecycle-race-matrix` | 安全、研发 | security | planned-blocking | Target Access Grant 尚未实现 |
| `RCF-019` | `dependency-preparation-matrix` | 研发、安全 | core、security | legacy-only | 当前由控制面执行 npm，不能作为 V7.4 证据 |
| `RCF-020` | `active-content-security-matrix` | 安全、QA | security | legacy-only | 有路径/下载测试，无独立报告安全上下文 |
| `RCF-021` | `target-lease-fault-matrix` | QA、运维 | core、security | planned-blocking | Environment Lease/fencing/cleanup 尚未实现 |
| `RCF-022` | `idempotency-matrix` | 研发、QA | core | planned-blocking | Run 创建 Idempotency-Key 尚未实现 |
| `RCF-023` | `manifest-integrity-matrix` | QA、安全 | core、security | planned-blocking | Evidence upload resume/part digest/Manifest/corruption 尚未实现 |
| `RCF-024` | `host-security-baseline` | 安全、运维 | security | legacy-only | 只有 image non-root 部分证据，无专用 Worker 主机基线 |

`core` 表示进入 `core-journey-catalog`，`comparison` 表示进入 `comparison-golden-matrix`，`security` 表示进入 `security-boundary-catalog`。一个 family 可以进入多个聚合目录，但 case 只执行一次并分别记录需求适用性。

## 4. 命令目录

所有命令必须在 Docker 容器内执行。以下是当前可用入口，不代表它们已经验证 V7.4 Worker 拓扑。

| 命令键 | 命令/入口 | 说明 |
|---|---|---|
| `CMD-BE-DEFAULT` | `docker compose -f docker-compose.dev.yml exec -T backend uv run pytest` | 当前后端 coverage gate；排除 e2e/docker |
| `CMD-BE-E2E` | `docker compose -f docker-compose.dev.yml exec -T backend uv run pytest -m e2e --no-cov` | 当前 SubprocessRunner/JUnit/Allure legacy 证据 |
| `CMD-BE-DOCKER` | `docker compose -f docker-compose.dev.yml exec -T backend uv run pytest -m docker --no-cov` | 当前 DockerRunner/image legacy 证据 |
| `CMD-FE-UNIT` | `docker compose -f docker-compose.dev.yml exec -T frontend npm run test:unit -- --run` | 当前 Vitest 组件契约 |
| `CMD-FE-PW-SMOKE` | `npm run test:ui:smoke` | 官方 Playwright 一次性容器内执行 |
| `CMD-FE-PW-LIVE` | `npm run test:ui:live-api` | 当前浏览器 + legacy 控制面/API |
| `CMD-FE-PW-MOCKED` | `npm run test:ui:mocked-ui` | 确定性 UI 契约，不证明 Worker/SUT |
| `CMD-FE-PW-A11Y` | `npm run test:ui:a11y` | 动态 skip 必须登记适用性 |
| `CMD-FE-PW-RUNNER` | `npm run test:ui:playwright-runner` | 当前 Playwright artifact legacy 链路 |
| `CMD-WORKER-CONTRACT` | Worker protocol contract harness | 当前不存在，阻断 RCF-003/004/018/022/023 |
| `CMD-WORKER-ISOLATION` | Worker isolation/fault harness | 当前不存在，阻断 RCF-005/006/007/019～021/024 |
| `CMD-OPS-DRILL` | 双主机 deployment/recovery/rebuild drill | 当前不存在，阻断 RCF-013/014 |

Playwright 的完整官方容器命令以 [部署文档的 E2E 章节](deployment.md#运行-e2e-测试) 为准；不得在宿主机直接运行 `npm`、`pytest` 或外部测试代码。

## 5. 最小 case 集合

以下 case 是 `release-gate-catalog@1.0.0` 的最小集合。实现可以增加 case，但不能删除或弱化这些语义；拆分 case 时必须保留旧 ID 到新 ID 的映射。

### 5.1 核心、状态和权限

| Case ID | 场景 | 预期 |
|---|---|---|
| `RCF-001.SUITE.GIT_ONBOARD` | 接入批准 Git Suite | 形成不可变 Suite Revision，首次 Run 使用同一 digest |
| `RCF-001.SUITE.FIRST_RUN` | 新 Suite 首次执行 | 无静默 fallback；结果、日志和错误可见 |
| `RCF-004.PROVENANCE.CREATE_FREEZE` | 创建 V7 Run | 所有必填输入在创建事务冻结，缺字段拒绝创建 |
| `RCF-008.STATE.UNIQUE_TERMINAL` | 取消/超时/失败/重启竞态 | 最终只有一个终态，Manifest 状态原子收敛 |
| `RCF-008.STATE.UNKNOWN_ATTEMPT` | Worker 身份不确定 | `worker_lost/attempt_unknown`，不得自动第二 attempt |
| `RCF-008.STATE.CONTROL_RESTART` | 控制面在 running assignment 中重启 | 恢复同 assignment/fence；不立即判失败，不创建第二 attempt |
| `RCF-009.EVIDENCE.OWNER_ACCESS` | owner/admin 读取证据 | 他人访问拒绝；路径、符号链接和归档边界生效 |
| `RCF-011.SCHEDULE.HEARTBEAT` | 定时触发到终态 | 每个触发点有 triggered/missed/blocked/failed 记录 |
| `RCF-012.OWNER.RESOURCE_MATRIX` | 普通用户访问他人对象 | 列表不泄露；读取/修改按 403/404 契约拒绝 |
| `RCF-015.TRIAGE.FAILURE_LOOP` | 失败进入日志、diff、历史和重跑 | 每个关键结果有断言，不能因 return/skip 假通过 |
| `RCF-016.API.WEB_PARITY` | Web 与 API 执行同一动作 | 权限、状态、错误码和结果一致 |
| `RCF-017.EXTENSION.DISABLED_CORE_GREEN` | AI/通知关闭或故障 | P0/P1 核心旅程仍全部通过 |
| `RCF-022.IDEMPOTENCY.SAME_KEY` | 同 key 同请求重试 | 只返回同一个 Run |
| `RCF-022.IDEMPOTENCY.DIFFERENT_BODY` | 同 key 不同请求 | conflict，不创建第二 Run |
| `RCF-023.MANIFEST.FINALIZE` | 终态证据收敛 | finalized/finalize_failed 与终态原子提交 |
| `RCF-023.MANIFEST.CORRUPTION` | 篡改、缺失或恢复损坏 | 标记 corrupt/missing，禁止 strict baseline |
| `RCF-023.UPLOAD.RESUME` | 上传中断后恢复同一 session | 已确认 part 不重复写入；续传不重跑 executor，root digest 一致 |

### 5.2 比较黄金矩阵

比较规则以 [SYSTEM_REQUIREMENTS.md §5](SYSTEM_REQUIREMENTS.md#5-provenance-与比较契约) 为准。

| Case ID | Base | Head | 期望 |
|---|---|---|---|
| `RCF-010.DIFF.PASS_TO_FAIL` | passed | failed/error | `new_failure`, strict |
| `RCF-010.DIFF.FAIL_TO_PASS` | failed/error | passed | `fixed`, strict |
| `RCF-010.DIFF.FAIL_TO_FAIL` | failed/error | failed/error | `still_failing`, strict |
| `RCF-010.DIFF.ANY_TO_SKIP` | any | skipped | `coverage_change`，不得标记 fixed |
| `RCF-010.DIFF.NEW_CASE` | missing | any | `new_case` |
| `RCF-010.DIFF.REMOVED_CASE` | any | missing | `removed_case` |
| `RCF-010.DIFF.PASS_TO_PASS` | passed | passed | 无变化 |
| `RCF-010.BASELINE.INCOMPLETE_PROVENANCE` | any | any | `not-comparable` |
| `RCF-010.BASELINE.CORRUPT_MANIFEST` | any | any | `not-comparable` |
| `RCF-010.BASELINE.DIFFERENT_TARGET` | any | any | `not-comparable` |
| `RCF-010.BASELINE.INCOMPATIBLE_SCOPE` | any | any | `not-comparable` |
| `RCF-010.BASELINE.CONTEXT_CHANGED` | valid | valid | contextual 或无基线，绝不静默 strict |

现有 `tests/unit/core/test_regression.py` 和 `test_flaky.py` 可作为初始 executor locator，但候选发布必须按上述稳定 case ID 重新登记和执行。

### 5.3 Worker、网络和隔离

| Case ID | 场景 | 预期 |
|---|---|---|
| `RCF-002.SOURCE.EGRESS_DENY` | Git 指向未批准 host/IP/元数据 | 获取失败，不访问目标，不回退控制面 |
| `RCF-002.SOURCE.GIT_FEATURE_DENY` | hook/filter/submodule/LFS 未批准 | 拒绝或禁用，审计原因可查 |
| `RCF-003.CLAIM.RACE` | 两次 claim 同一任务 | 仅一个有效 assignment |
| `RCF-003.CLAIM.EXPIRED_BEFORE_START` | claim 到期且从未 commit-start | 只有 journal/Docker 证明无容器时才 fence 后重排；不增加 execution attempt |
| `RCF-003.CREATE.AMBIGUOUS` | commit-start 或 Docker create 响应丢失、资源状态不可核对 | `worker_lost/attempt_unknown`，不得自动重排或启动第二 executor |
| `RCF-003.RECONNECT.SAME_ASSIGNMENT` | Worker/网络短暂中断后重连 | 仅同 worker generation + assignment + fence 在恢复窗口续报 |
| `RCF-003.FENCING.STALE_WRITE` | 旧 token 上传状态/证据 | 拒绝且不覆盖新状态 |
| `RCF-003.SEQUENCE.CONFLICT` | event sequence 跳号、倒退或同序异体 | 返回稳定 conflict/expected sequence；同序异体触发 quarantine |
| `RCF-003.HEARTBEAT.EXPIRY` | assignment 到期/控制面失联 | Worker 本地停止容器和 approved-target 访问 |
| `RCF-005.SANDBOX.NO_HOST_ACCESS` | 容器访问 agent/socket/控制面/元数据 | 全部拒绝 |
| `RCF-006.RESIDUE.CROSS_RUN` | 连续 task/Run 检查可写层、目录、秘密、网络 | 无前一任务残留；失败触发 quarantine |
| `RCF-006.RECONCILE.ORPHAN` | agent 重启发现无主容器、挂载、网络或 staging | 完成 reconcile 前不 claim；无法解释/清理则 quarantine |
| `RCF-007.TARGET.POSITIVE` | approved-target 访问批准非生产 SUT | 浏览器及 request context 全链路可达且受控 |
| `RCF-007.TARGET.NEGATIVE` | 重定向、DNS/IP 或未批准端口 | 全部拒绝并审计 |
| `RCF-018.GRANT.MISMATCH` | Suite/Profile/Dependency/secret 任一变化 | 旧 Grant 失效，Run 不启动 |
| `RCF-018.GRANT.REVOKE_RACE` | 排队/启动/运行中吊销 | fail closed，在时限内停止并收敛 |
| `RCF-019.DEPENDENCY.REGISTRY_ONLY` | 准备容器访问 Registry/SUT | 只允许批准 Registry，不得访问 SUT |
| `RCF-020.CONTENT.ACTIVE_REPORT` | HTML/SVG/script 尝试读控制面会话 | 无 Cookie/DOM/storage/API 能力 |
| `RCF-021.LEASE.FENCING` | stale/重复 Lease token | 不能继续使用目标 |
| `RCF-021.LEASE.CLEANUP_FAIL` | mandatory cleanup 失败 | Run 不进入 completed，证据保留 |
| `RCF-024.HOST.NO_CONTROL_DOCKER` | 控制面主机检查 Docker 执行能力 | 无 socket、daemon、SDK 执行和 subprocess fallback |

### 5.4 部署、恢复与重建

| Case ID | 场景 | 预期 |
|---|---|---|
| `RCF-013.DEPLOY.CONFIG_LOAD` | 双主机生产配置 | 控制面/Worker 独立加载批准配置和镜像 digest |
| `RCF-013.RECOVERY.DATA` | 恢复 DB/WAL、Artifact、Suite、密钥、Manifest | digest、权限、状态和审计关系一致 |
| `RCF-014.REBUILD.IDENTITY_ROTATE` | 重建 Worker | 新身份和主机基线通过后才 ready |
| `RCF-014.REBUILD.FENCE_OLD_HOST` | 旧 Worker 恢复连接 | 不能 claim、续租、写状态或上传证据 |
| `RCF-014.REBUILD.SPLIT_BRAIN` | 新 generation ready 后旧主机与新主机同时在线 | 只有新 generation 有效；旧主机所有 Worker API 请求被拒绝并审计 |
| `RCF-024.HOST.RUNTIME_BASELINE` | seccomp/LSM/namespace/device/patch 审计 | 全部符合批准基线，残余风险有签署 |

## 6. 样本套件目录

### 6.1 摘要算法

排除缓存、编译产物和版本控制目录；按相对 POSIX 路径排序，将每个文件规范化为 `path\0size\0file_sha256\n`，再对拼接结果计算 SHA-256。目录版本发布后内容摘要不可原地变化。

### 6.2 已存在样本

| 样本 ID | 路径 | tree SHA-256 | 分类 | 用途与限制 |
|---|---|---|---|---|
| `SS-PYTEST-MIXED-001@v1` | `examples/sample_tests` | `8dbe0ddbf78476c420b90dcb78b3989dbe88ea2352f60f32c2775d6c9a3d796b` | legacy-smoke | 2 pass + 1 intentional fail；不证明 Worker/Grant/Lease |
| `SS-PLAYWRIGHT-ARTIFACT-001@v1` | `examples/sample_playwright` | `cfa450b25b89e395c6d000934a53592e0abd4a163bebb622380f335b42ff1b34` | legacy-smoke | `page.setContent()` + trace/screenshot/video；不访问真实 SUT |

`external_tests/` 中的压力和规模套件属于运行时/本地数据，不进入发布正确性分母，也不作为冻结样本来源。

### 6.3 V7.4 必须新增的样本契约

| 样本 ID | 用途 | 必须固定的内容 | 当前状态 |
|---|---|---|---|
| `SS-PYTEST-SCOPE-001@v1` | provenance、用例身份和比较矩阵 | 多 revision、稳定 case ID、pass/fail/skip/error 和预期分类 | planned-blocking |
| `SS-PLAYWRIGHT-APPROVED-TARGET-001@v1` | 真实非生产 SUT 正反向网络 | SUT revision、允许/拒绝路径、Grant/Lease、重定向和 cleanup | planned-blocking |
| `SS-UNTRUSTED-BOUNDARY-001@v1` | Git/依赖/主动内容边界 | hook/filter、Registry、路径、MIME、压缩和恶意输出 | planned-blocking |
| `SS-WORKER-RESIDUE-001@v1` | claim、断线、重放、drain、重建和残留 | fault schedule、旧 token、预期状态、容器/目录/秘密检查 | planned-blocking |

目录治理评审必须批准这些样本身份和契约。实际内容摘要缺失会继续阻断相关 family 的 `verified/accepted`，但不等同于目录治理决策未定义。

## 7. 当前测试资产快照

以下数字只证明收集结果，不证明测试通过：

- 后端：40 个测试文件、851 个 pytest case；默认 gate 842，`e2e` 2，`docker` 7。CI 当前只运行默认 gate。
- 前端 Vitest：32 个文件、168 条声明；当前无稳定全局 case ID 或机器可读 release manifest。
- 前端 Playwright：38 个 spec、289 条声明；包含 legacy duplicates、固定 skip 和条件性 skip。
- `RCF-010` 当前最成熟；Worker、approved-target、Grant/Lease、幂等和 Manifest family 基本为空。

以下现有测试在修复前不能进入冻结发布分母：

- J5 suite onboarding 允许 link 失败后回退到已有 Suite，且未断言新 Suite 已接入。
- J3 failure analysis 计算 diff 是否存在但没有断言。
- `run-lifecycle` 在无 Suite/Run 时可以直接返回并通过。
- `R-API-4.2` 固定 skip；a11y 存在多个条件性 skip。
- Docker integration 直接使用本机 Docker；backend e2e 使用 SubprocessRunner；均为 legacy-only。

## 8. 评审签署

| 角色 | 必须确认 | 状态 |
|---|---|---|
| 产品负责人 | core family、分母、P0/P1 范围和业务例外 | 待评审 |
| 技术负责人 | family/case 可实现性、命令、状态和证据字段 | 待评审 |
| QA 负责人 | case identity、适用性、skip、样本和黄金矩阵 | 待评审 |
| 安全负责人 | security family、Grant/Lease、内容和主机边界 | 待评审 |
| 运维负责人 | Worker、容量、部署、备份、恢复和重建演练 | 待评审 |

五方批准后，将本 RC 发布为 `release-gate-catalog@1.0.0` 并关闭 OI-022。所有 `planned-blocking` 继续保留为实现/发布阻断，不得因 OI 关闭而自动变为通过。
