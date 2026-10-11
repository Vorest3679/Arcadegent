# セッション run のライフサイクルと SSE

1 回の Agent 対話（run）がどのように受け付け・実行・キャンセル・終了されるか、SSE ストリームがどのように再送・終了するか、フロントエンドがどのように購読するかを説明します。対応ソース：`backend/app/session/`、`backend/app/services/chat_run_service.py`、`backend/app/api/stream/sse.py`、`apps/web/src/lib/sse/runStream.ts`。

## モジュール構成

| ファイル | 役割 |
| --- | --- |
| `session/models.py` | run の状態（pending / running / cancelling / completed / failed / cancelled）、`RunRecord`、状態遷移関数 `transition`、エラー型 |
| `session/injector.py` | runtime に渡す入口：`RunPublisher`、`RunContext`、`RunExecutor`、`TerminalHook`、評価・テスト用の `CollectingPublisher` |
| `session/run_log.py` | `RunLog`：セッションごとに最新 run のイベント記録を 1 冊保持し、run 内の採番・再送・待機・封印を行う |
| `session/runs.py` | `RunManager`：1 セッションにつき有効な run は 1 つ。task、キャンセル、停止時の drain、終了処理を担当 |
| `services/chat_run_service.py` | アプリケーション層の接着：所有者チェック、`ReactRuntime.run_chat` を run として包む、キャンセル・失敗時にセッションストアへ記録 |

`session/` は agent、protocol、services、データベース実装に依存せず、イベント内容も解釈しません。runtime は `RunPublisher.publish(event, data, output_id=)` だけでイベントを発行します。

## run のライフサイクル

1. `POST /api/chat/sessions`：`ChatRunService` がセッションの使用中・所有者を確認して running に設定し、`RunManager.dispatch` を呼びます。`r_xxx` を採番し、新しい記録を開き、バックグラウンド task を作成して、`run_id` を含む 202 をすぐ返します。
2. task 開始時に状態が running になり、control イベント `run.state` が書き込まれます。runtime の業務イベント（`tool.*`、`assistant.token` など）が順に続きます。
3. 終了処理は run ごとに一度だけ、常に 5 段階です：終状態に変更 → `TerminalHook` を呼ぶ（アプリケーションが保存し、必要に応じて `session.failed` を発行）→ 終状態の `run.state` を発行 → 記録を封印 → セッションの占有を解除。
4. 保存に失敗しても終状態は変わらず、`error_code=persist_failed` のみ記録します。executor が例外を出した run は failed（`error_code=executor_failed`）とし、例外原文は外部に出しません。

run の状態は実行結果だけを表します。モデルエラーやフォールバック応答などの業務上の失敗は completed の run であり、`session.failed` イベントとセッション詳細の `status/last_error` で表現します。

## キャンセル

`POST /api/chat/sessions/{id}/runs/{run_id}/cancel`：

- 最初のリクエストだけが task をキャンセルし、後続リクエストは同じ終了処理を待って最初の理由を保持します。
- 応答時点で task は停止し、セッションストアへの書き込みと記録の封印が完了しています。呼び出し側が切断しても終了処理は中断されません。
- 対象が終了済みなら現在の状態を返します。`run_id` が有効な run でない場合は 409 を返し、新しい run に影響しません。
- 開始前にキャンセルされた task は完了コールバックで終了処理を補い、占有が残りません。
- 実行がすでに終わり終了処理が始まった後にキャンセルが届いた場合は task を中断せず、run は実際の結果（completed または failed）を記録します。状態の並びは cancelling → completed/failed です。
- 終了処理のどこかで例外が起きても、終状態イベント・封印・占有解除は必ず実行され、SSE が未封印のまま残りません。

停止時：SIGTERM/SIGINT を受けた時点で drain を開始し（新しい run を受け付けず、有効な run をキャンセルして終了を待つ）、有効な SSE は終状態を受け取って自然に終了してから、サーバーが接続を閉じます。lifespan shutdown は同じ drain を再利用します。Docker の起動コマンドに `--timeout-graceful-shutdown 15`、compose に `stop_grace_period: 30s` を設定し、上限を保証しています。

## SSE エンベロープと終了条件

`GET /api/stream/{session_id}?run_id=&last_event_id=`（`run_id` は必須。`Last-Event-ID` ヘッダーも読みます）。各フレームの `event:` 行は常に `message` で、業務イベント名は JSON エンベロープの `event` フィールドにのみ入ります：

```json
{"id": 4, "session_id": "s_x", "run_id": "r_x", "kind": "event", "event": "assistant.token",
 "output_id": "out_x", "at": "...", "data": {"delta": "追加テキスト"}}
```

- `id` は run 内で 1 から増加し、control と業務イベントで同じ連番を使います。`kind` は `event` または `control`。
- control イベント：`run.state`（`status` 付き）、`stream.reset`（要求された cursor が破棄済み。`data.head_id` は現在の最大番号。クライアントはセッション詳細を再取得して続行します）。
- 記録が封印され、すべて送信された時点で接続を終了します。`assistant.completed` などの業務イベント名には依存しません。記録にない `run_id`（未実行、新しい run に置き換え済み、再起動で消失）は 404 です。
- 待機中は `SSE_KEEPALIVE_SECONDS` ごとに `: keep-alive` コメントを送ります。
- セッションごとに最新 run の記録のみを保持し、上限は `REPLAY_BUFFER_SIZE` 件（既定 2000）です。新しい run が始まると以前の run は購読できません。
- `assistant.token` は増分 `delta` のみを持ち、同じ応答の断片は `output_id` を共有します。`assistant.completed` は同じ `output_id` と完全な `reply` を持ちます。主 agent のモデル呼び出しごとに `output_id` が 1 つ割り当てられます。`LLM_STREAM=true` のときは、モデルの生成に合わせて断片をリアルタイムに送信します（`stream_mode: provider`）。それ以外では、呼び出しの完了後にテキスト全体を 1 回で送信します（`stream_mode: synthetic`）。詳しくは [ReAct ランタイムの中核ロジック](./ReAct运行时核心逻辑.md) を参照してください。

## フロントエンドの購読

- `apps/web/src/lib/sse/runStream.ts` の `openRunStream` は 1 つの run だけを購読し、React に依存しません。エンベロープを検証し（`session_id`/`run_id` が購読対象と一致しないフレームは破棄）、受信済み最大 id 以下の再送フレームを捨て、切断時は 500ms×2ⁿ のバックオフで `last_event_id` 付きで最大 3 回再接続し、終端の `run.state` を受けたら再接続せずに閉じます。
- テキストは `output_id` ごとに `delta` を追記します。新しい `output_id` が来ると直前の出力を**確定**（`onOutputSealed`）し「途中の返信」として表示し、新しい出力は別に表示します。メイン agent の各モデル呼び出しは独自の `output_id` を持ち、run の終了と詳細の再取得後は、これらの途中の返信を詳細の `steps` が引き継ぎます（次節）。
- run がまだライブ表示中かどうかは store の `activeRunId` / `committedRunId` で決まります。終端後にセッション詳細を再取得し、`committedRunId` と同じ更新で書き込むため、ストリーミング吹き出しと進捗カードは 1 回の更新で履歴メッセージに切り替わります。テキストの接頭辞や長さによる重複推測は行いません。
- ページ再読み込み時、詳細の `current_run` が未終了ならカーソルなしで購読して先頭から再送します。`stream.reset` を受けたら詳細を再取得して購読を続けます。
- 再接続が 3 回失敗した場合、詳細を取得して run がまだ実行中であることを確認してから `run_id` 指定でキャンセルします。

### フロントエンドの呼び出し経路

```text
App.tsx
  └─ useChatSessionController()            マウント時にセッション一覧を取得し、submitChat などのコールバックをコンポーネントに返す
       ├─ submitChat → dispatchChatSession → run_id を受け取る → startStream(sessionId, runId)
       ├─ 再読み込み／セッション切替 → loadSession → applySessionDetail → current_run が未終了 → startStream
       └─ startStream → openRunStream({ url, sessionId, runId, handlers })
            └─ connect() → new EventSource(url(lastId))
                 └─ addEventListener("message", handleMessage)
                      ├─ parseEnvelope：エンベロープを検証し、session_id/run_id が異なれば破棄
                      ├─ id <= lastId：再送フレームを破棄
                      ├─ control：stream.reset → onReset；run.state → onState（終端なら先に close）
                      └─ event：assistant.token → applyToken（onText / onOutputSealed）、その後 onEvent
```

**1. App.tsx は controller をマウントするだけ**で、SSE はコンポーネントで扱いません：

```tsx
// apps/web/src/App.tsx
const chat = useChatSessionController();
// ...
<ChatPanel onSubmit={chat.submitChat} streamReply={chat.streamReply} ... />
```

**2. controller が購読を開始して handlers を登録**します。各コールバックは store への書き込みか詳細の再取得だけを行います：

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
      onEvent: (envelope) => handleRunEvent(sessionId, envelope), // 業務イベント → 段階・地図・進捗カード
      onText: (_outputId, text, delta) => appendStreamReply(text, delta), // ストリーミング吹き出し
      onOutputSealed: (outputId, text) => { /* sealedOutputs に追加（途中の返信） */ },
      onState: (status) => { /* 終端 → loadSession、詳細到着後に履歴へ引き継ぎ */ },
      onReset: () => { /* 詳細を再取得し購読を継続 */ },
      onConnection: (connected) => store.setStreamConnected(connected),
      onRetry: recordStreamReconnect,
      onGiveUp: () => { void giveUpRun(sessionId, runId); } // 実行中を確認してから run_id でキャンセル
    }
  });
  streamRef.current = stream;
}
```

**3. runStream は connect() で handleMessage を登録**し、転送層の処理後に handlers へ渡します：

```ts
// apps/web/src/lib/sse/runStream.ts
function connect(): void {
  const current = new EventSource(url(lastId));      // 再接続時は last_event_id 付き
  source = current;
  current.addEventListener("message", (event) => handleMessage(event as MessageEvent<string>, current));
  current.onerror = () => { /* 閉じる。回数内なら 500ms×2ⁿ 後に connect()、超えたら onGiveUp */ };
}

function handleMessage(message: MessageEvent<string>, current: EventSource): void {
  if (closed || source !== current || !message.data) return;          // 古い接続の遅延フレーム
  const envelope = parseEnvelope(message.data, sessionId, runId);
  if (!envelope) return;                                               // 不正なエンベロープ、または別の run
  if (lastId !== undefined && envelope.id <= lastId) return;           // 再送の重複除去
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
    handlers.onOutputSealed(outputId, outputText);                     // 直前の出力を途中の返信として確定
    outputText = "";
  }
  outputId = nextOutputId;
  outputText += delta;
  handlers.onText(nextOutputId, outputText, delta);
}
```

責務の境界：`runStream.ts` はエンベロープ・カーソル・再接続・output_id ごとのテキスト組み立てだけを扱い、tool/worker/route などの業務イベントは知りません。業務上の意味は controller の `handleRunEvent` と `lib/sse/chatStream.ts`（`mapArtifactsForEvent`、`toProgressText`）で解釈し、コンポーネントは store を読むだけです。

## ラウンドのステップと履歴の復元

途中の返信が SSE で見えるのは run の実行中だけです。run が終わるとフロントエンドがセッション詳細を再取得し、過程は詳細 API から得ます。ページを再読み込みしたときも同じ方法で復元します。

- **保存**：メイン agent の各モデル呼び出しのテキストと各ツール記録は、すでに `chat_sessions.turns` に永続化されています（モデルの証拠は `payload.model` にあり、チャット UI は直接使いません）。新しいテーブルは不要です。
- **投影**：`GET /api/chat/sessions/{id}` の各 user ターンは `steps`（`api/http/chat.py` の `_round_steps`）を持ちます。そのユーザーメッセージから次のユーザーメッセージまでに起きたことを、発生順に並べます：
  - `{"kind": "text", "agent", "content", "created_at"}`：メイン agent がツール呼び出しと一緒に書いたテキスト（途中の返信）。ツール呼び出しのない呼び出しが最終返信で、assistant ターン自体が表すため重ねて投影しません。
  - `{"kind": "tool", "call_id", "name", "agent", "status", "created_at"}`：1 回のツール呼び出し。`status` は `completed` または `failed`。worker 内のツール呼び出しも含まれ、`agent` は worker 名です。
- **送らないもの**：モデルの証拠（transcript、usage）、ツールの引数と結果、worker のモデルテキスト。
- **フロントエンド**：`ChatPanel` は履歴メッセージ、`steps` の途中の返信、ライブのストリーミング吹き出しを 1 つの key 付きリストに入れます。履歴とライブは同じ key を使うため、引き継ぎ時に DOM ノードを再利用します（再マウント、入場アニメーションの再生、地図カードの再読み込みなし）。実行中の再読み込みでは、保存済みの steps を先に表示し、その後の購読の先頭からの再送でライブの途中の返信が引き継ぎ、重複しません。
- **バックグラウンドの再取得では読み込みバナーを出しません**：引き継ぎや `stream.reset` による詳細の再取得は `preserveStreamState` を使い、内容だけを更新して「セッションを読み込み中」を表示しません。
- **詳細を取得できない場合のフォールバック**：`commitRunLocally` が `assistant.completed` の最終返信とライブの途中の返信からローカルに履歴を作り、引き継ぎの失敗で返信が消えないようにします。
- 既知の制限：ツールのステップは今のところデータとして返すだけで、画面にはまだ表示しません（計画 2 の Phase 3）。キャンセルされた run でまだ書き込まれていないツール記録は見えません。

## 状態の出どころ

- 有効な run の有無、セッション削除の可否、SSE の終了時期、詳細の `current_run` は `RunManager` だけを参照します。
- セッションストアの `status` は一覧・詳細表示用の履歴ラベルです。有効な run がある間、API は `running` を表示します。
- キャンセルや実行失敗の場合、`ChatRunService` が終了フックでセッションを failed とし、次の入力のためにコンテキストを保持します。

## 制限

- run の登録とイベント記録はプロセスメモリ上にあります。単一プロセスのみ有効で、レプリカ間の排他や共有再送はなく、再起動後に実行中の run は再開できません。
- セッションストアは現在も同期呼び出しです。task が完全に終了し終了フックの保存が終わるまでセッションを解放しないため、run 単位の書き込みガードは不要です。ストアを非同期化またはスレッドに逃がす場合はガードを追加する必要があります。
- runtime は `ContextVar` で現在の run の発行口を受け取り、非同期の子タスクにも引き継がれます。
