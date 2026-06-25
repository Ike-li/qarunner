# 外部测试套件接入：跨环境（dev / 服务器）设计

> 状态：**round 3（2026-06-24），做减法收敛**。不含实现代码。
> round 1 方案有洞 → round 2 补洞补过头（引入与单实例架构/SQLite/playwright 冲突的复杂度）
> → round 3 砍掉多副本过度设计、修正 G2 错误、贴回项目现状。修订轨迹见 §12。
> 背景触发：容器化后用 `AddSuiteModal` 绑定本地项目 `~/code/my-e2e-suite`，
> 后端在容器内 `external_tests/` 建了指向宿主绝对路径的软链接，但容器未挂载该路径 →
> 软链悬空 → 警告；orchestrator `copytree(..., ignore_dangling_symlinks=True)` 跳过悬空软链 →
> **测试拿不到文件**。

## 1. 问题陈述

当前「添加套件」（`POST /tests/link`，`routes.py:265`）在 `tests_root`（`/app/external_tests`）下建软链 → `payload.path`（**宿主绝对路径**），缺陷：

- **依赖宿主文件系统布局**：软链写死宿主绝对路径，容器须挂载完全相同的绝对路径（`source==target`）。
- **部署到服务器不可行** [COMMON]：服务器无 `~/code/...`；生产仓库来源是 git/CI/持久卷。
- **加仓库成本高**：每个新仓库受限于"是否在已挂载目录树下"。
- **深路径 bind mount 脆弱** [INFERRED]：挂宿主 `/Users/...` 深路径在 macOS Docker Desktop 睡眠/重启后失效（已在 artifacts 踩过）。
- **suite 当前零元数据** [COMMON]：`list_tests`（`routes.py:244`）只 `iterdir()` 扫目录名；无 Suite model/store。无来源/归属/ref 可记。

## 2. 目标 / 非目标

**目标**

- 后端只认容器内固定 suites 根，与"宿主在哪"解耦。
- 提供 **环境无关** 的加仓库方式（git），dev 与服务器一致。
- 换环境只改卷配置，不改应用代码。
- 保留 dev "链接本地目录"的便利。
- suite 具备元数据与归属（来源/repo/ref/owner），支撑更新、删除、鉴权。

**非目标**

- 不做 Web 端文件上传。
- 不改 Docker executor 隔离机制。
- 不改 runner 命令构建。
- **不支持平台多副本**（round 3 明确）：项目当前是单实例架构——crash recovery + in-process scheduler 假设单实例独占 DB（CONC-2，`README` §Operational notes）。多副本是一项**独立的前置工程**（需先把 scheduler/crash-recovery 多实例化），不由本特性引入。本设计锁定**单实例**。

## 3. 核心设计

### 3.1 后端只认容器内固定 suites 根

后端只读 `QARUNNER_TESTS_ROOT`（默认 `/app/external_tests`，`config.py:38`）下的子目录，不关心宿主路径。谁填充该目录由部署/卷决定。

```
后端视角（不变）          环境提供（可换，仅改 compose/manifest）
/app/external_tests/  ←─  dev:     bind mount 宿主目录
  ├── my-e2e/        ←─  服务器:   可写持久卷（named volume / host path）
  └── smoke/                        平台单实例直接 git clone/pull 落此
```

### 3.2 Suite 来源抽象：`git`（主力） + `local`（dev 便利）

| 来源 | 机制 | 适用 | 环境无关性 |
|---|---|---|---|
| **git**（主力） | 平台单实例在 suites 根下 `git clone <url> <name>`；更新见 §3.4 | dev + 服务器 | ✅ 不碰宿主 fs |
| **local**（便利） | 现有软链宿主路径逻辑 | 仅 dev、项目在已挂载根下 | ❌ dev 专属 |

### 3.3 Suite 元数据层（新增持久化，地基）

git 来源需存 `repo_url/ref/凭证引用/owner`，而当前 suite 零元数据（§1），故新建：

- **新 model**（`models.py`，与 `TestProfile` 平级，**不要**塞进 TestProfile）：
  `TestSuite { name, source: Literal["local","git"], repo_url?, ref?, credential_ref?, created_by, created_at }`。
- **新 store 接口 + sqlite 表 + 迁移**（`ports/store.py` + `adapters/sqlite_store.py`）：`suites` 表，主键 `name`。
- **`list_tests` 用「文件系统为实体 + 元数据左 join」**（R5，**不是**"元数据为真相源"）：仍扫 `tests_root` 目录得到实体列表，左 join `suites` 表补 `source` 等信息；**未登记的目录照常显示**（标记 `source` 缺省为 `local`/`未登记`），不破坏 dev 直接 clone/放目录的工作流。

### 3.4 git 执行者（单实例）

- **执行者 = 平台单实例自身**（凭证在平台 DB、suites 卷可写）。clone/pull/delete 都由这个实例做。无 leader/follower、无写路由——项目本就是单实例（§2 非目标）。
- **更新（决策 1，手动）**：浅克隆下不用 naive `git pull`，用
  `git fetch --depth 1 origin <ref> && git reset --hard FETCH_HEAD`（规避 shallow 限制，G3）。
- **clone 落实际 ref**（R6）：clone 未指定 ref 时，记录解析出的默认分支（`git rev-parse --abbrev-ref HEAD`），供后续 fetch/reset 使用，避免 `fetch origin <空>`。

## 4. 各环境的卷配置（仅改这一层）

**dev（`docker-compose.dev.yml`）**：

```yaml
# 方式 1：git clone 落项目内 external_tests（浅挂载稳健）
- ./external_tests:/app/external_tests
# 方式 2：local link 宿主项目，env 驱动项目根（source==target），默认 ~/code（决策 3）
- ${QARUNNER_PROJECTS_ROOT:-${HOME}/code}:${QARUNNER_PROJECTS_ROOT:-${HOME}/code}:ro
```

**服务器单实例（`docker-compose.yml`）** —— 可写持久卷，平台直接 clone/pull：

```yaml
services:
  platform:
    volumes:
      - suites-data:/app/external_tests        # 或 /srv/qarunner/suites:/app/external_tests
volumes:
  suites-data:
```

> **多副本**：超出本设计范围（§2）。需先解决 CONC-2（scheduler/crash-recovery 多实例化）；届时再单独设计 suites 的跨副本一致性（注意 SQLite 元数据不随文件系统卷共享，需共享/复制 DB 层）。

## 5. 后端改动点

1. **suite 元数据层**（§3.3）：新 `TestSuite` model + store + sqlite 表 + 迁移。**先决项**。
2. **`POST /tests/clone`**：入参 `{ url, name?, ref?, credential_ref? }`；`git clone --depth 1 [-b ref]` 落 suites 根 + 写记录（`source="git"`, `created_by`=当前用户, ref=实际默认分支）。已存在则拒绝（更新走 pull）。
3. **`POST /tests/{name}/pull`**（决策 1）：`git fetch --depth 1 origin <记录的 ref> && git reset --hard FETCH_HEAD`。**owner-scope**。
4. **`DELETE /tests/{name}`**（G1）：git → `rmtree`+删记录；local → `unlink`+删记录。**owner-scope**。
5. **`list_tests`**：文件系统实体 + `suites` 左 join（R5），返回带 `source`。
6. **安全**（§7）：URL 白名单、子进程参数化、`name` 路径逃逸校验、克隆超时/体积上限、**owner-scope**。
7. **依赖准备 / jail 复制（R1，重做 G2）**——**不能 ignore `node_modules`**：playwright runner 跑 `npx playwright test`（playwright_runner.py:23），依赖 `node_modules/@playwright/test`（package.json:23），ignore 会让它跑不起来。正确处理分两种来源：
   - **git suite**：仓库通常 `.gitignore` 掉 node_modules → clone 后本就**没有** node_modules → 需在 clone 后 / 运行前对 node 项目执行一次 `npm ci`（依赖准备步骤）。copytree 不涉及（仓库里没有）。
   - **local suite**：宿主项目**已带** node_modules → copytree 会全量复制（开销大但 playwright 能跑）。缓解开销的选项留待实现时定（如对 node_modules 用只读 mount 而非复制，或接受复制），**但绝不 ignore**。
   - 结论：`_JAIL_IGNORE_NAMES`（orchestrator.py:57）**不加 node_modules**；依赖准备作为 node 类 runner 的独立步骤。

## 6. 前端改动点

- `AddSuiteModal`（已接入 `App.tsx`）扩展为两个 tab：
  - **Git URL**（主力）：URL + 可选分支 + 可选凭证 → `POST /tests/clone`。
  - **本地路径**（dev）：→ `POST /tests/link`，保留"路径不可达"警告。
- suite 列表按 `source` 区分：git 显示"更新"（`/pull`）+"移除"（`DELETE`）；local 显示"移除"。
- 沿用 `onSuiteLinked`（`fetchTests`）刷新。

## 7. 安全考量 [COMMON]

- **鉴权 / owner-scope**（M3，对齐 BUG-1）：suite 记录含 `created_by`；任意登录用户可 `clone`，但 `pull`/`delete` 限 **owner 或 admin**。**回填的 `system` 属主 suite 为 admin-only**（R7）。
- **命令注入**：参数化子进程（`["git","clone",url,name]` 列表形式），禁 shell 拼接。
- **URL 白名单 / SSRF**：仅 `https://`、`git@`；**禁 `file://`、`ext::`** 等可读本地/执行命令的协议；可选 host 白名单。
- **platform 出网面**（M4）：clone 要求**平台容器出站网络** —— 与 executor 的 `network_mode="none"`（docker_runner.py:138，SEC-3 零网络）是两套基线，是新增攻击面。生产给平台配出站白名单/egress 代理。
- **路径逃逸**：`name` 拒 `..`/绝对路径/分隔符，确保落在 `tests_root` 内。
- **凭证**：每仓库（决策 2），值经环境/挂载注入、记录只存引用，不入库明文、不写日志（SEC-2）。由平台单实例持有并使用。
- **资源**：`--depth 1`、clone 超时、磁盘配额。
- **执行隔离**：克隆只拉代码，跑测试仍由 executor 隔离，不变。

## 8. Docker executor 配合

- **服务器**：platform 与 executor 共享同一 named volume（suites-data），executor 用 volume 名挂载，绕开宿主路径一致难题。
- **dev**：默认 subprocess executor 不涉及；要测 Docker executor 再按 `source==target` 单独处理。
- **node_modules**：见 §5.7——不 ignore；依赖准备 + 复制/只读 mount 策略实现时定。

## 9. 兼容与迁移

- 现有 `local` 软链 suite 不受影响。
- **元数据回填**：首次上线扫 `tests_root`，对"目录存在但无记录"的项回填 `source="local"`、`created_by="system"`（admin-only，R7）。
- list_tests 左 join 后，**未登记目录仍显示**（不强制回填即可见，R5）。
- 不删 `POST /tests/link`。

## 10. 分阶段落地建议（round 3）

- **阶段 0（已完成）**：dev artifacts 浅挂载修复（`/app/artifacts`），消除 DB 503。
- **阶段 1（地基）**：suite 元数据层（model/store/sqlite 表/迁移/回填）+ `list_tests` 左 join。
- **阶段 2**：`POST /tests/clone`（落实际 ref）+ `/pull`（fetch+reset）+ `DELETE` + 安全（owner-scope/URL 白名单/注入）。
- **阶段 3**：node 类 suite 的依赖准备（`npm ci`）+ jail 复制策略（不 ignore node_modules）。
- **阶段 4**：compose 把 suites 根卷化（dev/服务器单实例两套）。
- **阶段 5**：前端 Git tab + 来源区分 + 更新/移除按钮 + 凭证输入。
- **（未来，超范围）**：多副本支持——先解决 CONC-2，再设计 suites 跨副本一致性（含 SQLite 元数据共享）。

## 11. 决策（已定稿，含 round 3 修订）

| # | 决策 | 落地含义 |
|---|---|---|
| 1 | **更新方式：手动** | 不自动 pull / 无 webhook；"更新"按钮 → `/pull`，内部 `fetch --depth 1 + reset --hard`（兼容浅克隆）。 |
| 2 | **凭证：每仓库** | 每 git suite 存自己的凭证引用；值经环境/挂载注入、不落明文（SEC-2）；由平台单实例持有。 |
| 3 | **dev 本地软链根：保留** | `QARUNNER_PROJECTS_ROOT` 默认 `~/code`，可 `.env` 调窄。 |
| 4 | **部署模型：单实例**（round 3 修订） | 平台单实例 = 唯一 git 执行者与写者，贴合 CONC-2。**多副本超出范围**，列为未来前置工程。 |
| 5 | **suite 持久化：新建元数据层** | 新建 `TestSuite` model + store + 迁移 + owner；list_tests 文件系统实体左 join 元数据（不破坏手动放目录）。 |
| 6 | **node_modules：不 ignore**（round 3 修订） | playwright 需要它；改为依赖准备（git suite `npm ci`）+ 复制/只读 mount 策略，绝不加进 jail ignore。 |

## 12. 评审修订记录

**round 1 → round 2**

| 编号 | 发现 | 处置 |
|---|---|---|
| B1 | suite 无持久化层，误塞 TestProfile | 新建 suite 元数据层 |
| M1/M2 | 手动更新×只读副本、每仓库凭证×外部写入 冲突 | （round 2 引入 leader/follower —— round 3 已撤，见下） |
| M3 | clone/pull/删除 缺 owner-scope | suite 加 `created_by`，pull/delete 限 owner/admin |
| M4 | platform 出网面未界定 | §7 明确平台出网 vs executor 零网络两套基线 |
| G1 | 缺删除 | `DELETE /tests/{name}` |
| G2 | jail 含 node_modules，copytree 爆炸 | （round 2 错误地 ignore —— round 3 已修，见下） |
| G3 | 浅克隆×naive pull | `fetch --depth 1 + reset --hard` |

**round 2 → round 3（做减法 + 纠错）**

| 编号 | round 2 的问题 | round 3 处置 |
|---|---|---|
| R1 | G2 修反了：ignore node_modules 会让 playwright 跑不起来 | §5.7 重做——不 ignore；git suite `npm ci` 准备依赖，local suite 复制/只读 mount（决策 6） |
| R2 | leader/follower 与单实例架构（CONC-2）脱节 | 砍掉多副本设计，锁定单实例（§2 非目标、决策 4） |
| R3 | SQLite 元数据 × 多副本只读卷 自相矛盾 | 随多副本一并移除；未来多副本需单独解决 DB 共享 |
| R4 | 307 写路由在浏览器+LB+SameSite cookie 下不可行 | 随多副本移除；单实例无需路由 |
| R5 | list_tests 改"元数据为真相源"破坏手动放目录 | 改为"文件系统实体 + 元数据左 join" |
| R6 | fetch ref 缺失未定义 | clone 落实际默认分支（`rev-parse --abbrev-ref HEAD`） |
| R7 | system 属主 suite 权限未定 | admin-only |

---

**TL;DR**：后端读容器内固定 suites 根、卷由环境提供；加仓库主力改 git clone；新建 suite 元数据层（list_tests 左 join，不破坏手动放目录）。**round 3 做减法**：砍掉与单实例架构（CONC-2）冲突的多副本/leader 设计、修掉"ignore node_modules 会让 playwright 跑不起来"的错误。设计回到单实例现实，实现仍以"元数据层 + clone/pull/delete + 依赖准备"为主线。
