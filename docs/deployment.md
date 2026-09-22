# 本地部署与更新指南

> [!CAUTION]
> 本文记录当前已实现的单宿主开发/验证部署，不是 V7.4 目标生产拓扑。目标要求控制面与专用 Worker 分离，控制面无 Docker socket，每个 Run 在 Worker 上使用全新一次性容器。GAP-021/SOR-GAP-023 关闭前，本文的 `docker-compose.yml` 不得用于运行不可信外部测试代码。

## 两种部署模式

qarunner 提供两套 Docker Compose 配置，**日常开发用 dev，legacy 单宿主验证用 prod 文件**；该文件不是 V7.4 生产部署。

| 模式 | 文件 | 前端 | 后端 | 热更新 |
|------|------|------|------|--------|
| 开发 | `docker-compose.dev.yml` | Vite dev server (:5173) | Uvicorn --reload (:8000) | ✅ 前后端都支持 |
| legacy 验证 | `docker-compose.yml` | 静态文件由 FastAPI serve | Uvicorn (:8000) | ❌ 需手动操作 |

---

## 开发模式（推荐日常使用）

> 新建 PostgreSQL volume 或未确认 revision 时，先按下方 **PostgreSQL migration operator**
> 段落完成 `upgrade head`（或 exact legacy adoption）；backend 在迁移前会按设计 fail closed。
> 数据库 revision 与代码期望的 head 一致时，才可以直接执行下面的完整启动命令；
> 不一致时后端会 fail closed 并在日志中打印 current / expected revision。

### 首次启动

```bash
# 启动（如果首次运行或改了 docker-compose，加 --build）
docker compose -f docker-compose.dev.yml up -d

# 查看服务状态
docker compose -f docker-compose.dev.yml ps
```

启动后访问：
- **前端**: http://localhost:5173（主要入口，Vite 代理 API 请求到后端）
- **后端 API**: http://localhost:8000/docs（Swagger 文档）
- **管理员**: 用户名 `admin`，密码为 `.env` 中 `QARUNNER_ADMIN_PASSWORD` 的值（弱口令会被拒绝，平台将无法启动）

### PostgreSQL migration operator

开发 Compose 的 PostgreSQL DDL 由显式 operator 命令执行，backend 启动不会隐式迁移。
首次使用空数据库时先启动 PostgreSQL，再在一次性 backend 容器中升级到 head：

```bash
docker compose -f docker-compose.dev.yml up -d postgres
docker compose -f docker-compose.dev.yml run --rm \
  -e QARUNNER_MIGRATION_DATABASE_URL='postgresql://qarunner@postgres:5432/qarunner' \
  -e QARUNNER_MIGRATION_SCHEMA=public \
  backend uv run python -m qarunner.migrations upgrade head
docker compose -f docker-compose.dev.yml up -d backend frontend
```

如果目标是未接管的 exact legacy v10 Schema，必须先显式 adoption，再执行同一条
`upgrade head`；adoption 会严格校验 v1-v10 checksum 和物理 catalog，失败时不会 stamp：

```bash
docker compose -f docker-compose.dev.yml run --rm \
  -e QARUNNER_MIGRATION_DATABASE_URL='postgresql://qarunner@postgres:5432/qarunner' \
  -e QARUNNER_MIGRATION_SCHEMA=public \
  backend uv run python -m qarunner.migrations adopt-legacy
```

运行时若 `alembic_version` 缺失、落后、超前、分支或未知，backend 会 fail closed。生产默认
只允许 expand/forward-fix；`downgrade` 仅用于 disposable Schema，并且必须显式设置
`QARUNNER_MIGRATION_ALLOW_DOWNGRADE=true`。迁移前应先完成并验证可恢复的 `pg_dump`。

### 更新代码后

| 修改了什么 | 需要做什么 |
|-----------|-----------|
| 前端 `frontend/src/*.tsx`、`*.css` | **自动生效**（Vite HMR），刷新浏览器即可 |
| 后端 `src/*.py` | **自动重启**（Uvicorn --reload），等 2-3 秒 |
| `frontend/package.json`（新增/更新 npm 包） | `docker compose -f docker-compose.dev.yml up -d --build` |
| `pyproject.toml`（新增/更新 Python 包） | `docker compose -f docker-compose.dev.yml up -d --build` |
| `docker-compose.dev.yml` | `docker compose -f docker-compose.dev.yml up -d --build` |

### 容器结构

```
docker-compose.dev.yml
├── backend (qarunner-backend-dev)
│   ├── 镜像: Dockerfile.dev
│   ├── 端口: 8000 → 容器 8000
│   ├── 挂载: src/、tests/（代码修改即时生效）
│   └── 依赖: Docker daemon socket
│
└── frontend (qarunner-frontend-dev)
    ├── 镜像: node:20-slim
    ├── 端口: 5173 → 容器 5173
    ├── 挂载: frontend/（代码修改即时生效）
    └── VITE_BACKEND_URL: http://backend:8000
```

---

## Legacy 单宿主验证模式（非 V7.4 生产）

### 首次部署

```bash
# 1. 创建环境变量文件（仅首次）
cp .env.example .env
# 编辑 .env，填入两个必填项：
#   QARUNNER_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(64))")
#   QARUNNER_ADMIN_PASSWORD=你的强密码

# 2. 创建必要目录
mkdir -p artifacts external_tests

# 3. 构建镜像并启动
docker compose up -d --build

# 4. 验证
curl -f http://localhost:8000/health
```

启动后访问 **http://localhost:8000**（前后端一体，单端口）。

### 更新代码后

| 修改了什么 | 需要做什么 | 原因 |
|-----------|-----------|------|
| 后端 `src/*.py` | `docker compose up -d --build`（或 `docker compose restart app`） | Python 代码即时生效，但如果改了 endpoint 需重建 |
| 前端 `frontend/src/*` | `docker compose up -d --build` | 前端代码编译为静态文件打包进镜像，**必须重建** |
| `frontend/package.json` | `docker compose up -d --build` | npm 依赖变更 |
| `pyproject.toml` | `docker compose up -d --build` | Python 依赖变更 |
| `Dockerfile.server` | `docker compose up -d --build` | 镜像构建逻辑变更 |
| `docker-compose.yml` | `docker compose up -d --build` | 容器配置变更 |
| `.env` | `docker compose up -d`（不需要 --build） | 环境变量在容器启动时注入 |

### Legacy 验证容器结构

```
docker-compose.yml
└── app (qarunner)
    ├── 镜像: Dockerfile.server（多阶段构建：Node build → Python runtime）
    ├── 端口: 8000
    ├── 挂载:
    │   ├── artifacts/ → 数据库 + 运行产物（持久化）
    │   ├── external_tests/ → 测试套件代码
    │   └── /var/run/docker.sock → Docker daemon（执行测试容器用）
    └── 健康检查: GET /health
```

---

## 常用运维命令

```bash
# ── 查看状态 ──
docker compose ps                      # 查看所有服务
docker compose logs -f app             # legacy 验证日志
docker compose -f docker-compose.dev.yml logs -f backend frontend  # 开发日志

# ── 进入容器 ──
docker compose exec app bash                   # legacy 验证
docker compose -f docker-compose.dev.yml exec backend bash  # 开发后端
docker compose -f docker-compose.dev.yml exec frontend bash # 开发前端

# ── 重启 ──
docker compose restart app                     # 重启 legacy 验证
docker compose -f docker-compose.dev.yml restart backend  # 重启开发后端

# ── 彻底清理 ──
docker compose down -v                         # 停止并删除 volumes
docker compose -f docker-compose.dev.yml down -v

# ── 重建并启动 ──
docker compose up -d --build                  # legacy 验证
docker compose -f docker-compose.dev.yml up -d --build  # 开发

# ── 在容器内运行测试 ──
docker compose -f docker-compose.dev.yml exec backend uv run pytest                  # 后端单元测试
docker compose -f docker-compose.dev.yml exec backend uv run pytest -m e2e --no-cov  # 后端 E2E 测试
docker compose -f docker-compose.dev.yml exec frontend npm run test:unit -- --run    # 前端单元测试
```

---

## 运行 E2E 测试

前端 E2E 测试使用官方 Playwright Docker 镜像运行，避免宿主机浏览器、Node
依赖和 Chrome channel 差异影响结果。开发环境的 compose project network 默认是
`qarunner_default`，容器内前端服务地址是 `http://frontend:5173`。

> 与仓库根目录的 `Dockerfile.playwright` 是两回事：那个文件构建的是
> `qarunner-playwright-executor:latest`——qarunner **运行时**用来在 docker
> executor 中执行用户自己的 `runner=playwright` 测试套件的镜像（见
> `QARUNNER_PLAYWRIGHT_EXECUTOR_IMAGE`），不是本节用来跑 qarunner 自身前端
> E2E 测试的镜像。本节场景直接用下面的官方镜像即可。

```bash
# 1. 确保前后端服务都在运行
docker compose -f docker-compose.dev.yml up -d

# 2. 列出 Playwright project/spec 分配
docker run --rm \
  --network qarunner_default \
  -v "$PWD":/work \
  -w /work/frontend \
  -e BASE_URL=http://frontend:5173 \
  -e E2E_ADMIN_PASSWORD='Demo-Qarunner-2026!' \
  -e PLAYWRIGHT_USE_BUNDLED_CHROMIUM=true \
  mcr.microsoft.com/playwright:v1.61.1-noble \
  bash -lc 'npx playwright test --list'

# 3. 运行完整前端 E2E
docker run --rm \
  --network qarunner_default \
  -v "$PWD":/work \
  -w /work/frontend \
  -e BASE_URL=http://frontend:5173 \
  -e E2E_ADMIN_PASSWORD='Demo-Qarunner-2026!' \
  -e PLAYWRIGHT_USE_BUNDLED_CHROMIUM=true \
  mcr.microsoft.com/playwright:v1.61.1-noble \
  bash -lc 'npx playwright test --reporter=list'

# 4. 运行特定测试文件
docker run --rm \
  --network qarunner_default \
  -v "$PWD":/work \
  -w /work/frontend \
  -e BASE_URL=http://frontend:5173 \
  -e E2E_ADMIN_PASSWORD='Demo-Qarunner-2026!' \
  -e PLAYWRIGHT_USE_BUNDLED_CHROMIUM=true \
  mcr.microsoft.com/playwright:v1.61.1-noble \
  bash -lc 'npx playwright test run-details.authed-admin.spec.ts --reporter=list'
```

后端 E2E 测试在容器内运行（需要 pytest + allure CLI）：

```bash
docker compose -f docker-compose.dev.yml exec backend uv run pytest -m e2e --no-cov
```

---

## 快速决策表

| 我想做什么 | 命令 |
|-----------|------|
| 日常开发 | `docker compose -f docker-compose.dev.yml up -d` |
| 改前端 UI 看效果 | 自动热更新，刷新浏览器 |
| 改后端逻辑 | 自动 reload，等 3 秒 |
| 新增 npm 或 pip 包 | `docker compose -f docker-compose.dev.yml up -d --build` |
| 验证 legacy 单宿主部署 | `docker compose up -d --build` |
| legacy 验证前端代码改了 | `docker compose up -d --build` |
| legacy 验证后端代码改了 | `docker compose restart app` |
| 查看运行的测试 | `docker compose -f docker-compose.dev.yml exec backend uv run pytest` |
