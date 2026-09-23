# qarunner：面向 pytest 与 Playwright 的自托管回归测试运行服务

[English](README.md) | 简体中文

**qarunner 是一个自托管服务：把你已有的 pytest 和 Playwright 回归套件放进一次性
Docker 容器里执行，完整保存每次运行的证据，并把每次运行与可比的历史运行对比，
指出新增失败、已修复用例和不稳定（flaky）用例。** 它面向受信内网里的单个团队，
由 FastAPI 后端、React Web 控制台和 REST API 组成，用 Docker Compose 部署。

> [!CAUTION]
> **当前版本请勿用来执行你不信任的测试代码。**
>
> 控制面进程直接挂载宿主的 `/var/run/docker.sock`。挂载 Docker socket 等价于把宿主
> root 权限交给该进程，测试代码一旦逃出执行容器，就能控制整台宿主机。
>
> - ✅ **适用**：你自己或团队编写、依赖来源可控的回归套件，部署在受信内网。
> - ❌ **不适用**：来源不明的测试代码、外部贡献者提交的 PR 测试、多租户共享环境。
>
> 目标架构（控制面与专用 Worker 主机分离、控制面不持有 Docker socket、每 Run 一次性
> 容器）尚未落地，内部追踪编号 GAP-021 / SOR-GAP-023。

## 为什么需要它

把测试跑一遍只能知道这次过没过。要得到可信的回归信号，还得知道：和上一次**真正可比**
的运行相比，哪些用例变了；某个失败是新出现的，还是已经反复翻转了好几周；以及排查所需
的日志和报告。qarunner 为每次运行保存用例级结果，只在套件、runner、参数都相同的运行
之间做对比；找不到可比基线时会明确告诉你，而不是给出误导性的 diff。

## 功能

- **隔离执行**：pytest 和 Playwright 在一次性 Docker 容器中运行，非 root、无网络、
  丢弃全部 Linux capability、只读根文件系统、限制内存 / CPU / PID，每次运行使用全新的
  工作目录。
- **接入已有测试**：链接本地目录或克隆 Git 仓库（私有仓库用加密保存的 HTTPS token），
  浏览文件树，按 pytest marker 或 Playwright `@tag` 过滤。
- **Profile、运行与调度**：保存运行配置，即时触发、重跑、取消，或用 cron 表达式加
  IANA 时区定时执行。
- **每次运行的证据**：实时日志流（SSE）、基于 JUnit 的汇总、Allure HTML 报告，以及
  Playwright 的 trace、截图和录像。
- **跨次回归视图**：基线 diff 分五类（新增失败、已修复、持续失败、新增用例、消失用例），
  另有通过率趋势、flaky 检测、用例级历史和近 7 天质量度量。
- **可选的 AI 失败诊断**：把失败用例、日志尾部、基线 diff 和 flaky 历史交给
  Anthropic 或 OpenAI，返回六类根因之一，附置信度和证据。只读；未配置 API key 时自动关闭。
- **Web 控制台与 REST API**：React 界面支持中英文、明暗主题和键盘操作；REST API 自带
  Swagger UI、ReDoc 和 OpenAPI schema。
- **账户与权限**：admin / user 两级角色，run、profile、调度按归属隔离，登录限流；
  运行结束可发送飞书卡片通知。

完整、经源码核对的功能清单（包括刻意不做的部分）见 [docs/FEATURES.md](docs/FEATURES.md)。

## 适合谁

已经在维护 pytest 或 Playwright 套件的 QA、测试开发和质量负责人：需要一个运维成本低的
地方，定时运行这些套件、保存证据、看清变化。开发人员在提交、发布或故障复盘前使用这些结果。

它**不是** CI 系统，不是命令行工具，也不支持多租户。产品方向与信任模型见
[docs/DIRECTION.md](docs/DIRECTION.md)。

## 快速开始（开发模式）

需要 Docker 与 Docker Compose。完整说明见 **[docs/deployment.md](docs/deployment.md)**。

```bash
# 1) 准备配置。QARUNNER_SECRET_KEY 与 QARUNNER_ADMIN_PASSWORD 必须设为强值：
#    平台会拒绝 change-me / admin123 等占位口令并拒绝启动。
cp .env.example .env
$EDITOR .env

# 2) 启动开发环境（前后端热更新）
docker compose -f docker-compose.dev.yml up -d

# 3) 首次启动需要先执行数据库迁移，否则后端会按设计拒绝启动，
#    具体命令见 docs/deployment.md 的 "PostgreSQL migration operator" 一节。

# 访问 http://localhost:5173，用户名 admin，密码为你在 .env 中设置的值
```

配置项（均以 `QARUNNER_` 为前缀的环境变量）、单镜像部署、API 与架构说明见英文
[README](README.md#configuration)。

## 常见问题

### qarunner 是什么？

一个自托管的回归测试运行服务。它在隔离的 Docker 容器中执行已有的 pytest 和 Playwright
套件，为每次运行保存日志、报告和用例级结果，并与可比的历史运行对比。

### 支持哪些测试框架？

pytest 和 Playwright，各有专用的执行镜像。qarunner 收集 JUnit 结果、生成 Allure 报告，
并保存 Playwright 的 trace、截图和录像。

### 如何判断回归和不稳定用例？

每次运行与同一套件、同一 runner、同一参数的最近一次已完成运行对比，每个用例归入五类之一：
新增失败、已修复、持续失败、新增用例、消失用例。某个用例最近的结果在通过与失败之间反复
翻转时，被标记为不稳定（默认至少 4 次观测、翻转不少于 3 次）。

### 能替代 CI 吗？

不能。qarunner 负责定时或按需运行回归套件，并跨次对比结果；CI 流水线可以通过 REST API
触发它的运行。

### 可以用来运行不受信任的测试代码吗？

当前版本不可以，见本文开头的警示。测试虽然在加固容器里运行，但控制面持有 Docker socket，
只适合在受信网络中运行你信任的测试代码。

### 必须配置大模型 API key 吗？

不必。AI 失败诊断是可选功能；不设置 `QARUNNER_AI_API_KEY` 时其余功能照常可用，AI 标签页
会说明如何开启。

### 有命令行工具或 Slack、邮件通知吗？

没有。qarunner 提供 Web 控制台和 REST API，运行结束时可以发送飞书卡片；其他通知渠道和
命令行工具不在范围内。

### 使用什么许可证？

MIT。

## 文档与参与

- [docs/deployment.md](docs/deployment.md)：本地部署与更新指南
- [docs/FEATURES.md](docs/FEATURES.md)：功能总览
- [docs/API_REFERENCE.md](docs/API_REFERENCE.md)：API 端点参考
- [ARCHITECTURE.md](ARCHITECTURE.md)：代码架构
- [docs/DIRECTION.md](docs/DIRECTION.md)：产品方向（唯一权威来源）
- 完整文档索引见英文 [README](README.md#documentation)

参与贡献见 [CONTRIBUTING.md](CONTRIBUTING.md)，漏洞报告见 [SECURITY.md](SECURITY.md)，
行为准则见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 许可证

[MIT](LICENSE)
