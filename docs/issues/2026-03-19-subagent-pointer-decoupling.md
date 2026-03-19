# Issue: subagent 结束信号不应绑定在业务 tools 上

- 日期: 2026-03-19
- 优先级: P1
- 状态: Open / Backlog
- 影响范围: `backend/app/agent/runtime`, `backend/app/agent/tools`, `backend/app/agent/subagents`

## 背景

当前 subagent 的切换依赖 `select_next_subagent` 这一兼容工具，以及业务 tool 执行结果上的副作用信号。这个设计能跑通，但控制面信号与业务面工具耦合过深，已经开始影响路由清晰度和鲁棒性。

用户的目标更接近：

- function calling 只负责“需要继续调用工具时”的分派
- 当模型不再发起 function call 时，就视为当前 subagent 已结束并返回上层流程
- 不再要求额外调用一个“告诉系统我要结束”的 tool

## 当前现状

当前实现中：

- `SubAgentBuilder` 仍把 `select_next_subagent` 注入到多个 subagent 的 `allowed_tools`。
- `ToolRegistry` 将 `select_next_subagent` 作为 builtin tool 处理。
- `TransitionPolicy` 会读取该工具输出里的 `next_subagent` 作为切换依据。
- `ToolActionObserver` 还会把 `next_subagent_candidate`、`subagent_done` 等状态写入 working memory。

这意味着：

- 子智能体是否结束，不是由“没有后续 tool call”自然决定。
- 模型需要额外学会一个控制面工具。
- `db_query_tool`、`route_plan_tool`、`summary_tool` 的完成状态，间接承载了流程推进语义。

## 为什么这是问题

1. 控制面与业务面耦合

- `db_query_tool` 本来只应该表达“查到了什么”。
- 现在它同时承载“查完后该不该跳 summary”这类流程语义。

2. 提示词负担偏重

- 模型除了学会何时查库、何时规划路线，还要学会何时额外调用 `select_next_subagent`。
- 这会增加无意义 tool call，也更容易出现漏调用或误调用。

3. 父子边界不清晰

- 一个 subagent 的自然结束条件，本应是“它已经没有进一步工具要调用，或者已经产出最终结果”。
- 当前却必须显式发一个 pointer tool，导致“结束”不是协议内建语义，而是额外约定。

4. 不利于后续主 agent 架构

- 如果后面改成“主 agent 调 worker subagent”，主 agent 需要的是明确的返回结果。
- 继续沿用 `select_next_subagent` 会把未来的主从架构拖回链式流程。

## 建议方案

### 1. 去掉 `select_next_subagent` 作为主路径依赖

建议把它降级为过渡期兼容层，而不是正式协议的一部分。

- 新协议下，subagent 的一次执行轮次只有两种结果：
  - 继续调用工具
  - 结束并返回结果

### 2. 将“无 tool call”定义为显式结束信号

推荐约定：

- 当模型返回 `tool_calls=[]` 时，认为当前 subagent 已完成本轮目标。
- 返回体可以是：
  - `text`
  - 或一个受控 JSON 结构，例如 `{"status":"done","result":...}`

其中哪种更适合，可以在主 agent 方案中统一约束，但关键是不要再依赖额外 pointer tool。

### 3. 让 runtime 负责解释结束，而不是 tool 负责宣告结束

建议将流程判断移入 runtime：

- 如果当前 subagent 返回工具调用，则继续执行工具
- 如果当前 subagent 没有工具调用，则将其输出视为 return value
- 父级 runtime 再决定：
  - 是否结束整个会话
  - 是否把结果交还给主 agent 继续汇总

### 4. 为过渡期保留兼容策略

在迁移阶段可以：

- 保留 `select_next_subagent`
- 但仅用于兼容旧 prompt / 旧测试
- 新 prompt 与新 runtime 默认不再生成或依赖该工具

## 推荐实施拆分

1. 在 runtime 层增加“subagent return”语义，支持 `tool_calls` 为空时直接结束当前 subagent。
2. 将 `TransitionPolicy` 从“基于 select_next_subagent 输出切换”收缩为“只处理 runtime 层状态机”。
3. 逐步从 prompt 和 `allowed_tools` 中移除 `select_next_subagent`。
4. 最后删除与该工具相关的 working memory 字段、单测和兼容逻辑。

## 验收标准

- `intent_router`、`search_agent`、`navigation_agent` 的主流程不再依赖 `select_next_subagent`。
- 业务 tool 执行结果只表达业务结果，不表达“流程跳转意图”。
- 当模型不再返回 tool call 时，runtime 能稳定结束当前 subagent 并拿到返回值。
- 相关单测不再把 `select_next_subagent` 视为必经路径。

## 关联文档

- [链式 subagent 改为主 agent hub](./2026-03-19-main-agent-hub-architecture.md)
- [工具注册表从静态注入改为动态装配](./2026-03-19-dynamic-tool-registry.md)
