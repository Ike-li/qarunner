# 可访问性 (a11y) 测试计划

> 创建日期：2026-07-03
> 范围：键盘导航 / 焦点管理 / 屏幕阅读器语义 / 表单关联 / 焦点指示器
> 优先级：P2（在用户旅程、角色矩阵之后）
> 依据：`frontend/src/a11y.ts`、`frontend/src/hooks/useDialogA11y.ts`（FE-5 a11y 改造已全部落地）
> 框架：Playwright 原生键盘 API；可选用 `@axe-core/playwright` 做无门槛自动化扫描（见阶段 0）

---

## 0. 背景与现有基础设施

### 0.1 已落地的 a11y 改造（FE-5）
- `a11y.ts`：`activateOnKey(action)` — 让非按钮元素（div/span 带 onClick）在 Enter/Space 时 `preventDefault` 并触发，使键盘可达。用于 `RunsTable`、`ProjectSidebar`。
- `hooks/useDialogA11y.ts`：`useDialogA11y({ isOpen, onClose })` — 开启时聚焦容器内首个可聚焦元素、Tab 陷阱、Esc 关闭、关闭后还原焦点；keydown 监听绑容器（非 document），堆叠 dialog 不双重响应。当前 TriggerRunModal、AddSuiteModal、ScheduleModal、RunDetailsDrawer、FullscreenTerminalOverlay、FullscreenReportOverlay 已接入；UserManagementModal 仍依赖 Semi Modal portal 自带语义与 Esc 行为。
- 单元测试：`a11y.test.ts`、`useDialogA11y.test.ts`。
- E2E 测试：`a11y-axe.authed-admin.spec.ts`、`a11y-dialog-focus.authed-admin.spec.ts`、`a11y-keyboard-journey.authed-admin.spec.ts`、`a11y-form-semantics.authed-admin.spec.ts`。

### 0.2 关键缺口（来自调研）
早期计划中 4 个 Semi `<Modal>` + 1 个 `<SideSheet>` 未接入 `useDialogA11y`。当前状态如下：

| 组件 | Semi 组件 | useDialogA11y | a11y 测试现状 |
|------|----------|---------------|---------------|
| AddSuiteModal | `<Modal>` | ✅ | `a11y-dialog-focus.authed-admin.spec.ts` |
| ScheduleModal | `<Modal>` | ✅ | `a11y-dialog-focus.authed-admin.spec.ts` |
| TriggerRunModal | `<Modal>` | ✅ | `a11y-dialog-focus.authed-admin.spec.ts` |
| UserManagementModal | `<Modal>` | ❌（依赖 Semi portal） | `a11y-dialog-focus.authed-admin.spec.ts` 只验 dialog 语义 + Esc |
| RunDetailsDrawer | `<SideSheet>` | ✅ | `a11y-dialog-focus.authed-admin.spec.ts` |
| FullscreenTerminalOverlay | 自绘 div | ✅ | `a11y-dialog-focus.authed-admin.spec.ts` |
| FullscreenReportOverlay | 自绘 div | ✅ | `a11y-dialog-focus.authed-admin.spec.ts` |

**结论**：a11y E2E 层已建立，后续重点不是从零补测试，而是继续收紧 UserManagementModal 焦点契约、减少 Semi UI 噪声白名单，并把键盘旅程纳入稳定的日常回归。

### 0.3 FE-5 验收目标回顾
> "键盘可完整操作主流程"（验证：Tab/Enter/Esc 完整走登录→触发 modal→用户 modal→抽屉/终端，无鼠标）

本计划将此验收目标转成可重复跑的自动化断言。

---

## 1. 不放过的优先级与阶段

按"风险 × 自动化可达"分 4 阶段，每阶段独立可交付：

| 阶段 | 主题 | 投入 | 价值 |
|------|------|------|------|
| 0 | axe-core 自动扫描 | 低 | 30-40% 问题自动捕获 |
| 1 | Modal/Drawer 焦点陷阱 + Esc + 焦点还原 | 中 | 5 个未接入 hook 的对话框是最大风险 |
| 2 | 键盘可达主流程旅程（无鼠标跑通） | 中高 | FE-5 验收原话 |
| 3 | 表单 label 关联 + 颜色对比/焦点指示器 | 中 | 视觉/屏幕阅读器辅助 |

---

## 2. 测试场景

### 阶段 0 — axe-core 自动扫描（基线）

**File:** `tests-e2e/a11y-axe.authed-admin.spec.ts`
**库：** `@axe-core/playwright`（**引入需向用户确认**——属新增依赖，见项目宪法 §4 ⚠️ 边界）

#### A0.1 登录页无 axe 违规
  1. `goto('/')`，登录页渲染
  2. `await AxeBuilder().withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa']).analyze()`
     - expect: `violations.length === 0`（或白名单已知 Semi UI 噪声）

#### A0.2 Dashboard 主视图无严重违规
  1. admin 登录，等 dashboard 稳定
  2. 对 `main` 区域跑 axe
     - expect: 无 critical/serious 违规（moderate/minor 列入白名单）

#### A0.3 每个打开的 Modal/Drawer 跑 axe
  - 对每个 modal 打开态快照跑 axe，断言 `role=dialog`、`aria-modal`、可访问名存在。

> 投入说明：axe 依赖若用户拒绝，本阶段跳过，专注阶段 1–3 的纯 Playwright 键盘断言（不依赖）。

---

### 阶段 1 — Modal/Drawer 焦点管理专项

**File:** `tests-e2e/a11y-dialog-focus.authed-admin.spec.ts`
**覆盖对象：** 5 个 Modal + 1 SideSheet + 2 全屏 Overlay

每个对话框测以下 4 个不变量（`useDialogA11y` 契约）：

| # | 不变量 | 断言 |
|---|--------|------|
| F1 | 打开后焦点进入 | 打开瞬间 `document.activeElement` 在容器内（首个可聚焦元素或容器本身） |
| F2 | Tab 陷阱 | 在容器末元素按 Tab → 焦点回到首元素；首元素 Shift+Tab → 焦点到末元素 |
| F3 | Esc 关闭 | 容器有焦点时按 Esc → `onClose` 触发，容器 DOM 移除/隐藏 |
| F4 | 焦点还原 | 关闭后 `document.activeElement` 回到打开前的触发器按钮 |

#### A1.1 TriggerRunModal 四不变量
  1. admin 登录，`page.keyboard.press('Tab')` 走到 `open-trigger-button`，记下为 preTrigger
  2. 按 Enter 打开 modal
     - expect: F1 — `document.activeElement` 在 trigger-modal 内
  3. Tab 在 modal 内循环
     - expect: F2 — 焦点不逃逸到 modal 外
  4. 按 Esc
     - expect: F3 — `trigger-modal` 不可见
     - expect: F4 — `document.activeElement === preTrigger`

#### A1.2 AddSuiteModal 四不变量
#### A1.3 ScheduleModal 四不变量
#### A1.4 UserManagementModal 四不变量
#### A1.5 RunDetailsDrawer (SideSheet) 四不变量
  - 触发器：RunsTable 行（需 keyboard 可达 → 依赖 `activateOnKey` 已接）
#### A1.6 FullscreenTerminalOverlay 四不变量（已接 hook，验证 hook 真生效）
#### A1.7 FullscreenReportOverlay 四不变量

> 当前 A1.1–A1.3、A1.5–A1.7 已覆盖已接 `useDialogA11y` 的对象；A1.4 UserManagementModal 仍是 Semi portal 语义验证，尚未收紧到完整 F1/F2/F4 焦点契约。

---

### 阶段 2 — 键盘可达主流程（FE-5 验收旅程）

**File:** `tests-e2e/a11y-keyboard-journey.authed-admin.spec.ts`
**约束：** 全程仅 `page.keyboard`，不 `page.click` / `page.fill`（用 `page.focus`+type 例外见下）

#### A2.1 无鼠标完成登录
  1. `goto('/')`
  2. Tab 到 `login-username`（或聚焦），keyboard.type 用户名
  3. Tab 到 `login-password`，type 密码
  4. Tab 到 `login-submit`，Enter
     - expect: dashboard 可见

#### A2.2 键盘打开 TriggerRun modal、填表、提交
  1. 承接 A2.1，Tab/Shift+Tab 导航到 `open-trigger-button`，Enter
     - expect: `trigger-modal` 打开，焦点进入
  2. Tab 到 `trigger-args-input`，type `'-k smoke'`
  3. Tab 到 `trigger-submit-button`，Enter
     - expect: modal 关闭，runs 表新增行（衔接 journey J2）

#### A2.3 键盘打开 RunDetails drawer + 全屏终端 + Esc 还原
  1. runs 表首行已聚焦，Enter 开 drawer
     - expect: drawer 打开焦点入
  2. Tab 到 fullscreen-toggle，Enter
     - expect: `FullscreenTerminalOverlay` 开，焦点入
  3. Esc
     - expect: 全屏关，焦点回 fullscreen-toggle
  4. Esc
     - expect: drawer 关，焦点回 runs 表首行

**断言重点：** 焦点逐层还原链——这是 FE-5 验收的真正含义，多 dialog 堆叠时焦点不丢。

#### A2.4 键盘切换主题/语言
  - Sidebar/Header Tab 顺序可达 theme/language toggle，Enter 切换，断言视觉变化。

---

### 阶段 3 — 表单语义 + 焦点指示器

**File:** `tests-e2e/a11y-form-semantics.authed-admin.spec.ts`

#### A3.1 每个 input 有可访问 label 关联
对每个表单（Login / TriggerRun / AddSuite / Schedule / UserMgmt）：
  - expect: 每个 input 有显式 `<label htmlFor>` 关联或 `aria-label`，或被 `<label>` 包裹
  - 断言方式：`page.locator('input').evaluateAll` 检查 `id` 存在且 `<label for=id>` 存在，或 `aria-label`/`aria-labelledby` 存在
  > FE-5 Stage A 应已做 htmlFor 关联，本测试验收其真到位。

#### A3.2 错误提示与字段关联
  - 触发表单错误（空提交/错值）
     - expect: error 元素有 `role="alert"` 或 `aria-live`，且与对应 input 通过 `aria-describedby` 关联

#### A3.3 所有可聚焦元素有可见焦点指示器
  - 对关键交互（modal 打开按钮、表单 input、表行、toggle），focus 后截图或检查 `:focus-visible` outline 非透明
  > 自动化断言焦点环较脆，可降级为：focus 后 `getComputedStyle` outline-color 非 `transparent`/`rgba(0,0,0,0)`。

#### A3.4 图标按钮有可访问名
  - 对所有 `title=` 的图标按钮，断言同时有 `aria-label`（或 `aria-labelledby`）
  - 断言：`element.getAttribute('aria-label')` 非空
  > FE-5 Stage B 已加 aria-label，本测试验收。

#### A3.5 颜色对比（可选，依赖 axe 阶段 0）
  - 若引入 axe，`wcag2aa` 的 color-contrast 规则覆盖；否则标 skip。

---

## 3. 断言技术要点

- **焦点断言**统一用 `const a = await page.evaluate(() => document.activeElement?.id)`，对比预期容器内元素 id（组件需有稳定 id 或 data-testid，焦点元素可用 `aria-describedby`/`data-testid` 定位）。若 Semi Modal 内元素无稳定 id，先定位容器 `[role=dialog]`，再断言 `activeElement` 是其后代（`contains`）。
- **Tab 陷阱**断言：记首/末可聚焦元素（`F1` 前预扫），Tab N 次后断言 activeElement 仍在容器内且循环回到首元素。
- **Esc**用 `page.keyboard.press('Escape')`，且确保焦点在容器内（keydown 监听绑容器）。
- **焦点还原**记 `preTriggerId = await page.evaluate(() => document.activeElement?.id)` 于打开前，关闭后断言相等。

---

## 4. 完成定义 (Definition of Done)

- [ ] 阶段 0：若允许引入 axe，A0.1–A0.3 绿（含 Semi UI 噪声白名单）；若不允许，标 skip 并记录
- [ ] 阶段 1：A1.1–A1.7 跑在 `chromium-authed-admin`；已接 hook 的对象验证焦点进入/Esc/焦点还原，UserManagementModal 当前只验 Semi dialog 语义 + Esc
- [ ] 阶段 2：A2.1–A2.4 绿（此为 FE-5 验收核心）
- [ ] 阶段 3：A3.1–A3.4 绿；A3.5 视 axe 决定
- [ ] 所有 a11y spec **不依赖新生产代码改动即可绿的部分为交付**，依赖改动的标 fixme

---

## 5. 依赖与已知预期红

> 本计划只写测试。以下改动**不属于本计划**，但决定哪些测试当下能绿、哪些预期红：

1. **UserManagementModal 接入 `useDialogA11y` 或补等价焦点契约** — 当前只验 Semi portal 的 dialog 语义 + Esc；若要完整覆盖 F1/F2/F4，需要把它纳入同一 hook 契约或在测试中明确 Semi 行为边界。
2. **表单 htmlFor 关联、aria-label**（FE-5 Stage A/B）— A3.1/A3.4 验收，若改造已完整则绿，否则 fixme。
3. **`@axe-core/playwright` 依赖**（阶段 0）— ⚠️ 新增依赖，需向用户确认（项目宪法 §4 边界）。

**唯一无需改动即可交付**：阶段 2 键盘旅程（A2.1–A2.4，依赖现有 `activateOnKey` + 已接 hook 的 overlay）+ 阶段 1 的 A1.6/A1.7。这是本计划真正的"绿"产出。
