# ReAct ランタイムの中核ロジック

`ReactRuntime` が 1 回の対話を実行する主な流れを説明します。対応ソース：`backend/app/agent/runtime/react_runtime.py`、`backend/app/agent/llm/provider_adapter.py`、`backend/app/agent/llm/streaming.py`。run の受付・キャンセルと SSE の配信については [セッション run のライフサイクルと SSE](./会话运行生命周期与SSE.md) を参照してください。

> 現時点では主 agent ループとモデル出力の配信方法のみを記載しています。worker、コンテキスト構築、ツールメモリなどは今後追記します。

## 主 agent ループ

各ループではまずモデルを 1 回呼び出します。その呼び出しが完全に終わってから、ツールを実行するか、ループを終えるか、エラーで抜けるかを決めます。

```python
# _run_main_agent（簡略版）
while not guard.exhausted:
    output_id = _new_output_id()                 # モデル呼び出しごとに新しい出力
    response, published = await self._call_main_model(output_id=output_id, ...)
    self._record_model_call(...)                 # transcript / usage / ツール引数の evidence

    if response.error is not None:
        break                                    # モデルエラー：この呼び出しのツール呼び出しは実行しない
    if response.tool_calls:
        await self._execute_tool_calls(...)      # invoke_worker → _run_worker を含む
        if memory.get("reply"):                  # summary_tool が書き込んだ応答
            final_text = memory["reply"]; break
        continue                                 # 次のループへ、新しい output_id
    if response.text:
        final_text = response.text.strip()
        final_output_id = output_id if published else None
        break
```

- ツールは `_call_main_model` が戻った後にのみ実行されます。その時点でモデル呼び出しは完全に終わっており、ツール引数は組み立て済みで、解析も 1 回だけです。ストリーミングで変わるのはテキストを送るタイミングだけで、ループの構造は変わりません。
- worker（`_run_worker`）は常に `complete()` を呼びます。worker のテキストは evidence にのみ記録され、`assistant.token` としては配信されません。

## 1 回のモデル呼び出し：`_call_main_model`

```python
async def _call_main_model(self, *, output_id, **request):
    if not adapter.streaming:                    # LLM_STREAM=false
        response = await adapter.complete(**request)
        if response.error is None and text:      # 呼び出し完了後にテキスト全体を 1 回で配信
            publish("assistant.token", {"delta": text, "stream_mode": "synthetic"}, output_id)
        return response, published

    async with aclosing(adapter.stream(**request)) as events:   # LLM_STREAM=true
        async for event in events:
            if isinstance(event, TextDelta):     # 受信しながら配信
                publish("assistant.token", {"delta": event.text, "stream_mode": "provider"}, output_id)
            elif isinstance(event, StreamDone):
                final = event.response           # complete() と同じ形の ModelResponse
    return final, published
```

`adapter.stream()` は 4 種類のイベントを返し、配信されるのは `TextDelta` だけです。

| イベント | 処理 |
| --- | --- |
| `TextDelta(text)` | 現在の `output_id` ですぐに配信 |
| `ReasoningDelta(text)` | 配信せず、evidence にのみ記録 |
| `ToolCallDelta(index, ...)` | 配信せず、provider 層が index ごとに組み立てる |
| `StreamDone(response)` | 最後のイベント。収集済みの完全な結果を持つ |

run がキャンセルされると、`async for` の位置で `CancelledError` が送出されます。`aclosing` がジェネレーターを閉じ、その中の `httpx` ストリームも閉じられるため、上流へのリクエストが止まります。

## 最終応答と output_id

```python
# _run_chat_session（簡略版）
final_text, model_error, output_id = await self._run_main_agent(...)
if not final_text:
    final_text, output_id = self._fallback_reply(...), None
if output_id is None:                            # summary_tool の応答またはフォールバック文言
    output_id = _new_output_id()
    publish("assistant.token", {"delta": final_text, "stream_mode": "synthetic"}, output_id)
publish("assistant.completed" or "session.failed", {"reply": final_text, ...}, output_id)
```

たとえば「一言述べてからツールを呼ぶ」場合、イベントの順序は次のとおりです。

```text
ループ 1  token(out_A,"まず") token(out_A,"調べます。") → tool.started → worker.* → tool.completed
ループ 2  token(out_B,"Gamma が") token(out_B,"見つかりました。")
終了      assistant.completed(out_B, reply="Gamma が見つかりました。")
```

フロントエンドは `out_B` の最初の token を受け取った時点で、`out_A` を「中間応答」として確定表示します。

既知のトレードオフ：

- モデルが途中で失敗した場合、送信済みの部分テキストは画面に残り、その後フォールバック文言が新しい output_id で表示されます。
- 中間テキストは画面に残るため、ツール呼び出しの前に中身のない前置きを出さないよう、主 agent の prompt で制約する必要があります。

## 設定

`LLM_STREAM`（または provider profile の `stream`）の既定値は `false` です。無効の場合、provider は非ストリーミングのリクエストを使います。画面表示は上記の output_id の規則に従い、各テキストは呼び出しの完了時にまとめて表示されます。
