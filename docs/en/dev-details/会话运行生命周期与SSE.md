# Session Run Lifecycle and SSE

This note explains how one Agent conversation turn (a run) is accepted, executed, cancelled, and finished, and how the SSE stream replays and ends. Source: `backend/app/session/`, `backend/app/services/chat_run_service.py`, `backend/app/api/stream/sse.py`.

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

`POST /api/chat/sessions/{id}/cancel?run_id=...`:

- Only the first request cancels the task; later requests wait for the same finish and keep the first reason.
- When it returns, the task has stopped, the session store is written, and the book is sealed; a caller that disconnects does not interrupt finishing.
- A finished target returns its current state; a `run_id` that is not the active run returns 409 and leaves the new run untouched.
- Without `run_id`, the run that is current at request time is cancelled (for the existing frontend).
- A task cancelled before it starts is finished by its done callback, so no reservation is left behind.
- If the cancel arrives after execution has ended (finishing already started), the task is not interrupted and the run records its actual outcome, completed or failed; the status sequence is cancelling → completed/failed.
- If any finishing step fails, the terminal event, sealing, and release still happen, so SSE never stays unsealed.

Shutdown: on SIGTERM/SIGINT draining starts immediately (no new runs, active runs are cancelled and awaited), so active SSE streams receive their terminal events and end before the server closes connections; the lifespan shutdown reuses the same drain. The Docker command sets `--timeout-graceful-shutdown 15` and compose sets `stop_grace_period: 30s` as a backstop.

## SSE envelope and termination

`GET /api/stream/{session_id}?run_id=&last_event_id=` (the `Last-Event-ID` header is also read):

```json
{"id": 4, "session_id": "s_x", "run_id": "r_x", "kind": "event", "event": "assistant.token",
 "output_id": "out_x", "at": "...", "data": {"delta": "new text"}}
```

- `id` increases from 1 within a run; control and business events share the sequence. `kind` is `event` or `control`.
- Control events: `run.state` (with `status`) and `stream.reset` (the requested cursor was evicted; `data.head_id` is the current highest id; the client should reload the session detail and continue).
- The connection ends once the book is sealed and fully delivered, independent of business event names such as `assistant.completed`. A session without a run ends at once; an unknown `run_id` returns 404.
- While waiting, a `: keep-alive` comment is sent every `SSE_KEEPALIVE_SECONDS`.
- Only the latest run's book is kept per session, capped at `REPLAY_BUFFER_SIZE` events (default 2000); a previous run cannot be subscribed to after a new one starts.
- `assistant.token` carries only the incremental `delta`; chunks of one reply share an `output_id`, and `assistant.completed` carries the same `output_id` with the full `reply`. The chunks are still produced locally after the full reply is available (`stream_mode: synthetic`).

The SSE frame's `event:` line still equals the business event name; it will become a fixed frame name once the frontend subscribes per run.

## Source of status

- Whether a run is active, whether a session can be deleted, when SSE ends, and `current_run` in the detail come only from `RunManager`.
- The stored session `status` is a history label for the list and detail; while a run is active the API shows `running`.
- For cancelled or failed executions, `ChatRunService` marks the session failed in the terminal hook and keeps its context for the next input.

## Limitations

- The run registry and event books live in process memory: single process only, no cross-replica exclusion or shared replay, and a run in progress cannot resume after a restart.
- The session store is still synchronous. No run-level write guard is needed because the session is released only after the task has fully ended and the terminal hook has persisted; an asynchronous or thread-offloaded store must add one.
- The runtime gets the current run's publisher through a `ContextVar`, inherited by asynchronous child tasks.
