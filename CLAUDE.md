# Antigravity Rules - qarunner 项目级提示词规则

本文档是 **qarunner** 项目级提示词（Rules）的官方标准定义，作为 AI Agent 在本项目中进行开发、重构、测试与维护时的核心宪法与行动指南。

> [!IMPORTANT]
> 任何在本项目中工作的 AI 助手都必须无条件遵守本规则。所有行动必须以测试和质量门禁为导向。

---

## 1. 核心宪法 (Core Constitution)

### 🚨 最高优先级：需求对齐
- **对齐高于执行**：当任务目标、修改边界、验收标准或用户意图存在任何不确定或模糊不清时，**必须立刻停下来询问用户**。
- **严禁模糊执行**：不确定的功能和逻辑绝对不准凭空捏造或模糊处理。

### 🔍 事实第一，禁止猜测
- **零猜测原则**：如果不确定接口、业务规则、文件结构或外部事实，应优先检索项目现有代码和测试。
- **知之为知之**：检索不到时应明确向用户承认“不知道”，禁止捏造、主观臆测、伪造引用或捏造事实。

### 🧩 优先复用，杜绝平行实现
- **阅读既有约定**：软件开发默认先读现有代码、测试和约定。
- **杜绝冗余设计**：必须优先复用已有接口与既定架构，绝对禁止在不了解全局的情况下凭空创造另一套平行实现。

### 🧪 默认遵循 TDD (测试驱动开发)
- **测试表达预期**：必须先用失败的测试表达预期行为，再进行最小必要改动实现。
- **契约与边界**：测试用以保护外部可观察行为、接口契约、回归风险和关键边界。不要编写只为了覆盖率或纯镜像代码细节的测试。
- **用证据说话**：完成后必须提供实际跑测的命令行及其返回输出，以客观事实证明“测试通过”，禁止口头无证据断言。
- **例外情况**：在 TDD 确实不可行（如纯前端样式/微调）时，必须先说明原因，再进行最小但有意义的本地验证。

---

## 2. 🛠️ 项目技术栈与架构 (Tech Stack & Architecture)

本项目 **qarunner** 的 V7.4 目标拓扑是单控制面实例 + 一台专用加固 Worker 主机；当前代码仍是控制面直连 Docker 的单宿主 legacy 实现（GAP-021/SOR-GAP-023）。

### 后端 (Backend)
- **语言/环境**：Python 3.12+ (使用 `uv` 依赖/环境管理器)
- **Web 框架**：FastAPI 0.115 (Uvicorn 驱动，异步 ASGI)
- **数据校验**：Pydantic v2
- **数据库**：SQLite (通过 `aiosqlite` 异步操作 Raw SQL + 自定义 migration 系统)
- **任务调度**：APScheduler + croniter (带时区的 Cron 调度)
- **核心逻辑（目标）**：控制面经受认证 Worker 协议调度，Worker agent 使用本机 Docker API 创建 Source/Dependency/Report 及每 Run 一次性容器；当前 DockerRunner 尚未迁移

### 前端 (Frontend)
- **框架/打包**：React 18.3 + TypeScript 5.2 + Vite 5.3
- **UI 库**：Semi UI (Douyinfe) 2.100 + Lucide React 图标
- **状态 & 国际化**：React Context/Hooks + i18n 多语言 + 主题切换 (Light/Dark)

---

## 3. ⚙️ 工具链注册表与常用命令 (Toolchain & Registry)

> [!CAUTION]
> **🚫 禁止在宿主机上直接运行开发、测试或部署命令。** 所有开发、测试、部署操作**必须在 Docker 容器内**通过 `docker compose` 执行，严禁在宿主机上直接运行 `uv`、`npm`、`pytest` 等命令（格式化/lint 除外，见下文）。
>
> **唯一例外**：代码格式化与静态检查（`ruff format`、`ruff check`）允许在宿主机运行，因为它们不涉及运行时依赖。

开发与测试**统一使用 `docker-compose.dev.yml`**（服务名 `backend` + `frontend`）。根目录 `docker-compose.yml` 的 `app` 服务是**生产部署专用镜像**（`uv sync --no-dev` 构建、不含 npm/前端源码、不含 `tests/` 目录），不能用来跑测试或前端命令。完整说明见 [`docs/deployment.md`](docs/deployment.md)。

进行开发或测试时，请在**容器内**运行以下标准指令：

### 🐍 后端常用指令 (容器内运行，基于 `docker-compose.dev.yml`)
*   **启动开发环境**：`docker compose -f docker-compose.dev.yml up -d`
*   **进入后端容器**：`docker compose -f docker-compose.dev.yml exec backend bash`
*   **代码格式化**（允许宿主机）：`uv run ruff format` (单行宽度限制为 **99** 字符)
*   **静态代码检查**（允许宿主机）：`uv run ruff check --fix`
*   **运行单元/集成测试 (100% 覆盖率门禁)**（容器内）：
    `docker compose -f docker-compose.dev.yml exec backend uv run pytest`
    *(注：默认 pytest 会运行非 e2e 且非 docker 的测试，并强制校验 100% 行/分支覆盖率)*
*   **运行 Docker 隔离测试**（容器内）：
    `docker compose -f docker-compose.dev.yml exec backend uv run pytest -m docker --no-cov`
*   **运行 E2E 冒烟测试**（容器内）：
    `docker compose -f docker-compose.dev.yml exec backend uv run pytest -m e2e --no-cov`
*   **验证生产镜像**（非日常开发用途）：`docker compose up -d --build`（服务名 `app`，见 `docs/deployment.md`）

### ⚛️ 前端常用指令 (容器内运行，基于 `docker-compose.dev.yml`)
*   **安装依赖**：`docker compose -f docker-compose.dev.yml exec frontend npm ci`
*   **前端开发服务器**：随 `docker compose -f docker-compose.dev.yml up -d` 自动启动并热更新（http://localhost:5173），无需单独执行 `npm run dev`
*   **运行单元/组件测试 (Vitest)**（容器内）：`docker compose -f docker-compose.dev.yml exec frontend npm run test:unit -- --run`
*   **运行 Playwright 端到端测试**（容器内）：不经 `exec` 进 frontend 容器，而是用官方 `mcr.microsoft.com/playwright` 镜像启动一次性容器、加入 `qarunner_default` 网络后运行 `npx playwright test`；完整命令与 `test:ui:*` 各细分脚本见 [`docs/deployment.md`](docs/deployment.md#运行-e2e-测试) 与 `frontend/package.json`

---

## 4. 🧭 开发约定与边界 (Conventions & Boundaries)

### 📂 测试组织与文件命名
- **后端测试**：文件命名为 `test_*.py`，全部置于 `tests/` 目录下（包括 `unit/`, `integration/`, `e2e/`）。
- **前端单元测试**：使用 Vitest，文件与源文件同目录共存，命名匹配 `*.test.ts` / `*.test.tsx`。
- **前端端到端测试**：使用 Playwright，集中存放在 `frontend/tests-e2e/`，命名匹配 `*.spec.ts`。
- **Mock 与 Fake**：后端统一在 `tests/fakes/` 下实现测试双，严禁在测试中编写零散的 mock。

### 🎨 前端 DOM 元素选择器策略
- **首要优先**：使用 `data-testid` 属性进行稳定定位（kebab-case，如 `data-testid="invoice-create-button"`）。
- **次要优先**：使用语义化的 ARIA 角色 and 无障碍选择器（如 `getByRole`），提升无障碍（a11y）兼容性。

### 🚧 行为边界划分 (三层安全模型)

*   **✅ 允许独立执行 (Allowed)**:
    - 运行已有测试、linter、formatter。
    - 针对已有业务逻辑编写补全测试或回归测试用例。
    - 局部重构，消除冗余，并在测试保护下修复 bug。
*   **⚠️ 必须事先向用户确认 (Ask First)**:
    - 引入新的外部依赖包（修改 `pyproject.toml` 或 `package.json`）。
    - 破坏已有的 API 契约、改变数据库 Schema 或数据库迁移规则。
    - 涉及删除已有源码文件、测试用例、公共 Fake 代码。
    - 变更测试策略或现有测试基础库。
*   **🚫 严禁执行 (Never)**:
    - 严禁将 API 密钥、密码、敏感 token 等硬编码提交至代码库。
    - 严禁为了通过测试而随意下调 100% 覆盖率门禁指标。
    - 严禁提交未经本地测试运行通过的源码。
    - 严禁创建任何与项目既有接口平行的冗余实现。
