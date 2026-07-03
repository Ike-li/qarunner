# E2E 测试计划模板

> 每个测试计划文件（`specs/*.md`）必须在"测试场景"章节前逐条核对本清单。
> 未覆盖的项需标注原因（"不适用" / "已有单独测试" / "本迭代略过"）。

---

## 组件交互清单（必检项）

### 每个 Modal / Drawer / Popover

| # | 检查项 | 覆盖状态 |
|---|--------|---------|
| 1 | 打开方式 | ☐ |
| 2 | 关闭：X 关闭按钮 | ☐ |
| 3 | 关闭：Escape 键 | ☐ |
| 4 | 关闭：点击遮罩层（mask/overlay） | ☐ |
| 5 | 关闭：取消按钮（如有） | ☐ |
| 6 | 关闭后焦点恢复（a11y） | ☐ |

### 每个表单

| # | 检查项 | 覆盖状态 |
|---|--------|---------|
| 1 | 所有必填项为空 → 提交（前端校验） | ☐ |
| 2 | 无效数据格式 → 提交（前端校验） | ☐ |
| 3 | 有效数据 → 提交成功（正常路径） | ☐ |
| 4 | 后端返回 4xx/5xx → 错误提示 | ☐ |
| 5 | 网络错误（fetch 失败）→ 错误提示 | ☐ |
| 6 | 提交中 loading 状态 | ☐ |
| 7 | 提交成功后状态更新（列表刷新/跳转） | ☐ |

### 每个数据列表 / 表格

| # | 检查项 | 覆盖状态 |
|---|--------|---------|
| 1 | 有数据状态（≥1 行） | ☐ |
| 2 | 空数据状态（0 行） | ☐ |
| 3 | 加载中状态（spinner/skeleton） | ☐ |
| 4 | 错误状态（API 失败） | ☐ |
| 5 | 搜索/筛选后无结果 | ☐ |
| 6 | 分页（数据量超过页面大小） | ☐ |
| 7 | 排序（如有） | ☐ |

### 每个 Toggle / 开关 / 下拉选择

| # | 检查项 | 覆盖状态 |
|---|--------|---------|
| 1 | 从状态 A 切换到状态 B | ☐ |
| 2 | 切换后的 UI 变化 | ☐ |
| 3 | 页面刷新后状态持久化（如有持久化） | ☐ |

### 每个可操作按钮（有副作用）

| # | 检查项 | 覆盖状态 |
|---|--------|---------|
| 1 | 点击 → 操作执行成功 | ☐ |
| 2 | 点击 → 操作失败 → 错误提示 | ☐ |
| 3 | 操作中 loading 状态 | ☐ |
| 4 | 需要确认的操作：确认执行 | ☐ |
| 5 | 需要确认的操作：取消执行 | ☐ |

---

## 项目组件清单

以下列出 qarunner 所有需要核对以上清单的 UI 组件：

### Modals（模态框）
- [ ] TriggerRunModal — 触发运行
- [ ] AddSuiteModal — 添加套件
- [ ] ScheduleModal — 调度管理
- [ ] UserManagementModal — 用户管理

### Drawers（抽屉）
- [ ] RunDetailsDrawer — 运行详情（SideSheet）

### Overlays（全屏叠加层）
- [ ] FullscreenTerminalOverlay — 全屏终端
- [ ] FullscreenReportOverlay — 全屏报告

### Forms（表单）
- [ ] 登录表单（LoginScreen）
- [ ] 触发运行表单（TriggerRunModal 内）
- [ ] 添加套件表单（AddSuiteModal 内）
- [ ] 调度表单（ScheduleModal 内）
- [ ] 用户创建表单（UserManagementModal 内）

### Data Lists / Tables（数据列表）
- [ ] RunsTable — 运行记录表格
- [ ] 用户列表（UserManagementModal 内）
- [ ] 侧边栏套件列表（ProjectSidebar）
- [ ] 测试文件树（TestFileTree）

### Toggles / Selects（开关/选择器）
- [ ] 主题切换（Header + LoginScreen）
- [ ] 语言切换（Header + LoginScreen）
- [ ] 全屏终端：搜索过滤
- [ ] 全屏终端：日志级别筛选
- [ ] 全屏终端：自动换行切换
- [ ] 全屏终端：自动滚动切换
- [ ] 运行表格：状态筛选
- [ ] 运行表格：Owner 筛选
- [ ] 运行表格：日志来源筛选（All/Manual/Scheduled）
- [ ] 调度开关（Schedule enabled toggle）
