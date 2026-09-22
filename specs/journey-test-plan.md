# E2E 用户旅程测试补全计划

> 创建日期：2026-07-03
> 范围：跨组件端到端业务流程（非单组件状态机）
> 优先级：P0（本批最高）
> 依据：`specs/TEST_PLAN_TEMPLATE.md`、`docs/API_REFERENCE.md`、现有 21 个 spec 调研结果

---

## 0. 背景与目标

现有 124 个 E2E 用例覆盖**单组件交互状态机**（打开/关闭/加载/空/错误/表单校验），但**没有任何一个 spec 跨组件走完一条真实业务流程**。本计划补 3–5 条最高价值的端到端用户旅程，验证组件协作、状态流转与数据贯通。

本计划与 `specs/ui-test-plan.md` 互补：ui-test-plan 按"组件 × 交互清单"组织，本计划按"用户目标 × 跨组件步骤"组织。

---

## 1. 前置依赖与诚实声明

> ⚠️ 按用户确认「保留全流程」路线，以下旅程按理想业务流程写全。**真实跑测前需补齐的前置依赖单列于第 6 节**，标注哪些旅程当前可立即落地、哪些依赖未闭环项。

### 系统当前已知的不闭环点（来自项目记忆）
- **schedule 验证**：7/7 现有 schedule spec 跳过需 profile 依赖的部分，未真跑通。
- **非 admin 身份触发 run / 建 profile**：现无 spec 以非 admin 身份操作 profile/run。
- **默认密码/共享 fixture 已收敛**：旅程 spec 统一通过 `E2E_ADMIN_PASSWORD` + `globalSetup` 生成 storageState，并跑在 `chromium-authed-admin` / `chromium-authed-user` 项目下。

### 端到端跑测需活的前后端
- `docker compose -f docker-compose.dev.yml up -d`（后端 :8000 / 前端 :5173）
- `E2E_ADMIN_PASSWORD='Demo-Qarunner-2026!'` 与后端 `QARUNNER_ADMIN_PASSWORD` 同值

---

## 2. 旅程价值评估与选取

按「业务核心度 × 跨组件数 × 覆盖风险面」打分，选 5 条：

| # | 旅程 | 核心组件链 | 价值 | 选定 |
|---|------|-----------|-----|------|
| J1 | 新用户全链路接入 | LoginScreen → Header(Users) → UserMgmt → 登出 → 新用户登录 → Dashboard | 高（认证+首登+无 admin 能力验证） | ✅ |
| J2 | 回归核心闭环 | Sidebar → TriggerRun → RunsTable → RunDetailsDrawer → Diff tab | **最高**（产品路线 A 的灵魂：跨次对比） | ✅ |
| J3 | 探索式失败分析 | Dashboard 筛选 → RunDetails → Logs → 全屏终端 → Diff → 历史导出 | 高（管理员日常分析路径） | ✅ |
| J4 | 调度驱动的回归心跳 | Profile → ScheduleModal → trigger → 生成的 run 入表 | 中高（验证 cron→run 落库链路） | ✅ |
| J5 | 套件接入与首跑 | AddSuiteModal(Local) → Sidebar → TriggerRun → 首跑入表 | 中（git clone 旅程见 ui-test-plan §6，不重复） | ✅ 仅 Local 路径 |

---

## 3. 测试场景

### Journey 1 — 新用户接入全链路

**File:** `tests-e2e/journey-onboarding.authed-admin.spec.ts`
**角色切换：** admin → 登出 → 新建 user → 以新 user 登录
**前置：** admin 已登录（共享 fixture）

#### J1.1 admin 创建新用户后新用户能登录并进入 Dashboard

**Steps:**
  1. 以 admin 登录
     - expect: dashboard 可见，`open-users-button` 可见
  2. 点击 `open-users-button` 打开用户管理 modal
     - expect: `user-modal` 可见
  3. 在 `user-new-username` 填唯一名（如 `e2e_journey1_<ts>`），`user-new-password` 填强密码，点击 `user-add-submit`
     - expect: 用户列表出现新行 `user-row-username` 含该用户名
  4. 关闭 modal，点击 header 登出按钮
     - expect: 回到 `login-title`
  5. 以新用户名 + 密码登录
     - expect: `profile-username` 显示新用户名
     - expect: `profile-role` 显示 `user`（非 admin）
     - expect: `open-users-button` **不可见**（角色矩阵交叉验证）
     - expect: `stat-total` 等四张 stat 卡片可见

**断言重点：** 角色 role 字段在登录→`/auth/me`→前端渲染链路正确传递；非 admin 头部按钮条件渲染生效。
**数据清理：** 测试末尾以 admin API `DELETE /users/{username}` 删除新建用户（避免污染）。

#### J1.2 新用户的资源默认隔离（owner-scope 前置铺垫）

**Steps:**
  1. 承接 J1.1 末态，新用户已登录
  2. 检查 `RunsTable`
     - expect: 表中**不出现** admin 历史创建的 run（后端对非 admin 静默过滤）
     - expect: 空态占位 `'No runs yet'` 或 `launch-first-run` 按钮可见
  3. 检查 Sidebar
     - expect: `GET /suites` 返回的列表应能为新用户所见共享 suite（_suite 是认证即读，非 owner-scope_），具体断言见 J5

> 注：suite 是公共只读资源，run/profile/schedule 才做 owner-scope。本步仅断言 run 隔离，profile/schedule 隔离在角色矩阵计划中专项测。

---

### Journey 2 — 回归核心闭环（P0 核心）

**File:** `tests-e2e/journey-regression-loop.authed-admin.spec.ts`
**角色：** admin
**前置：** 存在可跑的 suite（用 `examples/sample_tests` 经 `POST /tests/link` 注册或复用 seed）；profile 可选

#### J2.1 触发一次 run 并在表中看到它流转到终态

**Steps:**
  1. 登录 admin，确认 Sidebar 含目标 suite
  2. 点击 `open-trigger-button` 打开 TriggerRun modal
     - expect: `trigger-modal` 可见，`trigger-runner-select` 默认 pytest
  3. 选定 suite、（可选）选定已有 profile，点击 `trigger-submit-button`
     - expect: modal 关闭
     - expect: `RunsTable` 顶部出现新 run 行，状态 `queued` 或 `running`
  4. 轮询表格（最长 N 秒），等待该 run 状态变为 `completed`/`failed`
     - expect: 状态标签稳定在终态，`Pass Rate` 列出现数值
  5. 点击该 run 行打开 `RunDetailsDrawer`
     - expect: drawer 可见，`drawer-tab-logs` 默认激活，console 输出可见

> 这是 `run-lifecycle.authed-admin.spec.ts`（仅 2 条浅测）的真扩展，覆盖完整排队→执行→终态→查看链路。

#### J2.2 同一 profile 二次触发后 Diff tab 出现跨次对比

**Steps:**
  1. 承接 J2.1，已有 run A（completed）
  2. 在 drawer 内点 `Re-run` 按钮（或回 trigger modal 用同 profile 再触发）→ 产生 run B
     - expect: B 进表、流转到 completed
  3. 打开 run B 的 drawer，切到 `drawer-tab-diff`
     - expect: diff 桶渲染（passed/failed/new/missing/regressed 五桶）
     - expect: `baseline` 非 null（因为存在更早的同范围 run A）
  4. 点某个 `diff-case-toggle`
     - expect: `case-history` 出现彩色历史点
     - expect: 若该用例在 A/B 间翻转，`flaky-badge` 可见

**断言重点：** 跨次对比是本产品的核心价值（见 `docs/FEATURES.md` §5 回归视图）。此旅程是唯一端到端验证"触发→对比→flaky"全链路的用例。
**前置依赖：** sample_tests 用例需产生可对比的差异（一 pass 一 fail），否则 diff 全空。若现成 sample 无差异，需用 mock `/diff` 兜底（见第 6 节）。

---

### Journey 3 — 探索式失败分析

**File:** `tests-e2e/journey-failure-analysis.authed-admin.spec.ts`
**角色：** admin
**前置：** 已有 ≥3 条 mixed-status 的 run（mock 或 J2 产出）

#### J3.1 从 Dashboard 筛选到失败 run 并深入分析

**Steps:**
  1. 登录 admin，`stat-failed` 卡片显示非零计数
  2. 在 `RunsTable` 用 `filter-status-select` 选 `failed`
     - expect: 表格只剩 failed 行
  3. 用 `search-run-input` 输入某 run ID 片段进一步收窄
     - expect: 单行结果
  4. 点击该行打开 drawer
     - expect: Logs tab 显示 stderr 末尾，含失败堆栈
  5. 点 Logs tab 内的 `fullscreen-toggle` 进入 `FullscreenTerminalOverlay`
     - expect: 全屏终端可见
     - expect: 按 Escape 退出全屏，焦点回到 drawer 内触发按钮
  6. 切 `drawer-tab-diff`，若 diff 含 `regressed`/`failed` 桶，展开该 case 看 `case-history`
     - expect: 历史点串显示该 case 在历次 run 的 pass/fail 序列

**断言重点：** 跨组件焦点流转（drawer → 全屏 → drawer）、Escape 还原焦点（a11y 验证可在此旅程串联，但专项断言放 a11y 计划）。

#### J3.2 复制/下载日志的副作用

**Steps:**
  1. 承接 J3.1 drawer Logs tab
  2. 点 `Copy` 按钮
     - expect: 触发 clipboard 写入（用 `page.evaluate` 验证 `navigator.clipboard.readText` 含日志片段，或断言按钮反馈态）
  3. 点 `Download` 按钮
     - expect: 浏览器发起下载（监听 `download` 事件断言文件名）

> 这条与 `run-details.authed-admin.spec.ts` 的 copy/download 用例有重叠，但本旅程强调"**作为失败分析流向的一部分**"触达，而非孤立按钮测试。

---

### Journey 4 — 调度驱动的回归心跳

**File:** `tests-e2e/journey-schedule-heartbeat.authed-admin.spec.ts`
**角色：** admin
**前置：** 已有 profile（用 `POST /profiles` 经 API 造，避免 UI 长 journey）

#### J4.1 从 profile 创建调度并立即触发，run 入表

**Steps:**
  1. 登录 admin，Sidebar 中目标 profile 行点 `open-schedule-button`
     - expect: `schedule-modal` 可见
  2. 填 `schedule-name-input`、`schedule-cron-input` 填 `'0 2 * * *'`
     - expect: `schedule-preview` 出现 5 条 future fire times
  3. 点 `schedule-save-button`
     - expect: modal 关闭，schedule 落库
  4. 重开 schedule modal，点 `schedule-trigger-button`，接受 confirm
     - expect: 触发 `POST /schedules/{id}/trigger`
     - expect: modal 关闭，`RunsTable` 出现新 run，`created_by` 为 `system:schedule` 或触发者
  5. 轮询该 run 至终态
  6. 测试末尾以 admin API `DELETE /schedules/{id}` 清理

**断言重点：** cron 写入 → 立即触发 → run 归属为调度而非手动 的全链路。这是 `schedule-trigger.authed-admin.spec.ts`（3 条）的深度组合。
**前置依赖：** profile 必须可创建且 trigger 端点可用——见第 6 节未闭环项。

---

### Journey 5 — Local 套件接入与首跑

**File:** `tests-e2e/journey-suite-onboard.authed-admin.spec.ts`
**角色：** admin
**前置：** 宿主机存在一个本地测试目录（用 `examples/sample_tests`）

#### J5.1 链接本地 suite 到首跑

**Steps:**
  1. 登录 admin，Sidebar 点 `open-add-suite-button`
     - expect: `add-suite-modal` 可见，`suite-tab-local` 默认激活
  2. 在 `link-path-input` 填 `examples/sample_tests` 的绝对路径，点 `link-submit`
     - expect: 反馈成功，`clone-feedback`（或 link 成功等价物）显示成功
  3. 关闭 modal，Sidebar 出现新 suite
     - expect: 该 suite 项可见
  4. 点 suite 进 TriggerRun modal，触发一次 run（衔接 J2.1 后半）
     - expect: run 入表、流转到 completed
  5. 测试末尾以 admin API `DELETE /tests/{suite}` 清理

> git clone 路径不在此旅程（已在 `suite-management.spec.ts §6.5` 测非白名单 URL 拒绝）。本旅程聚焦 **Local link → 入 Sidebar → 首跑** 这条最短接入路径。

---

## 4. 断言策略与稳定性

- **轮询而非 sleep：** run 终态用 `expect.poll` 或 `page.waitForResponse` + 重试，不写 `page.waitForTimeout`。
- **数据隔离：** 每个旅程 spec 末尾用 `page.request`（admin token）清理自己造的 user/profile/schedule/suite/run。**严禁依赖任何预置 demo 数据**——现无 seed 机制（见调研结论）。
- **ID 唯一化：** 新建用户名/suite 名带时间戳后缀，避免串行 spec 间碰撞。
- **mock 兜底边界：** 旅程的核心价值是真后端贯通，**原则上不 mock**。但当某端点依赖未闭环（如 diff 需多次 run 产生差异），允许对**单个**端点用 `page.route` 兜底，并在测试顶部注释说明为何 mock。

---

## 5. 完成定义 (Definition of Done)

- [ ] 5 个 journey spec 文件创建，全部 `test:ui` 绿
- [ ] 每个 spec 末尾有清理逻辑，跑后 DB 不残留测试数据（人工抽查 `/users`、`/profiles`、`/schedules`、`/runs`）
- [ ] 计划第 6 节列出的前置依赖项**已被解决或在 spec 注释中标注 mock 兜底**
- [ ] 新 spec 复用第 6 节定义的共享 fixture（`auth.ts` / `api.ts`），不重新内联 `login()`

---

## 6. 前置依赖与未闭环项（诚实清单）

> 这些项**不属于本计划**，但是本计划旅程能否真跑通的前提。需在前置基础设施工作中补齐。

### 6.1 基础设施缺口（必须在旅程落地前补）
1. ~~**共享登录 fixture**：`tests-e2e/fixtures/auth.ts` 导出 `loginAs(page, role)`，收敛 21 份重复 `login()`；统一 `ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD`（禁默认弱密码）。~~ ✅ **已落地（2026-07-03）**：`fixtures/auth.ts`（无弱密码兜底，未设 env 直接抛错）+ `global-setup.ts` 程序化产 `tests-e2e/.auth/{admin,user}.json` storageState + `playwright.config.ts` 加 `chromium-authed-admin`/`chromium-authed-user` 两 project（testMatch 隔离，零回归现有 21 spec）+ `helpers/api.ts`（`loginAndGetContext`/`createUser`/`deleteUser`/`createProfile`/`createSchedule`/`pollRunToTerminal` 等）+ smoke spec 验真绿（15.8s）。e2e-user 常驻账号由 globalSetup 幂等建。
2. ~~**共享 API helper**：`tests-e2e/helpers/api.ts` 导出 `getAuthToken`/`createUser`/`createProfile`/`createSchedule`/`deleteUser`/`pollRunToTerminal`/`linkSuite`/`deleteSuite`，旅程 spec 复用以造数与清理。~~ ✅ 已落地（同上）。
3. **playwright.config.ts 扩展**：~~可选加 globalSetup 预登录 admin 产 storageState，per-role projects（admin/user）。~~ ✅ 已落 globalSetup + 两 authed project（用 testMatch 隔离）。CI 自动起前后端的 `webServer` 仍属后续。

### 6.2 业务功能未闭环项（旅程需据此取舍）
- **schedule 真跑通**：J4 依赖 profile + schedule trigger 端点联调。若仍跳过，J4 标记 `test.skip` 并附 issue 号。
- **非 admin 触发 run**：J1 后半若要测新用户也能触发 run（验证非 admin 路径），依赖后端对非 admin `POST /runs` 的实际可用性。当前未验证。
- **run 产生可对比 diff**：J2.2 依赖 sample_tests 在两次跑间产生差异。若现成 sample 一致，需补一个"故意变 X 用例"的 sample 或对 `/diff` mock 兜底。

### 6.3 本计划**不**覆盖（已在别处）
- 单组件状态机 → `specs/ui-test-plan.md`
- 角色矩阵 × 越权 → `specs/role-matrix-test-plan.md`
- a11y 焦点/键盘 → `specs/a11y-test-plan.md`
