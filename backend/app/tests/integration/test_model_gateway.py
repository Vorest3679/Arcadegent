"""ThinkResult equivalence across streamed/non-streamed adapters and request shape."""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent.llm.model_gateway import ThinkRequest, think
from app.agent.llm.provider_adapter import ModelResponse
from app.agent.llm.streaming import StreamDone, TextDelta


class _Adapter:
    def __init__(self, *, streaming: bool, response: ModelResponse) -> None:
        self.streaming = streaming
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> ModelResponse:
        self.calls.append(kwargs)
        return self.response

    async def stream(self, **kwargs: Any):
        self.calls.append(kwargs)
        for part in ("he", "llo"):
            yield TextDelta(part)
        yield StreamDone(self.response)


def _run(adapter: _Adapter, *, on_text: bool) -> tuple[Any, list[tuple[str, str]]]:
    seen: list[tuple[str, str]] = []
    request = ThinkRequest(instructions="i", messages=[], tools=[], hints={"k": 1})
    cb = (lambda text, mode: seen.append((text, mode))) if on_text else None
    return asyncio.run(think(adapter, request, on_text=cb)), seen


def test_streamed_and_plain_results_are_equivalent() -> None:
    response = ModelResponse(text="hello")
    streamed, deltas = _run(_Adapter(streaming=True, response=response), on_text=True)
    plain, whole = _run(_Adapter(streaming=False, response=response), on_text=True)

    assert streamed.content == plain.content == "hello"
    assert streamed.published and plain.published
    assert deltas == [("he", "provider"), ("llo", "provider")]
    assert whole == [("hello", "synthetic")]


def test_silent_call_never_publishes() -> None:
    adapter = _Adapter(streaming=True, response=ModelResponse(text="hello"))

    result, seen = _run(adapter, on_text=False)

    assert result.content == "hello" and not result.published and seen == []


def test_blank_or_error_text_is_not_published() -> None:
    blank, seen = _run(_Adapter(streaming=False, response=ModelResponse(text="  ")), on_text=True)
    failed, seen2 = _run(
        _Adapter(streaming=False, response=ModelResponse(text="x", error={"type": "e"})), on_text=True
    )

    assert not blank.published and not failed.published and seen == seen2 == []


def test_model_and_effort_forwarded_only_when_set() -> None:
    adapter = _Adapter(streaming=False, response=ModelResponse(text="a"))
    asyncio.run(think(adapter, ThinkRequest(instructions="i", messages=[], tools=[])))
    asyncio.run(think(adapter, ThinkRequest(instructions="i", messages=[], tools=[], model="m", reasoning_effort="low")))

    assert set(adapter.calls[0]) == {"instructions", "messages", "tools", "runtime_hints"}
    assert adapter.calls[1]["model"] == "m" and adapter.calls[1]["reasoning_effort"] == "low"
