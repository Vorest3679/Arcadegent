"""Event catalog of the run stream: the closed set of names a run may publish.

Each name lists the `data` fields the web client reads; further fields may
be added, existing ones are not renamed without updating
`apps/web/src/types.ts` (`ChatRunEventName`) in the same change.

Business events (`kind="event"`) are published by the runtime:

- `session.started`: `intent`, `model`, `active_subagent`.
- `subagent.changed`: `active_subagent`, `to_subagent`, `reason`,
  optional `from_subagent`, `worker_run_id`.
- `worker.started`: `worker`, `run_id`, `task_preview`, `active_subagent`.
- `worker.completed`: `worker`, `run_id`, `status`, `summary`, `active_subagent`.
- `worker.failed`: `worker`, `run_id`, `error`, `active_subagent`.
- `tool.started` / `tool.completed`: `tool`, `call_id`, `active_subagent`,
  `worker_run_id` (`tool.completed` may add `distance_m`).
- `tool.failed`: as above plus `error`.
- `navigation.route_ready`: the route summary (`provider`, `mode`,
  `distance_m`, `duration_s`, `origin`, `destination`, `polyline`, `hint`).
- `assistant.token`: `delta` (appended per `output_id`), `active_subagent`,
  `stream_mode`; carries `output_id` on the envelope.
- `assistant.completed`: `reply`, `active_subagent`, `reply_source`,
  `model_error`, `error`; terminal reply of the run.
- `session.failed`: `error`, `active_subagent`, and `reply` when a
  fallback reply was stored.

Control events (`kind="control"`) are published by the run machinery:

- `run.state`: envelope `status` is the run status; `data.error_code` on failure.
- `stream.reset`: the subscriber's cursor fell behind the bounded log;
  reload the session detail. `data.head_id` is the newest event id.
"""

from __future__ import annotations

from enum import StrEnum


class RunEvent(StrEnum):
    SESSION_STARTED = "session.started"
    SUBAGENT_CHANGED = "subagent.changed"
    WORKER_STARTED = "worker.started"
    WORKER_COMPLETED = "worker.completed"
    WORKER_FAILED = "worker.failed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    NAVIGATION_ROUTE_READY = "navigation.route_ready"
    ASSISTANT_TOKEN = "assistant.token"
    ASSISTANT_COMPLETED = "assistant.completed"
    SESSION_FAILED = "session.failed"


class ControlEvent(StrEnum):
    RUN_STATE = "run.state"
    STREAM_RESET = "stream.reset"


def is_known_event(kind: str, event: str) -> bool:
    """Whether `event` belongs to the catalog of the given envelope kind."""
    catalog = RunEvent if kind == "event" else ControlEvent if kind == "control" else None
    return catalog is not None and event in catalog._value2member_map_
