"""Streaming events and chunk collectors for OpenAI-compatible providers.

Collectors turn protocol-specific stream payloads into normalized events for provider_adapters,
once the stream ends, rebuild the same decoded body the non-stream endpoint would have returned,
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
    """解析一行 SSE 数据，区分结束标记、有效载荷与非法数据。

    返回 ("done", None)、("data", payload) 或 ("invalid", None)。
    非 data 行和空数据返回 None；有效载荷必须是 JSON 对象。
    """
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
    """生成统一的流式错误信息，仅提取供应商错误的 code 或 type 字段。"""
    # 供应商的原始错误消息可能回显请求内容，因此不保留 message 字段。
    code = ""
    if isinstance(raw, dict):
        code = str(raw.get("code") or raw.get("type") or "")[:80]
    return {"type": "stream_error", "message": f"provider stream error {code}".strip()}


class ChatStreamCollector:
    """Accumulates chat.completions chunks."""

    def __init__(self) -> None:
        """初始化文本、推理内容、工具调用片段及响应元数据的收集状态。"""
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
        """判断是否收到完成信号，用于检查流是否意外中断。"""
        # 部分供应商省略末尾的 [DONE]，收到 finish_reason 也可视为响应完整。
        return self.saw_done or self._finish_reason is not None

    @property
    def finished(self) -> bool:
        """判断是否应停止读取：收到 [DONE] 或供应商错误即结束。"""
        # finish_reason 不立即终止读取，后续分片仍可能携带 usage。
        return self.saw_done or self.error is not None

    def mark_done(self) -> None:
        """记录已收到 SSE 的 [DONE] 结束标记。"""
        self.saw_done = True

    def feed(self, payload: dict[str, Any]) -> list[StreamEvent]:
        """收集一个 Chat Completions 分片，返回其中的标准化增量事件。

        保存响应元数据，仅处理首个 choice；分别累积文本、推理和工具调用。
        遇到供应商错误时记录错误，并返回空事件列表。
        """
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
        """将工具调用分片合并到对应索引，并返回本次增量。

        缺少索引时，先按调用 ID 匹配；新 ID 或首次调用分配新索引，
        无 ID 的后续片段归入当前最大索引。ID 和名称仅首次写入，参数按到达顺序拼接。
        """
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
        """重建 Chat Completions 非流式响应结构，供适配器复用解析逻辑。

        工具调用按索引排序，忽略缺少名称的调用，并为缺少 ID 的调用生成临时 ID。
        """
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


def _has_output_text(item: dict[str, Any]) -> bool:
    content = item.get("content")
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "output_text" and part.get("text") for part in content
    )


class ResponsesStreamCollector:
    """Accumulates Responses API server-sent events."""

    def __init__(self) -> None:
        """初始化文本、输出项、工具参数片段及终态响应的收集状态。"""
        self._parts: dict[int, dict[int, str]] = {}  # output_index -> content_index -> text
        self._items: dict[int, dict[str, Any]] = {}
        self._arguments: dict[int, str] = {}
        self._completed: dict[str, Any] | None = None
        self._response_id: str | None = None
        self._model: str | None = None
        self.error: dict[str, Any] | None = None

    @property
    def complete(self) -> bool:
        """判断是否收到终态响应；完整、未完成和失败响应均属于终态。"""
        return self._completed is not None

    @property
    def finished(self) -> bool:
        """判断是否应停止读取：收到终态响应或独立错误事件即结束。"""
        return self._completed is not None or self.error is not None

    def mark_done(self) -> None:
        """兼容统一收集器接口；[DONE] 不改变状态，完成状态由终态响应事件确定。"""

    def feed(self, payload: dict[str, Any]) -> list[StreamEvent]:
        """按 Responses 事件类型更新收集状态，并返回标准化增量事件。

        输出项和工具参数按 output_index 关联，参数 done 事件覆盖累计片段。
        推理增量仅转发为事件；终态响应整体保存，未知事件返回空列表。
        """
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
                part = payload.get("content_index") if isinstance(payload.get("content_index"), int) else 0
                parts = self._parts.setdefault(index, {})
                parts[part] = parts.get(part, "") + delta
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
        """以终态响应为基础重建 Responses 非流式响应结构。

        缺少 output 时按索引组装已收集的输出项，并补齐工具参数；断流时消息项
        可能仍是 added 时的空内容，用按索引累计的文本补齐；没有对应消息项的文本
        填入 output_text。未收到终态响应则标记为 incomplete。
        """
        base = dict(self._completed or {})
        base.setdefault("id", self._response_id)
        base.setdefault("model", self._model)
        if not base.get("output"):
            output = []
            for index, item in sorted(self._items.items()):
                item = dict(item)
                if item.get("type") == "function_call" and not item.get("arguments"):
                    item["arguments"] = self._arguments.get(index, "")
                elif item.get("type") == "message" and index in self._parts and not _has_output_text(item):
                    item["content"] = [{"type": "output_text", "text": text}
                                       for _, text in sorted(self._parts[index].items())]
                output.append(item)
            base["output"] = output
            orphan = "".join(
                text
                for index, parts in sorted(self._parts.items())
                if self._items.get(index, {}).get("type") != "message"
                for _, text in sorted(parts.items())
            )
            if orphan:
                base["output_text"] = orphan
        if self._completed is None:
            base["status"] = "incomplete"
        return base
