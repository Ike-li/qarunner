# 外部测试套件接入：跨环境（dev / 服务器）设计

> 状态：设计草案，待评审。不含实现代码。
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

## 2. 目标 / 非目标

**目标**

- 后端与"宿主在哪"解耦：只认容器内固定的 suites 根。
- 提供一个 **环境无关** 的加仓库方式，dev 与服务器一致。
- 换环境只改卷配置（compose / k8s manifest），**不改应用代码**。
- 保留 dev 下"链接本地目录"的便利。

**非目标**

- 不做 Web 端项目文件上传（大型测试仓库不现实，且丢失 git 历史）。
- 不在本设计内重写 Docker executor 的隔离机制（仅说明卷共享方式）。
- 不改变 pytest/playwright runner 的命令构建逻辑。

## 3. 核心设计：来源（source）与挂载（mount）分层

### 3.1 后端只认容器内固定 suites 根

后端永远只读 `QARUNNER_TESTS_ROOT`（默认 `/app/external_tests`，`config.py:38`）下的子目录，**不关心宿主路径**。"谁来填充这个目录"是部署/环境的职责，由卷提供。

```
后端视角（不变）          环境提供（可换，仅改 compose/manifest）
/app/external_tests/  ←─  dev:    bind mount 宿主目录
  ├── my-e2e/        ←─  服务器:  named volume / host path / NFS
  └── smoke/          ←─  k8s:     共享网络卷 或 initContainer git clone
```

### 3.2 Suite 来源抽象：`git`（主力） + `local`（dev 便利）

给 suite 增加"来源类型"，加仓库收敛成两条路径：

| 来源 | 机制 | 适用 | 环境无关性 |
|---|---|---|---|
| **git**（主力） | 后端在 suites 根下 `git clone <url> <name>`；更新 `git pull` | dev + 服务器 + k8s | ✅ 完全无关，不碰宿主 fs |
| **local**（便利） | 现有软链宿主路径逻辑 | 仅 dev、且项目在已挂载根下 | ❌ dev 专属 |

**git 成为主力**：服务器/CI 天然支持，更新方便，不依赖任何宿主路径。`local` 降级为 dev 的可选快捷方式。

## 4. 各环境的卷配置（仅改这一层）

**dev（`docker-compose.dev.yml`）** —— bind 宿主目录，支持 local link + git：

```yaml
# 方式 1：仍用项目内 external_tests（git clone 落这里，浅挂载稳健）
- ./external_tests:/app/external_tests
# 方式 2：local link 宿主项目，env 驱动一个项目根（source==target），默认 ~/code（决策 3）
#   覆盖请在 .env 设 QARUNNER_PROJECTS_ROOT
- ${QARUNNER_PROJECTS_ROOT:-${HOME}/code}:${QARUNNER_PROJECTS_ROOT:-${HOME}/code}:ro
```

**服务器（`docker-compose.yml`）** —— named volume 或 host path，git clone 落持久卷：

```yaml
volumes:
  - suites-data:/app/external_tests        # 或 /srv/qarunner/suites:/app/external_tests
# ...
volumes:
  suites-data:
```

**k8s / 多副本** —— 共享**只读**网络卷（NFS/EFS 等）承载 suites，由单一写入点（CI / 运维 / leader）`git clone/pull` 填充，各副本只读消费以保证 ref 一致（决策 4）。

> 关键收益：从 dev 到服务器，**应用镜像不变、代码不变**，只换上面这段卷定义。

## 5. 后端改动点

1. **新增 `POST /tests/clone`**（与现有 `POST /tests/link` 并列，`routes.py`）
   - 入参草图：`CloneTestSuiteRequest { url: str, name: str | None, ref: str | None }`（`name` 缺省取仓库名，`ref` 可选分支/tag）。
   - 行为：在 `tests_root` 下 `git clone --depth 1 [-b ref] <url> <name>`。已存在时**不自动覆盖/拉取**。
   - 更新走独立动作（如 `POST /tests/{name}/pull`），由前端"更新"按钮**手动**触发 `git pull`（决策 1）。
   - 复用现有 `LinkTestSuiteResponse` 形态（`success` / `message`）。
2. **模型**：`TestProfile`（`models.py`）与 suite 记录增加 `source: Literal["local","git"]`（默认 `"local"` 向后兼容），git 来源额外存 `repo_url` / `ref` / **每仓库凭证引用**（决策 2），便于"更新/重新拉取"。
3. **`list_tests`（`routes.py:244`）**：维持扫描 `tests_root` 子目录的方式，git clone 出来的目录自动被发现，**无需特判**。
4. **安全**（见 §7）：URL 校验、子进程参数化（杜绝命令注入）、克隆超时/体积上限、`name` 路径逃逸校验。

## 6. 前端改动点

- `AddSuiteModal`（已接入 `App.tsx`）从单一"本地路径输入"扩展为**两个 tab**：
  - **Git URL**（主力）：URL + 可选分支 → `POST /tests/clone`。
  - **本地路径**（dev 便利，现状）：→ `POST /tests/link`，保留当前"路径不可达"的警告文案。
- 成功后沿用现有 `onSuiteLinked`（即 `fetchTests`）刷新列表。

## 7. 安全考量（git 来源新增面） [COMMON]

- **命令注入**：用参数化子进程（`["git","clone",url,name]` 列表形式），**禁止** shell 字符串拼接。
- **URL 白名单 / SSRF**：限制协议（`https://`、`git@`）；考虑限制 host 或要求显式允许，避免被当作内网探测跳板。
- **路径逃逸**：`name` 经 `secure` 校验，拒绝 `..`、绝对路径、分隔符，确保 clone 目标落在 `tests_root` 内。
- **凭证**：**每仓库**配置（决策 2），token/SSH key 通过环境/挂载注入、记录里只存引用，**不入库明文、不写日志**（与 SEC-2 一致）。
- **资源**：`--depth 1`、clone 超时、磁盘配额，防止超大仓库拖垮平台容器。
- **执行隔离**：克隆只是拉代码，真正跑测试仍由 executor（subprocess/Docker SEC-3）隔离，不变。

## 8. Docker executor 配合

启用 Docker executor（DooD，`docker_runner.py`）时，executor 容器挂载 suites 的方式：

- **服务器推荐**：platform 与 executor **共享同一个 named volume**（suites-data），executor 用 volume 名挂载——**绕开宿主路径一致难题**，也避开深路径 bind 的脆弱性。
- **dev**：默认 subprocess executor 不涉及此问题；若 dev 要测 Docker executor，再单独按 `source==target` 处理。

## 9. 兼容与迁移

- 现有 `local` 软链 suite **不受影响**（`source` 默认 `"local"`）。
- 新增 git 能力是叠加，不删除 `POST /tests/link`。
- 老数据无 `source` 字段时按 `"local"` 读取。

## 10. 分阶段落地建议

- **阶段 0（已完成）**：dev artifacts 浅挂载修复（`/app/artifacts`），消除睡眠失效导致的 DB 503。
- **阶段 1**：后端 `POST /tests/clone` + `source` 字段 + 安全校验；compose 把 suites 根做成可换卷；文档化 dev/服务器/k8s 三套卷配置。
- **阶段 2**：前端 `AddSuiteModal` 加 Git tab；suite 列表区分来源、git 来源提供"更新"按钮。
- **阶段 3（可选）**：Docker executor + named volume 共享在服务器上的端到端验证；私有仓库凭证注入。

## 11. 评审决策（已定稿 2026-06-24）

| # | 决策 | 落地含义 |
|---|---|---|
| 1 | **更新方式：手动** | git 来源已存在时**不自动 pull**；前端提供显式"更新"按钮触发 `git pull`。**不做 webhook / 定时拉取**。 |
| 2 | **凭证：每仓库配置** | 每个 git suite 单独存自己的凭证引用（token / SSH key 引用），不共用平台级单 token。凭证值经环境/挂载注入，记录里只存引用、不落明文（SEC-2）。 |
| 3 | **dev 本地软链根：保留** | `QARUNNER_PROJECTS_ROOT` 默认 `~/code`（可在 `.env` 调窄）。dev 下 `local` link 仍可用，根下任意项目零改 compose 即可绑定。 |
| 4 | **多副本：共享只读网络卷** | 服务器 / k8s 多副本挂同一**只读**网络卷（NFS/EFS 等）承载 suites，由单一写入点（CI / 运维 / 一个 leader）`git clone/pull`，各副本只读消费，保证 ref 一致。 |

> 留到阶段 1 实现时再定的小问：每仓库凭证的具体存储形态（env 命名约定 vs 挂载 secret 目录）——但粒度已确定是"每仓库"。

---

**TL;DR**：把"后端读容器内固定 suites 根"和"卷由环境提供"分层，加仓库主力改用 **git clone**（dev/服务器一致、不碰宿主 fs），`local` 软链降级为 dev 便利。换环境只改卷配置、不改代码。
