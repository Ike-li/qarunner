# 本地部署与更新指南

## 两种部署模式

qarunner 提供两套 Docker Compose 配置，**日常开发用 dev，验证部署用 prod**。

| 模式 | 文件 | 前端 | 后端 | 热更新 |
|------|------|------|------|--------|
| 开发 | `docker-compose.dev.yml` | Vite dev server (:5173) | Uvicorn --reload (:8000) | ✅ 前后端都支持 |
| 生产 | `docker-compose.yml` | 静态文件由 FastAPI serve | Uvicorn (:8000) | ❌ 需手动操作 |

---

## 开发模式（推荐日常使用）

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
- **默认管理员**: admin / admin123

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

## 生产模式（验证最终部署效果）

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

### 生产容器结构

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
docker compose logs -f app             # 生产日志
docker compose -f docker-compose.dev.yml logs -f backend frontend  # 开发日志

# ── 进入容器 ──
docker compose exec app bash                   # 生产
docker compose -f docker-compose.dev.yml exec backend bash  # 开发后端
docker compose -f docker-compose.dev.yml exec frontend bash # 开发前端

# ── 重启 ──
docker compose restart app                     # 重启生产
docker compose -f docker-compose.dev.yml restart backend  # 重启开发后端

# ── 彻底清理 ──
docker compose down -v                         # 停止并删除 volumes
docker compose -f docker-compose.dev.yml down -v

# ── 重建并启动 ──
docker compose up -d --build                  # 生产
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
| 验证生产部署 | `docker compose up -d --build` |
| 生产前端代码改了 | `docker compose up -d --build` |
| 生产后端代码改了 | `docker compose restart app` |
| 查看运行的测试 | `docker compose -f docker-compose.dev.yml exec backend uv run pytest` |
