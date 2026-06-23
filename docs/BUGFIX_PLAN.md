# QA Platform v2 — Bug Hunt & Fix Plan

> 生成日期：2026-06-23
> 方法：诊断纪律优先——每个结论标证据等级，bug 必须在**真实表面**（真起 uvicorn 打 API）复现取证，不靠读代码或单测自证。
> 范围：在 38 项整改（见 `REMEDIATION_PLAN.md`）功能性闭环之后的**新一轮真 bug 猎**。

证据等级：`[已端到端验证]`（真 app 观测攻击向量前后对比）> `[读代码推断]`（dep 链/数据流追到根，未跑 app）> `[猜测]`。

---

## 第 1 轮发现

### 🔴 BUG-1：profile / schedule 全面 IDOR（对象级越权）— [已端到端验证]

**结论**：任一登录的**普通用户**可以列出、读取、修改、删除**任意其他用户**的 execution profile 与 test schedule。

**真表面复现**（真 uvicorn 127.0.0.1:8231，两个普通用户 userA / userB，curl + cookie 鉴权）：

| 攻击向量（userB 操作 userA 的资源） | 实测 | 期望 |
|---|---|---|
| `GET /profiles` | 200，**列表含 userA 的 profile** | 仅自己 |
| `PUT /profiles/{A_id}` | 200，名被改为 `PWNED-by-userB`（`created_by` 仍 userA = 静默篡改） | 403 |
| `DELETE /profiles/{A_id}` | 200，**userA 的 profile 被删（A 复列=0）** | 403 |
| `GET /schedules` / `GET /schedules/{A_id}` | 200，泄露他人调度 | 403 / 过滤 |
| `PUT /schedules/{A_id}` | 200，改 cron / 停用他人调度 | 403 |
| `DELETE /schedules/{A_id}` | 200，删他人调度 | 403 |

**对照组（证明是漏覆盖、非设计）**：同会话 `GET /runs/{A_run}` → **403**；`GET /runs` 对 userB **不含** A 的 run。run 走 `_require_run_access`（`api/routes.py`），profile/schedule 完全无。

**根因**（file:line，修复前）：
- `api/routes.py`：`update_profile` / `delete_profile` / `list_profiles`、`get_schedule` / `update_schedule` / `delete_schedule` / `list_schedules` 的鉴权参数均为 `_current_user`（下划线 = 拿到登录身份但**故意不用**）。
- `core/profile_service.py`、`core/schedule_service.py`：`update` / `delete` 不接收调用者身份、不校验归属。
- `adapters/sqlite_store.py`：`list_profiles` / `list_schedules` **不按 owner 过滤**。
- `models.py`：`TestProfile.created_by` / `TestSchedule.created_by` 存在且 create 时写入——**有归属字段却从不校验**，是 SEC-4 只做 run、漏这两类的强信号。测试中无任何 owner 隔离断言。

**严重度**：🔴（多用户部署下，任一登录用户可静默破坏/泄露他人全部 profile 与 schedule）。

**决策（用户已定）**：**完全 owner-scoped，对齐 run**。非 admin：list 仅见自己；get/update/delete 命中他人 → 403；admin 可见可改全部。

**修复**（最小改动，对齐 run 的 `_require_run_access` 模式）：
1. `api/routes.py` 新增 `_require_owner_access(created_by, user)`（admin 或 owner 放行，否则 403）；`_require_run_access` 改为委托它（行为不变、单一真相）。
2. 6 个 profile/schedule 端点：`_current_user` → `current_user`，在路由层做 owner 校验。
   - update/delete：**预取**资源对象做 owner 校验；预取为 `None`（不存在）时**放行给 service 产 404**，以保住 service 既有 not-found 分支的 100% 覆盖。
   - get_schedule：取后非 None 即校验 owner。
3. list_profiles / list_schedules：非 admin 用 `created_by == current_user.username` 过滤。

**回归测试**（`tests/unit/api/test_auth_flow.py`，真 container + 真 SqliteStore + 真两用户、无 auth override = 真接口级）：
- `test_profile_idor_blocked_for_non_owner` / `test_schedule_idor_blocked_for_non_owner`：bob 对 alice 资源 list 不可见 + get/put/delete → 403，alice 资源完好。
- `test_profile_access_allowed_for_owner_and_admin` / `test_schedule_access_allowed_for_owner_and_admin`：owner 与 admin 全程可达。
- 没修就转红：去掉任一 owner 校验 → 对应测试转红（见变异验证）。

---

### 🟠 BUG-2：更新 profile 会级联删光其全部 schedule（静默数据丢失）— [已端到端验证]

**结论**：更新任意 profile（哪怕只是改名）会**静默删除**绑定到它的所有 test schedule。

**发现方式**：在 BUG-1 修复的**真接口验证**中抓到——`PUT /profiles/{id}` 返回 200 后，owner 与 admin 对之前创建的 schedule 的 GET 都变 404，且访问日志里**无任何成功的 DELETE**。这正是单测（FakeStore）看不见、必须真表面验证的典型。

**复现 + 机制确认**：
- 隔离 API 复现：建 P2 + 其 schedule S2（S2 在=200）→ owner 仅改名更新 P2（200）→ S2 变 404。
- 直接 SQL 机制确认：`PRAGMA foreign_keys=ON` 下对父行做 `INSERT OR REPLACE`，子行（`ON DELETE CASCADE`）从 1 删到 0。

**根因**：`adapters/sqlite_store.py` `save_profile` 用 `INSERT OR REPLACE`（= 先 DELETE 旧行再 INSERT），叠加 `test_schedules.profile_id` 的 `ON DELETE CASCADE`（schema）+ `PRAGMA foreign_keys=ON`（`_connect`）。REPLACE 的 DELETE 触发 FK 级联，删光该 profile 的 schedule。`save_profile` 同时服务 create 与 update，故每次编辑都中招。

**严重度**：🟠（数据丢失 / 正确性——非安全，但任一 profile 编辑即清空其自动化调度）。**注**：`delete_profile` 的级联删是**有意设计**（`test_schedule_crud_and_cascade` 验证），修复须保留它、只治 update 路径。

**修复**：`save_profile` 改 `INSERT ... ON CONFLICT(id) DO UPDATE SET ...`（行保留 UPSERT，原地更新不删行）；级联只在真正的 `delete_profile` 触发。

**回归测试**（`tests/unit/adapters/test_sqlite_store.py::test_updating_a_profile_keeps_its_schedules`，放 **SqliteStore 层**——FakeStore 不复刻 SQLite REPLACE/CASCADE 语义、抓不到）：建 profile + schedule，再 re-save profile，schedule 须存活。**未修代码上转红（`assert None is not None`）、修复后转绿** = 变异验证。

---

## 第 2 轮发现

### 🟠 BUG-3：subprocess 执行器把宿主全部非-QARUNNER 环境变量泄漏给被测代码 — [已端到端验证]

**结论**：默认的 subprocess 执行器在构造子进程环境时**只剥离 `QARUNNER_*` 前缀**、继承宿主其余全部环境变量，被测代码可读取宿主任意非-QARUNNER 机密（`AWS_*`/`GITHUB_TOKEN`/`DATABASE_URL` 等）并经 run 的 stdout（API 可读）外带。

**真表面复现**：起 uvicorn 时注入 `MY_CLOUD_SECRET=...`（非 QARUNNER 前缀）+ 一个 dump `os.environ` 的测试 → `POST /runs`（subprocess, `extra_args=-s` 禁 pytest 捕获）→ `GET /runs/{id}` 的 stdout 含 `CLOUD_SECRET_VALUE=s3cr3t...`（泄漏），`QARUNNER_SECRET_KEY=<absent>`（黑名单只挡了 QARUNNER_*）。

**根因**：`adapters/subprocess_runner.py` `full_env = {k:v for ... if not k.startswith("QARUNNER_")}`（黑名单）。SEC-3 计划原文要的是**白名单最小环境**（「不传 `dict(os.environ)`」），黑名单是半成品。**docker 执行器不受影响**（`docker_runner.py` `environment=env or {}` 只传用户 env + network none/read_only/cap drop/非 root）。

**严重度**：🟠（机密泄漏；[INFERRED] 纯 API 用户无文件上传端点、需能把测试塞进 tests_root 才能利用，但平台多用户语义下测试作者即不同用户，违反 SEC-3 隔离意图）。**用户决策：改白名单最小环境**。

**修复**：`subprocess_runner.py` 改为 `_ENV_ALLOWLIST`（PATH/HOME/USER/locale/TMP*/TZ/JAVA_HOME——pytest 与共用此 runner 的 allure CLI 的功能最小集）+ 用户 env 叠加。**注**：subprocess 仍共享平台 uid 与文件系统/网络，docker 仍是不可信套件的隔离路径；本修复专堵 env 变量泄漏。

**回归测试**（`tests/unit/adapters/test_subprocess_runner.py::test_non_allowlisted_host_env_not_forwarded`）：注入非-allowlist 宿主机密 → 子进程读到 `ABSENT`、PATH 仍在；黑名单下红、白名单后绿（变异验证已复核）。**真接口复验**：泄漏堵（`<absent>`）+ PATH 在 + **allure 报告仍正常生成**（白名单经 `env -i` 隔离测 + 真 run 双证，`html_generated=true`+index.html 落盘）。

**关联（未实现，记录缘由）**：SEC-1 计划改法 #3 还要求 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`/`-p no:cacheprovider`/`-o addopts=`/`--noconftest`，`PytestRunner.build_command` 均未加。**未一并实现**：① `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 会**禁用 allure-pytest 插件**→ `--alluredir` 报错，破坏招牌报告功能，需显式 `-p allure_pytest` 重新接线（较大改动）；② `--noconftest` 会破坏依赖 conftest fixture 的正常套件；③ 经 API 无法把 `conftest.py`/`pytest.ini` 注入 tests_root（无上传端点），故非 API 可利用的漏洞，仅 defense-in-depth。判定为**独立后续项**，不在本轮 BUG-3 内强塞。

---

## 本轮排查后判定干净的表面（按收敛规则记录）

- **safe_subpath 路径穿越 / 软链逃逸** — [已端到端验证] 干净。`/tests/..%2f..%2fetc/tree` 等编码穿越 → 404；suite 内放指向 `/etc` 的软链 → tree 不跟随泄露。实现（`resolve()` + `is_relative_to`）稳。
- **JWT / cookie / `?token=` / admin 边界** — [已端到端验证] 干净。无凭证 / 垃圾 token / `?token=` URL 旁路 → 全 401；普通用户打 `/users` → 403。SEC-5/SEC-6 实际生效。

## 后续项（已记录，未在本轮修）

- **SEC-1 plugin/conftest 加固**（见 BUG-3「关联」）：`PYTEST_DISABLE_PLUGIN_AUTOLOAD`/`--noconftest` 与 allure-pytest、正常 conftest fixture 冲突，且无 API 注入面，列为独立后续项。
- **subprocess 模式的文件系统/网络隔离**：subprocess 共享平台 uid，被测代码可读平台用户可访问的任意文件（`~/.aws/` 等）、可联网。SEC-3 已注明「不可信场景禁用 subprocess / 用 docker」。docker 执行器已具备完整隔离。属已知设计取舍，非新 bug。
