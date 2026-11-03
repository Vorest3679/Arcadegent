"""Event catalog of the run stream: the names a run may publish.

Each name lists the `data` fields the web client reads; further fields may
be added, existing ones are not renamed without updating
`apps/web/src/types.ts` (`ChatRunEventName`) in the same change.

Business events (`kind="event"`) come from three sources:

- `RunEvent`: published by the agent kernel; the names are defined once in
  `app.agent.runtime.events.KernelEvent` and re-exported here.
- `AppEvent`: published by an application domain under a name the web client
  already reads (`navigation.route_ready`).
- `domain.<namespace>.<name>`: any further domain event (built with
  `app.agent.runtime.events.domain_event`). The catalog accepts the whole
  namespace without listing each name; the web client drops names it does
  not know, so a domain adds client support when it needs one.

Payloads read by the web client:

- `session.started`: `model`, `active_subagent`, plus optional domain fields
  (the arcade domain supplies `intent`).
- `subagent.changed`: `active_subagent`, `to_subagent`, `reason`,
  optional `from_subagent`, `worker_run_id`.
- `worker.started`: `worker`, `run_id`, `worker_run_id` (same value; the run id
  is the stable discriminator when several workers overlap),
  `task_preview`, `active_subagent`.
- `worker.completed`: `worker`, `run_id`, `worker_run_id`, `status`,
  `summary`, `active_subagent` (the next active agent; `main_agent` once the
  last worker finishes).
- `worker.failed`: `worker`, `run_id`, `worker_run_id`, `error`,
  `active_subagent`.
- `tool.started` / `tool.completed`: `tool`, `call_id`, `active_subagent`,
  `worker_run_id` (`tool.completed` may add `distance_m`).
- `tool.failed`: as above plus `error`.
- `navigation.route_ready`: the route summary (`provider`, `mode`,
  `distance_m`, `duration_s`, `origin`, `destination`, `polyline`, `hint`).
- `domain.arcade.results_ready`: `shops`, the shop summaries committed by
  `result_selection_tool` (at most 20, catalog/cached coordinates only).
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

from app.agent.runtime.events import KernelEvent as RunEvent
from app.agent.runtime.events import is_domain_event

__all__ = ["AppEvent", "ControlEvent", "RunEvent", "is_known_event"]


class AppEvent(StrEnum):
    NAVIGATION_ROUTE_READY = "navigation.route_ready"


class ControlEvent(StrEnum):
    RUN_STATE = "run.state"
    STREAM_RESET = "stream.reset"


def is_known_event(kind: str, event: str) -> bool:
    """Whether `event` belongs to the catalog of the given envelope kind."""
    if kind == "event":
        return (
            event in RunEvent._value2member_map_
            or event in AppEvent._value2member_map_
            or is_domain_event(event)
        )
    if kind == "control":
        return event in ControlEvent._value2member_map_
    return False
