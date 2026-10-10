# 会话运行生命周期与 SSE

本文说明一次 Agent 对话（一个 run）如何被接受、执行、取消和结束，SSE 事件流如何回放与结束，以及前端如何订阅。对应源码：`backend/app/session/`、`backend/app/services/chat_run_service.py`、`backend/app/api/stream/sse.py`、`apps/web/src/lib/runStream.ts`。

## 模块划分

| 文件 | 职责 |
| --- | --- |
| `session/models.py` | run 状态（pending / running / cancelling / completed / failed / cancelled）、`RunRecord`、合法迁移函数 `transition` 和错误类型 |
| `session/injector.py` | 交给 runtime 的入口：`RunPublisher`、`RunContext`、`RunExecutor`、`TerminalHook`，以及评测/测试用的 `CollectingPublisher` |
| `session/run_log.py` | `RunLog`：每个会话只保留最近一轮的事件记录本，run 内编号、回放、等待、封口 |
| `session/runs.py` | `RunManager`：同一会话同时只有一个活动 run；负责 task、取消、关停 drain 和收尾 |
| `services/chat_run_service.py` | 应用层胶水：会话归属检查、把 `ReactRuntime.run_chat` 包成 run、取消/失败时写回会话存储 |

`session/` 不依赖 agent、protocol、services 或数据库实现，也不解释事件内容。runtime 只通过 `RunPublisher.publish(event, data, output_id=)` 发事件，看不到登记簿和记录本。

## 一个 run 的生命周期

1. `POST /api/chat/sessions`：`ChatRunService` 检查会话是否忙、归属是否匹配，把会话标记为 running 后交给 `RunManager.dispatch`。后者发号 `r_xxx`、为该会话开一本新记录本、创建后台 task，立即返回 202（含 `run_id`）。
2. task 开始时状态变为 running，并写入 control 事件 `run.state`；runtime 执行过程中的业务事件（`tool.*`、`assistant.token` 等）依次写入记录本。
3. 结束时收尾固定五步，每个 run 只执行一次：改终态 → 调用 `TerminalHook`（应用层写库，必要时发布 `session.failed`）→ 发布终态 `run.state` → 封口 → 解除会话占用。
4. 写库失败不改变终态，只在 `error_code` 记录 `persist_failed`。执行异常的 run 记为 failed、`error_code=executor_failed`；对外不输出异常原文。

run 状态只表示执行结果：模型失败、走兜底回复等业务失败仍是 completed 的 run，业务失败通过 `session.failed` 事件和会话详情的 `status/last_error` 表达。

## 取消

`POST /api/chat/sessions/{id}/runs/{run_id}/cancel`：

- 只有第一个取消请求真正取消 task，后续请求等待同一次收尾并保留第一次的原因。
- 返回时 task 已停止、会话存储已写入、记录本已封口；取消调用方断开不会中断收尾。
- 目标 run 已结束时直接返回当前状态；`run_id` 不是当前活动 run 时返回 409，不影响新 run。
- 还没开始执行就被取消的 task 由完成回调补齐收尾，不会残留占用。
- 取消到达时执行已经结束（收尾已开始）的，不再打断任务，run 按实际结果记为 completed 或 failed；状态序列为 cancelling → completed/failed。
- 收尾中任何一步异常，终态事件、封口和释放占用仍会执行，SSE 不会停在未封口状态。

服务关停：收到 SIGTERM/SIGINT 时立即开始 drain（不再接受新 run，取消所有活动 run 并等待收尾），活动 SSE 因此收到终态并自然结束，随后服务器再关闭连接；lifespan shutdown 复用同一次 drain。Docker 启动命令设置了 `--timeout-graceful-shutdown 15`，compose 设置 `stop_grace_period: 30s` 作为兜底。

## SSE 外壳与结束条件

`GET /api/stream/{session_id}?run_id=&last_event_id=`（`run_id` 必填；也读取 `Last-Event-ID` 头）。每帧的 `event:` 行固定为 `message`，业务事件名只在 JSON 外壳的 `event` 字段中：

```json
{"id": 4, "session_id": "s_x", "run_id": "r_x", "kind": "event", "event": "assistant.token",
 "output_id": "out_x", "at": "...", "data": {"delta": "新增文字"}}
```

- `id` 在 run 内从 1 递增，control 与业务事件共用序列；`kind` 为 `event` 或 `control`。
- control 事件：`run.state`（带 `status`）、`stream.reset`（请求的 cursor 已被淘汰，`data.head_id` 为当前最大编号；客户端应重拉会话详情后继续）。
- 记录本封口且全部推送完毕后连接结束，不依赖 `assistant.completed` 等业务事件名。指定的 `run_id` 不在记录本中（从未运行、已被新一轮替换或进程重启）返回 404。
- 等待期间按 `SSE_KEEPALIVE_SECONDS` 发送 `: keep-alive` 注释。
- 每个会话只保留最近一轮的记录本，单轮上限为 `REPLAY_BUFFER_SIZE`（默认 2000），新一轮开始后旧 run 不能再订阅。
- `assistant.token` 只携带增量 `delta`，同一回复的片段共用 `output_id`，`assistant.completed` 带相同 `output_id` 与完整 `reply`。每次主 agent 模型调用对应一个 `output_id`；`LLM_STREAM=true` 时片段随模型生成实时发出（`stream_mode: provider`），否则在调用结束后整段发出（`stream_mode: synthetic`），详见 [ReAct 运行时核心逻辑](./ReAct运行时核心逻辑.md)。

## 前端订阅

- `apps/web/src/lib/runStream.ts` 的 `openRunStream` 只订阅一个 run，不依赖 React：校验外壳（`session_id`/`run_id` 与订阅一致，其余帧丢弃）；`id <= 已收最大 id` 的重放帧丢弃；断线后按 500ms×2ⁿ 退避、带 `last_event_id` 重连，最多 3 次；收到终态 `run.state` 后关闭，不再重连。
- 文本按 `output_id` 追加 `delta`。出现新的 `output_id` 时上一段**定格**（`onOutputSealed`），界面显示为「中间回复」，新的一段单独显示。目前后端只为最终回复发 token，多段输出要等真实流式接入后才会出现。
- 「当前 run 是否仍在显示中」由 store 的 `activeRunId` / `committedRunId` 决定：终态后重拉会话详情，详情与 `committedRunId` 在同一次更新中写入，流式气泡与进度卡片随之切换为历史消息。不再用文本前缀或长度猜测重复。
- 刷新页面时，若详情的 `current_run` 未结束，则不带游标订阅该 run，从头回放；收到 `stream.reset` 时重拉详情后继续订阅。
- 重连 3 次仍失败时，先拉详情确认该 run 仍在运行，再按 `run_id` 取消。

### 前端调用链

```text
App.tsx
  └─ useChatSessionController()            挂载时拉会话列表；返回 submitChat 等回调给组件
       ├─ submitChat → dispatchChatSession → 拿到 run_id → startStream(sessionId, runId)
       ├─ 刷新/切换会话 → loadSession → applySessionDetail → current_run 未结束 → startStream
       └─ startStream → openRunStream({ url, sessionId, runId, handlers })
            └─ connect() → new EventSource(url(lastId))
                 └─ addEventListener("message", handleMessage)
                      ├─ parseEnvelope：校验外壳，session_id/run_id 不符则丢弃
                      ├─ id <= lastId：重放帧，丢弃
                      ├─ control：stream.reset → onReset；run.state → onState（终态先 close）
                      └─ event：assistant.token → applyToken（onText / onOutputSealed），再 onEvent
```

**1. App.tsx 只挂载 controller**，SSE 不在组件里处理：

```tsx
// apps/web/src/App.tsx
const chat = useChatSessionController();
// ...
<ChatPanel onSubmit={chat.submitChat} streamReply={chat.streamReply} ... />
```

**2. controller 发起订阅并注册 handlers**，每个回调只负责写 store 或触发重拉详情：

```ts
// apps/web/src/hooks/useChatSessionController.ts
const dispatched = await dispatchChatSession({ session_id, client_id, message, ... });
startStream(dispatched.session_id, dispatched.run_id);

function startStream(sessionId: string, runId: string): void {
  stopStream();
  store.setActiveRunId(runId);
  const stream = openRunStream({
    url: (afterId) => buildChatStreamUrl(sessionId, runId, afterId, clientIdRef.current),
    sessionId,
    runId,
    handlers: {
      onEvent: (envelope) => handleRunEvent(sessionId, envelope), // 业务事件 → 阶段、地图、进度卡片
      onText: (_outputId, text, delta) => appendStreamReply(text, delta), // 流式气泡
      onOutputSealed: (outputId, text) => { /* 追加到 sealedOutputs（中间回复） */ },
      onState: (status) => { /* 终态 → loadSession，详情到达后交接为历史消息 */ },
      onReset: () => { /* 重拉详情，继续订阅 */ },
      onConnection: (connected) => store.setStreamConnected(connected),
      onRetry: recordStreamReconnect,
      onGiveUp: () => { void giveUpRun(sessionId, runId); } // 确认仍在运行后按 run_id 取消
    }
  });
  streamRef.current = stream;
}
```

**3. runStream 在 connect 时挂上 handleMessage**，处理传输层逻辑后再分发给 handlers：

```ts
// apps/web/src/lib/runStream.ts
function connect(): void {
  const current = new EventSource(url(lastId));      // 重连时带 last_event_id
  source = current;
  current.addEventListener("message", (event) => handleMessage(event as MessageEvent<string>, current));
  current.onerror = () => { /* 关闭连接；未超次数则 500ms×2ⁿ 后 connect()，否则 onGiveUp */ };
}

function handleMessage(message: MessageEvent<string>, current: EventSource): void {
  if (closed || source !== current || !message.data) return;          // 旧连接的迟到帧
  const envelope = parseEnvelope(message.data, sessionId, runId);
  if (!envelope) return;                                               // 外壳不合法或不属于本 run
  if (lastId !== undefined && envelope.id <= lastId) return;           // 重放去重
  lastId = envelope.id;
  attempts = 0;

  if (envelope.kind === "control") {
    if (envelope.event === "stream.reset") { handlers.onReset(); return; }
    if (envelope.event === "run.state" && envelope.status) {
      if (isTerminalRunStatus(envelope.status)) { close(); handlers.onConnection(false); }
      handlers.onState(envelope.status);
    }
    return;
  }
  if (envelope.event === "assistant.token") applyToken(envelope);
  handlers.onEvent(envelope);
}

function applyToken(envelope: ChatStreamEnvelope): void {
  const delta = envelope.data.delta;
  if (typeof delta !== "string" || !delta) return;
  const nextOutputId = envelope.output_id ?? "";
  if (outputId !== null && outputId !== nextOutputId) {
    handlers.onOutputSealed(outputId, outputText);                     // 上一段定格为中间回复
    outputText = "";
  }
  outputId = nextOutputId;
  outputText += delta;
  handlers.onText(nextOutputId, outputText, delta);
}
```

职责边界：`runStream.ts` 只懂外壳、游标、重连和按 output_id 拼文本，不认识 tool/worker/route 等业务事件；业务含义在 controller 的 `handleRunEvent` 和 `lib/chatStream.ts`（`mapArtifactsForEvent`、`toProgressText`）中解释；组件只读 store。

## 状态来源

- 是否有活动 run、能否删除会话、SSE 何时结束、详情中的 `current_run`：只看 `RunManager`。
- 会话存储里的 `status` 是历史标签，供列表与详情展示；有活动 run 时 API 显示为 `running`。
- 取消与执行异常由 `ChatRunService` 在收尾回调中把会话标记为 failed 并保留上下文，下一次输入继续使用已保存的上下文。

## 限制

- run 登记与记录本在进程内存中：单进程有效，不支持跨副本互斥或共享回放；进程重启后不能恢复正在执行的 run。
- 会话存储仍是同步调用。之所以不需要 run 级写守卫，是因为会话占用在 task 完全结束、收尾写库完成后才释放；若存储改为异步或线程卸载，需要同时加入写守卫。
- runtime 通过 `ContextVar` 获得当前 run 的发布句柄，异步子任务会继承该上下文。
