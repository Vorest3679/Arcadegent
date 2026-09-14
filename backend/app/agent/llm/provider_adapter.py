"""Provider adapter for OpenAI-compatible Responses / Chat Completions protocols."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from time import perf_counter
from copy import deepcopy
from typing import Any
from uuid import uuid4

import httpx

from app.agent.llm.llm_config import LLMConfig
from app.infra.observability.logger import get_logger

logger = get_logger(__name__)


def _safe_json_loads(raw: str | bytes | None) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


@dataclass(frozen=True)
class ModelToolCall:
    """Normalized function call emitted by provider model."""

    call_id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: Any = None
    parse_error: str | None = None


@dataclass(frozen=True)
class ModelResponse:
    """Normalized model response with optional tool calls."""

    text: str | None = None
    tool_calls: list[ModelToolCall] = field(default_factory=list)
    reasoning_items: list[dict[str, Any]] = field(default_factory=list)
    response_id: str | None = None
    error: dict[str, Any] | None = None
    reported_model: str | None = None
    finish_reason: str | None = None
    status: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw_usage: dict[str, Any] | None = None
    duration_ms: float | None = None
    provider_ttft_ms: float | None = None
    stream_mode: str = "synthetic"
    protocol: str | None = None
    transcript: dict[str, Any] = field(default_factory=dict)


class ProviderAdapter:
    """Execute one model turn against OpenAI-compatible provider."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def complete(
        self,
        *,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        runtime_hints: dict[str, Any] | None = None,
    ) -> ModelResponse:
        started = perf_counter()
        protocol = "chat_completions"
        try:
            protocol = "chat_completions" if self._prefer_chat_completions() else "responses"
            if not self.enabled:
                raise ValueError("llm provider disabled or missing API key")
            tool_choice = self._resolve_tool_choice(tools=tools, runtime_hints=runtime_hints)
            self._log_request_summary(
                active_subagent=str((runtime_hints or {}).get("active_subagent") or "").strip(),
                tool_choice=tool_choice,
                instructions=instructions,
                messages=messages,
                tools=tools,
                protocol=protocol,
            )
            method = self._try_chat_completions if protocol == "chat_completions" else self._try_responses_api
            response, error = await method(instructions=instructions, messages=messages,
                                           tools=tools, tool_choice=tool_choice)
            response = response or self._error_response(error or "empty provider response")
        except (ValueError, TypeError) as exc:
            response = self._error_response(str(exc))
        response = replace(response, duration_ms=(perf_counter() - started) * 1000, protocol=protocol)
        self._log_response_summary(provider=protocol, response=response)
        return response

    async def _post_json(
        self,
        *,
        endpoint: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                response = await client.post(
                    endpoint,
                    json=payload,
                    headers={
                        self._config.auth_header: (f"Bearer {self._config.api_key}" if self._config.auth_header.lower() == "authorization" else self._config.api_key),
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return None, f"http_error status={exc.response.status_code}"
        except httpx.TimeoutException:
            return None, "timeout_error request timed out"
        except httpx.RequestError as exc:
            return None, f"url_error {type(exc).__name__}"
        except Exception as exc:  # pragma: no cover
            return None, f"unexpected_error {type(exc).__name__}: {exc}"
        decoded = _safe_json_loads(response.text)
        if not isinstance(decoded, dict):
            return None, "response body is not a JSON object"
        return decoded, None

    async def _try_responses_api(
        self,
        *,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
    ) -> tuple[ModelResponse | None, str | None]:
        endpoint = self._config.base_url.rstrip("/") + "/responses"
        payload: dict[str, Any] = {
            "model": self._config.model,
            "instructions": instructions,
            "input": self._normalize_responses_messages(messages),
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "temperature": self._config.temperature,
            "max_output_tokens": self._config.max_tokens,
            "tool_choice": tool_choice,
            "parallel_tool_calls": self._config.parallel_tool_calls,
        }
        self._apply_parameters(payload, "responses")
        if tools:
            payload["tools"] = [self._to_responses_tool(tool) for tool in tools]

        decoded, request_error = await self._post_json(endpoint=endpoint, payload=payload)
        if not isinstance(decoded, dict):
            return None, request_error or "responses api returned no data"

        tool_calls: list[ModelToolCall] = []
        reasoning: list[dict[str, Any]] = []
        text_chunks: list[str] = []
        output = decoded.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "")
                if item_type == "function_call":
                    tool_call = self._parse_responses_tool_call(item)
                    if tool_call:
                        tool_calls.append(tool_call)
                    continue
                if item_type == "reasoning":
                    reasoning.append(item)
                    continue
                if item_type == "message":
                    text_chunks.extend(self._extract_responses_message_text(item))
                    continue
                if item_type == "output_text":
                    chunk = str(item.get("text") or "").strip()
                    if chunk:
                        text_chunks.append(chunk)

        if not text_chunks:
            output_text = decoded.get("output_text")
            if isinstance(output_text, str) and output_text.strip():
                text_chunks.append(output_text.strip())

        text = "\n".join(chunk for chunk in text_chunks if chunk).strip() or None
        if tool_choice == "required" and not tool_calls:
            return None, "responses api returned no tool_calls under required tool_choice"
        if text is None and not tool_calls:
            return None, "responses api returned no text, tool_calls, or reasoning"

        response_id = decoded.get("id")
        return (
            ModelResponse(
                text=text,
                tool_calls=tool_calls,
                reasoning_items=reasoning,
                response_id=str(response_id) if response_id is not None else None,
                reported_model=decoded.get("model"), status=decoded.get("status"),
                error=({"type": "incomplete_response", "message": str(decoded.get("incomplete_details") or decoded.get("error") or decoded.get("status"))}
                       if decoded.get("status") not in (None, "completed") or decoded.get("error") else None),
                usage=self._usage(decoded.get("usage")), raw_usage=decoded.get("usage"),
                transcript={"responses_output": deepcopy(output or [])},
            ),
            None,
        )

    def _to_responses_tool(self, tool: dict[str, Any]) -> dict[str, Any]:
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            function = tool["function"]
            payload = {
                "type": "function",
                "name": function.get("name"),
                "description": function.get("description"),
                "parameters": function.get("parameters"),
            }
            if "strict" in function:
                payload["strict"] = function.get("strict")
            return payload
        return tool

    def _parse_responses_tool_call(self, payload: dict[str, Any]) -> ModelToolCall | None:
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            return None
        raw_args = payload.get("arguments")
        args, parse_error = self._parse_arguments(raw_args)
        call_id = payload.get("call_id") or payload.get("id") or f"call_{uuid4().hex[:12]}"
        return ModelToolCall(call_id=str(call_id), name=name, arguments=args, raw_arguments=raw_args, parse_error=parse_error)

    def _extract_responses_message_text(self, message_item: dict[str, Any]) -> list[str]:
        chunks: list[str] = []
        content = message_item.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and str(part.get("type")) == "output_text":
                    text = str(part.get("text") or "").strip()
                    if text:
                        chunks.append(text)
        return chunks

    async def _try_chat_completions(
        self,
        *,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
    ) -> tuple[ModelResponse | None, str | None]:
        endpoint = self._config.base_url.rstrip("/") + "/chat/completions"
        raw_tool_messages = sum(
            1
            for item in messages
            if isinstance(item, dict) and str(item.get("role") or "") == "tool"
        )
        normalized_messages = self._normalize_chat_messages(messages)
        if raw_tool_messages > 0:
            logger.debug(
                "llm.chat.normalize dropped_tool_messages=%s raw_messages=%s normalized_messages=%s",
                raw_tool_messages,
                len(messages),
                len(normalized_messages),
            )
        payload = self._build_chat_payload(
            instructions=instructions,
            messages=normalized_messages,
            tools=tools,
            tool_choice=tool_choice,
        )

        decoded, request_error = await self._post_json(endpoint=endpoint, payload=payload)
        if not isinstance(decoded, dict):
            return None, request_error or "chat completions api returned no data"

        choices = decoded.get("choices")
        if not isinstance(choices, list) or not choices:
            return None, "chat completions api returned empty choices"

        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else None
        if not isinstance(message, dict):
            return None, "chat completions api returned invalid message payload"

        text = self._extract_chat_text(message.get("content"))
        reasoning = self._extract_chat_reasoning(message.get("reasoning_content"))
        tool_calls: list[ModelToolCall] = []
        raw_tool_calls = message.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            for raw_call in raw_tool_calls:
                parsed = self._parse_chat_tool_call(raw_call)
                if parsed:
                    tool_calls.append(parsed)

        if tool_choice == "required" and not tool_calls:
            return None, "chat completions api returned no tool_calls under required tool_choice"
        finish = first.get("finish_reason")
        if text is None and not tool_calls and finish in (None, "stop", "tool_calls"):
            finish = "empty_response"
        return ModelResponse(text=text, tool_calls=tool_calls, reasoning_items=reasoning,
            response_id=decoded.get("id"), reported_model=decoded.get("model"), finish_reason=finish,
            status="completed" if finish in (None, "stop", "tool_calls") else "incomplete",
            error=({"type": "incomplete_response", "message": str(finish)} if finish not in (None, "stop", "tool_calls") else None),
            usage=self._usage(decoded.get("usage")), raw_usage=decoded.get("usage"),
            transcript={"chat_message": deepcopy(message)}), None

    def _build_chat_payload(
        self,
        *,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [{"role": "system", "content": instructions}] + messages,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
            if self._config.parallel_tool_calls:
                payload["parallel_tool_calls"] = True
        self._apply_parameters(payload, "chat_completions")
        return payload

    def _apply_parameters(self, payload: dict[str, Any], protocol: str) -> None:
        if not self._config.send_temperature:
            payload.pop("temperature", None)
        if protocol == "chat_completions" and self._config.token_limit_parameter != "max_tokens":
            payload[self._config.token_limit_parameter] = payload.pop("max_tokens")
        reserved = {"model", "messages", "input", "tools", "tool_choice", "stream", "store", "include", "instructions", "previous_response_id"}
        if reserved.intersection(self._config.extra_parameters):
            raise ValueError("extra_parameters cannot override protocol or tool controls")
        payload.update(deepcopy(self._config.extra_parameters))

    def _normalize_chat_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = []
        pending = set()
        for item in messages:
            if item.get("role") == "tool":
                if item.get("tool_call_id") in pending:
                    normalized.append({k: item[k] for k in ("role", "content", "tool_call_id")})
                    pending.remove(item["tool_call_id"])
                else:
                    normalized.append({"role": "assistant", "content": "[Legacy tool observation] " + str(item.get("content", ""))})
                continue
            message = deepcopy(item.get("chat_message") or {k: v for k, v in item.items() if k in ("role", "content", "tool_calls", "reasoning_content")})
            if message.get("role") in ("user", "assistant", "system"):
                normalized.append(message)
                pending.update(call["id"] for call in message.get("tool_calls", []))
        return normalized

    def _normalize_responses_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        pending = set()
        for item in messages:
            if item.get("responses_output") is not None:
                output = deepcopy(item["responses_output"])
                result.extend(output)
                pending.update(x["call_id"] for x in output if x.get("type") == "function_call")
            elif item.get("role") == "tool":
                call_id = item.get("tool_call_id")
                if call_id in pending:
                    result.append({"type": "function_call_output", "call_id": call_id, "output": item["content"]})
                    pending.remove(call_id)
                else:
                    result.append({"role": "assistant", "content": "[Legacy tool observation] " + str(item.get("content", ""))})
            else:
                # Chat history can be reused across configured protocols without fabricated IDs.
                chat = item.get("chat_message") or item
                if chat.get("content"):
                    result.append({"role": chat["role"], "content": chat["content"]})
                for call in chat.get("tool_calls", []):
                    result.append({"type": "function_call", "call_id": call["id"], **call["function"]})
                    pending.add(call["id"])
        return result

    @staticmethod
    def _parse_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(value, dict):
                raise ValueError("tool arguments must be a JSON object")
            return value, None
        except (ValueError, TypeError) as exc:
            return {}, str(exc)

    @staticmethod
    def _usage(raw: Any) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        input_details = raw.get("input_tokens_details") or raw.get("prompt_tokens_details") or {}
        output_details = raw.get("output_tokens_details") or raw.get("completion_tokens_details") or {}
        return {"input_tokens": raw.get("input_tokens", raw.get("prompt_tokens")),
                "output_tokens": raw.get("output_tokens", raw.get("completion_tokens")),
                "total_tokens": raw.get("total_tokens"),
                "cached_input_tokens": input_details.get("cached_tokens", raw.get("prompt_cache_hit_tokens")),
                "reasoning_tokens": output_details.get("reasoning_tokens")}

    def _extract_chat_text(self, raw_content: Any) -> str | None:
        if isinstance(raw_content, str):
            text = raw_content.strip()
            return text or None
        if isinstance(raw_content, list):
            chunks: list[str] = []
            for item in raw_content:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "")
                if item_type not in {"text", "output_text"}:
                    continue
                value = str(item.get("text") or item.get("value") or "").strip()
                if value:
                    chunks.append(value)
            merged = "\n".join(chunks).strip()
            return merged or None
        return None

    def _extract_chat_reasoning(self, raw_reasoning: Any) -> list[dict[str, Any]]:
        if isinstance(raw_reasoning, str):
            text = raw_reasoning.strip()
            return [{"type": "reasoning", "text": text}] if text else []
        if isinstance(raw_reasoning, list):
            chunks: list[str] = []
            for item in raw_reasoning:
                if not isinstance(item, dict):
                    continue
                token = str(item.get("token") or "").strip()
                if token:
                    chunks.append(token)
            merged = "".join(chunks).strip()
            return [{"type": "reasoning", "text": merged}] if merged else []
        return []

    def _parse_chat_tool_call(self, raw_call: Any) -> ModelToolCall | None:
        if not isinstance(raw_call, dict):
            return None
        call_id = raw_call.get("id") or f"call_{uuid4().hex[:12]}"
        function = raw_call.get("function")
        if not isinstance(function, dict):
            return None
        name = function.get("name")
        if not isinstance(name, str) or not name:
            return None
        args_raw = function.get("arguments")
        args, parse_error = self._parse_arguments(args_raw)
        return ModelToolCall(call_id=str(call_id), name=name, arguments=args, raw_arguments=args_raw, parse_error=parse_error)

    def _error_response(self, message: str) -> ModelResponse:
        return ModelResponse(error={"type": "provider_error", "message": message}, status="failed")

    def _is_deepseek_compatible(self) -> bool:
        base = self._config.base_url.strip().lower()
        model = self._config.model.strip().lower()
        return "deepseek" in base or model.startswith("deepseek")

    def _prefer_chat_completions(self) -> bool:
        if self._config.api_mode not in ("auto", "responses", "chat_completions"):
            raise ValueError("unsupported api_mode")
        if self._config.api_mode != "auto":
            return self._config.api_mode == "chat_completions"
        if self._config.prefer_chat_completions:
            return True
        return self._is_deepseek_compatible()

    def _resolve_tool_choice(
        self,
        *,
        tools: list[dict[str, Any]],
        runtime_hints: dict[str, Any] | None,
    ) -> str:
        if not tools:
            return "none"
        _ = runtime_hints
        choice = self._config.tool_choice.strip().lower()
        if choice not in self._config.supported_tool_choices:
            raise ValueError(f"unsupported tool_choice: {choice}")
        return choice

    def _log_request_summary(
        self,
        *,
        active_subagent: str,
        tool_choice: str,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        protocol: str,
    ) -> None:
        tool_names = self._tool_names(tools)
        message_preview = self._message_preview(messages)
        logger.info(
            "llm.request provider_pref=%s model=%s subagent=%s tool_choice=%s tools=%s messages=%s instruction_preview=%s",
            protocol,
            self._config.model,
            active_subagent or "-",
            tool_choice,
            tool_names,
            message_preview,
            self._short(instructions, limit=120),
        )

    def _log_response_summary(
        self,
        *,
        provider: str,
        response: ModelResponse,
    ) -> None:
        logger.info(
            "llm.response provider=%s response_id=%s status=%s tool_calls=%s has_text=%s reasoning_items=%s usage=%s error=%s tool_names=%s text_preview=%s",
            provider,
            response.response_id,
            response.status,
            len(response.tool_calls),
            bool(response.text),
            len(response.reasoning_items),
            {key: value for key, value in response.usage.items() if value is not None},
            response.error,
            [call.name for call in response.tool_calls],
            self._short(response.text, limit=120),
        )

    def _tool_names(self, tools: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") != "function":
                continue
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names

    def _message_preview(self, messages: list[dict[str, Any]]) -> list[str]:
        rows: list[str] = []
        for item in messages[-4:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "-")
            content = item.get("content")
            content_text = content if isinstance(content, str) else str(content)
            rows.append(f"{role}:{self._short(content_text, limit=60)}")
        return rows

    def _short(self, value: str | None, *, limit: int = 120) -> str:
        if not isinstance(value, str):
            return ""
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return compact[: max(1, limit - 3)] + "..."
