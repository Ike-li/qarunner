# 角色矩阵 × 越权测试计划

> 创建日期：2026-07-03
> 范围：匿名 / admin / user 三角色 × 组件交叉覆盖 + 后端 API 越权 HTTP 断言
> 优先级：P1（在用户旅程之后）
> 依据：`frontend/src/components/` 角色判断点调研、`docs/API_REFERENCE.md` §越权行为

---

## 0. 背景与目标

系统三角色：

| 角色 | 来源 | 能力 |
|------|------|------|
| 匿名 | 未登录 | 只能访问登录页 |
| admin | `UserProfile.role === 'admin'` | 全功能 + 用户管理 + cleanup |
| user | `UserProfile.role === 'user'` | 自身资源 CRUD，看不到他人资源 |

早期权限测试主要集中在 `user-mgmt.authed-admin.spec.ts`（10 条，覆盖 admin gate + 管理按钮）和 `user-management.authed-admin.spec.ts`（6 条，重叠）。角色矩阵专项已拆到 `role-anonymous.spec.ts`、`role-user-gate.authed-user.spec.ts`、`role-api-scoping.authed-admin.spec.ts`、`role-user-ops.authed-admin.spec.ts`。原始明显缺口包括：匿名重定向、非 admin 越权 API 行为、owner-scope 静默过滤、最后 admin 保护、登录限流。

本计划分两层：
- **A. UI 层**：组件 × 角色 × 可见/可操作 交叉矩阵
- **B. API 层**：以 `page.request` 带各角色 token 直接打 API 验证越权（403/404/静默过滤/限流）

---

## 1. 角色判断点清单（来自源码调研）

### 1.1 Admin-only 前端 gate
| 位置 | 行为 | 已测? |
|------|------|------|
| `Header.tsx:34` | `role === 'admin'` 才渲染 `open-users-button` | ✅ user-mgmt.authed-admin |
| `DashboardLayout.tsx:55` | `role === 'admin'` 才挂 `UserManagementModal` | ❌ |
| `useUsers.ts:25-28` | `isAdmin=false` 时 `fetchUsers` 直接 return 不发请求 | ❌ |
| `UserManagementModal.tsx:89,110` | toggle-role / delete 操作 | 部分（按钮可见性测，**真生效未测**） |

### 1.2 Owner-scope（后端强制，前端仅展示）
| 资源 | 行为 |
|------|------|
| `GET /runs` | 非 admin 静默过滤，只返自己的 |
| `GET /profiles` / `GET /schedules` | 非 admin 静默过滤 |
| `GET /runs/trend` / `/runs/{id}/diff` | 非 admin 基线只在自己 run 里选 |
| `GET /cases/history` | 非 admin 按 `created_by` 过滤 |
| 单资源 `GET/PUT/DELETE /{id}` | 他人存在→403，自身不存在→404 |
| `POST /runs` | 非开放 executor_mode 对非 admin 400；并发上限非 admin 20、admin 豁免 |
| `POST /users` / `GET /users` / `PUT /users` / `DELETE /users` | 仅 admin |

### 1.3 匿名
- 无 React Router。SPA 单页：`auth.isAuthenticated === false` → 渲染 `LoginScreen`。
- "直接访问受保护 URL 应重定向"——**本项目无路由概念**，所有受保护内容靠前端 gate + 后端 401。匿名访问真 API 不带 token → 401。**本计划测匿名对真 API 的 401 而非前端重定向**（无路由可重定向）。

---

## 2. 测试场景

### A. UI 层交叉矩阵

#### R-UI-1 匿名只能见登录页

**File:** `tests-e2e/role-anonymous.spec.ts`

**R-UI-1.1 未登录访问根 URL 渲染登录页**
  1. 不带 cookie `goto('/')`
     - expect: `login-title` 可见
     - expect: `stat-total` **不可见**（dashboard 未渲染）
     - expect: `open-trigger-button` / `open-users-button` **不可见**

#### R-UI-2 非管理员头部不显示用户管理入口

**File:** `tests-e2e/role-user-gate.authed-user.spec.ts`（使用 `chromium-authed-user` storageState）

**R-UI-2.1 user 角色登录后头部按钮集合**
  1. 以非 admin user 登录
     - expect: `open-trigger-button` 可见（普通用户可触发 run）
     - expect: `open-add-suite-button` 可见（普通用户可加 suite）
     - expect: `open-users-button` **不可见**
     - expect: `profile-role` 显示 `user`

**R-UI-2.2 user 角色即便尝试打开用户管理 modal 也不会渲染**
  1. user 登录
  2. 用 `page.evaluate` 断言不存在 `user-modal`（因 DashboardLayout 条件不满足）
  3. （若前端无路径触发）用 `page.request` 以 user token 调 `GET /users`
     - expect: **403**（后端强vpn gate 验证）

#### R-UI-3 `useUsers` hook 对非管理员不发请求

**File:** 同上（用 console 拦截验证）

**R-UI-3.1 user 登录后无 `/users` 网络请求**
  1. user 登录，监听网络请求
  2. 等待 dashboard 稳定（所有初始请求完成）
     - expect: 未出现对 `/users` 的 GET（`useUsers.fetchUsers` 内 `isAdmin=false` 提前 return）

> 这条验证前端守门，避免无谓 403 噪声。

---

### B. API 层越权断言

> 走 `page.request` + `Authorization: Bearer <token>`，不依赖 UI。每 spec 准备 3 个 token：admin / userA / userB。

#### R-API-1 静默过滤（列表端点）

**File:** `tests-e2e/role-api-scoping.authed-admin.spec.ts`

**R-API-1.1 `GET /runs` 非 admin 只见自己的**
  1. admin 造一条 run（admin_scoped_run）
  2. userA 造一条 run（userA_scoped_run）
  3. 以 userA token `GET /runs`
     - expect: 响应含 userA_scoped_run
     - expect: 响应**不含** admin_scoped_run

**R-API-1.2 `GET /profiles` / `GET /schedules` 同理静默过滤**
  - 同上结构，admin 与 userA 各造一个 profile/schedule，userA 列表不含 admin 的。

**R-API-1.3 `GET /runs/trend` 非 admin 只见自己 run 的趋势点**
  - admin 造 2+ 条同 tests_path run，userA 造 1 条同 tests_path run
  - 以 userA token `GET /runs/trend?tests_path=X`
     - expect: 趋势点只含 userA 的 run（admin 的不出现）

#### R-API-2 越权单资源：403 vs 404 差异

**File:** 同上

**R-API-2.1 `PUT /profiles/{admin_profile_id}` 以 userA token**
  - admin 造 profile P
  - userA token `PUT /profiles/P`
     - expect: **403**（资源存在但非 owner）

**R-API-2.2 `PUT /profiles/{不存在的 id}` 以 userA token**
  - userA token `PUT /profiles/00000000-0000-0000-0000-000000000000`
     - expect: **404**（资源不存在，泄漏与否的分水岭）

> 这对断言验证后端不通过 404 泄漏"资源属于别人"这一信息——见 API_REFERENCE §越权返回 403-vs-404 设计。

**R-API-2.3 `DELETE /runs/{admin_run_id}` 以 userA token → 403**
**R-API-2.4 `DELETE /schedules/{admin_schedule_id}` 以 userA token → 403**
**R-API-2.5 `GET /runs/{admin_run_id}/diff` 以 userA token → 403（且基线选择只在自己 run 里）**
**R-API-2.6 `GET /runs/{admin_run_id}/stream` 以 userA token → 403**（SSE 也需 owner gate）

#### R-API-3 admin 保护规则

**File:** 同上

**R-API-3.1 非管理员 `POST /users` → 403**
  - userA token 调 `POST /users` 建用户
     - expect: 403

**R-API-3.2 `DELETE /users/{admin 自己}` → 400（不能删自己）**
  - admin token `DELETE /users/admin`
     - expect: 400

**R-API-3.3 `PUT /users/{最后一个 admin}` 降级 → 400（不能降最后一个 admin）**
  - 若库内仅 admin 一个 admin，admin token `PUT /users/admin {role:'user'}`
     - expect: 400
  - 若库内有多个 admin，先确保只剩一个再做此断言

**R-API-3.4 `POST /runs/cleanup` 非管理员 → 403**
  - userA token `POST /runs/cleanup?retention_days=30`
     - expect: 403

#### R-API-4 executor_mode 与并发限制

**File:** 同上

**R-API-4.1 非管理员 `POST /runs` executor_mode=subprocess → 400**
  - userA token `POST /runs {executor_mode:'subprocess', ...}`
     - expect: 400（subprocess 非开放）

**R-API-4.2 非管理员并发超限 → 429（条件性）**
  - userA 先堆 20 条 queued+running（admin 豁免，userA 不豁免），再触发第 21 条
     - expect: 429 `Retry-After`
  - 注：此条造数成本高，标 `test.skip` 并附 issue 直到有廉价造数手段

#### R-API-5 匿名 API

**File:** `tests-e2e/role-anonymous.spec.ts`

**R-API-5.1 不带 token 访问受保护端点 → 401**
  1. `page.request.get('/runs')` 不带 Authorization
     - expect: 401
  2. 同验 `/profiles`、`/schedules`、`/auth/me`
     - expect: 全 401

**R-API-5.2 公开端点不带 token 可用**
  1. `GET /health` 不带 token
     - expect: 200 `{status:'ok'}`
  2. `POST /auth/login` 带错凭据
     - expect: 401

#### R-API-6 登录限流

**File:** 同匿名 spec

**R-API-6.1 连续 5 次错密码后触发 lockout**
  1. 对 admin 用错密码连发 5 次 `POST /auth/login`
     - expect: 第 5 次（或第 6 次，依实现）返回 429 + `Retry-After` 头
  2. （清理）等待 lockout 过期或以其他方式重置——**注意**：此条会污染后续 admin 登录，**必须放最末且带长 retry 间隔或 test.skip**
  > 标注为 `test.skip`，附 issue：需提供测试专用限流旁路（如 `TESTING=1` 跳过限流），否则 CI 风险大。

#### R-API-7 角色/密码操作真生效（补 `user-mgmt.authed-admin` 缺口）

**File:** `tests-e2e/role-user-ops.authed-admin.spec.ts`

**R-API-7.1 toggle-role 后下次该用户登录 role 真变化**
  1. admin 建用户 X 为 `user`
  2. admin `PUT /users/X {role:'admin'}`
  3. 以 X 重新 `POST /auth/login` + `GET /auth/me`
     - expect: `role === 'admin'`

**R-API-7.2 改密码后旧密码失效、新密码可用**
  1. admin 建 X，初始密码 P0
  2. admin `PUT /users/X {password:P1}`
  3. 用 P0 登录
     - expect: 401
  4. 用 P1 登录
     - expect: 200 + token

> 这两条补 `user-management.authed-admin.spec.ts` "只点按钮不验证后端真生效" 的缺口。

---

## 3. 断言策略与稳定性

- **三 token 准备**：spec beforeEach 用 admin `POST /auth/login` 拿 admin token，用 admin token `POST /users` 造 userA/userB 并各自登录取 token。afterEach 删 userA/userB。
- **资源归属造数**：admin 与 userA 各自用自己 token 造 run/profile/schedule，确保归属正确后再发越权请求。
- **403 vs 404 区分**：用 `expect(res.status()).toBe(403)` 与 `.toBe(404)` 精确断言，不用 `>=400`。
- **并发 429 测试**：标 skip；登录限流标 skip（见 R-API-6）。

---

## 4. 完成定义 (Definition of Done)

- [ ] R-UI-1..3 创建，绿
- [ ] R-API-1..7 创建（其中 R-API-4.2、R-API-6 标 `test.skip` 并附 issue 号），其余绿
- [ ] 测试末尾清理所有造的 user/profile/schedule/run
- [ ] 403-vs-404 差异断言至少在 profile / run / schedule 三类资源各 1 条
- [ ] 角色/密码操作真生效（R-API-7）绿，证明"点按钮即生效"不再是个空断言

---

## 5. 依赖

- 共享 `tests-e2e/fixtures/auth.ts`（与 journey-test-plan §6.1 同一份基础设施）
- 共享 `tests-e2e/helpers/api.ts`（造数 + 清理 + 轮询）
- 后端越权语义见 `docs/API_REFERENCE.md` §越权行为（已对源码核对）
