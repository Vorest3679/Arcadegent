# Issue: 会话执行需要异步化，并为真实 SSE 流式输出做准备

- 日期: 2026-03-19
- 优先级: P1
- 状态: Open / Backlog
- 影响范围: `backend/app/api/http`, `backend/app/agent/runtime`, `apps/web/src`

## 背景

当前前后端会话链路虽然已经有 SSE 事件流，但主聊天请求仍然是同步等待最终结果返回。用户感知上表现为：

- 前端发起会话后，很多操作会被 `sending` 状态一起锁住
- SSE 更像附带的阶段面板，而不是会话真正的数据主通道
- 后续若接入 provider 原生 token 流，现有结构会比较别扭

## 当前现状

后端现状：

- `/api/chat` 是同步接口，直接调用 `container.orchestrator.run_chat(request)`。
- `Orchestrator.run_chat()` 也是同步包装。
- `ReactRuntime.run_chat()` 会完整执行一轮，再返回最终 `ChatResponse`。

前端现状：

- `sendChat()` 直接 `POST /api/chat` 并等待完整 JSON。
- SSE 通过 `buildChatStreamUrl()` 单独连接，但最终回复仍依赖 `sendChat()` 的返回值兜底。
- UI 里存在全局 `sending` 态，导致会话发起期间交互粒度偏粗。

## 为什么这是问题

1. 实时链路名义上存在，主数据链路实际上仍是同步

- 这会让 SSE 只承担“看起来在流动”的角色。
- 真正的业务完成点仍然取决于同步 HTTP 返回。

2. 不利于前端交互

- 会话一开始，前端容易把大量按钮一起置灰。
- 后续如果支持取消、重试、并行查看历史，这种同步模型会更吃力。

3. 不利于 provider 级 streaming

- 一旦引入 `stream=true`，后端主通道应当是持续事件流，而不是“等最后一次性回包”。
- 当前结构需要先解开同步耦合，后面再接 token 流会顺得多。

## 建议方案

### 1. 将“发起会话”与“消费结果”拆成两步

推荐接口形态：

1. `POST /api/chat/sessions` 或 `POST /api/chat/dispatch`
   - 只负责创建 / 启动一次执行
   - 立即返回 `session_id`
2. `GET /api/stream/{session_id}`
   - 负责持续消费阶段事件、token、最终完成信号
3. 可选保留 `GET /api/v1/chat/sessions/{session_id}`
   - 用于断线重连后的状态补拉

### 2. 后端执行改为后台任务

后端需要把一次 chat run 从“同步阻塞请求线程”改为“异步启动后后台执行”，例如：

- API 层创建 session 并快速返回
- runtime 在后台任务中推进
- replay buffer / session store 持续写入事件

关键点不是一定要换成复杂队列，而是先把“启动”和“等待完成”解耦。

### 3. 前端改为 SSE 优先，HTTP 最终结果只做兜底或补拉

前端状态机建议改成：

- 创建 session
- 立即连接 SSE
- 只根据流式事件更新阶段与回复内容
- `assistant.completed` 到达后结束本轮 UI
- 若中途断连，再调用 session detail 接口补拉最终状态

### 4. 为后续真实 token 流预留协议

建议现在就把事件协议理顺：

- `session.started`
- `subagent.changed`
- `tool.started`
- `tool.completed`
- `assistant.token`
- `assistant.completed`
- `session.failed`

其中：

- `assistant.token` 承担真正的增量输出
- `assistant.completed` 承担最终收口

## 推荐实施顺序

1. 新增“创建并启动会话”的 API，返回 `session_id`。
2. 把 `run_chat` 包装进后台任务或异步执行入口。
3. 前端去掉对 `sendChat` 最终 `reply` 的主依赖，改为消费 SSE。
4. 收窄 `sending` 的作用域，不再全局阻塞无关按钮。
5. 在此基础上再接 provider 原生 token streaming。

## 风险与边界

- 如果 session store / replay buffer 不是线程安全或协程安全，需要先补边界保护。
- 如果前端仍保留“HTTP 最终回复覆盖 SSE”逻辑，会继续制造伪流式观感。
- 若后端暂时仍使用同步模型调用 LLM，也建议先把 API 边界改为异步启动，后续再切 provider 流。

## 验收标准

- 发起会话后，前端不再依赖一次同步 HTTP 才能拿到最终回复。
- SSE 成为会话进行中的主数据通道。
- 发送消息时，历史查看、列表点击等无关 UI 不会被整体阻塞。
- 断线重连后可通过 session detail 或 replay 恢复会话状态。
- 后续接入 provider token streaming 时，不需要再推翻整个前后端协议。

## 关联文档

- [Issue: SSE 已接通，但 Agent 仍非 Provider 实时 Token 输出（低优先级）](../others/2026-03-04-sse-token-streaming-status-issue.md)
- [流式渲染改造 Handoff（2026-03-04）](../handoffs/streaming-provider-handoff-2026-03-04.md)
