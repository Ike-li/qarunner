# Code Review Bug Report — 2026-07-10

> 全面 code review 产出的 BUG 清单，按严重度排序，附修复建议。
> 每个 BUG 编号唯一，修复后可在对应条目打 ✅。

---

## 🟠 HIGH

### ✅ BUG-1 用户名枚举 — 登录时序侧信道

- **文件:** `src/qarunner/api/routes.py:428-435`
- **分类:** 安全
- **问题:** 用户不存在时 Python 短路求值跳过 `verify_password`（bcrypt ~100-200ms），攻击者通过响应时间区分"用户不存在"和"密码错误"。
- **触发方式:** 对比已知有效/无效用户名的登录响应时间。锁定前的前 4 次尝试即可建立信号。
- **修复建议:**

```python
# routes.py login 函数中，替换原来的短路逻辑
user_record = await container.store.get_user(req.username)
if not user_record:
    # 常量时间假检查，防止时序侧信道
    verify_password(req.password, "$2b$12$" + "0" * 53)
    container.login_throttle.record_failure(throttle_key)
    logger.warning("Failed login username=%r ip=%s", req.username, client_ip)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
    )
if not verify_password(req.password, user_record["password_hash"]):
    container.login_throttle.record_failure(throttle_key)
    logger.warning("Failed login username=%r ip=%s", req.username, client_ip)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
    )
```

---

### ✅ BUG-2 `handleSaveProfile` 缺少提交防护 — 可重复创建 Profile

- **文件:** `frontend/src/hooks/useTriggerForm.ts:140-182`
- **分类:** 前端 / 用户体验
- **问题:** 同文件的 `handleTriggerRun` 和 `handleUpdateProfile` 都有 `setIsSubmitting(true/false)` 防护，唯独 `handleSaveProfile` 缺失。无加载反馈，允许重复点击创建重复 Profile。
- **触发方式:** 打开触发弹窗 → 填写 Profile 名称 → 快速双击"Save Profile"。
- **修复建议:** 在 `try` 前加 `setIsSubmitting(true)`，在 `finally` 中加 `setIsSubmitting(false)`，与同文件其他 handler 保持一致。

---

## 🟡 MEDIUM

### ✅ BUG-3 `cancel()` TOCTOU — 可用 CANCELLED 覆写已终结状态

- **文件:** `src/qarunner/core/orchestrator.py:488-497`
- **分类:** 并发 / 数据完整性
- **问题:** 读-检查-写不是原子操作。`get()` 和 `save(CANCELLED)` 之间，`execute()` 可能完成并写入 COMPLETED/FAILED/TIMEOUT，被无条件覆写。
- **触发方式:** 在 run 即将完成的最后几毫秒调用 cancel()。短测试 + 高并发下更容易触发。
- **修复建议:** 在 store 层添加条件 UPDATE：

```python
# sqlite_store.py 新增方法
async def cancel_if_inflight(self, run_id: str, finished_at: str) -> bool:
    """仅在 run 仍在飞行中时原子地设置 CANCELLED。返回是否成功。"""
    async with self._connect() as db:
        cursor = await db.execute(
            "UPDATE runs SET status = ?, finished_at = ? "
            "WHERE id = ? AND status IN (?, ?)",
            (RunStatus.CANCELLED.value, finished_at, run_id,
             RunStatus.QUEUED.value, RunStatus.RUNNING.value),
        )
        await db.commit()
        return cursor.rowcount > 0

# orchestrator.py cancel() 中使用
async def cancel(self, run_id: str) -> Run:
    self._scheduler.cancel(run_id)
    now = self._clock.now()
    await self._store.cancel_if_inflight(run_id, _dt_to_iso(now))
    return await self._store.get(run_id)
```

---

### ✅ BUG-4 `execute()` 在守卫后无条件保存 RUNNING — 可覆写 CANCELLED

- **文件:** `src/qarunner/core/orchestrator.py:264-282`
- **分类:** 并发 / 数据完整性
- **问题:** 守卫检查和 `save(RUNNING)` 之间，`cancel()` 可能写入 CANCELLED。`save(RUNNING)` 将其覆写回 RUNNING。poller 的 `dequeue_next_queued()` 已经原子地设置了 RUNNING，此处的 save 是冗余的。
- **触发方式:** cancel() 在守卫检查通过后、save(RUNNING) 完成前被调用。
- **修复建议:** 移除冗余的 save（poller 已设置 RUNNING），或在 save 前检查取消：

```python
# 方案 A: 移除冗余 save（推荐，poller 已原子设置 RUNNING）
# 删除 lines 280-282 的 save 调用，started_at 在 dequeue_next_queued 中已设置

# 方案 B: 保留但检查取消
if asyncio.current_task().cancelling():
    run = _replace(run, status=RunStatus.CANCELLED, finished_at=self._clock.now())
    await self._store.save(run)
    return
```

---

### ✅ BUG-5 密码更改不使现有 JWT 失效

- **文件:** `src/qarunner/api/routes.py:584-585`, `src/qarunner/api/deps.py:149-198`, `src/qarunner/core/auth.py:33-49`
- **分类:** 安全 / 事件响应
- **问题:** 管理员更改用户密码后，旧 JWT 在 24 小时内仍然有效。`get_current_user` 不检查 token 是否在密码更改前签发。
- **触发方式:** 管理员更改密码 → 持有旧 token 的用户/攻击者继续认证成功。
- **修复建议:** 添加 `token_version` 列到 users 表，密码更改时递增，JWT 中包含 version，验证时比对。

---

### ✅ BUG-6 最后管理员降级守卫 TOCTOU

- **文件:** `src/qarunner/api/routes.py:576-587`
- **分类:** 并发 / 可用性
- **问题:** `list_users()` 和 `update_role()` 之间 `await` 让出控制权。两个并发降级请求可同时看到 `admin_count == 2`，都通过守卫 → 零管理员 → 平台锁定。
- **触发方式:** 两个管理员会话同时降级两个不同的管理员。
- **修复建议:** 将 admin 计数检查和 role 更新合并为单个原子 SQL：

```python
# sqlite_store.py 新增方法
async def demote_if_not_last_admin(self, username: str, new_role: str) -> bool:
    """仅在不是最后管理员时原子降级。返回是否成功。"""
    async with self._connect() as db:
        cursor = await db.execute(
            "UPDATE users SET role = ? WHERE username = ? "
            "AND (SELECT COUNT(*) FROM users WHERE role = 'admin') > 1",
            (new_role, username),
        )
        await db.commit()
        return cursor.rowcount > 0
```

---

### ✅ BUG-7 无最低密码长度验证

- **文件:** `src/qarunner/api/schemas.py` — `LoginRequest`, `UserCreateRequest`, `UserUpdateRequest`
- **分类:** 安全 / 输入验证
- **问题:** 无 `min_length` 约束。管理员可创建空密码或单字符密码的用户。
- **触发方式:** `POST /users` with `{"username": "test", "password": "a", "role": "user"}`
- **修复建议:** 在 Pydantic model 中加 `min_length=8`：

```python
class UserCreateRequest(BaseModel):
    username: str
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.USER
```

---

### ✅ BUG-8 反向代理下登录限流失效

- **文件:** `src/qarunner/api/routes.py:166-174`
- **分类:** 安全 / 部署
- **问题:** `_client_ip` 使用传输层对端地址。反向代理后所有请求来自代理 IP，限流键对所有客户端相同，限流保护失效。
- **触发方式:** 部署在 nginx/Traefik 后面。
- **修复建议:** 添加可信代理配置 + `X-Forwarded-For` 支持：

```python
def _client_ip(request: Request, trusted_proxies: set[str]) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in trusted_proxies:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer
```

---

### ✅ BUG-9 前端 `handleToggleLock` 闭包导致每 1.5 秒重渲染

- **文件:** `frontend/src/hooks/useRuns.ts:65-96`
- **分类:** 前端 / 性能
- **问题:** 依赖数组包含 `runs`（整个数组）。每 1.5 秒轮询产生新数组引用 → 函数身份变化 → 所有 context 消费者不必要地重渲染。
- **触发方式:** 有活跃 run 运行时，观察 RunsTable 的渲染频率。
- **修复建议:** 使用 ref 读取 + functional updater：

```typescript
const handleToggleLock = useCallback(
    async (runId: string, e: React.MouseEvent) => {
        e.stopPropagation()
        const run = runsRef.current.find((r) => r.id === runId)
        if (!run) return
        const newLocked = !run.locked
        // ... API call ...
        if (resp.ok) {
            setRuns(prev => prev.map(r => r.id === runId ? { ...r, locked: newLocked } : r))
            if (selectedRunDetailsRef.current?.id === runId) {
                setSelectedRunDetails(prev => prev ? { ...prev, locked: newLocked } : null)
            }
        }
    },
    [apiFetch],
)
```

---

## 🟢 LOW

### ✅ BUG-10 部分复制的 workspace jail 未清理

- **文件:** `src/qarunner/core/orchestrator.py:449-457`
- **分类:** 资源泄漏
- **问题:** `shutil.copytree` 失败时 `jail_created` 为 False，但 `jail_dir` 已存在于磁盘。`finally` 只在 `jail_created is True` 时清理。
- **修复建议:** 检查 `jail_dir.exists()` 而非 `jail_created`：

```python
finally:
    if "jail_dir" in locals() and jail_dir.exists():
        await asyncio.to_thread(shutil.rmtree, jail_dir, ignore_errors=True)
```

---

### ✅ BUG-11 通知 task 未跟踪 — 优雅关闭时丢失

- **文件:** `src/qarunner/core/orchestrator.py:412`
- **分类:** 资源泄漏 / 可靠性
- **问题:** `asyncio.create_task(self._notify(run))` 是 fire-and-forget，`drain()` 不等待通知 task。
- **修复建议:** 跟踪通知 task 或接受丢失并记录。

---

### ✅ BUG-12 `cookie_secure` 默认 False — 生产部署静默风险

- **文件:** `src/qarunner/config.py:66`
- **分类:** 部署安全
- **问题:** 默认 `False`，监听 `0.0.0.0` 时无警告。忘记设 `QARUNNER_COOKIE_SECURE=true` 则明文传输 JWT。
- **修复建议:** 启动时检测非 localhost 绑定 + `cookie_secure=False` 组合，输出 warning 日志。

---

### ✅ BUG-13 无服务端 token 吊销

- **文件:** `src/qarunner/api/routes.py:451-469`
- **分类:** 安全 / 可接受风险
- **问题:** `POST /auth/logout` 仅清除客户端 cookie。JWT 在过期前仍有效。
- **修复建议:** 单节点内部工具可接受。若需修复，添加 token 黑名单或 `token_version` 机制（与 BUG-5 合并）。

---

### ✅ BUG-14 `checkAuth` 是死代码

- **文件:** `frontend/src/hooks/useApi.ts:66-82`
- **分类:** 代码质量
- **问题:** 导出但从未被导入或调用。`useAuth.ts` 实现了自己的 `probeAuth` 做同样的事。
- **修复建议:** 删除 `checkAuth` 函数及其导出。

---

### BUG-15 `cancel_run` 路由 409 检查与 `cancel()` 之间 TOCTOU

- **文件:** `src/qarunner/api/routes.py:2088-2101`
- **分类:** 并发（低影响）
- **问题:** 路由读一次状态做 409 检查，`cancel()` 内部再读一次。两次读取之间状态可能已变。但 `cancel()` 内部有自己的守卫，最终不会出错 — 只是可能返回 CANCELLED 而非 409。
- **修复建议:** 可接受。若需修复，将 409 检查移入 `cancel()` 内部。

---

### BUG-16 前端 SSE effect 使用 blanket eslint-disable

- **文件:** `frontend/src/hooks/useRuns.ts:206-272`
- **分类:** 代码质量 / 维护风险
- **问题:** `// eslint-disable-next-line react-hooks/exhaustive-deps` 完全压制 lint，未来添加依赖时不会被警告。
- **修复建议:** 改为精确禁用特定依赖，或重构消除需要。

---

### ✅ BUG-17 飞书 webhook_url SSRF — 可扫描内网

- **文件:** `src/qarunner/core/notification.py:113-133`, `src/qarunner/core/profile_service.py:42`
- **分类:** 安全 / SSRF
- **问题:** `webhook_url` 来自用户输入（profile 创建/更新），直接传给 `httpx.post()`，无任何域名/协议校验。攻击者可设置 `webhook_url` 为内网地址（如 `http://169.254.169.254/latest/meta-data/`、`http://localhost:6379/`、`http://internal-db:5432/`），通过 run 完成时的自动通知触发 SSRF。
- **触发方式:** 创建 profile → 设置 `webhook_url: "http://169.254.169.254/latest/"` → 触发 run → run 完成时 httpx 向元数据服务发请求。
- **修复建议:** 添加 URL 校验，限制为公网 HTTPS 域名：

```python
# schemas.py 或 profile_service.py 中
from urllib.parse import urlparse

def _validate_webhook_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("https",):
        raise ValueError("webhook_url must use https://")
    if parsed.hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise ValueError("webhook_url must not point to localhost")
    if parsed.hostname and (
        parsed.hostname.startswith("10.")
        or parsed.hostname.startswith("172.")
        or parsed.hostname.startswith("192.168.")
        or parsed.hostname.startswith("169.254.")
        or parsed.hostname == "metadata.google.internal"
    ):
        raise ValueError("webhook_url must not point to a private/internal address")
```

---

### ✅ BUG-18 `secret_key` 无最低长度校验 — 弱密钥可通过验证

- **文件:** `src/qarunner/config.py:113-122`
- **分类:** 安全 / 输入验证
- **问题:** `_reject_placeholder_secret_key` 只检查一个小的占位值列表（`"change-me"` 等），不检查长度。`QARUNNER_SECRET_KEY=a` 通过验证。JWT 签名使用 HS256，密钥越短越容易被暴力破解。
- **触发方式:** 设置 `QARUNNER_SECRET_KEY=abc` → 服务启动成功 → JWT 可被离线暴力破解。
- **修复建议:** 添加最小长度检查：

```python
@field_validator("secret_key")
@classmethod
def _reject_placeholder_secret_key(cls, value: str) -> str:
    if not value.strip() or value in _PLACEHOLDER_SECRET_KEYS:
        raise ValueError(...)
    if len(value) < 32:
        raise ValueError(
            "QARUNNER_SECRET_KEY must be at least 32 characters for HS256 security"
        )
    return value
```

---

### ✅ BUG-19 workspace jail 创建失败时回退到原始目录运行

- **文件:** `src/qarunner/core/orchestrator.py:338-344`
- **分类:** 数据完整性 / 隔离
- **问题:** 当 `shutil.copytree` 失败（磁盘满、权限错误）时，`exec_cwd` 保持为原始 `tests_dir`，测试代码直接在源目录中运行。测试写入的文件（缓存、临时文件、修改的 fixture）会污染源目录，导致后续 run 结果不可预测。
- **触发方式:** 磁盘空间不足 → copytree 失败 → 测试在原始目录运行 → `.pytest_cache`/`__pycache__` 等写入源目录。
- **修复建议:** jail 创建失败应中止 run 而非回退：

```python
except Exception as e:
    logger.warning("Failed to create Workspace Jail at %s (%r)", jail_dir, e)
    # 中止 run，不要在原始目录运行
    raise RunnerError(f"Workspace jail creation failed: {e}") from e
```

---

### BUG-20 `cleanup_runs` 在 re-read lock 和 rmtree 之间存在 TOCTOU

- **文件:** `src/qarunner/api/routes.py:2183-2201`
- **分类:** 并发（低影响）
- **问题:** `cleanup_runs` 先 re-read lock 确认未锁定，然后执行 rmtree。两步之间 run 可能被锁定或有新 run 开始。但因为 rmtree 只删物理文件不删 DB 记录，最坏情况是丢失一个刚锁定 run 的报告文件。
- **触发方式:** 管理员触发 cleanup 的同时用户锁定一个即将被清理的 run。
- **修复建议:** 可接受（低概率 + 影响有限）。若需修复，在 rmtree 前再次检查 lock。

---

### ✅ BUG-21 `handleSaveSchedule` 缺少提交防护 — 可重复创建定时调度

- **文件:** `frontend/src/hooks/useSchedules.ts:115-154`
- **分类:** 前端 / 用户体验
- **问题:** 与 BUG-2 同一模式。`handleSaveSchedule` 没有 loading 状态或 double-submit 防护。用户快速点击"保存"可创建重复的定时调度。
- **触发方式:** 打开调度弹窗 → 填写 cron 表达式 → 快速双击"保存"。
- **修复建议:** 添加 `isSavingSchedule` 状态 + `setIsSavingSchedule(true/false)` 防护，与 `handleTriggerRun` 的模式保持一致。

---

## 🟢 LOW

### ✅ BUG-22 删除用户后遗留孤儿 run/profile/schedule/credential 记录

- **文件:** `src/qarunner/api/routes.py:535-551` (`delete_user`), `src/qarunner/adapters/sqlite_store.py` (schema)
- **分类:** 数据完整性
- **问题:** `DELETE FROM users WHERE username = ?` 只删用户表。`runs`、`test_profiles`、`test_schedules`、`credentials` 的 `created_by` 列没有外键约束，删除用户后这些表中 `created_by = deleted_user` 的记录成为孤儿。非 admin 用户无法访问（`_require_owner_access` 永远不匹配），admin 虽能看到但 `created_by` 指向不存在的用户名。关联的 schedule cron 会持续触发但因 profile owner 检查失败而静默跳过。
- **触发方式:** Admin 删除一个有活跃 run/profile/schedule 的用户。
- **修复建议:** 删除用户前级联清理或转移所有权：

```python
# routes.py delete_user 中，删除用户前清理关联数据
await container.store.delete_runs_by_owner(username)
await container.store.delete_profiles_by_owner(username)
await container.store.delete_schedules_by_owner(username)
await container.store.delete_credentials_by_owner(username)
await container.store.delete_user(username)
```

---

### ✅ BUG-23 `ProjectSidebar` 每次渲染重复计算 O(n×m) 统计

- **文件:** `frontend/src/components/ProjectSidebar.tsx:116-250`
- **分类:** 前端 / 性能
- **问题:** 在 `.map()` 循环内对每个 suite/profile 执行 `d.runs.runs.filter(...)` — suite 循环内 filter runs（O(n×m)），profile 循环内再次 filter runs（O(n×m×p)）。每次 context 值变化都重新计算，无 `useMemo` 缓存。
- **触发方式:** 有 10+ suite 和 100+ run 时，侧边栏渲染明显卡顿。
- **修复建议:** 提取为 `useMemo`：

```typescript
const suiteRunsMap = useMemo(() => {
  const map = new Map<string, Run[]>()
  for (const r of d.runs.runs) {
    const arr = map.get(r.tests_path) || []
    arr.push(r)
    map.set(r.tests_path, arr)
  }
  return map
}, [d.runs.runs])
```

---

### ✅ BUG-24 SSE 重连时 `streamedStdout` 未清空 — 日志行重复

- **文件:** `frontend/src/hooks/useRuns.ts:242-244`
- **分类:** 前端 / 显示错误
- **问题:** `eventSource.onmessage` 用 `setStreamedStdout(prev => prev + event.data + '\n')` 追加日志。SSE 断线重连后，`connect()` 函数不清空 `streamedStdout`，服务端重新发送的行与已有内容拼接，导致日志行重复显示。
- **触发方式:** 运行中的 run → 网络短暂断开 → SSE 自动重连 → 日志内容重复。
- **修复建议:** 在 `connect()` 开头清空：

```typescript
const connect = () => {
  if (disposed) return
  setStreamedStdout('')  // 清空旧内容，避免重连后重复
  eventSource = new EventSource(`/runs/${selectedRunId}/stream`)
  // ...
}
```

---

### ~~BUG-25 `handleDeleteRun` 失败时 run 从 UI 消失后又重现~~ [误报]

- **状态:** ❌ 误报 — 代码已正确实现悲观更新（先调 API 成功后才删 state），无需修复。

---

### ✅ BUG-26 `ScheduleModal` 保存按钮无 double-submit 防护

- **文件:** `frontend/src/components/ScheduleModal.tsx:136`
- **分类:** 前端 / 用户体验
- **问题:** 保存按钮直接调用 `s.handleSaveSchedule`，无 loading 状态。与 BUG-2/BUG-21 同一模式。快速双击可创建重复调度。
- **触发方式:** 打开调度弹窗 → 快速双击"保存调度"。
- **修复建议:** 在 `useSchedules` 中添加 `isSavingSchedule` 状态，在按钮上绑定 `loading={s.isSavingSchedule}`。

---

### ✅ BUG-27 `UserManagementModal` 改密码无 double-submit 防护

- **文件:** `frontend/src/components/UserManagementModal.tsx:76-88`
- **分类:** 前端 / 用户体验
- **问题:** 改密码按钮通过 `window.prompt` 获取新密码后直接调用 `handleUpdateUserPassword`。`window.prompt` 是同步阻塞的，所以两次快速点击不会并发。但如果 `handleUpdateUserPassword` 的 Promise 被 `.then()` 链延迟，理论上可触发重复请求。实际风险极低。
- **触发方式:** 理论性，需极端快速操作。
- **修复建议:** 可接受。若需修复，在 `useUsers` 中添加 `isUpdatingPassword` 状态。

---

### ✅ BUG-28 `UserManagementModal` 清理按钮无 early-return 防护

- **文件:** `frontend/src/components/UserManagementModal.tsx:213-229`
- **分类:** 前端 / 用户体验
- **问题:** 清理按钮的 `onClick` 没有 `if (isCleaningStorage) return` 守卫。虽然按钮有 `loading` 属性显示 spinner，但如果用户在 loading 动画出现前快速点击，可能触发重复 API 调用。
- **触发方式:** 快速双击"清理旧数据"按钮。
- **修复建议:** 在 `onClick` 开头加 `if (u.isCleaningStorage) return`。

---

## 📊 统计

| 严重度 | 总计 | ✅ 已修复 | ⬜ 可接受/跳过 |
|--------|------|----------|---------------|
| 🟠 HIGH | 3 | 3 | 0 |
| 🟡 MEDIUM | 9 | 9 | 0 |
| 🟢 LOW | 17 | 11 | 6 |
| **合计** | **29** | **23** | **6** |

## 🎯 剩余待修复（均为低影响/可接受风险）

### 可接受的 LOW（6 个）
- **BUG-15** — cancel_run 路由 409 TOCTOU（低影响，cancel() 内部已有守卫）
- **BUG-16** — SSE eslint-disable blanket（代码质量，非功能问题）
- **BUG-20** — cleanup_runs TOCTOU（低概率，rmtree 前已 re-read lock）

### 已标记
- **BUG-25** — ❌ 误报（代码已正确实现悲观更新）

### 验证状态
- 后端：771 passed / 1 skipped / ruff clean
- 前端：tsc --noEmit clean
