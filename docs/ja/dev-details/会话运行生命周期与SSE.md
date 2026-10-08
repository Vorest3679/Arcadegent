# セッション run のライフサイクルと SSE

1 回の Agent 対話（run）がどのように受け付け・実行・キャンセル・終了されるか、また SSE ストリームがどのように再送・終了するかを説明します。対応ソース：`backend/app/session/`、`backend/app/services/chat_run_service.py`、`backend/app/api/stream/sse.py`。

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

`POST /api/chat/sessions/{id}/cancel?run_id=...`：

- 最初のリクエストだけが task をキャンセルし、後続リクエストは同じ終了処理を待って最初の理由を保持します。
- 応答時点で task は停止し、セッションストアへの書き込みと記録の封印が完了しています。呼び出し側が切断しても終了処理は中断されません。
- 対象が終了済みなら現在の状態を返します。`run_id` が有効な run でない場合は 409 を返し、新しい run に影響しません。
- `run_id` を省略すると、リクエスト時点の現在の run をキャンセルします（既存フロントエンド向け）。
- 開始前にキャンセルされた task は完了コールバックで終了処理を補い、占有が残りません。
- 実行がすでに終わり終了処理が始まった後にキャンセルが届いた場合は task を中断せず、run は実際の結果（completed または failed）を記録します。状態の並びは cancelling → completed/failed です。
- 終了処理のどこかで例外が起きても、終状態イベント・封印・占有解除は必ず実行され、SSE が未封印のまま残りません。

停止時：SIGTERM/SIGINT を受けた時点で drain を開始し（新しい run を受け付けず、有効な run をキャンセルして終了を待つ）、有効な SSE は終状態を受け取って自然に終了してから、サーバーが接続を閉じます。lifespan shutdown は同じ drain を再利用します。Docker の起動コマンドに `--timeout-graceful-shutdown 15`、compose に `stop_grace_period: 30s` を設定し、上限を保証しています。

## SSE エンベロープと終了条件

`GET /api/stream/{session_id}?run_id=&last_event_id=`（`Last-Event-ID` ヘッダーも読みます）：

```json
{"id": 4, "session_id": "s_x", "run_id": "r_x", "kind": "event", "event": "assistant.token",
 "output_id": "out_x", "at": "...", "data": {"delta": "追加テキスト"}}
```

- `id` は run 内で 1 から増加し、control と業務イベントで同じ連番を使います。`kind` は `event` または `control`。
- control イベント：`run.state`（`status` 付き）、`stream.reset`（要求された cursor が破棄済み。`data.head_id` は現在の最大番号。クライアントはセッション詳細を再取得して続行します）。
- 記録が封印され、すべて送信された時点で接続を終了します。`assistant.completed` などの業務イベント名には依存しません。run のないセッションは即座に終了し、存在しない `run_id` は 404 です。
- 待機中は `SSE_KEEPALIVE_SECONDS` ごとに `: keep-alive` コメントを送ります。
- セッションごとに最新 run の記録のみを保持し、上限は `REPLAY_BUFFER_SIZE` 件（既定 2000）です。新しい run が始まると以前の run は購読できません。
- `assistant.token` は増分 `delta` のみを持ち、同じ応答の断片は `output_id` を共有します。`assistant.completed` は同じ `output_id` と完全な `reply` を持ちます。断片は現在も完全な応答の生成後にローカルで分割しています（`stream_mode: synthetic`）。

SSE フレームの `event:` 行は現在も業務イベント名と同じです。フロントエンドが run 単位で購読するようになった段階で固定名に統一します。

## 状態の出どころ

- 有効な run の有無、セッション削除の可否、SSE の終了時期、詳細の `current_run` は `RunManager` だけを参照します。
- セッションストアの `status` は一覧・詳細表示用の履歴ラベルです。有効な run がある間、API は `running` を表示します。
- キャンセルや実行失敗の場合、`ChatRunService` が終了フックでセッションを failed とし、次の入力のためにコンテキストを保持します。

## 制限

- run の登録とイベント記録はプロセスメモリ上にあります。単一プロセスのみ有効で、レプリカ間の排他や共有再送はなく、再起動後に実行中の run は再開できません。
- セッションストアは現在も同期呼び出しです。task が完全に終了し終了フックの保存が終わるまでセッションを解放しないため、run 単位の書き込みガードは不要です。ストアを非同期化またはスレッドに逃がす場合はガードを追加する必要があります。
- runtime は `ContextVar` で現在の run の発行口を受け取り、非同期の子タスクにも引き継がれます。
