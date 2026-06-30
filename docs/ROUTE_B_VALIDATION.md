# 路线 B 验证:需求证伪 + microVM 技术冲击（ROUTE_B_VALIDATION）

> **背景与前提**
> - 目标已由用户当面确认:**做开源/商业产品**(非自用工具、非工程作品)。
> - 这推翻了 [`POSITIONING.md`](POSITIONING.md) 选「路线 A(单团队 runner)」时的前提——A 的推荐明文写着「若用途是给自己/团队内部」。目标变成产品,前提没了,结论应跟着翻。
> - 本文**不改** `POSITIONING.md`(那份记录的是「内部工具」假设下的取舍),而是给出:既然目标是产品,为什么 B 是唯一活路、投 B 前必须先过的**需求 gate**、以及它的**工程成本**。
> - 标签约定同 `CLAUDE.md`:[KNOWN] 训练/源码核对事实 · [INFERRED] 推论 · [GUESS] 无根据;置信度 HIGH/MED/LOW。

---

## 0. 为什么是 B,且为什么「先验证、再动工」（摘要）

把所有 feature 还原成原子能力只有三块:跑起来 / cron / 报告。②③ 是 CI runner 的通用商品功能,全部差异化押在 ①「跑得安全、能跑不受信任的代码」一个赌注上。作为**产品**:

- **A(单团队自托管 runner + 看板)是死路** `[INFERRED] MED-HIGH`:竞争对手是 GitHub Actions / GitLab CI / Jenkins——免费、已集成 VCS、self-hosted runner 免费、已经在跑这些团队的测试。一个团队没有理由再装第三个、还不能横向扩展(单节点 SQLite)的东西。「看板好看/部署简单」不构成采用理由。
- **B(自托管、不受信任代码执行沙箱)有真实付费场景** `[KNOWN 截至 2026-01] MED`:2026 年增长最快的需求是 **AI agent / LLM 生成代码的执行沙箱**;在位者(E2B / Modal / Daytona)偏 SaaS/云托管,**开源 + 自托管 + 单镜像部署**是空位。受监管/数据敏感团队不能把代码执行外包给 SaaS。
- **但当前架构兑现不了 B 的承诺** `[INFERRED] HIGH`:`POSITIONING.md` 自承 DooD = 平台进程拿宿主 root,隔离边界在编排层是漏的。作为产品这是 **dealbreaker,不是脚注**——第一个认真的安全审计就钉死。
- **压倒一切的风险:0 真实外部用户验证** `[INFERRED] HIGH`:`external_tests/massive_scale_suite` 是合成假套件,唯一的真实自用套件 `my-e2e-suite` 是空目录。整个系统从未被一个不是作者的人碰过。这是 "build it and they will come" 陷阱的教科书形态。

**决策门:第 1 部分访谈亮绿灯 → 才投第 2 部分的 microVM 工程。在访谈出结果前写任何隔离层代码,都是在赌一个还没验证的假设。**

---

## Part 1 — 需求证伪工具包

### 1.1 先讲反方:大多数需求访谈是自我安慰

最大的风险不是问得不够多,是**问的方式制造假绿灯**。规则(Mom Test):

- **只问过去的具体行为和已花掉的钱/人力**,不问「你会不会用 / 你觉得有用吗」这类对未来的假设。
- **全程不推销 qarunner**——一介绍,之后所有回答都在迎合你。
- 对方说不出**具体的上一次实例**,那个痛就是想象的。

### 1.2 先约谁（按「有真需求 × 够得着 × 不能用 SaaS」排序）

1. **做 AI agent 产品、但终端客户在受监管行业(金融/医疗/法律)的团队** —— 既有跑 LLM 生成代码的真需求,又因客户合规不能送进 E2B SaaS。**最可能的甜点。** `[INFERRED] MED`
2. **大企业内部平台 / 开发者体验团队** —— 给内部开发者或内部 agent 提供代码执行,数据不能出网。预算有、够得着。
3. **受监管行业自己的 AI / 创新团队**(银行、保险、医院的内部 AI 组) —— 直接终端用户。
4. ~~air-gap / 国防承包商~~ —— 需求硬但销售周期以年计,早期项目够不着。先不碰。
5. ~~autograder / 判题~~ —— 有 Gradescope/nbgrader 占位,弱。先不碰。

目标:约 1–3 类,做 5–8 个真实对话。

### 1.3 访谈脚本（每组标在验证哪个假设）

**A. 现状取证**(有没有这个场景,不预设)
- 你们现在有没有需要执行「自己没写的」或「模型生成的」代码的场景?具体是什么?
- 上一次这么做是什么时候?那段代码当时跑在哪、怎么跑起来的?

**B. 痛强度**(痛是否真实、可量化——问已付出的代价)
- 为了让它跑得安全、不出事,你们实际做了什么?花了多少人力或钱?
- 出过事吗?最近一次因为这个被卡或出问题是什么时候,后果是什么?

**C. 自托管 vs SaaS 生死线**(整个产品的命根)
- E2B / Modal 这类托管沙箱,你们**能**用吗?
- 若不能 —— **是谁规定的?是白纸黑字的合规条款、客户合同,还是团队偏好?**(硬约束 vs 偏好,必须区分)
- 你们现在把代码执行放自己基础设施里,还是外包了?

**D. 隔离技术门槛**(microVM 是不是必须、容器够不够)
- 对「安全隔离」,你们内部或合规要求到什么程度?容器够,还是必须 VM 级?
- 有没有过「方案只是容器隔离、被安全团队判定不够强」而被否掉的经历?

**E. 采购与够不够得着**(单人/早期项目能否交付)
- 把一个执行沙箱放进生产,必须过哪些关?(安全评审/渗透测试/SOC2/供应商审查/采购)
- 这种东西你们会买、会用开源自建,还是自己从零写?决策链多长、预算量级?

**F. 反推销陷阱**(最后才问,避免确认偏误)
- 全程不提 qarunner。结尾才问:「如果有个开源、能自托管的方案,你第一个担心的是什么?」
- 留意他们**自发**提到的现有方案里有没有「自建/开源沙箱」——若整场没人提这个品类,说明它在他们心智里不存在,你要从零教育市场(极贵)。

### 1.4 判读标准（比问题本身更重要）

| 🔴 红灯(任一出现 = 方向重大警示) | 🟢 绿灯(需同时满足才继续) |
|---|---|
| 约不到 3 个愿意聊的人 → 没人在乎 | ≥3 人**独立**确认「不能用 SaaS」并能指出白纸黑字来源 |
| 多数人能用且在用 SaaS、无硬合规阻碍 → **自托管假设崩,B 失去差异化** | 痛有**具体实例** + 已经为它花过钱/人力 |
| 痛说不出具体实例,只有「会更安全」→ 想象需求 | 隔离门槛里「VM 级/强隔离」是关键诉求 → microVM 是真加分 |
| 隔离要求「容器就够」→ **microVM 重写白做,docker 版已够** | ≥1–2 人愿为开源自托管方案付费/采用,且决策链够得着 |
| 门槛是 SOC2+供应商审查+长采购 → 早期项目够不着 | |
| 倾向「自己写」而非用现成 → 这群人不是你的用户 | |

**只有亮绿灯,才值得进入 Part 2 的 microVM 工程投入。否则方向要改,而不是去写隔离层。**

---

## Part 2 — 把 Docker 换成 microVM 的真实冲击

基于实读 `ports/process.py`、`adapters/docker_runner.py`、`core/orchestrator.py`。

### 2.1 好消息:端口契约是容器中立的,换引擎在契约层是局部的 `[KNOWN] HIGH`

`ProcessRunner.run(cmd, cwd, env, timeout, stdout_file, stderr_file) -> ProcessResult`(`ports/process.py:14-22`)——签名里**没有**任何「容器/镜像/mount」概念。core 里 `executor_mode` 只用来在两个已注入的 runner 实例间二选一(`orchestrator.py:327-329`),不实现任何容器逻辑。runner(pytest/playwright)只管 `build_command(ctx)` 构造命令行,**完全不知道 executor 是什么**(`orchestrator.py:317-323`)。

**含义**:加一个 `MicroVMRunner` 实现同一个 Protocol,core 不用动;改动只是——`executor_mode` 枚举加一值、DI 多注入一个实例、`orchestrator.py:328` 那个 `if` 改成三选一、config + 前端下拉各加一项。**几十行。六边形架构在这里兑现了承诺。**

### 2.2 坏消息:真实成本不在签名里,在三个「宿主文件系统原地访问」的隐含耦合 `[INFERRED] HIGH`

docker 与 subprocess 都满足一个**没写进端口、但 core 默认成立**的假设:*executor 能就地读写宿主路径*。microVM 打破它:

1. **文件要进出 VM。** core 在宿主上用 `shutil.copytree` 把套件复制成 workspace jail(`orchestrator.py:292-304`),把宿主路径 `exec_cwd` 交给 runner;docker 靠 bind mount 把它映进容器(`docker_runner.py:174`)。microVM 看不到宿主目录——adapter 得用 virtio-fs 共享,或把 jail 打包注入 VM。core 准备好的宿主 jail 对 microVM **错位**。
2. **结果要从 VM 捞回来。** 跑完 core 直接读宿主 `results_dir` 做 `collector.collect`(`orchestrator.py:347-350`);docker 靠 bind mount results_dir 让容器内写入直接落宿主(`docker_runner.py:176-177`)。microVM 里结果写在 VM 内,**不显式拷回,core 读到空目录 → run 被判 FAILED**。adapter 必须补「出料」逻辑。
3. **实时日志要穿过 VM 边界。** docker 用 `container.logs(stream=True, follow=True)` 边跑边写 stdout_file(`docker_runner.py:227-244`),撑起前端 SSE 实时日志。microVM 要走 vsock/串口把日志流出来重写这条管道——能做,但这是实时观测卖点,不能退化成「跑完才有日志」。

外加 **制品链 100% 重写**:`_ensure_image` 那套 docker 镜像构建(`docker_runner.py:58-79`)要换成 rootfs+kernel 打包(含 python/node/playwright/浏览器)。而 SEC-3 那串隔离原语(`docker_runner.py:197-215`:cap_drop/network=none/read_only/pids/mem/cpu/tmpfs)——microVM **天然提供或更强**(VM 边界本身就是隔离),安全目标其实更容易达到,但配置方式 100% 不同,代码全部重写。

### 2.3 净评估

> **契约层:加 adapter,小(架构红利真实存在)。adapter 内部 + 制品链 + 日志管道:2–4 周量级的实打实工程,不是周末活。** `[INFERRED] MED`(工程量依赖选型:自己拼 Firecracker 偏贵;用 `microsandbox`/libkrun 这类现成 microVM 封装可大幅压缩 `[KNOWN 截至 2026-01] MED`)

两个必须现在认清的连带后果:

- **它会侵蚀「单镜像 compose 部署」卖点。** microVM 需要宿主有 KVM、要管 kernel+rootfs 制品——简单部署变复杂。这正好印证 `POSITIONING.md` 说的 A↔B 反向拉扯:为真隔离,牺牲部署简单。
- **gVisor 是更便宜的中间档,但不是 VM。** 若访谈结论是「要比容器强,但不必到 VM 级」,gVisor(syscall 拦截)能去掉 DooD 的 host-root 风险、部署冲击远小于 Firecracker——但它在合规话术里不等于「VM 隔离」。**选 Firecracker 还是 gVisor,由 Part 1 的 D 组回答决定,不是技术品味决定。** `[INFERRED] MED`

---

## 决策门（总结）

```
Part 1 访谈
  ├─ 红灯  → 停。回到 POSITIONING.md 重新选路线(A 当作品/自用,或放弃)。
  └─ 绿灯  → Part 2 投 microVM 工程
              ├─ D 组「必须 VM 级」 → Firecracker / libkrun / microsandbox
              └─ D 组「比容器强即可」 → gVisor(更便宜,先去 DooD host-root)
```

**顺序是死的:先访谈 → 看 C 组(能不能用 SaaS)和 D 组(容器够不够)→ 才决定投不投、投哪个引擎。**

---

## 附:可信度

- Part 2 的源码事实(端口签名、orchestrator 选择逻辑、docker bind-mount/日志流/隔离原语行号)均经实读 `[KNOWN] HIGH`。
- 「A 死路 / B 唯一活路」为产品判断 `[INFERRED] MED-HIGH`;市场态势(E2B/Modal/Daytona、AI 沙箱增长)截至 2026-01,有约半年未校准 `[KNOWN 截至 2026-01] MED`。
- 「2–4 周工程量」为估计 `[INFERRED] MED`,依 microVM 选型而变。
</content>
</invoke>
