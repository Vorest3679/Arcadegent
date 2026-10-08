# 会话运行生命周期与 SSE

本文说明一次 Agent 对话（一个 run）如何被接受、执行、取消和结束，以及 SSE 事件流如何回放与结束。对应源码：`backend/app/session/`、`backend/app/services/chat_run_service.py`、`backend/app/api/stream/sse.py`。

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

`POST /api/chat/sessions/{id}/cancel?run_id=...`：

- 只有第一个取消请求真正取消 task，后续请求等待同一次收尾并保留第一次的原因。
- 返回时 task 已停止、会话存储已写入、记录本已封口；取消调用方断开不会中断收尾。
- 目标 run 已结束时直接返回当前状态；`run_id` 不是当前活动 run 时返回 409，不影响新 run。
- 省略 `run_id` 时取请求时刻的当前 run（兼容旧前端）。
- 还没开始执行就被取消的 task 由完成回调补齐收尾，不会残留占用。
- 取消到达时执行已经结束（收尾已开始）的，不再打断任务，run 按实际结果记为 completed 或 failed；状态序列为 cancelling → completed/failed。
- 收尾中任何一步异常，终态事件、封口和释放占用仍会执行，SSE 不会停在未封口状态。

服务关停：收到 SIGTERM/SIGINT 时立即开始 drain（不再接受新 run，取消所有活动 run 并等待收尾），活动 SSE 因此收到终态并自然结束，随后服务器再关闭连接；lifespan shutdown 复用同一次 drain。Docker 启动命令设置了 `--timeout-graceful-shutdown 15`，compose 设置 `stop_grace_period: 30s` 作为兜底。

## SSE 外壳与结束条件

`GET /api/stream/{session_id}?run_id=&last_event_id=`（也读取 `Last-Event-ID` 头）：

```json
{"id": 4, "session_id": "s_x", "run_id": "r_x", "kind": "event", "event": "assistant.token",
 "output_id": "out_x", "at": "...", "data": {"delta": "新增文字"}}
```

- `id` 在 run 内从 1 递增，control 与业务事件共用序列；`kind` 为 `event` 或 `control`。
- control 事件：`run.state`（带 `status`）、`stream.reset`（请求的 cursor 已被淘汰，`data.head_id` 为当前最大编号；客户端应重拉会话详情后继续）。
- 记录本封口且全部推送完毕后连接结束，不依赖 `assistant.completed` 等业务事件名。没有 run 的会话立即结束；指定的 `run_id` 不存在返回 404。
- 等待期间按 `SSE_KEEPALIVE_SECONDS` 发送 `: keep-alive` 注释。
- 每个会话只保留最近一轮的记录本，单轮上限为 `REPLAY_BUFFER_SIZE`（默认 2000），新一轮开始后旧 run 不能再订阅。
- `assistant.token` 只携带增量 `delta`，同一回复的片段共用 `output_id`，`assistant.completed` 带相同 `output_id` 与完整 `reply`。当前片段仍是在完整回复生成后本地分块（`stream_mode: synthetic`）。

当前 SSE 帧的 `event:` 行仍等于业务事件名；后续前端改为按 run 订阅后会统一为固定帧名。

## 状态来源

- 是否有活动 run、能否删除会话、SSE 何时结束、详情中的 `current_run`：只看 `RunManager`。
- 会话存储里的 `status` 是历史标签，供列表与详情展示；有活动 run 时 API 显示为 `running`。
- 取消与执行异常由 `ChatRunService` 在收尾回调中把会话标记为 failed 并保留上下文，下一次输入继续使用已保存的上下文。

## 限制

- run 登记与记录本在进程内存中：单进程有效，不支持跨副本互斥或共享回放；进程重启后不能恢复正在执行的 run。
- 会话存储仍是同步调用。之所以不需要 run 级写守卫，是因为会话占用在 task 完全结束、收尾写库完成后才释放；若存储改为异步或线程卸载，需要同时加入写守卫。
- runtime 通过 `ContextVar` 获得当前 run 的发布句柄，异步子任务会继承该上下文。
