"""Streaming events and chunk collectors for OpenAI-compatible providers.

Collectors turn protocol-specific stream payloads into normalized events and, once the
stream ends, rebuild the same decoded body the non-stream endpoint would have returned,
so the adapter can reuse a single response parser for both modes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from app.agent.llm.provider_adapter import ModelResponse


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    """Reasoning text; evidence only, never published to clients."""

    text: str


@dataclass(frozen=True)
class ToolCallDelta:
    index: int
    call_id: str | None = None
    name: str | None = None
    arguments_fragment: str = ""


@dataclass(frozen=True)
class StreamDone:
    """Final event carrying the fully collected response."""

    response: "ModelResponse"


StreamEvent = TextDelta | ReasoningDelta | ToolCallDelta | StreamDone

DONE_SENTINEL = "[DONE]"


def parse_sse_line(line: str) -> tuple[str, Any] | None:
    """Return ("done", None) / ("data", payload) / ("invalid", None); None for non-data lines."""
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data:
        return None
    if data == DONE_SENTINEL:
        return "done", None
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return "invalid", None
    return ("data", payload) if isinstance(payload, dict) else ("invalid", None)


def _error_info(raw: Any) -> dict[str, Any]:
    # Only type/code are kept; provider messages may echo request content.
    code = ""
    if isinstance(raw, dict):
        code = str(raw.get("code") or raw.get("type") or "")[:80]
    return {"type": "stream_error", "message": f"provider stream error {code}".strip()}


class ChatStreamCollector:
    """Accumulates chat.completions chunks."""

    def __init__(self) -> None:
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._calls: dict[int, dict[str, str]] = {}
        self._finish_reason: str | None = None
        self._usage: Any = None
        self._response_id: str | None = None
        self._model: str | None = None
        self.error: dict[str, Any] | None = None
        self.saw_done = False

    @property
    def complete(self) -> bool:
        # Some providers drop the trailing [DONE]; a finish_reason is enough to trust the body.
        return self.saw_done or self._finish_reason is not None

    @property
    def finished(self) -> bool:
        return self.saw_done or self.error is not None

    def mark_done(self) -> None:
        self.saw_done = True

    def feed(self, payload: dict[str, Any]) -> list[StreamEvent]:
        if payload.get("error"):
            self.error = _error_info(payload["error"])
            return []
        if isinstance(payload.get("id"), str):
            self._response_id = payload["id"]
        if isinstance(payload.get("model"), str):
            self._model = payload["model"]
        if isinstance(payload.get("usage"), dict):
            self._usage = payload["usage"]
        choices = payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        if choice is None:
            return []
        if choice.get("finish_reason"):
            self._finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return []
        events: list[StreamEvent] = []
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            self._reasoning.append(reasoning)
            events.append(ReasoningDelta(reasoning))
        content = delta.get("content")
        if isinstance(content, str) and content:
            self._text.append(content)
            events.append(TextDelta(content))
        raw_calls = delta.get("tool_calls")
        if isinstance(raw_calls, list):
            for raw in raw_calls:
                if isinstance(raw, dict):
                    events.append(self._merge_call(raw))
        return events

    def _merge_call(self, raw: dict[str, Any]) -> ToolCallDelta:
        call_id = raw.get("id") if isinstance(raw.get("id"), str) and raw.get("id") else None
        index = raw.get("index")
        if not isinstance(index, int):
            known = next((i for i, c in self._calls.items() if call_id and c["id"] == call_id), None)
            if known is not None:
                index = known
            elif call_id or not self._calls:
                index = len(self._calls)
            else:
                index = max(self._calls)
        slot = self._calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
        name = function.get("name") if isinstance(function.get("name"), str) else None
        fragment = function.get("arguments") if isinstance(function.get("arguments"), str) else ""
        if call_id and not slot["id"]:
            slot["id"] = call_id
        if name and not slot["name"]:
            slot["name"] = name
        slot["arguments"] += fragment
        return ToolCallDelta(index, call_id, name, fragment)

    def decoded(self) -> dict[str, Any]:
        text = "".join(self._text)
        message: dict[str, Any] = {"role": "assistant", "content": text or None}
        if self._reasoning:
            message["reasoning_content"] = "".join(self._reasoning)
        calls = [
            {
                "id": slot["id"] or f"call_{uuid4().hex[:12]}",
                "type": "function",
                "function": {"name": slot["name"], "arguments": slot["arguments"]},
            }
            for _, slot in sorted(self._calls.items())
            if slot["name"]
        ]
        if calls:
            message["tool_calls"] = calls
        return {
            "id": self._response_id,
            "model": self._model,
            "choices": [{"message": message, "finish_reason": self._finish_reason}],
            "usage": self._usage,
        }


class ResponsesStreamCollector:
    """Accumulates Responses API server-sent events."""

    def __init__(self) -> None:
        self._text: list[str] = []
        self._items: dict[int, dict[str, Any]] = {}
        self._arguments: dict[int, str] = {}
        self._completed: dict[str, Any] | None = None
        self._response_id: str | None = None
        self._model: str | None = None
        self.error: dict[str, Any] | None = None

    @property
    def complete(self) -> bool:
        return self._completed is not None

    @property
    def finished(self) -> bool:
        return self._completed is not None or self.error is not None

    def mark_done(self) -> None:
        """Responses streams end with response.completed, not [DONE]."""

    def feed(self, payload: dict[str, Any]) -> list[StreamEvent]:
        kind = str(payload.get("type") or "")
        index = payload.get("output_index") if isinstance(payload.get("output_index"), int) else 0
        if kind in {"response.created", "response.in_progress"}:
            response = payload.get("response")
            if isinstance(response, dict):
                self._response_id = response.get("id") or self._response_id
                self._model = response.get("model") or self._model
        elif kind == "response.output_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                self._text.append(delta)
                return [TextDelta(delta)]
        elif kind in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                return [ReasoningDelta(delta)]
        elif kind == "response.output_item.added":
            item = payload.get("item")
            if isinstance(item, dict):
                self._items[index] = item
                if item.get("type") == "function_call":
                    return [ToolCallDelta(index, item.get("call_id") or item.get("id"), item.get("name"), "")]
        elif kind == "response.function_call_arguments.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                self._arguments[index] = self._arguments.get(index, "") + delta
                return [ToolCallDelta(index, arguments_fragment=delta)]
        elif kind == "response.function_call_arguments.done":
            if isinstance(payload.get("arguments"), str):
                self._arguments[index] = payload["arguments"]
        elif kind == "response.output_item.done":
            item = payload.get("item")
            if isinstance(item, dict):
                self._items[index] = item
        elif kind in {"response.completed", "response.incomplete", "response.failed"}:
            response = payload.get("response")
            self._completed = response if isinstance(response, dict) else {"status": "failed"}
        elif kind == "error":
            self.error = _error_info(payload.get("error") or payload)
        return []

    def decoded(self) -> dict[str, Any]:
        base = dict(self._completed or {})
        base.setdefault("id", self._response_id)
        base.setdefault("model", self._model)
        if not base.get("output"):
            output = []
            for index, item in sorted(self._items.items()):
                item = dict(item)
                if item.get("type") == "function_call" and not item.get("arguments"):
                    item["arguments"] = self._arguments.get(index, "")
                output.append(item)
            base["output"] = output
            if not any(item.get("type") == "message" for item in output) and self._text:
                base["output_text"] = "".join(self._text)
        if self._completed is None:
            base["status"] = "incomplete"
        return base
