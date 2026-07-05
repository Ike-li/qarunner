# qarunner 接口文档（API Reference）

> 面向接口测试的完整端点参考。所有字段、状态码、枚举值均来自源码核对
> （`routes.py` / `schemas.py` / `deps.py` / `models.py` / `config.py`）。
>
> 结构性内容也可对照运行中的 `GET /openapi.json`（FastAPI 自动生成，`/docs` 为交互式 UI）。
> 二者如有出入，**以代码 / `/openapi.json` 为准**（ARCHITECTURE.md 中的旧路径已过时）。
>
> Base URL：默认 `http://localhost:8000`，下文路径均相对此。

---

## 一、通用约定

### 1.1 认证方式
绝大多数接口需要认证。两种携带凭证方式（二选一）：
- **API 客户端**：请求头 `Authorization: Bearer <access_token>`。
- **浏览器 / 同源**：登录后下发的 `token` Cookie（`HttpOnly; SameSite=Strict`）自动携带。
- ⚠️ `?token=` 查询参数已废弃，传它无效（等同未认证）。

获取 token：调 `POST /auth/login`，从响应体 `access_token` 取。

### 1.2 角色与权限标识
下文每个接口标注其权限要求：

| 标识 | 含义 |
|---|---|
| **公开** | 无需认证 |
| **认证** | 任意已登录用户 |
| **owner** | 已登录 **且** 是该资源创建者，或 admin；否则 403（资源存在）/ 404（不存在） |
| **admin** | 仅 admin 角色，否则 403 |

- 角色枚举 `UserRole`：`admin` / `user`。
- **列表接口对非 admin 是静默过滤**：只返回自己创建的，不报错（200）。

### 1.3 错误响应结构
- 业务 / 鉴权错误：`{"detail": "<字符串>"}`
- 请求体校验失败（Pydantic）：`422`，`{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}`
- `401` 响应带 `WWW-Authenticate: Bearer` 头；登录锁定的 `429` 带 `Retry-After: <秒>` 头。

### 1.4 通用状态码
`200` 成功 · `201` 已创建 · `202` 已受理（异步）· `204` 无内容 ·
`400` 业务校验失败 · `401` 未认证 · `403` 无权限 · `404` 不存在 ·
`409` 冲突 · `422` 请求体 schema 校验失败 · `429` 限流 · `500` 内部错误 ·
`502` 外部子进程失败（git/npm）· `503` 依赖不可用（DB）。

> **`400` vs `422`**：业务规则（重名、未知 runner、非 git 套件…）是 400；
> schema 层（缺字段、类型错、`Literal` 非法、数值越界）是 422。

---

## 二、健康检查

### `GET /health` — 公开
存活 + 就绪探针。
- **成功**：`200` `{"status": "ok"}`（仅当数据库可达）。
- **失败**：`503` `{"detail": "database unavailable"}`。

---

## 三、认证与用户

### `POST /auth/login` — 公开
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| username | str | 是 | |
| password | str | 是 | |

- **成功**：`200` `{"access_token": "<JWT>", "token_type": "bearer"}`，并下发 `token` Cookie。
- **错误**：`401`（用户名或密码错）· `422`（缺字段）· `429`（同一 `用户名|IP` 连续 5 次失败后锁定，带 `Retry-After`）。

### `POST /auth/logout` — 公开
- 恒 `204`，清除 `token` Cookie（过期会话也能登出）。无请求体。

### `GET /auth/me` — 认证
- **成功**：`200` `UserResponse`（见 §八）。
- **错误**：`401`。

### `POST /users` — admin
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| username | str | 是 | | |
| password | str | 是 | | |
| role | enum | 否 | `user` | `admin` / `user` |

- **成功**：`201` `UserResponse`。
- **错误**：`400`（用户名已存在）· `403`（非 admin）· `422` · `401`。

### `GET /users` — admin
- **成功**：`200` `{"users": [UserResponse, ...]}`。
- **错误**：`403` · `401`。

### `PUT /users/{username}` — admin
修改用户的密码和/或角色。请求体 `{password?: str, role?: "admin"|"user"}`（至少一个字段）。
- **成功**：`200` `UserResponse`。
- **错误**：`400`（无字段，或会降级最后一个 admin）· `404`（用户不存在）· `403` · `401`。

### `DELETE /users/{username}` — admin
删除一个用户。不能删除自己（`400`）。
- **成功**：`200` `{"status":"success","message":...}`。
- **错误**：`400`（删自己）· `404`（用户不存在）· `403` · `401`。

---

## 四、测试套件

### `GET /tests` — 认证
- **成功**：`200` `["suite_a", "suite_b", ...]`（`tests_root` 下的目录名）。

### `GET /suites` — 认证
- **成功**：`200` `[SuiteInfoResponse, ...]`（带 source/repo/ref 元数据，见 §八）。

### `POST /tests/link` — 认证（已注册套件按 owner；磁盘已存在但未注册则仅 admin）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| path | str | 是 | 要软链接的本地目录路径 |

- **成功**：`200` `LinkTestSuiteResponse`。
- **错误**：`400`（无法从 path 提取目录名）· `403` · `500`。

### `POST /tests/clone` — 认证（任意用户均可 clone）
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| url | str | 是 | | 仅允许 `https://` 或 `git@`（scp 式），否则 400 |
| name | str | 否 | 从 url 推导 | 套件目录名 |
| ref | str | 否 | 默认分支 | 分支 / tag |
| credential_ref | str | 否 | | 已存凭证的 id（见下「凭证」节）；私有仓库用，token 经 GIT_ASKPASS env 注入，不进 argv/URL/日志 |

- **成功**：`200` `LinkTestSuiteResponse`。
- **错误**：`400`（URL 不在白名单 / `credential_ref` 不存在）· `403`（`credential_ref` 属于他人）· `409`（套件名已存在）· `502`（git clone 失败）· `503`（克隆成功但登记失败，已回滚克隆目录）。

### `POST /tests/{suite_name}/pull` — owner
- 路径参数：`suite_name`。无请求体。
- **成功**：`200` `LinkTestSuiteResponse`。
- **错误**：`404`（套件不存在）· `403` · `400`（仅 git 套件可 pull）· `502`（git 失败）。

### `POST /tests/{suite_name}/prepare` — owner
- 路径参数：`suite_name`。无请求体。对 git 套件执行 `npm ci --ignore-scripts`。
- **成功**：`200`（local 套件 / 无 package.json 也返回成功，message 不同）。
- **错误**：`404` · `403` · `502`（npm ci 失败）。

### `DELETE /tests/{suite_name}` — owner（磁盘存在但未注册则仅 admin）
- 路径参数：`suite_name`。
- **成功**：`200` `{"status": "success", "message": "..."}`。
- **错误**：`404` · `403`。

### `GET /tests/{suite_name}/tree` — 认证
- 路径参数：`suite_name`。返回测试文件树（仅含 `.py` test 文件与 `.spec/.test.*` JS/TS）。
- **成功**：`200` 树节点数组：`{name, path, is_dir, children?}`。
- **错误**：`400`（不安全路径）· `404`（套件不存在）。

### `GET /tests/{suite_name}/markers` — 认证
- 路径参数：`suite_name`。静态解析 pytest marker 与 Playwright 测试标题中的 `@tag`。
- **成功**：`200` `["smoke", "slow", ...]`（不存在的套件返回 `[]`）。
- **错误**：`400`（不安全路径）。

### `POST /credentials` — 认证
存储一个 git 凭证（私有仓库用）。请求体 `{name, type:"https_token", secret}`；`secret` **只写不回读**——加密落库（Fernet，密钥由 `SECRET_KEY` 经 HKDF 派生），API 永不返回明文。
- **成功**：`201` `CredentialResponse`（含 id/name/type/created_by/created_at，**无 secret**）。
- **错误**：`422`（`type` 非 `https_token`）· `401`。

### `GET /credentials` — 认证（非 admin 只看自己的）
- **成功**：`200` `{"credentials": [CredentialResponse, ...]}`（均无 secret）。

### `DELETE /credentials/{credential_id}` — owner
- **成功**：`200` `{"status":"success","message":...}`。
- **错误**：`404`（不存在）· `403`（他人）。

---

## 五、配置档（Profile）

请求体（`POST` 用 Create，`PUT` 用 Update，字段相同）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| name | str | 是 | | |
| description | str \| null | 否 | null | |
| tests_path | str | 是 | | |
| runner | str | 否 | `pytest` | `pytest` / `playwright` |
| selected_files | list[str] | 否 | `[]` | `pytest` → `.py` / node id；`playwright` → `.spec/.test.*` JS/TS 测试文件 |
| selected_markers | list[str] | 否 | `[]` | `pytest` → `-m "a or b"`；`playwright` → `--grep "@a|@b"`（按标题/标签过滤） |
| extra_args | str | 否 | `""` | |
| executor_mode | enum | 否 | `docker` | `docker` / `subprocess`（非法值 → 422） |
| timeout | int \| null | 否 | null | `>0` 且 `<=86400`，否则 422 |
| env | dict[str,str] | 否 | `{}` | |

### `POST /profiles` — 认证
- **成功**：`201` `TestProfileResponse`。 · **错误**：`422` · `401`。

### `GET /profiles` — 认证（非 admin 静默过滤）
- 查询参数：`tests_path`（可选，按路径过滤）。
- **成功**：`200` `[TestProfileResponse, ...]`。

### `PUT /profiles/{profile_id}` — owner
- **成功**：`200` `TestProfileResponse`。 · **错误**：`403`（他人）· `404`（不存在）· `422`。

### `DELETE /profiles/{profile_id}` — owner
- **成功**：`200` `{"status": "success", "message": "..."}`。 · **错误**：`403` · `404`。

---

## 六、运行（Run）

`RunRequest` 请求体：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| tests_path | str | 是 | | |
| runner | str | 否 | `pytest` | `pytest` / `playwright` |
| args | list[str] | 否 | `[]` | |
| allure | bool | 否 | `true` | |
| timeout | int \| null | 否 | null | `>0` 且 `<=86400` |
| executor_mode | enum | 否 | `docker` | `docker` / `subprocess` |
| selected_files | list[str] | 否 | `[]` | `pytest` → `.py` / node id；`playwright` → `.spec/.test.*` JS/TS 测试文件 |
| selected_markers | list[str] | 否 | `[]` | `pytest` → `-m "a or b"`；`playwright` → `--grep "@a|@b"`（按标题/标签过滤） |
| extra_args | str | 否 | `""` | |
| env | dict[str,str] | 否 | `{}` | |

### `POST /runs` — 认证
**异步**：受理后返回初始状态，不代表执行完成。
- **成功**：`202` `RunResponse`（`status="queued"`）。
- **错误**：
  - `400`：`executor_mode=subprocess` 但非 admin 且未开放 / 未知 runner / 不安全路径或参数。
  - `429`：同一用户 `queued+running` 的 run 数 ≥ 上限（默认 20，admin 豁免）。
  - `422`：schema 校验失败。
  - **`runner=playwright` + `executor_mode=docker`**：受支持的正常组合，返回 `202`。docker executor 按命令自动选用 Playwright 专用镜像（`qarunner-playwright-executor:latest`，可配 `QARUNNER_PLAYWRIGHT_EXECUTOR_IMAGE`，内含 Node.js + 浏览器），**不在接口层拒绝**。仅当该镜像不可用且关闭了运行时自动构建（`QARUNNER_EXECUTOR_AUTOBUILD=false`）时，该 run 在**异步执行阶段**失败、终态 `failed`（而非同步 4xx/5xx）。

### `GET /runs` — 认证（非 admin 静默过滤）
- **成功**：`200` `{"runs": [RunResponse, ...]}`（按时间倒序）。

### `GET /runs/{run_id}` — owner
- **成功**：`200` `RunResponse`（额外含 `stdout`/`stderr`，为末尾最多 256KB）。
- **错误**：`404`（不存在）· `403`（他人）。

### `GET /runs/{run_id}/report` — owner
- **成功**：`200`，`Content-Type: text/html`（Allure 报告首页）。
- **错误**：`404`（run 不存在 或 报告未生成）· `403`。

### `GET /runs/{run_id}/report/{path}` — owner
- 路径参数：`run_id`、`path`（报告内静态资源相对路径）。
- **成功**：`200` 文件。
- **错误**：`404`（不存在 / 文件不存在）· `403`（路径穿越被拦）。

### `GET /runs/{run_id}/stream` — owner
**SSE 实时日志**，`Content-Type: text/event-stream`。
- **成功**：`200`，流式逐行 `data: <一行日志>\n\n`；终态后 flush 剩余（≤256KB）后结束；单连接最长 3600s；客户端断开即停。
- **错误**：`404` · `403`。
- 测试须用流式客户端（如 `httpx.stream` / `sseclient`），不能用普通阻塞 GET。

### `PUT /runs/{run_id}/lock` — owner
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| locked | bool | 是 | 锁定后不被 cleanup 删除 |

- **成功**：`200` `RunResponse`。 · **错误**：`404` · `403`。

### `POST /runs/{run_id}/cancel` — owner
取消一个排队中或运行中的 run（运行中的会终止其子进程 / 容器，并释放并发槽）。无请求体。
- **成功**：`200` `RunResponse`（`status="cancelled"`）。
- **错误**：`404`（不存在）· `403`（他人）· `409`（已是终态 completed/failed/timeout/cancelled，不可取消）。

### `DELETE /runs/{run_id}` — owner
彻底删除一个 run 的元数据行与物理产物目录（产物删除尽力而为，删不掉也仍删行）。无请求体。
- **成功**：`200` `{"status":"success","message":...}`。
- **错误**：`404`（不存在）· `403`（他人）· `409`（run 被锁定，或仍在 queued/running——需先解锁 / 取消）。

### `POST /runs/{run_id}/rerun` — owner
用原 run 的参数（runner / tests_path / 已编译 args / executor / timeout / env）建一个**新** run，原 run 不动；新 run 归属触发者。无请求体。
- **成功**：`202` `RunResponse`（新 run）。
- **错误**：`404`（原 run 不存在）· `403`（他人）。

### `POST /runs/cleanup` — admin
- 查询参数：`retention_days`（默认 `30`，约束 `>=1`，否则 422）。
- 删除超期且未锁定 run 的物理产物（元数据保留）。
- **成功**：`200` `{"status": "success", "cleaned_runs": <int>}`。
- **错误**：`403` · `422`。

### 跨次对比（Cross-run Comparison）

三个只读端点,把单次 run 视图升级成跨次回归视图。均依赖已持久化的 per-case 结果（`run_test_cases` 表）。owner-scope 与列表接口一致:**非 admin 只见自己的 run,admin 跨全部**。

#### `GET /runs/trend` — 认证（非 admin 静默过滤）
某套件的跨 run 通过率趋势。
- 查询参数：`tests_path`（必填）· `limit`（默认 `50`）。
- 仅纳入**同 `tests_path`、`COMPLETED`、且带 summary** 的 run；点按 `created_at` **升序**（oldest-first），取最近 `limit` 个。
- **成功**：`200` `RunTrendResponse`（`{tests_path, points: [TrendPoint]}`，见 §八）。空历史 → `points: []`。
- 非 admin 只统计自己的 run。

#### `GET /runs/{run_id}/diff` — owner
把该 run 的 per-case 结果与其**基线**比对,分五桶。
- **基线定义**：**同执行范围**（同 `tests_path` + `runner` + 编译后 `args`）、`created_at` 更早、状态 `COMPLETED` 的**最近一个** run。范围不等则用例集不可比,故严格匹配；非 `COMPLETED`（可能无/部分 cases）一律排除。
- **基线候选同样 owner-scope**：非 admin 只在**自己的** run 里选基线，admin 跨全部——避免基线泄露他人 run 的 id 与用例数据。
- 无可比基线 → `baseline: null` + 空 diff（**不**把全部用例塞进 `new_cases`）。
- **成功**：`200` `RunDiffResponse`（`{baseline, diff}`，见 §八）。
- **错误**：`404`（run 不存在）· `403`（他人）。

#### `GET /cases/history` — 认证（非 admin 静默过滤）
单个测试用例的跨 run 历史 + flaky 判定。
- 查询参数：`tests_path` · `suite` · `name`（三者**必填**,联合定位一个用例）· `limit`（默认 `20`）。
- 返回最近 `limit` 次 `(created_at, status)`，按 `created_at` **升序**；`flaky` 由相邻 `pass ↔ fail/error` 翻转次数判定（**≥2 次**为 flaky，单次回归/修复不算），`flip_count` 为翻转次数。
- **成功**：`200` `CaseHistoryResponse`（`{points, flaky, flip_count}`，见 §八）。
- 非 admin 经 run 的 `created_by` 过滤,只看自己 run 里的该用例历史。
- ⚠️ `flaky` 阈值为保守占位（`[GUESS]`，待真实数据校准）,判定逻辑与端点契约稳定,**阈值本身可能调整**。

#### `GET /metrics` — 认证（非 admin 只看自己的）
仪表盘级质量指标聚合。
- 返回 7 天窗口的 pass rate、平均 duration、run volume，30 天窗口的 flaky test count，以及按 suite 分组的 breakdown。
- **成功**：`200` `MetricsSummary`（见 §八）。
- 非 admin 经 `created_by` 过滤,只看自己的 runs。

---

## 七、调度（Schedule）

### `GET /schedules/preview` — 认证
- 查询参数：`expression`（cron 表达式，必填）、`timezone`（默认 `UTC`）。
- **成功**：`200` `{"next_runs": [datetime, ...]}`（未来 5 次）。
- **错误**：`400`（非法时区 / 非法 cron）。

`TestScheduleCreateRequest` / `UpdateRequest` 请求体：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| name | str | 是 | | |
| profile_id | str | 是 | | 必须引用已存在的 profile |
| cron_expression | str | 是 | | |
| enabled | bool | 否（Update 必填） | `true` | |
| timezone | str | 否 | `UTC` | |

### `POST /schedules` — 认证
- **成功**：`201` `TestScheduleResponse`。
- **错误**：`400`（未知 profile / 非法 cron / 非法时区）· `422`。

### `GET /schedules` — 认证（非 admin 静默过滤）
- 查询参数：`profile_id`（可选）。
- **成功**：`200` `[TestScheduleResponse, ...]`。

### `GET /schedules/{schedule_id}` — owner
- **成功**：`200` `TestScheduleResponse`。 · **错误**：`404` · `403`。

### `PUT /schedules/{schedule_id}` — owner
- **成功**：`200` `TestScheduleResponse`。 · **错误**：`404` · `403` · `400`（非法 cron 等）。

### `DELETE /schedules/{schedule_id}` — owner
- **成功**：`200` `{"status": "success", "message": "..."}`。 · **错误**：`403` · `404`。

### `POST /schedules/{schedule_id}/trigger` — owner
立即按该调度的 profile 触发一次 run（不走 cron 去重，run 归属触发者而非 `system:schedule`）。无请求体。
- **成功**：`202` `RunResponse`。
- **错误**：`404`（调度不存在）· `403`（他人）· `409`（调度的 profile 已不存在）。

---

## 八、数据模型（响应字段）

### TokenResponse
`access_token: str` · `token_type: str = "bearer"`

### UserResponse
`username: str` · `role: enum(admin/user)` · `created_at: str(ISO)`

### TestProfileResponse
`id` · `name` · `description?` · `tests_path` · `runner` · `selected_files[]` ·
`selected_markers[]` · `extra_args` · `executor_mode` · `timeout?` · `created_by` ·
`created_at` · `env{}`

### RunResponse
`id` · `status`（见枚举）· `runner` · `created_by` · `tests_path` · `args[]` ·
`executor_mode` · `summary?`（TestSummary）· `report?`（ReportRef）· `exit_code?` ·
`error?` · `passed?`（bool/null）· `created_at` · `started_at?` · `finished_at?` ·
`stdout?` · `stderr?` · `env{}` · `locked`
- `passed`：仅当 `status=completed` 且有 summary 时计算（`failed==0 且 error==0`），否则 `null`。

### TestSummary
`total` · `passed` · `failed` · `skipped` · `error` · `duration_ms` · `pass_rate`（计算字段）

### ReportRef
`allure_results_dir` · `allure_report_file?` · `html_generated: bool`

### TestCaseResult（diff 桶 / 用例项的元素）
`suite: str` · `name: str` · `status: str`（`passed`/`failed`/`skipped`/`error`）· `duration_ms: int` · `message?: str|null`

### RunTrendResponse
`tests_path: str` · `points: [TrendPoint]`（升序）

### TrendPoint
`run_id: str` · `created_at` · `pass_rate: float` · `total: int` · `passed: int` · `failed: int`

### RunDiffResponse
`baseline: RunDiffBaselineInfo | null` · `diff: RegressionDiff`

### RunDiffBaselineInfo
`id: str` · `created_at` · `status`（RunStatus）

### RegressionDiff
`new_failures[]` · `fixed[]` · `still_failing[]` · `new_cases[]` · `removed_cases[]`，每项为 `TestCaseResult`。
- 身份键 = `(suite, name)`；失败判据 `status ∈ {failed, error}`（`skipped` 不算失败）。
- `new_failures`/`fixed`/`still_failing`/`new_cases` 携带 **head**（当前 run）侧用例；`removed_cases` 携带 **base**（基线）侧用例。非失败 → 非失败的用例不进任何桶。

### CaseHistoryResponse
`points: [CaseHistoryPoint]`（升序）· `flaky: bool` · `flip_count: int`

### CaseHistoryPoint
`created_at` · `status: str`

### TestScheduleResponse
`id` · `name` · `profile_id` · `cron_expression` · `enabled` · `timezone` ·
`last_run_at?` · `next_run_at?` · `created_by` · `created_at`

### SuiteInfoResponse
`name` · `source: str = "local"`（local/git）· `repo_url?` · `ref?`

### LinkTestSuiteResponse
`success: bool` · `suite_name: str` · `is_accessible: bool` · `message: str`

### MetricsSummary
`window_days: int` · `total_runs: int` · `completed_runs: int` · `pass_rate_7d: float` · `avg_duration_ms_7d: float` · `flaky_count_30d: int` · `run_volume_7d: int` · `suites: list[SuiteMetrics]`

### SuiteMetrics
`tests_path: str` · `total_runs: int` · `pass_rate: float` · `avg_duration_ms: float` · `last_run_at: datetime | None`

### 枚举
- **RunStatus**：`queued` → `running` → 终态 `completed` / `failed` / `timeout` / `cancelled`（用户主动取消）。
- **UserRole**：`admin` / `user`。

---

## 九、关键语义备注（接口测试必读）

1. **owner-scope 越权**：单资源接口——他人**存在**资源返回 `403`，**不存在** id 返回 `404`；列表接口——`200` 静默过滤（看不到他人）。
2. **异步 run**：`POST /runs` 返回 `202`，需轮询 `GET /runs/{id}` 直到 `status ∈ {completed, failed, timeout}` 再断言结果。
3. **SSE**：`/runs/{id}/stream` 是长连接流，须用流式客户端。
4. **两种 429**：登录连错 5 次锁定（`Retry-After`）；单用户在跑 run 超上限（默认 20）。
5. **登录限流会污染测试**：同一 `用户名|IP` 连续 5 次失败即锁定，测「密码错=401」时需换用户名或控制失败次数。
6. **资源依赖顺序**：建 schedule 前需先有 profile；触发 run 前需 `tests_path` 指向 `tests_root` 下存在的套件。
7. **管理员账号**：用户名 = `QARUNNER_ADMIN_USER`（默认 `admin`），密码 = `QARUNNER_ADMIN_PASSWORD`（启动时种入；建议测试首步实测此登录）。
8. **跨次对比（trend/diff/cases-history）的 owner-scope**：三者均按**请求者**过滤,不是按目标 run 的归属——非 admin 的趋势点 / diff 基线 / 用例历史**只取自己的 run**，admin 跨全部。测越权时:用 A 造一批同套件 run、再用 B 调这三个端点,断言 B 看不到 A 的数据（diff 的基线应为 `null`）。
9. **diff 可比性**：基线必须**同执行范围**（`tests_path`+`runner`+`args` 全等）。同一套件「这次跑全量 vs 上次跑子集」不会互为基线;若刻意构造范围不等的两个 run,`baseline` 会是 `null` 而非错误分桶。

---

## 附：可信度说明
- 主体（路径 / 方法 / 请求体字段 / 响应字段 / 状态码 / 权限 / 枚举）来自对 `routes.py`、
  `schemas.py`、`deps.py`、`models.py`、`config.py` 的源码核对 `[KNOWN] HIGH`。
- `playwright + docker` 的行为已据 `orchestrator.py` / `docker_runner.py` 源码核实为**受支持组合**（见 §六），非早期描述的"被拒"。
- 结构性契约以运行中的 `GET /openapi.json` 为权威来源。
