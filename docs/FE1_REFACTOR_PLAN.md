# FE-1 — 拆分 App.tsx god component（Refactor Plan）

> 生成日期：2026-06-23
> 依据：`docs/REMEDIATION_PLAN.md` 前端维度 FE-1（XL）；FE-4/FE-3/FE-2 + SEC-6 客户端已落地
> 目标分支：master
> 深度档位：**混合**（展示组件 + 仅在 props 会爆炸处抽焦点 hook）

---

## 0. 背景与现状

`frontend/src/App.tsx` 当前 **3684 行单组件**：`export default function App()` 从第 424 行直贯到 3684（约 3260 行一个函数），内含约 **58 个 `useState`、7 个 `useRef`、17 个 `useEffect`、20 个 `useCallback`**，外加约 **1900 行 JSX 主体**与约 300 行 i18n 字典。这是整改计划 FE-1，也是 FE-5（a11y）的前置。

**目标**：在**完全保持现有行为**的前提下，把巨石拆成可维护的组件树 + 少量域 hook，让 `App.tsx` 回落到约 650 行的「域状态 + effect + 编排」骨架。

**深度对齐结论**：选「混合」档——展示组件 + 仅在 props 会爆炸处（终端 UI 状态、文件树选择）抽焦点 hook。**SSE/轮询流式 effect 保留在 App 不动**（FE-3 刚稳定其重连 dep 链，跨模块搬动风险高，收益低）。

## 1. 目标架构（文件布局）

```
frontend/src/
  App.module.css          ← 保持单一共享(所有组件 import 同一份，类名 hash 一致→e2e 选择器不变)
  types.ts                ← 7 个 interface: TestSummary/ReportRef/Run/UserProfile/Profile/Schedule/TreeNode
  i18n.ts                 ← translations(en+zh) + type Lang + type TranslationKey；t() 仍由 App 持有并下传
  logUtils.ts             ← 已存在(FE-4)，不动
  hooks/
    useFileTreeSelection.ts  ← selectedFiles/expandedFolders + getAllFilesUnderNode/getNodeCheckState/handleToggleNode/toggleFolder
    useTerminalView.ts       ← 终端 UI 状态(logLevelFilter/logSearchQuery/terminalFontSize/height/fullscreen/wrap/autoscroll/copySuccess/drawerTab/isDrawerExpanded) + getFilteredLogs/formatLogLine/renderFormattedLogs/downloadLogs/handleCopy
  components/
    LoginScreen.tsx          RunDetailsDrawer.tsx
    Header.tsx               TerminalConsole.tsx (含全屏终端 overlay)
    StatsCards.tsx           TriggerRunModal.tsx
    ProjectSidebar.tsx       UserManagementModal.tsx
    RunsTable.tsx            ScheduleModal.tsx
    TestFileTree.tsx
  App.tsx  ~650 行: 域状态 + fetcher/handler + 17 effect(SSE/轮询原样保留) + 组件编排
```

## 2. 关键不变量 / 约束（拆分时必守）

1. **CSS 单文件共享**：不拆 `App.module.css`；各组件 `import styles from '../App.module.css'`。同一 `.module.css` 多处 import → 类名 hash 一致，故 e2e 的 `class*="profileUsername"/"tdUsername"/"modalCloseButton"/"formErrorAlert"` 等子串选择器**保持有效**。**严禁改类名/拆 CSS**。
2. **文案/placeholder/按钮 label 逐字保留**——e2e 靠它们定位（`qarunner`、`QA TEST ORCHESTRATION`、`Sign In`、`Trigger Run`、`Users`、`Execution Records`、`Cancel`、`Add User Account`、placeholder `Enter username` 等）。
3. **SSE/轮询 effect + 流式状态（`streamedStdout`/`isStreaming`）保留在 App**；只把终端 **UI 偏好**搬进 `useTerminalView`，流数据作为 prop 下传 `TerminalConsole`。
4. **派生值** `selectedRun = selectedRunDetails || runs.find(...) || null`（App.tsx:1257）留在 App，下传抽屉/终端。
5. **strict tsc 是接线网**：`strict + noUnusedLocals + noUnusedParameters` 强制校验 props 漏传/多传/类型不符。每个组件按需 import 自己用的 lucide-react 图标。
6. **行为零变更**——纯结构搬运，不顺手改逻辑/样式/文案。

## 3. 分阶段实施（每阶段一次独立提交，阶段间须过验证门）

| Stage | 内容 | 产出文件 |
|---|---|---|
| **1** 静态抽取（零逻辑风险） | 搬 7 接口；搬 translations + 导出 Lang/TranslationKey；App import，`t` useCallback 留 App | `types.ts`、`i18n.ts` |
| **2** 叶子展示 + 树 hook | `LoginScreen`(~10 props)、`StatsCards`；`useFileTreeSelection` + `TestFileTree`(递归)。注意 `selectedFiles` 被触发 Modal/存方案也读——hook 在 App 调用、App 仍可读其值下传 | `LoginScreen` `StatsCards` `useFileTreeSelection` `TestFileTree` |
| **3** 三个 Modal（自包含 overlay） | `TriggerRunModal`(最大，表单+树+env)、`UserManagementModal`、`ScheduleModal`。表单 submit handler 留 App、作 prop 传入 | 三个 `*Modal.tsx` |
| **4** Header / Sidebar / RunsTable | `Header`(capsule+admin+主题/语言切换+登出)、`ProjectSidebar`(套件+方案卡，用 getProfileRunStats)、`RunsTable`(分段筛选 tab+运行表行) | `Header` `ProjectSidebar` `RunsTable` |
| **5** 抽屉 + 终端（最大块，带 hook） | `useTerminalView` 收拢终端 UI 状态与日志渲染 helper；`RunDetailsDrawer`(壳+状态徽章+信息矩阵+tab+报告 iframe)、`TerminalConsole`(各控件 + 全屏 overlay) | `useTerminalView` `RunDetailsDrawer` `TerminalConsole` |

> XL 任务：按 Stage 顺序推进，每 Stage 提交即落袋；各 Stage 独立有价值、可无缝续接。

## 4. 验证（每阶段 + 终局）

- **每阶段门禁**（在 `frontend/`）：`npx tsc --noEmit`（0 错） + `npx vitest run`（logUtils 9 passed） + `npm run build`（vite 构建过）。
- **终局行为验证**（`[已端到端验证]` 目标）：`npm run test:ui`（Playwright 5 用例：登录/失败校验/登录后看板/触发 Modal/用户 Modal），需后端 uvicorn + 浏览器。不可用则 `npm run dev` + 后端手测这 5 条流，并**显式标证据等级**（仅 tsc 过 = `[读代码推断]`，跑通 e2e = `[已端到端验证]`）。
- 抽屉/终端/调度/profile 侧栏 e2e 未覆盖 → Stage 4/5 后重点手动验证这些路径。

## 5. 提交纪律

- 精确 `git add` 改动文件、分 Stage 提交、信息结尾带 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`（pre-commit 有 gitleaks 钩子）。
- 完成后更新整改进度记录（FE-1 进度 + 实际落地的组件清单）。
