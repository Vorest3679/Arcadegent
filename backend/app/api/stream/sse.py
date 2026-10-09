"""Stream API layer: SSE encoding and the run event stream endpoint.

``format_sse`` and ``sse_response`` know nothing about runs or business
events and can serve any async source of StreamEvent/Heartbeat items. Every
frame uses the default ``message`` event name; the business label travels in the
JSON envelope. The endpoint subscribes to one named run of a session; the run
log ends the stream once the run is sealed and every event was delivered.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_container
from app.core.container import AppContainer
from app.session.run_log import Heartbeat, StreamEvent

router = APIRouter(tags=["stream"])

KEEP_ALIVE = ": keep-alive\n\n"


def format_sse(event: StreamEvent) -> str:
    body = json.dumps(event.to_json(), ensure_ascii=False)
    # Tell native EventSource clients a conservative reconnect delay. The web
    # client also applies its own bounded retry policy and replays by event id.
    return f"retry: 1000\nid: {event.id}\nevent: message\ndata: {body}\n\n"


def sse_response(source: AsyncIterator[StreamEvent | Heartbeat], *, request: Request) -> StreamingResponse:
    """Encode an async event source as an SSE response; stops when the client leaves."""

    async def iterator() -> AsyncIterator[str]:
        async for item in source:
            if await request.is_disconnected():
                return
            yield KEEP_ALIVE if isinstance(item, Heartbeat) else format_sse(item)

    return StreamingResponse(iterator(), media_type="text/event-stream")


def _parse_cursor(query_value: int | None, header_value: str | None) -> int | None:
    if query_value is not None:
        return query_value
    if isinstance(header_value, str):
        try:
            return int(header_value)
        except ValueError:
            return None
    return None


@router.get("/api/stream/{session_id}")
async def stream(
    session_id: str,
    request: Request,
    client_id: str | None = Query(default=None, min_length=1, max_length=128),
    run_id: str = Query(min_length=1, max_length=64),
    last_event_id: int | None = Query(default=None, ge=0),
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
    container: AppContainer = Depends(get_container),
) -> StreamingResponse:
    if client_id is not None and container.session_store.get_session(session_id, client_id=client_id) is None:
        raise HTTPException(status_code=404, detail=f"session '{session_id}' not found")

    run_log = container.run_log
    if not run_log.has_run(session_id, run_id):
        raise HTTPException(status_code=404, detail=f"run '{run_id}' of session '{session_id}' not found")

    source = run_log.subscribe(
        session_id,
        run_id,
        after_id=_parse_cursor(last_event_id, last_event_id_header),
        heartbeat_seconds=container.settings.sse_keepalive_seconds,
    )
    return sse_response(source, request=request)
