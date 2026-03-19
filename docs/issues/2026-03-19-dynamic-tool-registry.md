# Issue: 工具注册表需要从静态注入升级为动态装配

- 日期: 2026-03-19
- 优先级: P1
- 状态: Open / Backlog
- 影响范围: `backend/app/agent/tools`, `backend/app/core/container.py`, `backend/app/agent/subagents`

## 背景

当前工具系统的核心问题不是“能不能执行工具”，而是“工具体系仍偏静态”：

- builtin tools 在容器初始化阶段通过 constructor 全量注入
- `ToolRegistry` 内部仍是基于 `if tool_name == ...` 的显式分发
- subagent 的 `allowed_tools` 虽支持配置，但工具实例本身不是按 provider / descriptor 动态装配

随着 MCP、worker agent、view renderer 等能力扩展，这个形态会越来越难维护。

## 当前现状

当前运行时已经具备一定动态能力：

- `ToolRegistry.tool_definitions()` 会拼接 builtin 与 MCP tool definitions
- MCP tool 已经能通过 gateway 发现并执行

但整体上仍然偏静态：

- builtin tool 依赖在 `build_container()` 中手工逐个实例化
- `ToolRegistry.__init__()` 需要列出每个 builtin 依赖
- `_dispatch()` 仍通过条件分支手工路由
- 新增一个 builtin tool 时，容器、registry、schema、测试都要同步改多处

## 为什么这是问题

1. 扩展成本高

- 每加一个新工具，都要改 constructor、registry 分发、schema 绑定和容器装配。

2. 不利于多 provider / 多 worker

- 后续若有 `view_renderer`、外部 MCP、甚至分环境工具集，静态构造会越来越笨重。

3. 不利于权限和能力治理

- 真正合理的结构应该是“工具描述符 + 工具执行器 + 权限策略”三件事解耦。

## 建议目标

建议把工具系统统一为“provider 化”的注册模型：

```text
ToolRegistry
  |- BuiltinToolProvider
  |- MCPToolProvider
  |- RendererToolProvider
```

每个 provider 负责：

- 提供工具描述符
- 执行工具
- 暴露健康状态或元信息

`ToolRegistry` 只负责：

- 汇总 definitions
- 权限检查
- 路由到对应 provider

## 建议方案

### 1. 引入统一工具描述符

每个工具至少统一为以下信息：

- `name`
- `kind`
- `schema`
- `provider`
- `executor`
- `capabilities`

### 2. Builtin tool 改为 provider 注册

把当前 builtin tools 收口到一个 builtin provider 中，而不是在 registry 内逐个字段持有，例如：

- `db_query_tool`
- `geo_resolve_tool`
- `route_plan_tool`
- `summary_tool`

### 3. Registry 由“知道每个工具”改为“知道每个 provider”

这样 `ToolRegistry` 不需要再维护：

- `_db_query_tool`
- `_geo_resolve_tool`
- `_route_plan_tool`
- `_summary_tool`

而只需要：

- `providers: list[ToolProvider]`

### 4. schema 与执行器解耦

对 builtin tool 仍可保留 Pydantic 校验，但不应再把 schema 模型与 registry 的硬编码分发绑死。

建议：

- tool descriptor 自带 schema 解析器或 validator
- provider 负责具体执行

## 与现有 MCP 能力的关系

这个 issue 不是否定当前 MCP 设计，反而是在把它推广成统一范式。

当前 MCP 已经体现出：

- discovery
- dynamic definitions
- execute dispatch

下一步应当是让 builtin 也长成相同模型，而不是让 MCP 成为特殊分支。

## 推荐实施顺序

1. 抽象 `ToolProvider` 接口。
2. 先把 builtin tools 收口到 `BuiltinToolProvider`。
3. 让 `ToolRegistry` 只依赖 provider 列表。
4. 将 `_dispatch()` 的 if/else 路由迁移到 provider 内部。
5. 最后再接更多 provider，如 `RendererToolProvider`。

## 验收标准

- 新增 builtin tool 时，不再需要修改 `ToolRegistry.__init__()` 的参数列表。
- `ToolRegistry` 只依赖 provider 抽象，而不是每个具体工具实例。
- builtin 与 MCP tool 在 definitions 与 execute 两条链路上采用统一路由模型。
- `allowed_tools`、权限校验和健康检查仍能正常工作。

## 关联文档

- [subagent 结束信号与 pointer 解耦](./2026-03-19-subagent-pointer-decoupling.md)
- [链式 subagent 改为主 agent hub](./2026-03-19-main-agent-hub-architecture.md)
- [Arcadegent FastMCP MCP Client 接入实施文档](../fastmcp-mcp-client-implementation-plan.md)
