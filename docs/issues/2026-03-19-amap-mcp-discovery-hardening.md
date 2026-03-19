# Issue: 高德 MCP tool 自动识别需要增强鲁棒性

- 日期: 2026-03-19
- 优先级: P2
- 状态: Open / Backlog
- 影响范围: `backend/app/agent/tools/mcp`, `backend/app/api/http/health.py`, `README.md`

## 背景

`README.md` 当前已明确写出：

- 高德 MCP tool 的自动识别依赖启动时 discovery；如果第三方 tool 命名发生变化，可能需要手动设置 `MCP_AMAP_ROUTE_TOOL_NAME`。

这说明当前能力虽然可用，但鲁棒性仍建立在“第三方命名别变”的前提上。

## 当前现状

- 高德 MCP tool 发现依赖启动时 discovery
- route tool 选择受命名和配置影响较大
- `/health` 已能返回 `available_tools` 与选中的 route tool，具备一定可观测性

## 为什么这是问题

1. 对外部命名过于敏感

- 第三方一旦改名、加前缀、拆版本，自动识别就可能失效。

2. 启动期发现不等于运行期稳定

- 只在启动时 discovery，无法覆盖运行期 tool 集变化或短暂异常。

3. 配置回退不够结构化

- 现在主要依赖人工查看 `/health` 后再手动填写环境变量，运维成本偏高。

## 建议方案

### 1. 将 route tool 选择做成能力匹配，而不只是名字匹配

可综合判断：

- tool name
- description 关键词
- schema 中是否包含 `origin` / `destination`
- 返回值是否符合路线结构

### 2. 增加 alias 与 profile 机制

例如：

- `maps_direction_walking`
- `route_plan`
- `amap_direction_walk`

都可以映射到“步行路线规划能力”。

### 3. 支持懒刷新与降级回退

如果当前 route tool 调用失败，可尝试：

- 重新 discovery
- 切换到候选 alias
- 再退回 REST / offline fallback

### 4. 强化健康信息

建议 `/health` 额外暴露：

- candidate tools
- 选择理由
- 最近一次重新 discovery 时间
- 当前失败原因

## 验收标准

- 第三方 tool 在轻微改名情况下仍可自动识别。
- route tool 选择不再只靠单一字符串硬匹配。
- discovery 失败时能够输出清晰可观测信息。
- 仍支持人工通过配置强制指定 route tool。

## 关联文档

- [Arcadegent FastMCP MCP Client 接入实施文档](../fastmcp-mcp-client-implementation-plan.md)
- [当前 README 限制条目来源](../../README.md)
