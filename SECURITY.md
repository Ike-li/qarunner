# 安全策略 / Security Policy

## 报告漏洞 / Reporting a Vulnerability

**请不要通过公开 issue 报告安全漏洞。**
Please do **not** report security vulnerabilities through public issues.

请使用 GitHub 的私密报告通道：进入本仓库的 **Security → Advisories → Report a vulnerability**。
Use GitHub's private reporting: **Security → Advisories → Report a vulnerability**.

报告时请尽量包含：受影响的版本或 commit、复现步骤、你认为的影响范围。
我们会在收到报告后尽快确认，并在修复发布后与你确认致谢方式。

本项目由个人在业余时间维护，没有 SLA 承诺；请以此预期响应速度。
This project is maintained on a best-effort basis; there is no response-time SLA.

## 已知的安全边界 / Known Security Boundaries

在报告之前，请先了解以下**已知且已记录**的限制，它们不构成新漏洞：

### 1. 控制面持有宿主 Docker socket

当前实现中，控制面进程直接挂载 `/var/run/docker.sock`。这等价于宿主 root 权限。
因此：

- 任何能让控制面进程执行任意代码的缺陷，都直接等于宿主沦陷；
- 从执行容器中逃逸，同样可达宿主。

目标架构是控制面与专用 Worker 主机分离、控制面不持有 Docker socket，尚未落地
（内部追踪编号 GAP-021 / SOR-GAP-023）。

**因此：不要用当前版本执行你不信任的测试代码。** 这不是可以通过配置规避的问题。

### 2. 单实例假设

调度器与崩溃恢复逻辑假设单控制面实例运行。多副本部署会导致重复触发与恢复语义歧义。

### 3. 测试代码本身不受信任，但执行边界只做到容器级

每个 Run 在独立的一次性容器中执行，并施加了纵深防御（非 root、无网络、`cap_drop=ALL`、
只读根文件系统、内存/CPU/PID 上限、`no-new-privileges`）。但这是共享内核的容器隔离，
不是虚拟机级隔离。

## 适用范围 / Scope

以下**属于**安全报告范围：

- 认证绕过、越权访问他人资源（owner-scope 失效）
- 凭证泄露到日志 / argv / URL / API 响应
- 路径穿越、命令注入、SSRF、XXE
- 执行容器逃逸到控制面或宿主的**新**路径（已知的 Docker socket 问题除外）
- 拒绝服务中可被低成本远程触发的部分

以下**不属于**范围：

- 上述"已知安全边界"中列出的设计限制
- 需要已具备管理员权限才能触发的问题
- 依赖包的已知 CVE（请直接报告给上游；如需本仓库升级依赖，开普通 issue 即可）
- 自建部署中因未按文档设置 `QARUNNER_SECRET_KEY` / `QARUNNER_ADMIN_PASSWORD` 导致的问题

## 安全相关的配置要求 / Security-Relevant Configuration

部署前必须确认：

| 配置项 | 要求 |
|---|---|
| `QARUNNER_SECRET_KEY` | 强随机值，不得使用占位符（平台会拒绝启动） |
| `QARUNNER_ADMIN_PASSWORD` | 强口令，不得使用 `admin123` / `change-me` 等（平台会拒绝启动） |
| `QARUNNER_COOKIE_SECURE` | 生产环境设为 `true`（需 HTTPS） |
| 网络暴露 | 本项目设计用于受信内网，不应直接暴露到公网 |
