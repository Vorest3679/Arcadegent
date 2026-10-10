# Session Run Lifecycle and SSE

This note explains how one Agent conversation turn (a run) is accepted, executed, cancelled, and finished, how the SSE stream replays and ends, and how the frontend subscribes. Source: `backend/app/session/`, `backend/app/services/chat_run_service.py`, `backend/app/api/stream/sse.py`, `apps/web/src/lib/runStream.ts`.

## Modules

| File | Responsibility |
| --- | --- |
| `session/models.py` | Run statuses (pending / running / cancelling / completed / failed / cancelled), `RunRecord`, the transition function `transition`, and error types |
| `session/injector.py` | Entry points handed to the runtime: `RunPublisher`, `RunContext`, `RunExecutor`, `TerminalHook`, plus `CollectingPublisher` for evaluation and tests |
| `session/run_log.py` | `RunLog`: one event book per session for its latest run; per-run numbering, replay, waiting, sealing |
| `session/runs.py` | `RunManager`: at most one active run per session; owns tasks, cancellation, shutdown drain, and finishing |
| `services/chat_run_service.py` | Application glue: owner-scope check, wraps `ReactRuntime.run_chat` as a run, records cancelled/failed runs in the session store |

`session/` does not depend on agent, protocol, services, or database code and never interprets event payloads. The runtime only publishes through `RunPublisher.publish(event, data, output_id=)`.

## Lifecycle of a run

1. `POST /api/chat/sessions`: `ChatRunService` checks that the session is free and owned by the caller, marks it running, and calls `RunManager.dispatch`, which allocates `r_xxx`, opens a new event book for the session, creates a background task, and returns 202 with the `run_id`.
2. When the task starts, the run becomes running and a control `run.state` event is written; business events from the runtime (`tool.*`, `assistant.token`, …) follow in order.
3. Finishing always takes five steps, exactly once per run: set the terminal status → call the `TerminalHook` (the application persists state and may publish `session.failed`) → publish the terminal `run.state` → seal the book → release the session.
4. A persistence failure keeps the terminal status and only sets `error_code=persist_failed`. A run whose executor raised is failed with `error_code=executor_failed`; raw exception text is never exposed.

Run status only describes execution. Business failures (model error, fallback reply) are completed runs; they are expressed by the `session.failed` event and the session detail's `status/last_error`.

## Cancellation

`POST /api/chat/sessions/{id}/runs/{run_id}/cancel`:

- Only the first request cancels the task; later requests wait for the same finish and keep the first reason.
- When it returns, the task has stopped, the session store is written, and the book is sealed; a caller that disconnects does not interrupt finishing.
- A finished target returns its current state; a `run_id` that is not the active run returns 409 and leaves the new run untouched.
- A task cancelled before it starts is finished by its done callback, so no reservation is left behind.
- If the cancel arrives after execution has ended (finishing already started), the task is not interrupted and the run records its actual outcome, completed or failed; the status sequence is cancelling → completed/failed.
- If any finishing step fails, the terminal event, sealing, and release still happen, so SSE never stays unsealed.

Shutdown: on SIGTERM/SIGINT draining starts immediately (no new runs, active runs are cancelled and awaited), so active SSE streams receive their terminal events and end before the server closes connections; the lifespan shutdown reuses the same drain. The Docker command sets `--timeout-graceful-shutdown 15` and compose sets `stop_grace_period: 30s` as a backstop.

## SSE envelope and termination

`GET /api/stream/{session_id}?run_id=&last_event_id=` (`run_id` is required; the `Last-Event-ID` header is also read). Every frame's `event:` line is `message`; the business event name is only in the envelope's `event` field:

```json
{"id": 4, "session_id": "s_x", "run_id": "r_x", "kind": "event", "event": "assistant.token",
 "output_id": "out_x", "at": "...", "data": {"delta": "new text"}}
```

- `id` increases from 1 within a run; control and business events share the sequence. `kind` is `event` or `control`.
- Control events: `run.state` (with `status`) and `stream.reset` (the requested cursor was evicted; `data.head_id` is the current highest id; the client should reload the session detail and continue).
- The connection ends once the book is sealed and fully delivered, independent of business event names such as `assistant.completed`. A `run_id` not in the event book (never ran, replaced by a newer run, or lost on restart) returns 404.
- While waiting, a `: keep-alive` comment is sent every `SSE_KEEPALIVE_SECONDS`.
- Only the latest run's book is kept per session, capped at `REPLAY_BUFFER_SIZE` events (default 2000); a previous run cannot be subscribed to after a new one starts.
- `assistant.token` carries only the incremental `delta`; chunks of one reply share an `output_id`, and `assistant.completed` carries the same `output_id` with the full `reply`. Each main-agent model call has its own `output_id`. With `LLM_STREAM=true`, chunks are published as the model generates them (`stream_mode: provider`); otherwise the whole text of a call is published once the call finishes (`stream_mode: synthetic`). See [ReAct Runtime Core Logic](./ReAct运行时核心逻辑.md).

## Frontend subscription

- `openRunStream` in `apps/web/src/lib/runStream.ts` subscribes to exactly one run and has no React dependency. It checks the envelope (`session_id`/`run_id` must match the subscription; other frames are dropped), drops replayed frames with `id <=` the highest id seen, reconnects with `last_event_id` after 500ms×2ⁿ backoff up to 3 times, and closes on the terminal `run.state` without reconnecting.
- Text appends `delta` per `output_id`. When a new `output_id` arrives the previous output is **sealed** (`onOutputSealed`) and shown as an intermediate reply; the new output is shown on its own. The backend currently emits tokens only for the final reply, so multiple outputs appear only once real provider streaming lands.
- Whether a run is still shown live is decided by `activeRunId` / `committedRunId` in the store: after the terminal state the session detail is reloaded and written together with `committedRunId`, so the streaming bubble and progress card hand over to the history turn in one update. Duplicates are no longer guessed from text prefixes or lengths.
- On page reload, if the detail's `current_run` is unfinished, the client subscribes to it without a cursor and replays it from the start; on `stream.reset` it reloads the detail and keeps the subscription.
- After 3 failed reconnects the client reloads the detail to confirm the run is still active, then cancels it by `run_id`.

### Frontend call chain

```text
App.tsx
  └─ useChatSessionController()            loads the session list on mount; returns callbacks such as submitChat to components
       ├─ submitChat → dispatchChatSession → receives run_id → startStream(sessionId, runId)
       ├─ reload / switch session → loadSession → applySessionDetail → current_run unfinished → startStream
       └─ startStream → openRunStream({ url, sessionId, runId, handlers })
            └─ connect() → new EventSource(url(lastId))
                 └─ addEventListener("message", handleMessage)
                      ├─ parseEnvelope：validate envelope; drop if session_id/run_id differ
                      ├─ id <= lastId：replayed frame, dropped
                      ├─ control：stream.reset → onReset；run.state → onState (closes first on terminal status)
                      └─ event：assistant.token → applyToken（onText / onOutputSealed）, then onEvent
```

**1. App.tsx only mounts the controller**; components never handle SSE:

```tsx
// apps/web/src/App.tsx
const chat = useChatSessionController();
// ...
<ChatPanel onSubmit={chat.submitChat} streamReply={chat.streamReply} ... />
```

**2. The controller starts the subscription and registers handlers**; each callback only writes the store or triggers a detail reload:

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
      onEvent: (envelope) => handleRunEvent(sessionId, envelope), // business events → stage, map, progress card
      onText: (_outputId, text, delta) => appendStreamReply(text, delta), // streaming bubble
      onOutputSealed: (outputId, text) => { /* append to sealedOutputs (intermediate reply) */ },
      onState: (status) => { /* terminal → loadSession; hand over to history once the detail arrives */ },
      onReset: () => { /* reload detail, keep subscription */ },
      onConnection: (connected) => store.setStreamConnected(connected),
      onRetry: recordStreamReconnect,
      onGiveUp: () => { void giveUpRun(sessionId, runId); } // cancel by run_id after confirming it still runs
    }
  });
  streamRef.current = stream;
}
```

**3. runStream attaches handleMessage in connect()**, handles transport concerns, then dispatches to handlers:

```ts
// apps/web/src/lib/runStream.ts
function connect(): void {
  const current = new EventSource(url(lastId));      // carries last_event_id on reconnect
  source = current;
  current.addEventListener("message", (event) => handleMessage(event as MessageEvent<string>, current));
  current.onerror = () => { /* close; connect() again after 500ms×2ⁿ, or onGiveUp when out of retries */ };
}

function handleMessage(message: MessageEvent<string>, current: EventSource): void {
  if (closed || source !== current || !message.data) return;          // late frame of an old connection
  const envelope = parseEnvelope(message.data, sessionId, runId);
  if (!envelope) return;                                               // invalid envelope or another run
  if (lastId !== undefined && envelope.id <= lastId) return;           // replay de-duplication
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
    handlers.onOutputSealed(outputId, outputText);                     // seal the previous output as an intermediate reply
    outputText = "";
  }
  outputId = nextOutputId;
  outputText += delta;
  handlers.onText(nextOutputId, outputText, delta);
}
```

Boundaries: `runStream.ts` only knows the envelope, cursor, reconnects, and assembling text per output_id; it does not know business events such as tool/worker/route. Their meaning is interpreted in the controller's `handleRunEvent` and in `lib/chatStream.ts` (`mapArtifactsForEvent`, `toProgressText`); components only read the store.

## Source of status

- Whether a run is active, whether a session can be deleted, when SSE ends, and `current_run` in the detail come only from `RunManager`.
- The stored session `status` is a history label for the list and detail; while a run is active the API shows `running`.
- For cancelled or failed executions, `ChatRunService` marks the session failed in the terminal hook and keeps its context for the next input.

## Limitations

- The run registry and event books live in process memory: single process only, no cross-replica exclusion or shared replay, and a run in progress cannot resume after a restart.
- The session store is still synchronous. No run-level write guard is needed because the session is released only after the task has fully ended and the terminal hook has persisted; an asynchronous or thread-offloaded store must add one.
- The runtime gets the current run's publisher through a `ContextVar`, inherited by asynchronous child tasks.
