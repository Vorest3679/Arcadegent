# ReAct Runtime Core Logic

This note explains the main execution path of one conversation in `ReactRuntime`. Source: `backend/app/agent/runtime/react_runtime.py`, `backend/app/agent/llm/provider_adapter.py`, `backend/app/agent/llm/streaming.py`. For how runs are accepted and cancelled and how SSE is delivered, see [Session Run Lifecycle and SSE](./会话运行生命周期与SSE.md).

> This note currently covers only the main-agent loop and how model output is published. Workers, context building, tool memory, and other topics will be added later.

## Main-agent loop

Each iteration first makes one model call. Only after that call has fully finished does the loop decide whether to run tools, finish, or exit on an error:

```python
# _run_main_agent (simplified)
while not guard.exhausted:
    output_id = _new_output_id()                 # each model call is a new output
    response, published = await self._call_main_model(output_id=output_id, ...)
    self._record_model_call(...)                 # transcript / usage / tool-argument evidence

    if response.error is not None:
        break                                    # model error: tool calls from this call are not run
    if response.tool_calls:
        await self._execute_tool_calls(...)      # includes invoke_worker → _run_worker
        if memory.get("reply"):                  # reply written by summary_tool
            final_text = memory["reply"]; break
        continue                                 # next iteration, new output_id
    if response.text:
        final_text = response.text.strip()
        final_output_id = output_id if published else None
        break
```

- Tools run only after `_call_main_model` returns. At that point the model call has fully finished, and tool arguments have been fully assembled and parsed exactly once. Streaming changes only when text is sent, not the structure of the loop.
- Workers (`_run_worker`) always call `complete()`. Their text goes into evidence only and is never published as `assistant.token`.

## One model call: `_call_main_model`

```python
async def _call_main_model(self, *, output_id, **request):
    if not adapter.streaming:                    # LLM_STREAM=false
        response = await adapter.complete(**request)
        if response.error is None and text:      # publish the whole text once the call ends
            publish("assistant.token", {"delta": text, "stream_mode": "synthetic"}, output_id)
        return response, published

    async with aclosing(adapter.stream(**request)) as events:   # LLM_STREAM=true
        async for event in events:
            if isinstance(event, TextDelta):     # publish while receiving
                publish("assistant.token", {"delta": event.text, "stream_mode": "provider"}, output_id)
            elif isinstance(event, StreamDone):
                final = event.response           # same ModelResponse shape as complete()
    return final, published
```

`adapter.stream()` yields four kinds of events, and only `TextDelta` is published:

| Event | Handling |
| --- | --- |
| `TextDelta(text)` | Published immediately under the current `output_id` |
| `ReasoningDelta(text)` | Not published; goes into evidence only |
| `ToolCallDelta(index, ...)` | Not published; assembled by index in the provider layer |
| `StreamDone(response)` | Last event; carries the fully collected result |

When a run is cancelled, `CancelledError` is raised at the `async for`. `aclosing` closes the generator, which closes the `httpx` stream inside it, so the upstream request stops.

## Final reply and output_id

```python
# _run_chat_session (simplified)
final_text, model_error, output_id = await self._run_main_agent(...)
if not final_text:
    final_text, output_id = self._fallback_reply(...), None
if output_id is None:                            # summary_tool reply or fallback text
    output_id = _new_output_id()
    publish("assistant.token", {"delta": final_text, "stream_mode": "synthetic"}, output_id)
publish("assistant.completed" or "session.failed", {"reply": final_text, ...}, output_id)
```

For example, when the agent says something and then calls a tool, the events are:

```text
Iteration 1  token(out_A,"Let me ") token(out_A,"check.") → tool.started → worker.* → tool.completed
Iteration 2  token(out_B,"Found ") token(out_B,"Gamma.")
End          assistant.completed(out_B, reply="Found Gamma.")
```

When the frontend receives the first token of `out_B`, it freezes `out_A` as an intermediate reply.

Known trade-offs:

- If the model fails mid-call, the partial text already sent stays on screen, and the fallback text then appears under a new output_id.
- Intermediate text is kept on screen, so the main-agent prompt must discourage empty filler text before tool calls.

## Configuration

`LLM_STREAM` (or `stream` in the provider profile) defaults to `false`. When it is off, the provider uses non-streaming requests. The UI still follows the output_id rules above; each text segment simply appears all at once when its call finishes.
