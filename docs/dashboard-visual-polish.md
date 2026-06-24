# Dashboard 视觉打磨

## Context

用户反馈 dashboard "说不出来的不好看"。逐项诊断后定位到 4 处具体、低风险的硬伤(主因是**配色语义错误**与**头重脚轻**),而非整体风格问题。本次只做针对性打磨,不重做设计语言。

技术栈:Semi UI 2.100 + CSS Module(`App.module.css`)+ CSS 变量(非 Tailwind)。

## 改动

| # | 问题 | 改法 | 文件 |
|---|---|---|---|
| 1 | 状态徽章撞色:`completed && !passed` 与 `failed` 同为 `red`,无法区分 | 三档色:全过=`green`、完成但有失败=`orange`(警示)、执行失败=`red` | `components/runStatus.ts`、`RunsTable.tsx` |
| 2 | 进度条二元判色:非全过即红,75% 也红 | 按通过率分档:`>=80` 绿 / `50–79` 橙(`--semi-color-warning`)/ `<50` 红 | `components/runStatus.ts`、`RunsTable.tsx` |
| 3 | 退出按钮 `type="danger"` 常驻红,层级过高 | 去 `type="danger"`,改中性灰(与主题/语言按钮一致);删孤儿死代码 `.logoutButton` | `Header.tsx`、`App.module.css` |
| 4 | KPI 卡片过空、信息密度低 | 压 padding(`1.25→0.9rem`)、缩图标(`48→40`)、降字号(`1.85→1.6rem`)、压顶部 margin(`2→1.25rem`) | `App.module.css` |
| 5 | 数据少时底部大片裸留白(头重脚轻) | 表格卡片 `min-height: calc(100vh - 260px)` 撑起体量;数据多时仍自然增高、不裁剪 | `App.module.css` |

### 颜色逻辑抽为纯函数(TDD)

新增 `frontend/src/components/runStatus.ts`,导出两个纯函数,替换 `RunsTable` 原内联逻辑:

- `statusTagColor(status, passed)` → Semi Tag 色名(`completed && !passed` 或 `null` → `orange`,与 `failed` 的 `red` 区分)
- `passRateColor(rate)` → `var(--semi-color-*)` 分档色

单测见 `runStatus.test.ts`(覆盖六状态分支 + 通过率边界 `0/49/50/79/80/100`)。

## 验证

- `npx tsc --noEmit` — 通过(顺带修了 `record.passed` 为 `boolean | null` 的签名)
- `npm run test:unit`(vitest) — 17 passed
- `npm run test:ui`(playwright) — 5 passed

> e2e 注意:后端因 **SEC-2** 拒绝弱密码,seeded admin 密码不是 `admin123`。跑 e2e 需带真实密码:
> `E2E_ADMIN_PASSWORD='<.env 里的 QARUNNER_ADMIN_PASSWORD>' npm run test:ui`

改动均不触碰 DOM 结构、`data-testid`、i18n 文案与后端,e2e 回归风险低。
