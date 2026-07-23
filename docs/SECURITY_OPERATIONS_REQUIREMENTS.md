# qarunner 安全与运维需求

> 文档类型：Security & Operations Requirements
> 文档编号：QARUNNER-SOR-001
> 版本：V1.4.1
> 状态：评审中
> 更新日期：2026-07-12

## 0. 目的与适用边界

本文定义 qarunner 在已确认部署模型下必须满足的安全、隐私、部署、健康、调度、备份恢复和事件处置要求。控制面与 Worker 的传输、身份、状态、错误和恢复细节见
[Worker 协议参考规范](WORKER_PROTOCOL.md)。

面向安全扫描代理的精简 STRIDE 上下文见 [`.bug-hunter/threat-model.md`](../.bug-hunter/threat-model.md)。
安全/运维要求到候选发布证据和签署的映射见 [REQUIREMENTS_TRACEABILITY.md](REQUIREMENTS_TRACEABILITY.md)。

适用边界：

- 受信内网。
- 单团队。
- 单控制面实例 + 单专用加固 Worker 主机；不提供多 Worker、高可用或自动扩缩容。
- 不直接暴露公网。
- 管理员和运维可信。
- 普通用户半可信。
- 测试代码与第三方依赖不可信。
- 依赖准备过程、报告、日志、trace、截图和其他产物同样按不可信内容处理。

超出该边界的部署必须重新进行威胁建模和需求评审，不能默认继承本文结论。

## 1. 资产与信任边界

### 1.1 关键资产

| 资产 | 风险 |
|---|---|
| Docker socket / daemon | 获得控制通常等价于宿主高权限 |
| qarunner SECRET_KEY 与加密密钥 | 可伪造会话或解密存储秘密 |
| 管理员账号 | 可跨用户访问、删除和配置 |
| Git 凭证 | 可读取私有代码 |
| 测试运行密钥 | 可访问真实被测环境和业务数据 |
| 飞书 Webhook | 可向群组发送消息并泄露运行链接 |
| SQLite 数据库 | 包含账号、资源、Run、配置和历史 |
| Suite 与 Artifact | 可能包含源码、日志、截图、录屏、trace 和敏感数据 |
| Network Policy | 决定不可信测试代码可以访问哪些内网目标 |
| Target Access Grant | 决定哪份不可变代码、配置、依赖和密钥可以使用批准目标网络 |
| Dependency Snapshot | 包含可执行第三方代码，决定运行可复现性和供应链暴露面 |
| Worker identity / assignment token | 决定谁可以领取任务、续租、停止容器和上传证据 |
| Worker agent / local Docker daemon | 可创建容器并影响 Worker 主机，必须与控制面和不可信容器分隔 |
| Worker host/kernel | 共享内核逃逸后的高价值边界 |
| 备份 | 聚合数据库、产物、套件和密钥，泄露影响大 |

### 1.2 信任主体

| 主体 | 信任级别 | 允许能力 |
|---|---|---|
| 运维人员 | 可信 | 主机、部署、密钥、备份、网络和恢复 |
| 管理员 | 可信但需审计 | 全局账号、目标环境、网络策略和资源管理 |
| 普通用户 | 半可信 | 自有资源、批准目标和批准执行口径 |
| 测试代码/依赖 | 不可信 | 仅在 executor 中执行，不能接触控制面 |
| Worker agent | 受信执行控制组件 | 仅处理固定 task API、受限镜像/网络/挂载/资源策略；不得持有控制面数据库凭证 |
| Git/AI/飞书/SUT | 外部依赖 | 仅通过批准接口和网络策略访问 |
| 未认证请求 | 不可信 | 仅允许最小健康和认证入口 |

### 1.3 信任边界

1. 浏览器/API 客户端与 qarunner 控制面。
2. qarunner 控制面与 SQLite/文件系统。
3. 控制面与 Worker agent RPC/队列（双向身份、防重放、任务 digest）。
4. Worker agent 与本机 Docker daemon。
5. Docker daemon 与不可信一次性容器。
6. Worker/容器与 Git、Registry、AI、飞书和真实被测环境。
7. 运行数据、证据上传通道与备份介质。

## 2. 主要威胁场景

| 威胁 ID | 场景 | 主要影响 |
|---|---|---|
| THR-001 | 普通用户通过 Git URL 诱导 Worker Source Acquisition 访问内网或元数据服务 | SSRF、凭证或内部信息泄露 |
| THR-002 | 测试代码访问 Worker agent、Docker socket、控制面或宿主挂载 | Worker/宿主接管 |
| THR-003 | approved-target 范围过宽或可由普通用户修改 | 内网横向探测 |
| THR-004 | Profile env、Webhook 或日志保存明文密钥 | 密钥泄露 |
| THR-005 | SECRET_KEY 轮换或丢失导致凭证无法恢复 | 认证中断、存储秘密不可用 |
| THR-006 | 越权访问他人 Run、报告、产物、diff 或历史 | 数据泄露 |
| THR-007 | 恶意报告、参数、路径或超大输出消耗资源 | RCE、路径穿越、DoS |
| THR-008 | 生产配置未实际进入容器 | Secure Cookie、通知、镜像策略失效 |
| THR-009 | 服务停机导致 Schedule 漏跑但无记录 | 回归保护失效 |
| THR-010 | 数据库、WAL、文件和密钥备份不一致 | 无法恢复或恢复后数据不可解释 |
| THR-011 | 使用 latest 或运行时构建 executor | 供应链漂移、不可复现 |
| THR-012 | 磁盘被日志、产物或 Suite 填满 | 数据损坏、服务不可用 |
| THR-013 | 普通用户把新 Suite revision、Profile、依赖或密钥替换进已批准网络，或 queued Run 执行了 pull 后的可变目录 | 对真实 SUT 的未授权操作、数据破坏或秘密外传 |
| THR-014 | 包管理器或依赖准备在 Worker agent 或持有 Docker socket/控制面秘密的进程运行 | Worker/控制面 RCE、宿主接管 |
| THR-015 | 测试生成的 HTML/SVG/报告脚本在控制面同源执行 | 存储型 XSS、会话劫持、越权 API 调用 |
| THR-016 | 恶意文件名、符号链接、特殊文件或超大归档进入产物链路 | 路径逃逸、资源耗尽、下载端攻击 |
| THR-017 | 不可信 Git 仓库由 Worker 上未隔离的高权限进程 clone/fetch/checkout | Git 客户端/过滤器漏洞、Worker RCE、宿主接管 |
| THR-018 | 并发 Run 共享 SUT 数据、账号或清理状态 | 相互污染、破坏性冲突、假失败/假修复 |
| THR-019 | 结果或产物在归档后被覆盖、丢失或恢复损坏 | 错误发布判断、证据不可审计 |
| THR-020 | 系统时钟漂移或跳变 | Grant/lease 过期错误、重复调度、审计乱序 |
| THR-021 | 不可信 executor 利用共享内核或容器运行时逃逸 | Worker 主机/daemon 失陷；若控制面边界失效则扩大为平台失陷 |
| THR-022 | 攻击者伪造/窃取 Worker identity 或证据上传凭证 | 领取未授权任务、停止容器、伪造状态或上传证据 |
| THR-023 | 任务 payload、claim、heartbeat 或 fencing token 被重放/篡改 | 重复执行、旧代码/策略执行、状态回退或目标污染 |
| THR-024 | Worker API 接受任意镜像、挂载、网络或 Docker flags | Worker agent 被滥用为通用 Docker/宿主控制接口 |
| THR-025 | 一次性容器、临时目录、密钥或网络状态跨 Run 残留 | 跨用户/跨 Run 数据泄露和目标污染 |
| THR-026 | 单 Worker 失联、资源洪泛或残留容器堆积 | 所有 Run 停摆、队列无限积压或磁盘耗尽 |
| THR-027 | 控制面与 Worker 上传/重连顺序处理错误 | 证据覆盖、Run 状态回退或不确定 attempt 被重复执行 |

## 3. 安全需求

### 3.1 身份认证与会话

| ID | 要求 | 优先级 | 验证 |
|---|---|---|---|
| SEC-001 | SECRET_KEY 和初始管理员密码缺失、属于禁用值或不满足 Security Parameter Baseline 的最小熵/强度时拒绝启动 | P0 | 配置测试 |
| SEC-002 | 生产模式必须使用 HTTPS 和 Secure、HttpOnly、SameSite Cookie | P0 | 浏览器与配置测试 |
| SEC-003 | 登录失败按用户名 + 可信客户端 IP 执行 Security Parameter Baseline 定义的窗口、阈值和锁定策略 | P0 | 限流和代理测试 |
| SEC-004 | 登出、改密、角色变化和账号禁用在成功响应前递增 session/token version，之后所有旧令牌请求均拒绝 | P0 | 会话失效测试 |
| SEC-005 | 不能删除当前账号或最后一名管理员 | P0 | 用户管理测试 |

生产模式不得仅记录 Cookie 不安全警告后继续就绪；安全关键配置不满足时必须 fail closed。

### 3.2 授权

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-006 | owner scope 覆盖 Suite、Credential、Profile、Run、Schedule、Artifact、Report、Diff、Trend、Case History 和 AI | P0 |
| SEC-007 | 列表只返回可见资源；单对象访问不得泄露对象内容 | P0 |
| SEC-008 | 普通用户不能创建、修改或启用 Target Environment 与 Network Policy | P0 |
| SEC-009 | 管理员跨用户访问和破坏性操作必须审计 | P0 |

UI 隐藏不是授权控制；所有控制必须在 API 和核心服务层执行。

### 3.3 Executor 隔离

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-010 | 所有测试只在专用 Worker 的每 Run 全新 Docker executor 中运行 | P0 |
| SEC-011 | executor 非 root、非 privileged、drop capabilities、no-new-privileges、只读根文件系统 | P0 |
| SEC-012 | executor 限制 CPU、内存、PID、运行时间和输出 | P0 |
| SEC-013 | executor 只挂载本 Run workspace、results 和批准只读目录 | P0 |
| SEC-014 | executor 不能访问 Worker agent、Docker socket、数据库、控制面秘密、其他 Suite 或其他 Run | P0 |
| SEC-015 | workspace/jail 或 Worker 隔离创建失败时任务失败，不能回退到控制面、原 Suite 目录或 subprocess 直接执行 | P0 |

### 3.4 Git 接入

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-016 | Git 只允许批准的协议和目标主机 | P0 |
| SEC-017 | URL 校验必须覆盖解析后 IP、DNS 重绑定、重定向和 IPv4/IPv6 私网/元数据范围 | P0 |
| SEC-018 | Git 命令使用参数化 argv，不执行 shell 拼接 | P0 |
| SEC-019 | Git 凭证通过秘密引用和受控环境注入，不进入 URL、argv、日志或响应 | P0 |
| SEC-020 | clone/fetch 有超时、下载量、磁盘和并发限制 | P0 |
| SEC-021 | Git clone/fetch 不触发仓库脚本；依赖准备必须转入 SEC-045～051 定义的沙箱 | P0 |
| SEC-022 | SSH Git 必须有独立的 host allowlist、known_hosts 和环境白名单 | P0 |

仅限制 <code>https://</code> 或 <code>git@</code> 前缀不构成完整 SSRF 防护。

### 3.5 Playwright 网络

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-023 | 默认使用 isolated | P0 |
| SEC-024 | approved-target 在 V7 只供 Playwright 使用，且只允许管理员批准的显式协议/目标身份或 IP/端口范围 | P0 |
| SEC-025 | 普通用户不能通过 env、URL、DNS 或参数扩大访问范围 | P0 |
| SEC-026 | 明确拒绝控制面、Docker、宿主、元数据和未批准私网目标 | P0 |
| SEC-027 | 每次 Run 保存网络策略 ID/version 和目标环境 | P0 |
| SEC-028 | 策略变更、访问拒绝和绕过尝试产生审计事件 | P0 |

## 4. 密钥与敏感数据

### 4.1 密钥类型

- Git Credential。
- 测试环境账号、API Token、数据库连接和证书。
- Notification Webhook。
- AI API Key。
- JWT Signing Key。
- Stored Secret Encryption Key。

### 4.2 要求

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-029 | Profile 和 Run 不保存或回显秘密明文 | P0 |
| SEC-030 | 用户通过 secret_ref 选择秘密；API 只返回引用元数据 | P0 |
| SEC-031 | secret_ref 必须 owner scoped，管理员可以受审计地管理 | P0 |
| SEC-032 | Webhook 视为秘密，密文保存且默认不回显完整 URL | P0 |
| SEC-033 | JWT signing key 与 stored-secret encryption key 分离 | P0 |
| SEC-034 | 密钥具有版本、轮换、吊销和恢复流程；引用必须解析精确版本，不能把旧引用静默替换为当前值 | P0 |
| SEC-035 | 备份必须包含恢复密文所需的加密密钥或受控恢复机制 | P0 |
| SEC-036 | 日志、错误、通知、报告索引和 AI 输入对已知秘密进行脱敏 | P0 |
| SEC-037 | 测试代码主动输出未知秘密的残余风险必须通过最小权限、保留和事件响应控制 | P1 |

执行环境可以获得该 Run 批准的秘密值，但 Run provenance 只记录 secret ID/version。

## 5. 目标访问、依赖准备与不可信内容

### 5.1 Target Access Grant

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-038 | approved-target Run 必须同时引用 active Target Access Grant；Network Policy 本身不构成授权 | P0 |
| SEC-039 | Grant 固定 Suite Revision/content digest、Profile revision 或手工请求口径、Dependency Snapshot、Target Environment、Network Policy version 和 secret ref version | P0 |
| SEC-040 | 只有管理员可以签发、续期和吊销 Grant；普通用户不能替换任何绑定项 | P0 |
| SEC-041 | Run 创建与 executor 启动前分别校验 Grant，并满足 `expires_at - now >= run_timeout + revocation_stop_timeout + target_cleanup_timeout`；到期、吊销、失配、参数缺失和竞态均 fail closed，手工吊销使 queued Run 立即失败并在 revocation_stop_timeout 内终止 running Run | P0 |
| SEC-042 | Grant 具有原因、有效期、允许发起人和完整审计记录 | P0 |
| SEC-043 | Schedule 因 Grant/策略无效而未运行时记录 blocked-policy，不能静默改用其他授权 | P0 |
| SEC-044 | 在生产 Target Environment 决策未批准前，系统必须拒绝该类授权 | P0 |

Grant 不能扩大 Network Policy，也不能把控制面、宿主、Docker daemon 或元数据服务变成批准目标。
Grant 批准不改变 Suite、依赖和产物的不可信分类。Run 必须执行与 Grant content digest 一致的只读 Suite Revision，不能读取 pull 后的可变工作目录。

### 5.2 依赖准备

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-045 | 依赖准备在独立低权限容器中运行，不得接触 Docker socket、数据库、控制面秘密或其他 Suite | P0 |
| SEC-046 | 准备容器只访问版本化 approved-registry 策略允许的软件源，Registry 凭证只读、最小授权且仅临时注入，不得同时访问 SUT | P0 |
| SEC-047 | 准备阶段限制 CPU、内存、PID、时间、下载量、输出和磁盘，并在失败后完整清理 | P0 |
| SEC-048 | 默认禁用生命周期脚本；确需启用时记录策略且脚本仍只能在准备沙箱内运行 | P0 |
| SEC-049 | 输出形成不可变 Dependency Snapshot，绑定 Suite revision、锁定/解析后依赖清单、准备镜像和 Registry policy digest，并拒绝特殊文件或越界链接 | P0 |
| SEC-050 | executor 只读挂载批准 Snapshot，不在运行时向软件源联网安装 | P0 |
| SEC-051 | 支持矩阵内的依赖生态必须生成批准格式的可查询依赖清单/SBOM 和来源信息；无法生成时记录 `sbom_status=unsupported` 与原因，且不得宣称满足本要求 | P1 |

### 5.3 报告、日志与产物

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-052 | HTML、SVG、JavaScript 报告和浏览器可执行附件不得在携带控制面 Cookie/Token 的 origin 中执行 | P0 |
| SEC-053 | 主动报告使用不暴露公网的独立无控制面会话 origin + 限制性 CSP，并以短期、受众绑定的授权维持 owner scope；或使用无主动内容/仅下载模式 | P0 |
| SEC-054 | 未知、可执行或可嗅探类型默认 attachment，并设置 nosniff；不能信任测试提供的 MIME | P0 |
| SEC-055 | 文件枚举、下载和压缩拒绝路径穿越、符号链接逃逸、特殊文件、数量/大小越界和归档膨胀 | P0 |
| SEC-056 | 日志、用例名、错误、附件名和 ANSI 控制序列按不可信文本转义与限制 | P0 |
| SEC-057 | Allure 等报告生成器在受限容器运行，不接触控制面秘密；失败不覆盖原始证据 | P0 |

### 5.4 SUT 数据与隐私

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-058 | Target Environment 记录数据分类、允许的测试账号/secret refs、变更风险和保留要求 | P0 |
| SEC-059 | 测试账号遵循最小权限，不得复用管理员、生产个人或平台控制面账号 | P0 |
| SEC-060 | 截图、录屏、trace、日志和报告继承 SUT 数据分类，并应用访问、保留、导出和备份控制 | P0 |
| SEC-061 | 生产数据或个人数据的测试使用必须经单独批准；未批准时不得注入或采集 | P0 |

允许目标上的合法请求仍可能携带 Run secret 或 SUT 数据，网络白名单无法消除此残余风险，因此 Grant 审批必须同时评估目标、账号权限和测试影响。

### 5.5 Docker 宿主与运行时

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-062 | 生产 Worker 主机专用于 qarunner 执行且与控制面主机分离，不承载无关工作负载、个人数据、控制面数据库或其他服务秘密 | P0 |
| SEC-063 | executor 强制批准的 seccomp 与 AppArmor/SELinux，禁止 unconfined、host namespace、host device、Docker socket 和特权模式 | P0 |
| SEC-064 | OS kernel、Docker/containerd、浏览器和 executor image 具有漏洞监控、修补时限和紧急停跑流程 | P0 |
| SEC-065 | 控制面不得安装、挂载或远程访问 Docker socket/daemon；Worker agent 只能调用 `docker-api-allowlist@version` 中固定 method/resource/scope，其他调用拒绝并审计 | P0 |
| SEC-066 | Source Acquisition、依赖准备、报告生成和 executor 每 task/Run 新建容器，终态删除且不复用可写层、临时目录或网络状态；残留触发 Worker quarantine | P0 |
| SEC-067 | V7 不宣称抵御 Worker 内核/运行时逃逸；安全与运维接受专用 Worker 共享内核残余风险，公网、多租户、生产 SUT 或高对抗场景改用每 Run VM/microVM | P0 |

### 5.6 Source Acquisition

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-068 | clone/fetch/checkout/ref 解析与 workspace 物化在独立低权限容器运行，不接触 Docker socket、数据库或平台密钥 | P0 |
| SEC-069 | 仅访问 approved-source 的协议/host/端口；Git 凭证只读、最小授权、临时注入且不进入 URL/argv/log | P0 |
| SEC-070 | 使用清洁 Git config/env，默认禁用 hook、submodule、LFS/smudge/filter 和递归网络；例外需管理员策略 | P0 |
| SEC-071 | 限制时间、下载量、文件数、单文件/总大小、磁盘、CPU、内存和并发 | P0 |
| SEC-072 | 输出拒绝越界 symlink、device/FIFO/socket、异常权限与路径碰撞，并生成不可变 Suite Revision digest | P0 |
| SEC-073 | 获取失败不得留下可执行半成品，也不得回退到高权限控制面处理仓库 | P0 |

### 5.7 Environment Lease 与证据完整性

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-074 | Target Environment 定义 readiness、并发、isolation/namespace、测试数据与 cleanup policy | P0 |
| SEC-075 | Lease 原子获取并使用单调 fencing token；token 必须由网络/namespace/目标访问边界执行，stale/重复 token 不能继续访问目标 | P0 |
| SEC-076 | 取消、超时、崩溃、Grant 吊销和 cleanup 失败均按策略回收/隔离 lease 并审计 | P0 |
| SEC-077 | mandatory cleanup 未成功时 Run 不得成为 completed 或可信基线 | P0 |
| SEC-078 | 终态 Evidence Manifest 记录证据 path/size/digest/classification/truncation，并冻结只读 | P0 |
| SEC-079 | digest 不匹配、缺失或恢复损坏标记 evidence_corrupt，拒绝展示为完整证据或进入 strict comparison | P0 |
| SEC-080 | 平台只证明收集后的证据未被无声修改，不为测试逻辑、断言或测试输出真实性背书 | P0 |

### 5.8 请求、时间与安全参数完整性

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-081 | 所有 Run 创建入口以 actor + endpoint + Idempotency-Key 持久去重，同 key 异体请求拒绝 | P0 |
| SEC-082 | auth/Grant/lease/schedule/audit 使用 UTC，timeout/duration 使用 monotonic clock | P0 |
| SEC-083 | clock skew 超批准阈值时拒绝 readiness；时钟跳变产生告警且不能延长已到期授权或重复触发 | P0 |
| SEC-084 | 生产使用批准且版本化的 Security Parameter Baseline，覆盖认证/会话、网络、报告授权 TTL/CSP、Docker API allowlist 和相关安全阈值；必填项缺失或版本不匹配时拒绝 readiness | P0 |
| SEC-085 | 使用版本化漏洞与补丁 SLA，至少区分已利用/逃逸类、Critical 和 High；逾期时受影响 executor 停止接收新 Run 且 production readiness=false，除非存在未过期的批准例外 | P0 |
| SEC-086 | approved-target 出站控制覆盖 executor 内全部进程和协议，包括 Node、浏览器、worker/service worker、request context、DNS、HTTP(S)、WebSocket、重定向和下载；不能只依赖页面 URL 过滤 | P0 |
| SEC-087 | Playwright 浏览器 sandbox 在批准执行镜像中保持启用；`--no-sandbox`、`--disable-setuid-sandbox` 或等价降级仅能通过有到期日的安全例外、专用主机和补偿控制批准 | P0 |

### 5.9 Worker 身份、任务与证据通道

| ID | 要求 | 优先级 |
|---|---|---|
| SEC-088 | Worker 使用独立于用户/管理员会话的 mTLS 双向认证，身份绑定 worker_id、host_id、worker_generation、audience、task scope、证书/令牌版本和有效期；Worker 主动长轮询且不监听入站端口 | P0 |
| SEC-089 | Worker 身份支持单次 enrollment、轮换、吊销、drain 和 quarantine；同主机轮换递增 identity version，主机重建递增 worker_generation，旧身份和旧 generation 在激活/吊销后立即拒绝并产生审计 | P0 |
| SEC-090 | 每个 Worker task 绑定 canonical job digest、允许的 task type、镜像/挂载/网络/资源 allowlist 和 assignment fencing token；Worker 不接受任意 Docker 参数 | P0 |
| SEC-091 | claim 与 commit-start 幂等；renew/release、状态事件和证据上传使用严格连续 sequence、短期作用域凭证和 digest；乱序、重放、同序异体、旧 generation/token 或损坏内容拒绝覆盖 | P0 |
| SEC-092 | Worker agent 不持有控制面数据库凭证、JWT/管理员会话密钥或无关业务秘密；Run secret 仅按 task 临时获取并在终态清除 | P0 |
| SEC-093 | assignment 续租使用本地 monotonic deadline；Worker 使用 host watchdog/等价守护和容器硬超时，approved-target 使用独立网络 fencing，使 agent 崩溃、控制面失联、续租失败或 token 到期时仍能停止容器和目标访问 | P0 |
| SEC-094 | Worker agent/Docker API 仅本机管理面可达；测试容器、业务网络和控制面网络均不能直接连接该接口 | P0 |
| SEC-095 | Worker 使用持久 assignment journal、统一资源 label 和启动 reconcile，证明连续 task/Run 间无容器、挂载、可写层、临时目录、密钥、上传 staging 和网络状态复用；无法解释或清理时立即 quarantine | P0 |

## 6. AI 与外部通知

### 6.1 AI

- 默认关闭。
- 启用前完成数据分类、供应商、地域和保留评审。
- 只发送批准的、截断和脱敏后的证据。
- 把日志、报告、用例名和附件视为潜在 prompt injection 数据，不执行其中指令，也不扩大当前用户的数据范围。
- 不发送 Profile secret、Cookie、Git Credential 或完整环境变量。
- AI 不持有代码修改、部署、重跑、网络访问或秘密读取工具。
- 超时、限流、供应商错误和解析失败不得影响核心 Run。

### 6.2 Notification

- Webhook 作为秘密引用。
- 只发送最小结果摘要和受权限保护的链接。
- 不发送日志、环境变量、密钥、完整错误堆栈或未授权对象名称。
- 发送失败只产生可观察事件，不改变 Run。

## 7. 最小安全审计

qarunner 不是完整审计平台，但以下事件必须记录：

| 类别 | 事件 |
|---|---|
| 认证 | 登录成功/失败、锁定、登出、令牌失效 |
| 用户 | 创建、删除、改密、角色变化 |
| 密钥 | 创建、轮换、吊销、删除、使用失败；不记录值 |
| Suite | source acquisition、clone、pull、Revision/digest、删除、本地目录接入 |
| Profile | 创建、影响执行的 revision、删除 |
| Network | 策略创建、修改、批准、停用、拒绝和绕过尝试 |
| Grant | 创建、激活、校验失败、续期、到期、吊销和使用 |
| Dependency | 准备、Registry 拒绝、Snapshot 创建/失效和脚本策略变化 |
| Environment | readiness、lease 获取/续租/释放、fencing 拒绝、cleanup 和恢复 |
| Worker | 注册、身份轮换/吊销、capabilities、heartbeat、claim/renew/release、drain、quarantine、容器清理和证据上传 |
| Run | 幂等创建/冲突、取消、删除、锁定、清理、异常恢复 |
| Evidence | Manifest finalize/校验失败、corrupt、主动报告打开/导出、批量下载和拒绝事件 |
| Schedule | 创建、修改、删除、触发、missed、补跑 |
| 运维 | 启动、迁移、配置校验、时钟偏差/跳变、备份、恢复、健康降级 |

审计记录至少包含：

- event_id。
- timestamp。
- actor。
- actor_role。
- action。
- resource_type / resource_id。
- result。
- request/correlation ID。
- 可信代理归一化后的 source IP、受信代理链、认证方式、session/token ID hash、user-agent，以及适用的 host/executor ID。

审计内容不得包含密码、Token、Webhook 或测试 secret。

## 8. 生产配置

### 8.1 配置契约

| ID | 要求 | 优先级 |
|---|---|---|
| OPS-001 | 文档化的生产环境变量必须实际进入容器 | P0 |
| OPS-002 | 启动时输出脱敏后的有效配置摘要和配置来源 | P0 |
| OPS-003 | production mode 缺失 Secure Cookie、Worker identity/trust、固定控制面/Worker/阶段镜像、备份路径等关键项时拒绝就绪 | P0 |
| OPS-004 | 开发与生产默认值分离 | P0 |
| OPS-005 | 配置项变更有兼容、默认值和迁移说明 | P1 |

标准生产部署必须把控制面和 Worker 部署为不同主机上的独立单元。两侧配置必须显式传递或通过受控 env_file/secret 载入：

- Cookie Secure。
- Public URL。
- Control plane Worker API/queue URL、独立 audience、CA/trust bundle 和允许的 worker_id/host_id。
- Worker enrollment、短期身份轮换、heartbeat/claim TTL、drain/quarantine 和 worker_recovery_timeout。
- Worker agent image/digest、Docker API allowlist、允许 task type/image/mount/network/resource profiles。
- Executor autobuild。
- Executor image/digest。
- 并发、超时和保留策略。
- AI/通知开关与秘密引用。
- Trusted proxies。
- Network Policy backend。
- Target Access Grant 有效期、吊销和校验配置。
- Source Acquisition image、approved-source policy 和 Snapshot 存储。
- Dependency preparation image、Registry policy 和 Snapshot 存储。
- Report origin/CSP 或 download-only 模式。
- Target Environment concurrency/namespace/cleanup、lease TTL/heartbeat/recovery。
- Idempotency retention、Evidence Manifest 算法/存储和校验策略。
- NTP/clock skew 阈值、seccomp/LSM profile 和主机补丁策略。
- ResourceLimitProfile、Security Parameter Baseline 和 Operational Threshold Catalog 的 ID/version。
- release gate catalog 和 Release Evidence Bundle 存储/保留配置。
- 审计和备份配置。

### 8.2 镜像与供应链

| ID | 要求 | 优先级 |
|---|---|---|
| OPS-006 | 生产控制面、Worker agent 和各阶段/executor 镜像使用不可变版本或 digest，不使用 latest 作为发布证据 | P0 |
| OPS-007 | 生产控制面和 Worker 均禁止运行时自动构建 executor/阶段镜像 | P0 |
| OPS-008 | 构建产物记录源码 commit、锁文件、镜像 digest 和扫描结果 | P0 |
| OPS-009 | 缺少批准镜像时 Run 失败并告警，不静默构建 | P0 |
| OPS-031 | 启动/升级前分别验证控制面主机无 Docker socket/执行路径，以及 Worker 专用化、agent/socket、patch level、seccomp/LSM、namespace/device 基线 | P0 |

## 9. 健康、监控与告警

### 9.1 分层健康

| 检查 | 内容 |
|---|---|
| liveness | 进程事件循环可响应 |
| readiness-control | 数据库、迁移状态、配置和写入路径可用 |
| worker-link | 唯一 Worker 身份/generation 有效，mTLS 连接、heartbeat、claim/commit-start/renew 和 clock 在阈值内 |
| worker-agent | agent 版本/capabilities、drain/quarantine、journal/reconcile、assignment 和上传通道可用；degraded health component 可见 |
| worker-executor | Worker Docker daemon、批准阶段/executor 镜像、容量和最小一次性容器启动/销毁检查可用 |
| host-security | 控制面无 Docker 执行能力；Worker 专用化、patch level、agent/socket、seccomp/LSM、namespace/device 配置符合基线 |
| scheduler-health | 调度器心跳、最近轮询、future jobs 和 missed 积压 |
| storage-health | 数据库、artifact、Suite 路径可写；剩余磁盘高于阈值 |
| dependency-health | 可选，只影响对应 Git/AI/通知功能 |
| preparation-health | 依赖准备镜像、批准 Registry 策略和 Snapshot 存储可用 |
| source-health | Source Acquisition 镜像、approved-source 策略和 Revision 存储可用 |
| target-health | Target readiness、active/stale lease、cleanup backlog 和 fencing 状态 |
| evidence-health | 报告隔离 origin/下载模式和产物存储配置有效 |
| clock-health | NTP/时间源、当前偏差和最近 clock jump 在阈值内 |

数据库可访问不等价于平台可以执行测试。

### 9.2 告警

至少对以下情况告警：

- Worker identity/heartbeat/claim 失效、Worker offline/draining/quarantined。
- Worker Docker daemon、agent 或批准阶段/executor image 不可用。
- queued 积压超过阈值。
- running 超过最大合理时间。
- Schedule missed 或触发连续失败。
- 数据库迁移/写入失败。
- 磁盘剩余空间低或增长异常。
- 备份或恢复演练失败。
- 网络策略拒绝或绕过尝试。
- Grant 即将到期、吊销后仍被请求或连续 blocked-policy。
- stale/泄漏 lease、readiness 连续失败、cleanup backlog 或 fencing 拒绝异常。
- 依赖准备越界、Snapshot 校验失败或主动内容隔离配置失效。
- Source Acquisition 越界、Evidence Manifest 校验失败或 clock skew/jump 超阈值。
- 控制面出现 Docker socket/执行能力，或 Worker 补丁逾期、seccomp/LSM 失效、残留容器、跨 Run 状态或异常 Docker API 调用。
- Worker 任务/状态重放、旧 fencing token、上传 digest 不匹配或 assignment 顺序冲突。
- 未经 commit-start 创建容器、未知 attempt 自动产生第二 execution attempt，或旧 worker_generation 仍可请求。
- 审计写入失败。

每条健康与告警规则必须在版本化 Operational Threshold Catalog 中记录 signal、采样周期、timeout、stale-after、warn/critical 阈值、观察窗口、连续次数、severity、owner、route、dedupe、clear condition 和最大静默期。必填规则或责任人缺失时不得通过 GA 门禁。

## 10. 调度与生命周期

### 10.1 调度

| ID | 要求 | 优先级 |
|---|---|---|
| OPS-010 | 每个 cron 触发点持久化 triggered/missed/skipped-disabled 结果 | P0 |
| OPS-011 | missed 策略明确为 record-only、catch-up-once 或 manual | P0 |
| OPS-012 | 调度器心跳和最近成功触发可监控 | P0 |
| OPS-013 | 服务恢复后重建 future jobs，并处理未关闭的 missed | P0 |
| OPS-030 | Grant/策略无效的触发点持久化 blocked-policy 及失配原因 | P0 |
| OPS-032 | clock skew 超阈值时 scheduler、Grant 和 lease 服务拒绝 readiness，恢复后执行去重核对 | P0 |
| OPS-033 | 周期回收 stale Environment Lease，使用 fencing 防止旧 worker 恢复后重新占用 | P0 |

### 10.2 停机

| ID | 要求 | 优先级 |
|---|---|---|
| OPS-014 | `stop_grace_period_s >= drain_timeout_s + max(lease_cleanup_timeout_s, manifest_finalize_timeout_s) + shutdown_margin_s`，且全部参数来自批准的 Operational Threshold Catalog | P0 |
| OPS-015 | 控制面停机先停止新 cron/Run，再停止新 Worker claim并发出 drain，最后等待/取消在途 task 和证据上传 | P0 |
| OPS-016 | 控制面或 Worker 超过停机缓冲的 task/Run 必须形成明确终态；不得因任一侧重启而自动启动不确定的第二 attempt | P0 |
| OPS-017 | 停机过程中通知和审计写入有独立收敛策略 | P1 |

### 10.3 Worker 生命周期

| ID | 要求 | 优先级 |
|---|---|---|
| OPS-037 | Worker 使用一次性 enrollment 建立可轮换短期身份，不使用长期共享管理员/API token | P0 |
| OPS-038 | Worker drain 先停止新 claim，再等待/终止在途 task、上传已有证据、清理容器/挂载并释放 assignment/Environment Lease | P0 |
| OPS-039 | Worker offline/quarantined 时任务保持 queued，并显示稳定 `wait_reason=worker-unavailable/worker-quarantined`；控制面不得本地执行，running task 超恢复窗口后按 `worker_lost/attempt_unknown` 收敛 | P0 |
| OPS-040 | Worker 重建/替换必须使用不可变 agent 镜像和批准主机基线，递增 worker_generation、轮换身份并 fence 旧主机全部 claim/renew/event/upload 后才能 ready | P0 |
| OPS-041 | Worker 基于持久 journal 和统一资源 label 在启动及周期任务中核对本机容器、挂载、volume/network、临时目录、assignment 和上传 staging；发现无主/残留/不一致资源立即 quarantine 并告警 | P0 |

## 11. 数据、备份与恢复

### 11.1 备份范围

备份必须覆盖：

- SQLite 主数据库及 WAL/SHM 或一致性快照。
- Artifact。
- Suite 数据、Source Acquisition 元数据和不可变 Revision。
- 生产配置。
- Network Policy 和 Target Environment。
- Target Access Grant、Dependency Snapshot、Environment Lease/history 和 Idempotency records。
- Evidence Manifest 与其引用的保留期内证据。
- 审计记录。
- Worker identity/certificate version、capabilities、assignment history、host baseline digest、drain/quarantine 和重建记录。
- Stored-secret encryption key 的受控恢复材料。
- 控制面、Worker agent、阶段容器和 executor 镜像 digest/版本。

只复制数据库主文件或只复制 artifacts 不构成完整备份。

### 11.2 要求

| ID | 要求 | 优先级 |
|---|---|---|
| OPS-018 | 使用 SQLite backup API、停机快照或经验证的一致性方案 | P0 |
| OPS-019 | 备份加密、访问受限并有完整性校验 | P0 |
| OPS-020 | 恢复演练验证账号、密钥、历史 Run、报告、Suite 和调度 | P0 |
| OPS-021 | 升级前自动或强制确认可恢复备份 | P0 |
| OPS-022 | 数据库迁移失败时拒绝启动，不修改原备份 | P0 |
| OPS-023 | 当前不支持向下迁移，回退使用旧镜像 + 升级前备份 | P0 |
| OPS-024 | RTO、RPO、频率、保留期和异地策略在 GA 前批准 | P0 |
| OPS-034 | 备份与恢复校验 Suite/Dependency Snapshot 和 Evidence Manifest digest，损坏不得标记恢复成功 | P0 |

## 12. 容量与保留

| ID | 要求 | 优先级 |
|---|---|---|
| OPS-025 | 设置全局并发、单用户在途、运行超时和输出边界 | P0 |
| OPS-026 | Artifact、Suite、数据库和审计分别监控容量 | P0 |
| OPS-027 | 清理策略有 dry-run、锁定保护、审计和失败恢复 | P0 |
| OPS-028 | 达到关键磁盘阈值时停止接收新 Run，而不是继续写满磁盘 | P0 |
| OPS-029 | 保留期按数据类别定义；锁定 Run 例外明确 | P1 |

## 13. 事件响应

至少准备以下 Runbook：

1. 控制面主机、数据库或平台密钥疑似失陷。
2. Worker agent、Worker identity、Docker socket/daemon 或 Worker 主机疑似失陷。
3. Git/Test/AI 密钥或 Worker 短期凭证泄露。
4. approved-target 网络策略越界。
5. 数据库损坏或迁移失败。
6. Artifact/Suite/Worker staging 磁盘写满。
7. Schedule 大面积 missed。
8. 错误回归分类影响发布判断。
9. 备份不可恢复。
10. 恶意依赖或准备容器疑似突破隔离。
11. 不可信报告导致存储型 XSS 或会话访问。
12. Source Acquisition 容器、Git 凭证或仓库处理链疑似失陷。
13. Environment Lease 泄漏、fencing 失效或 cleanup 大面积失败。
14. Evidence Manifest/Worker 上传 digest 校验失败或备份恢复发现证据损坏。
15. Worker 伪造、任务重放、assignment 冲突、失联或不确定 attempt。
16. clock skew/jump 导致授权、任务续租、调度或审计异常。

每个 Runbook 必须定义检测、隔离、恢复、密钥轮换、数据保全、通知和复盘责任。
同时必须记录 primary/backup owner、ack/containment/recovery target、演练周期和最近一次演练证据；具体值由 Operational Threshold Catalog 版本化管理。

## 14. 发布门禁

| ID | 要求 | 优先级 |
|---|---|---|
| OPS-035 | 建立版本化 Operational Threshold Catalog，统一管理健康、告警、恢复、磁盘、保留、Runbook 演练和生产 timeout/TTL；必填值缺失时拒绝对应 readiness 或 GA | P0 |
| OPS-036 | 每个候选版本生成 Release Evidence Bundle，绑定需求、验收目录、commit/build、镜像 digest、schema/config/policy/environment、结果、例外与签署；证据必须来自最后一次相关变更之后 | P0 |

GA 前必须证明：

- 信任边界和威胁模型已由安全、技术和运维批准。
- 所有 P0 安全与运维需求有自动化测试、配置审计或演练证据。
- 控制面和 Worker 分离部署实际加载全部批准配置；控制面主机无 Docker socket/daemon 访问或本地执行回退。
- production mode 使用 Secure Cookie、固定镜像并禁用 runtime build。
- approved-target 通过正向和反向网络验收。
- Target Access Grant 的绑定、到期、吊销、排队竞态和 Schedule blocked-policy 验收通过。
- Source Acquisition 只访问 approved-source，禁用未批准 Git 扩展行为并生成不可变 Suite Revision。
- 依赖准备只在受限沙箱访问批准 Registry，Snapshot 可校验且执行阶段不联网安装。
- Target Environment readiness、Environment Lease/fencing、并发、命名空间和 cleanup 故障注入通过。
- 所有 Run 创建入口幂等；Evidence Manifest 可检测归档后篡改、缺失和恢复损坏。
- 不可信主动报告不能读取控制面 Cookie、存储、DOM 或调用带会话 API。
- Profile/Run 不保存或返回秘密明文。
- Git 出站不能访问未批准目标。
- 最小安全审计可查询且不泄露秘密。
- Worker 双向身份、任务 digest、claim/fencing、heartbeat、drain、quarantine、状态/证据上传重放和损坏矩阵通过。
- 每个 Source/Preparation/Report task 和每个 Run 使用全新容器，跨任务无可写层、临时目录、密钥或网络状态复用。
- 分层健康可发现 DB、scheduler、Worker identity/link/agent/Docker/image/capacity/disk 故障。
- 控制面无执行能力；专用 Worker、agent/socket、seccomp/LSM、namespace/device、patch level 和残留容器审计通过，残余风险已签署。
- Worker drain、offline、失联恢复、重建与旧身份 fencing 演练通过；控制面从不本地执行。
- clock skew/jump 健康与 Schedule/Grant/lease 恢复测试通过。
- Schedule missed 可见且补偿策略已验证。
- 备份恢复演练成功，且包含加密密钥。
- stop grace period 与 drain timeout 匹配。
- Release Evidence Bundle 完整，且产品、研发、QA、安全和运维完成适用签署。

## 15. 当前实现阻断项

以下为基于当前仓库的已知差距，关闭前不得宣称满足对应目标：

| Gap ID | 当前差距 | 阻断 |
|---|---|---|
| SOR-GAP-001 | Git URL 校验主要是协议前缀，缺少完整目标/DNS 出站控制 | SEC-016/017 |
| SOR-GAP-002 | Profile/Run env 和 Webhook 可明文存储/返回 | SEC-029～032 |
| SOR-GAP-003 | JWT signing key 同时派生凭证加密 key | SEC-033～035 |
| SOR-GAP-004 | Playwright executor 固定 isolated，无 approved-target | SEC-023～028 |
| SOR-GAP-005 | 生产 Compose 没有传入全部文档化设置 | OPS-001～004 |
| SOR-GAP-006 | 生产默认仍可能 runtime build，镜像使用 latest | OPS-006～009 |
| SOR-GAP-007 | /health 只检查数据库 | 分层健康 |
| SOR-GAP-008 | 调度没有持久化 missed 结果 | OPS-010～013 |
| SOR-GAP-009 | Compose 未显式设置足够 stop grace period | OPS-014～016 |
| SOR-GAP-010 | 备份方案未覆盖一致性快照和密钥恢复 | OPS-018～024 |
| SOR-GAP-011 | 删除、清理、角色、密钥和网络策略没有完整最小审计 | SEC-009、审计章节 |
| SOR-GAP-012 | 没有 Target Access Grant；approved-target 尚不能约束具体 Suite/Profile/依赖/密钥组合 | SEC-038～044 |
| SOR-GAP-013 | `npm ci --ignore-scripts` 仍由控制面进程执行，没有准备沙箱和 Dependency Snapshot | SEC-045～051 |
| SOR-GAP-014 | Allure HTML 同源提供且可直接打开，sandbox 不能覆盖该路径 | SEC-052～057 |
| SOR-GAP-015 | Suite pull 直接 hard reset 可变目录，queued Run 没有不可变 Revision 快照 | SEC-039～041、SYSTEM SYS-FR-019 |
| SOR-GAP-016 | Git clone/fetch/reset 在持有 Docker socket 的控制面进程执行，没有 Source Acquisition 沙箱 | SEC-068～073 |
| SOR-GAP-017 | 没有 Target Environment readiness、Environment Lease、fencing、namespace 或 cleanup 状态 | SEC-074～077、OPS-033 |
| SOR-GAP-018 | Run 创建无 Idempotency-Key，证据无 Evidence Manifest/digest | SEC-078～081、OPS-034 |
| SOR-GAP-019 | 生产控制面直挂 Docker socket且与 executor 共用宿主信任边界；尚无独立专用 Worker、控制面无执行能力证明和完整 Worker 主机基线 | SEC-062～067、SEC-088～095、OPS-031、OPS-037～041 |
| SOR-GAP-020 | 只有通用系统时钟，无 clock skew/jump health 或调度恢复核对 | SEC-082～083、OPS-032 |
| SOR-GAP-021 | 没有版本化 Security Parameter Baseline、补丁 SLA、Operational Threshold Catalog 和候选版本 Release Evidence Bundle | SEC-084～085、OPS-035～036 |
| SOR-GAP-022 | approved-target 尚未实现 namespace 级全进程/全协议出站控制，Playwright 浏览器 sandbox 也没有项目级批准基线与验收证据 | SEC-086～087 |
| SOR-GAP-023 | 没有 Worker identity/agent、受认证协议、job digest、claim/fencing、heartbeat/drain、幂等证据上传或 Worker 失联恢复 | SEC-088～095、OPS-037～041、SYSTEM SYS-FR-030～035 |

## 16. 与旧需求的追踪

| 旧要求 | 新要求 |
|---|---|
| NF-1、NF-2、UI 认证规则 | SEC-001～005 |
| Owner scope、CR-4、DF-3 | SEC-006～009 |
| RN-3、NF-3、SU-8、RP-5 | SEC-010～015 |
| SU-2、SU-4、SU-5、CR-1～CR-6、NF-4～NF-6 | SEC-016～022、SEC-029～037 |
| Playwright Docker-only | SEC-023～028 + SYSTEM 网络契约 |
| NT-1～NT-2、AI-1～AI-5 | AI/通知章节 |
| NF-15～NF-18、UI-7 | OPS-001～009、分层健康 |
| SC-1～SC-5、RN-15～RN-16 | OPS-010～017 |
| NF-17、NF-19 | OPS-018～024 |
| RN-8～RN-14、NF-12～NF-14 | OPS-025～029 |
| 真实 Playwright、SU-5、RP-3～RP-5 | SEC-038～061、OPS-030 |
| SU-2、RN-1/RN-5/RN-15、RP-1～RP-5、SC-3/SC-5 | SEC-062～095、OPS-031～041 |
