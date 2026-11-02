"""Single entry for one model "think" call, streamed or not.

Both the main agent (publishes text to the user) and workers (silent) call
``think``; the only difference is whether ``on_text`` is given.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall, ProviderAdapter, collect_stream


@dataclass(frozen=True)
class ThinkRequest:
    instructions: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    # None keeps the provider request unchanged; they are only forwarded when set.
    model: str | None = None
    reasoning_effort: str | None = None
    hints: dict[str, Any] | None = None

    def provider_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "instructions": self.instructions,
            "messages": self.messages,
            "tools": self.tools,
            "runtime_hints": self.hints,
        }
        if self.model is not None:
            kwargs["model"] = self.model
        if self.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.reasoning_effort
        return kwargs


@dataclass(frozen=True)
class ThinkResult:
    response: ModelResponse
    # True when the response text was handed to ``on_text`` (so it is already visible).
    published: bool

    @property
    def content(self) -> str | None:
        return self.response.text

    @property
    def reasoning(self) -> list[dict[str, Any]]:
        return self.response.reasoning_items

    @property
    def tool_calls(self) -> list[ModelToolCall]:
        return self.response.tool_calls


async def think(
    adapter: ProviderAdapter,
    request: ThinkRequest,
    *,
    on_text: Callable[[str, str], None] | None = None,
) -> ThinkResult:
    """Run one model call.

    With ``on_text(text, stream_mode)`` the text is delivered through it:
    streamed deltas (``"provider"``) when the adapter streams, otherwise once as
    a whole (``"synthetic"``, non-blank and error-free text only).
    Without ``on_text`` nothing is delivered. Cancelling the caller closes the
    provider stream through ``collect_stream``.
    """
    kwargs = request.provider_kwargs()
    if on_text is None:
        return ThinkResult(await adapter.complete(**kwargs), False)
    if not adapter.streaming:
        response = await adapter.complete(**kwargs)
        text = (response.text or "").strip()
        if response.error is not None or not text:
            return ThinkResult(response, False)
        on_text(text, "synthetic")
        return ThinkResult(response, True)
    published = False

    def deliver(text: str) -> None:
        nonlocal published
        on_text(text, "provider")
        published = True

    return ThinkResult(await collect_stream(adapter.stream(**kwargs), on_text=deliver), published)
