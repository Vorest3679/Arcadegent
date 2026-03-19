# Issue: 链式 subagent 应升级为主 agent hub 架构

- 日期: 2026-03-19
- 优先级: P1
- 状态: Open / Backlog
- 影响范围: `backend/app/agent/runtime`, `backend/app/agent/context`, `backend/app/agent/subagents`, `backend/app/agent/orchestration`

## 背景

当前运行时采用链式 subagent：

- `intent_router`
- `search_agent` / `navigation_agent`
- `summary_agent`

这种结构前期实现成本低，但测试与现有反馈已经暴露出问题：一旦中间某个阶段信号不稳定，整条链都会抖动，容错空间偏小。

用户希望改成更稳定的主从模式：

- 设立一个主 agent 作为会话唯一协调者
- 主 agent 负责意图识别、上下文整合、最终总结
- worker subagent 只负责具体任务执行
- 主 agent 只需要知道原始 query 和各 worker 的最终结果摘要

## 当前现状

当前代码的典型路径是：

1. 会话进入 `intent_router`
2. 再切到 `search_agent` 或 `navigation_agent`
3. 最后切到 `summary_agent`

这意味着：

- 意图识别被做成独立 subagent
- 总结被做成独立 subagent
- 工具权限是按子阶段切开的
- 主流程依赖多个中间切换点

## 为什么这是问题

1. 链式结构天然脆弱

- 任一阶段的错判、漏判、空返回，都会影响后续阶段。
- “必须先过 A，再过 B，再过 C”的执行链，对异常分支不友好。

2. 意图识别和总结不值得做独立 worker

- 这两类工作更像主控逻辑的一部分。
- 拆成独立 subagent，会增加路由和 prompt 维护成本。

3. 上下文分散

- 当前上下文被切散在多个 subagent 之间。
- 但真正需要对全局负责的，是那个最终向用户作答的主体。

4. 不利于扩展新的 worker

- 后续若增加 `view_renderer`、地图生成、特定领域工具 worker，链式扩展会越来越复杂。
- hub-and-spoke 模式更适合并列挂载多个 worker。

## 目标形态

建议改为以下结构：

```text
main_agent
  |- search_worker
  |- navigation_worker
  |- view_renderer_worker
```

其中：

- `main_agent`
  - 负责意图识别
  - 负责决定是否调用某个 worker
  - 负责接收 worker 的最终结果
  - 负责最后对用户回复
- `worker`
  - 只负责完成一个明确任务
  - 返回结构化结果，不负责最终面向用户的总结

## 工具与权限建议

### 主 agent

主 agent 需要：

- 对所有工具的“可读权限”
  - 即能看到能力描述、参数 schema、适用场景
- 对 `call_subagent` / `invoke_worker` 这一类 pointer tool 的调用权限

是否允许主 agent 直接执行业务 tools，可以作为策略开关，但初版建议先收敛为：

- 主 agent 负责决策
- worker 负责执行

这样边界最清晰。

### worker subagent

worker 只持有其职责范围内的执行权限。例如：

- `search_worker` 负责 `db_query_tool`
- `navigation_worker` 负责 `geo_resolve_tool`、`route_plan_tool`、相关 MCP tool
- `view_renderer_worker` 负责受限 `bash` 与指定 UI 模板生成

## 上下文协议建议

主 agent 持有的最小输入：

- 用户 query
- 历史对话摘要
- 当前会话 working memory
- worker 返回的 final result 列表

worker 返回给主 agent 的内容建议结构化，例如：

```json
{
  "worker": "search_worker",
  "status": "completed",
  "result": {
    "shops": [],
    "total": 0,
    "summary": "..."
  }
}
```

主 agent 不需要知道 worker 的完整思维链，只需要：

- 输入任务
- 最终结果
- 必要的错误信息

## 迁移建议

1. 先新增 `main_agent`，但暂时复用现有 `search_agent` / `navigation_agent` prompt。
2. 将 `intent_router` 逻辑内联到 `main_agent`。
3. 将 `summary_agent` 逻辑内联到 `main_agent`。
4. 把原先链式切换改为 `main_agent -> worker -> main_agent` 的往返。
5. 最后删掉 `intent_router`、`summary_agent` 及相关 transition 兼容逻辑。

## 风险与边界

- 如果主 agent 同时拥有“所有工具可读 + 所有工具可执行”，容易再次变成大一统 agent；需要策略控制。
- worker 返回结果的 schema 必须稳定，否则主 agent 上下文会再次混乱。
- 主 agent 的 prompt 需要明确：它是调度者和最终回答者，而不是另一个“链上的普通节点”。

## 验收标准

- 会话入口固定从 `main_agent` 开始，而不是 `intent_router`。
- 意图识别与最终总结由 `main_agent` 负责，不再存在独立 `intent_router` / `summary_agent`。
- `search`、`navigation`、`view renderer` 这类 worker 能被主 agent 按需调用并返回结构化结果。
- 主 agent 可基于 query 与 worker 返回结果稳定产出最终回复。
- 异常情况下，worker 失败不会直接破坏整个会话状态机。

## 关联文档

- [subagent 结束信号与 pointer 解耦](./2026-03-19-subagent-pointer-decoupling.md)
- [会话执行异步化与 SSE 预备改造](./2026-03-19-chat-async-and-sse-readiness.md)
- [Markdown 渲染与地图视图 renderer 子智能体](./2026-03-19-markdown-map-view-renderer.md)
