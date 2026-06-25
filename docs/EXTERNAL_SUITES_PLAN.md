# 外部测试套件接入：跨环境（dev / 服务器）设计

> 状态：**已评审并修订（round 2，2026-06-24）**。不含实现代码。
> round 2 解决了 round 1 评审发现的 1 个基础错误（suite 无持久化层）+ 2 处决策冲突
> （手动更新 × 只读多副本、每仓库凭证 × 外部写入）+ 若干遗漏。详见 §12。
> 背景触发：容器化后用 `AddSuiteModal` 绑定本地项目 `~/code/my-e2e-suite`，
> 后端在容器内 `external_tests/` 建了指向宿主绝对路径的软链接，但容器未挂载该路径 →
> 软链悬空、`is_dir()` 为假 → 警告；且 orchestrator `copytree(..., ignore_dangling_symlinks=True)`
> 会跳过悬空软链，**测试根本拿不到文件**。

## 1. 问题陈述

当前「添加套件」流程（`POST /tests/link`，`routes.py:265`）：

1. 在 `tests_root`（容器内 `/app/external_tests`）下建软链 `<suite_name>` → `payload.path`（**宿主绝对路径**）。
2. 用 `link_path.is_dir()` 判断是否可达。

这是一个 **dev-only 的实现**，根本缺陷：

- **依赖宿主文件系统布局**。软链写死宿主绝对路径，容器必须挂载**完全相同的绝对路径**（`source==target`）才能解析。
- **部署到服务器不可行** [COMMON]。服务器上不存在 `~/code/...`；生产环境的测试仓库来源是 git / CI / 持久卷，而不是"某台开发机的目录"。
- **加仓库成本高**。每个新仓库都受限于"是否落在已挂载的目录树下"，否则要改 compose + 重建。
- **深路径 bind mount 脆弱** [INFERRED]。挂宿主 `/Users/...` 深路径在 macOS Docker Desktop 睡眠/重启后会失效（本项目已在 artifacts 上踩过，见 `docker-compose.dev.yml` 改为 `/app/artifacts` 浅挂载的修复）。
- **suite 当前零元数据** [COMMON]。`list_tests`（`routes.py:244`）只 `iterdir()` 扫目录名；`models.py`/`ports/store.py` 无任何 Suite model/store 方法。suite 就是"tests_root 下的一个目录"，**没有来源、归属、ref 等任何记录可言**——这是引入 git 来源前必须先补的地基（见 §3.3）。

## 2. 目标 / 非目标

**目标**

- 后端与"宿主在哪"解耦：只认容器内固定的 suites 根。
- 提供一个 **环境无关** 的加仓库方式，dev 与服务器一致。
- 换环境只改卷配置（compose / k8s manifest），**不改应用代码**。
- 保留 dev 下"链接本地目录"的便利。
- **suite 具备元数据与归属**（来源、repo、ref、owner），支撑更新、删除、鉴权。

**非目标**

- 不做 Web 端项目文件上传（大型测试仓库不现实，且丢失 git 历史）。
- 不在本设计内重写 Docker executor 的隔离机制（仅说明卷共享方式）。
- 不改变 pytest/playwright runner 的命令构建逻辑。

## 3. 核心设计

### 3.1 后端只认容器内固定 suites 根

后端永远只读 `QARUNNER_TESTS_ROOT`（默认 `/app/external_tests`，`config.py:38`）下的子目录，**不关心宿主路径**。"谁来填充这个目录"是部署/环境的职责，由卷提供。

```
后端视角（不变）          环境提供（可换，仅改 compose/manifest）
/app/external_tests/  ←─  dev:      bind mount 宿主目录
  ├── my-e2e/        ←─  服务器:    可写卷（leader 实例 git clone/pull）
  └── smoke/          ←─  多副本:    follower 只读挂同一卷
```

### 3.2 Suite 来源抽象：`git`（主力） + `local`（dev 便利）

| 来源 | 机制 | 适用 | 环境无关性 |
|---|---|---|---|
| **git**（主力） | 平台在 suites 根下 `git clone <url> <name>`；更新见 §3.4 | dev + 服务器 + k8s | ✅ 完全无关，不碰宿主 fs |
| **local**（便利） | 现有软链宿主路径逻辑 | 仅 dev、且项目在已挂载根下 | ❌ dev 专属 |

`git` 为主力，`local` 降级为 dev 的可选快捷方式。

### 3.3 Suite 元数据层（新增持久化，地基）

**现状**：suite 无任何记录（§1 最后一条）。git 来源需要存 `repo_url/ref/凭证引用/owner`，因此必须新建一层 **suite 元数据持久化**：

- **新 model**（`models.py`，与 `TestProfile` 平级、**不要**塞进 TestProfile）：
  `TestSuite { name, source: Literal["local","git"], repo_url?: str, ref?: str, credential_ref?: str, created_by: str, created_at }`。
- **新 store 接口 + sqlite 实现 + 迁移**（`ports/store.py` + `adapters/sqlite_store.py`）：`suites` 表，主键 `name`。
- **`list_tests` 改为 元数据为准**：以 `suites` 表为真相源，文件系统目录为实体；兼容期对"目录存在但无记录"的老 suite 按 `local` 回填（见 §9）。

> 这是相对 round 1 估计**多出来的一档工作量**：不是"给已有记录加字段"，而是从零建 suite 持久化。

### 3.4 git 执行者与多副本模型（消除 round 1 的决策冲突）

**唯一写入者 = 平台自身的「可写 leader 实例」**（不是外部 CI）。理由：凭证在平台 DB（决策 2），由平台执行 git 才能用上；外部 CI 写入会让平台配的凭证形同虚设。

- **单实例 / dev**：该实例即 leader，挂**可写** suites 卷，直接执行 clone/pull。
- **多副本**：一个实例标记为 leader（`QARUNNER_SUITES_WRITER=true`，挂可写卷）执行所有 git 写操作；follower 挂**只读**卷、仅消费。
- **写请求路由**：`clone` / `pull` / `delete` 这类写操作必须落到 leader。两种实现：(a) 仅 leader 注册这些路由，follower 返回 307 指向 leader；(b) 入口层把写路径路由到 leader。**更新按钮（决策 1）因此能在多副本下正确工作**——请求最终由 leader 执行。
- **更新机制（决策 1，手动）**：浅克隆下不要 naive `git pull`。用
  `git fetch --depth 1 origin <ref> && git reset --hard FETCH_HEAD`，规避 shallow clone 的 pull 限制（见 §5）。

## 4. 各环境的卷配置（仅改这一层）

**dev（`docker-compose.dev.yml`）** —— 单实例即 leader，可写：

```yaml
# 方式 1：git clone 落项目内 external_tests（浅挂载稳健）
- ./external_tests:/app/external_tests
# 方式 2：local link 宿主项目，env 驱动项目根（source==target），默认 ~/code（决策 3）
#   覆盖请在 .env 设 QARUNNER_PROJECTS_ROOT
- ${QARUNNER_PROJECTS_ROOT:-${HOME}/code}:${QARUNNER_PROJECTS_ROOT:-${HOME}/code}:ro
```

**服务器单实例（`docker-compose.yml`）** —— named volume 或 host path，可写：

```yaml
services:
  platform:
    environment:
      - QARUNNER_SUITES_WRITER=true
    volumes:
      - suites-data:/app/external_tests        # 或 /srv/qarunner/suites:/app/external_tests
volumes:
  suites-data:
```

**k8s / 多副本** —— leader 挂可写卷执行 git；follower 挂同一卷的**只读**视图消费（NFS/EFS 的 ReadWriteMany + 单写者，或 leader 写 PVC、follower 只读挂载）。写请求路由到 leader（§3.4）。

> 关键收益：从 dev 到服务器，**应用镜像不变、代码不变**，只换这段卷定义 + `QARUNNER_SUITES_WRITER`。

## 5. 后端改动点

1. **suite 元数据层**（§3.3）：新 `TestSuite` model + store 接口 + sqlite 表 + 迁移。**这是先决项**。
2. **`POST /tests/clone`**（与 `POST /tests/link` 并列）
   - 入参：`CloneTestSuiteRequest { url, name?, ref?, credential_ref? }`。
   - 行为：`git clone --depth 1 [-b ref] <url> <name>` 落 suites 根；写 suite 记录（`source="git"`，含 owner=当前用户）。已存在则拒绝（更新走 pull）。
   - **仅 leader 执行**（§3.4）；follower 收到则 307 至 leader。
3. **`POST /tests/{name}/pull`**（决策 1，手动更新）
   - `git fetch --depth 1 origin <ref> && git reset --hard FETCH_HEAD`（**非** naive pull，兼容浅克隆，G3）。仅 leader 执行。
4. **`DELETE /tests/{name}`**（G1，生命周期补全）
   - git：`rmtree` 目录 + 删记录；local：`unlink` 软链 + 删记录。**owner-scope**（§7）。仅 leader 执行。
5. **`list_tests`**：改为以 `suites` 表为真相源（§3.3），返回时带 `source` 便于前端区分。
6. **安全**（§7）：URL 白名单、子进程参数化、`name` 路径逃逸校验、克隆超时/体积上限、**owner-scope 鉴权**。
7. **配套（orchestrator）**：把 `node_modules`（及 `test-results`/`playwright-report`）加入 `_JAIL_IGNORE_NAMES`（`orchestrator.py:57`，当前仅含 `.git/.venv/.pytest_cache/.ruff_cache/__pycache__`）——否则 git+playwright 套件每次运行会 `copytree` 全量复制 node_modules（可能 GB 级，G2）。

## 6. 前端改动点

- `AddSuiteModal`（已接入 `App.tsx`）扩展为**两个 tab**：
  - **Git URL**（主力）：URL + 可选分支 + 可选凭证 → `POST /tests/clone`。
  - **本地路径**（dev 便利）：→ `POST /tests/link`，保留"路径不可达"警告。
- suite 列表按 `source` 区分：git 来源显示**"更新"**（→ `/pull`）与**"移除"**（→ `DELETE`）按钮；local 显示"移除"。
- 沿用 `onSuiteLinked`（`fetchTests`）刷新。

## 7. 安全考量 [COMMON]

- **鉴权 / owner-scope**（M3，对齐 BUG-1）：suite 记录含 `created_by`；`clone` 任意登录用户可建，但 `pull`/`delete` 限 **owner 或 admin**。杜绝越权改/删他人 suite。
- **命令注入**：参数化子进程（`["git","clone",url,name]` 列表形式），**禁止** shell 拼接。
- **URL 白名单 / SSRF**：限制协议（仅 `https://`、`git@`），**禁 `file://`、`ext::`** 等可读本地/执行命令的协议；可选 host 白名单。
- **platform 出网面**（M4）：clone 要求 **platform（leader）容器出站网络** —— 这与 executor 的 `network_mode="none"`（`docker_runner.py:138`，SEC-3 零网络）是**两套基线**，是新增攻击面。生产应给 leader 配出站白名单/egress 代理，follower 无需出网。
- **路径逃逸**：`name` 校验拒绝 `..`/绝对路径/分隔符，确保落在 `tests_root` 内。
- **凭证**：**每仓库**（决策 2），值经环境/挂载注入、记录只存引用，**不入库明文、不写日志**（SEC-2）。
- **资源**：`--depth 1`、clone 超时、磁盘配额，防超大仓库拖垮 leader。
- **执行隔离**：克隆只拉代码，跑测试仍由 executor（subprocess/Docker SEC-3）隔离，不变。

## 8. Docker executor 配合

- **服务器推荐**：platform 与 executor **共享同一 named volume**（suites-data），executor 用 volume 名挂载——绕开宿主路径一致难题与深路径脆弱性。
- **dev**：默认 subprocess executor 不涉及；若 dev 要测 Docker executor，再单独按 `source==target` 处理。
- **jail 复制**：见 §5.7，git 套件务必把 `node_modules` 等纳入 ignore，否则 copytree 开销爆炸。

## 9. 兼容与迁移

- 现有 `local` 软链 suite **不受影响**。
- **元数据回填**：首次上线扫描 `tests_root`，对"目录存在但无 `suites` 记录"的项回填一条 `source="local"`、`created_by="system"` 的记录（或标记为待认领）。
- 新增 git 能力是叠加，不删 `POST /tests/link`。

## 10. 分阶段落地建议（按 round 2 重估）

- **阶段 0（已完成）**：dev artifacts 浅挂载修复（`/app/artifacts`），消除睡眠失效导致的 DB 503。
- **阶段 1（地基，先决）**：suite 元数据层（model/store/sqlite 表/迁移/回填）+ `list_tests` 改造。**这是 round 2 新识别的前置工作量。**
- **阶段 2**：`POST /tests/clone` + `/pull`（fetch+reset）+ `DELETE` + 安全（owner-scope/URL 白名单/注入）+ orchestrator jail ignore 补 node_modules。
- **阶段 3**：compose 把 suites 根卷化 + `QARUNNER_SUITES_WRITER` + 三套环境配置；leader/follower 写路由。
- **阶段 4**：前端 Git tab + 来源区分 + 更新/移除按钮 + 凭证输入。
- **阶段 5（可选）**：Docker executor + named volume 共享端到端验证；私有仓库凭证注入形态定稿。

## 11. 决策（已定稿，含 round 2 修订）

| # | 决策 | 落地含义 |
|---|---|---|
| 1 | **更新方式：手动** | 不自动 pull / 无 webhook；前端"更新"按钮 → `POST /tests/{name}/pull`，内部用 `fetch --depth 1 + reset --hard`（兼容浅克隆）。 |
| 2 | **凭证：每仓库** | 每 git suite 存自己的凭证引用；值经环境/挂载注入、不落明文（SEC-2）。**由平台 leader 持有并使用**（见决策 5）。 |
| 3 | **dev 本地软链根：保留** | `QARUNNER_PROJECTS_ROOT` 默认 `~/code`，可 `.env` 调窄。 |
| 4 | **多副本：共享卷 + 单写者** | follower 只读挂同一卷消费；**写入者是平台自己的 leader 实例**（非外部 CI），保证 ref 一致。 |
| 5 | **git 执行者：平台 leader**（round 2 新增） | clone/pull/delete 一律由 leader 执行（凭证在平台、卷可写）；follower 把写请求 307 到 leader。消除"手动更新 × 只读副本"和"每仓库凭证 × 外部写入"两处冲突。 |
| 6 | **suite 持久化：新建元数据层**（round 2 新增） | 不复用 TestProfile；新建 `TestSuite` model + store + 迁移 + owner，作为阶段 1 先决。 |

## 12. 评审修订记录（round 1 → round 2）

| 编号 | round 1 发现 | round 2 处置 |
|---|---|---|
| B1 | suite 无持久化层，`source` 无处可存；误塞 TestProfile | §3.3 新建 suite 元数据层；§10 阶段 1 重估为先决地基（决策 6） |
| M1 | 手动更新按钮 × 只读多副本冲突 | §3.4 leader/follower + 写请求路由到 leader（决策 5） |
| M2 | 每仓库凭证 × 外部 CI 写入归属不清 | 写入者收敛为平台 leader，凭证由 leader 持有（决策 4/5） |
| M3 | clone/pull/删除 缺鉴权/owner-scope | §7 suite 加 `created_by`，pull/delete 限 owner/admin（对齐 BUG-1） |
| M4 | platform 出网面未界定 | §7 明确 leader 需出网、与 executor 零网络是两套基线，配 egress 限制 |
| G1 | 缺 suite 删除/解绑 | §5.4 `DELETE /tests/{name}` |
| G2 | jail ignore 不含 node_modules，git+playwright copytree 爆炸 | §5.7 把 node_modules 等加入 `_JAIL_IGNORE_NAMES` |
| G3 | 浅克隆 × naive pull 不兼容 | §3.4/§5.3 改用 `fetch --depth 1 + reset --hard` |

---

**TL;DR**：后端读容器内固定 suites 根、卷由环境提供；加仓库主力改 git clone。round 2 补齐了三块地基——**suite 元数据持久化层**（原本没有）、**平台 leader 统一执行 git + 多副本 leader/follower**（消除更新/凭证冲突）、**owner-scope 鉴权 + jail ignore + 浅克隆更新**等安全/正确性细节。换环境仍只改卷配置，但实现工作量比 round 1 估计大一档（多出元数据层 + leader 路由）。
