# ReAct 运行时核心逻辑

本文说明 `ReactRuntime` 一次对话的执行主线。对应源码：`backend/app/agent/runtime/react_runtime.py`、`backend/app/agent/llm/provider_adapter.py`、`backend/app/agent/llm/streaming.py`。run 的接受、取消和 SSE 传输见 [会话运行生命周期与 SSE](./会话运行生命周期与SSE.md)。

> 本文目前只写了主 agent 循环与模型输出的发布方式，worker、上下文构建、工具记忆等内容后续补充。

## 主 agent 循环

每轮固定先调用一次模型，等这次调用完整结束后，再决定执行工具、结束循环或报错退出：

```python
# _run_main_agent（简化）
while not guard.exhausted:
    output_id = _new_output_id()                 # 每次模型调用对应一个新的输出
    response, published = await self._call_main_model(output_id=output_id, ...)
    self._record_model_call(...)                 # transcript / usage / 工具参数证据

    if response.error is not None:
        break                                    # 模型出错：不执行本次的工具调用
    if response.tool_calls:
        await self._execute_tool_calls(...)      # 含 invoke_worker → _run_worker
        if memory.get("reply"):                  # summary_tool 写入的回复
            final_text = memory["reply"]; break
        continue                                 # 下一轮，新的 output_id
    if response.text:
        final_text = response.text.strip()
        final_output_id = output_id if published else None
        break
```

- 工具只在 `_call_main_model` 返回之后执行，此时模型这次调用已经完整结束，工具参数已拼接完整并只解析一次。流式只改变文本发出的时机，不改变循环结构。
- worker（`_run_worker`）始终调用 `complete()`，它的文本只进入证据，不发布为 `assistant.token`。

## 一次模型调用：`_call_main_model`

```python
async def _call_main_model(self, *, output_id, **request):
    if not adapter.streaming:                    # LLM_STREAM=false
        response = await adapter.complete(**request)
        if response.error is None and text:      # 调用结束后整段发一次
            publish("assistant.token", {"delta": text, "stream_mode": "synthetic"}, output_id)
        return response, published

    async with aclosing(adapter.stream(**request)) as events:   # LLM_STREAM=true
        async for event in events:
            if isinstance(event, TextDelta):     # 边收边发
                publish("assistant.token", {"delta": event.text, "stream_mode": "provider"}, output_id)
            elif isinstance(event, StreamDone):
                final = event.response           # 与 complete() 同形的 ModelResponse
    return final, published
```

`adapter.stream()` 产出四种事件，只有 `TextDelta` 会发布：

| 事件 | 处理 |
| --- | --- |
| `TextDelta(text)` | 立即以当前 `output_id` 发布 |
| `ReasoningDelta(text)` | 不发布，只进入证据 |
| `ToolCallDelta(index, ...)` | 不发布；由 provider 层按 index 拼接 |
| `StreamDone(response)` | 最后一个事件，带收集完整的结果 |

取消 run 时，`CancelledError` 在 `async for` 处抛出，`aclosing` 关闭生成器，生成器内的 `httpx` 流随之关闭，上游请求停止。

## 最终回复与 output_id

```python
# _run_chat_session（简化）
final_text, model_error, output_id = await self._run_main_agent(...)
if not final_text:
    final_text, output_id = self._fallback_reply(...), None
if output_id is None:                            # summary_tool 回复或兜底文案
    output_id = _new_output_id()
    publish("assistant.token", {"delta": final_text, "stream_mode": "synthetic"}, output_id)
publish("assistant.completed" or "session.failed", {"reply": final_text, ...}, output_id)
```

以“先说一句再调工具”为例，事件顺序是：

```text
第 1 轮  token(out_A,"我先") token(out_A,"查一下。") → tool.started → worker.* → tool.completed
第 2 轮  token(out_B,"找到了") token(out_B," Gamma。")
结束     assistant.completed(out_B, reply="找到了 Gamma。")
```

前端收到 `out_B` 的第一条 token 时，把 `out_A` 定格为“中间回复”。

已知取舍：

- 模型中途出错时，已发出的部分文本仍留在界面上，随后兜底文案以新的 output_id 出现。
- 中间文本会被定格保留，需要靠 prompt 约束主 agent 不在工具调用前输出空洞客套话。

## 配置

`LLM_STREAM`（或 provider profile 的 `stream`）默认 `false`。关闭时 provider 走非流式请求，界面仍按上面的 output_id 规则展示，只是每段文本在调用结束后一次性出现。
