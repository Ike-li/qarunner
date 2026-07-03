# 测试规格目录

本目录存放 E2E 测试计划和模板。

### 文件说明

| 文件 | 说明 |
|------|------|
| `TEST_PLAN_TEMPLATE.md` | 测试计划模板 — 编写新测试计划时必须逐条核对的交互清单 |
| `ui-test-plan.md` | qarunner 全量 UI E2E 测试计划（9 个套件，覆盖所有组件） |

### 编写测试计划

1. 复制 `TEST_PLAN_TEMPLATE.md` 中的检查清单到新计划
2. 逐条核对每个组件的交互点
3. 确保覆盖：打开/关闭路径、表单校验、数据列表三态（有数据/空/加载）、Toggle 切换

### 将计划转为 Playwright 测试

```bash
# 使用 Playwright Test Generator agent
# 或手动编写，参考 frontend/tests-e2e/ 中的现有测试模式

# 运行测试
cd frontend
E2E_ADMIN_PASSWORD='Demo-Qarunner-2026!' npx playwright test --reporter=list
```
