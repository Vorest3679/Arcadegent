"""Provider adapter with Responses/Chat Completions dual-stack fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

from app.agent.llm.llm_config import LLMConfig
from app.infra.observability.logger import get_logger

logger = get_logger(__name__)


def _compact_tool_content(raw: Any, *, limit: int = 1200) -> str:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            compact = " ".join(text.split())
        else:
            compact = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(raw, dict):
        compact = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    else:
        compact = str(raw).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 3)].rstrip() + "..."


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


@dataclass(frozen=True)
class ModelResponse:
    """Normalized model response with optional tool calls."""

    text: str | None = None
    tool_calls: list[ModelToolCall] = field(default_factory=list)
    reasoning_items: list[dict[str, Any]] = field(default_factory=list)
    response_id: str | None = None


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
        tool_choice = self._resolve_tool_choice(
            tools=tools,
            runtime_hints=runtime_hints,
        )
        active_subagent = str((runtime_hints or {}).get("active_subagent") or "").strip()
        self._log_request_summary(
            active_subagent=active_subagent,
            tool_choice=tool_choice,
            instructions=instructions,
            messages=messages,
            tools=tools,
        )

        if not self._config.profile_enabled:
            return self._error_response(
                "llm provider disabled by profile "
                f"'{self._config.profile_name}'. switch AGENT_PROVIDER_PROFILE to 'default' "
                "or enable this profile."
            )
        if not self._config.api_key.strip():
            return self._error_response(
                "llm provider missing api key. set LLM_API_KEY."
            )

        by_responses: ModelResponse | None
        by_chat: ModelResponse | None
        responses_error: str | None
        chat_error: str | None
        if self._prefer_chat_completions():
            by_chat, chat_error = await self._try_chat_completions(
                instructions=instructions,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
            if by_chat is not None:
                self._log_response_summary(
                    provider="chat_completions",
                    response=by_chat,
                )
                return by_chat
            by_responses, responses_error = await self._try_responses_api(
                instructions=instructions,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
            if by_responses is not None:
                self._log_response_summary(
                    provider="responses",
                    response=by_responses,
                )
                return by_responses
        else:
            by_responses, responses_error = await self._try_responses_api(
                instructions=instructions,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
            if by_responses is not None:
                self._log_response_summary(
                    provider="responses",
                    response=by_responses,
                )
                return by_responses

            by_chat, chat_error = await self._try_chat_completions(
                instructions=instructions,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
            if by_chat is not None:
                self._log_response_summary(
                    provider="chat_completions",
                    response=by_chat,
                )
                return by_chat

        logger.warning(
            "llm.error provider=both responses_error=%s chat_error=%s",
            self._format_error(responses_error),
            self._format_error(chat_error),
        )
        return self._error_response(
            "llm provider failed after trying responses and chat completions; "
            f"responses_error={self._format_error(responses_error)}; "
            f"chat_completions_error={self._format_error(chat_error)}"
        )

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
                        "Authorization": f"Bearer {self._config.api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            details = " ".join(exc.response.text.split()) if exc.response is not None else ""
            suffix = f"; body={details[:280]}" if details else ""
            return None, f"http_error status={exc.response.status_code} reason={exc.response.reason_phrase}{suffix}"
        except httpx.TimeoutException:
            return None, "timeout_error request timed out"
        except httpx.RequestError as exc:
            return None, f"url_error reason={exc}"
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
            "input": messages,
            "temperature": self._config.temperature,
            "max_output_tokens": self._config.max_tokens,
            "tool_choice": tool_choice,
            "parallel_tool_calls": self._config.parallel_tool_calls,
        }
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
        if text is None and not tool_calls and not reasoning:
            return None, "responses api returned no text, tool_calls, or reasoning"

        response_id = decoded.get("id")
        return (
            ModelResponse(
                text=text,
                tool_calls=tool_calls,
                reasoning_items=reasoning,
                response_id=str(response_id) if response_id is not None else None,
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
        if isinstance(raw_args, str):
            args = _safe_json_loads(raw_args)
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}
        call_id = payload.get("call_id") or payload.get("id") or f"call_{uuid4().hex[:12]}"
        return ModelToolCall(call_id=str(call_id), name=name, arguments=args)

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
        if text is None and not tool_calls:
            return None, "chat completions api returned no text or tool_calls"
        return ModelResponse(text=text, tool_calls=tool_calls, reasoning_items=reasoning), None

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
            "top_p": 1,
            "frequency_penalty": 0,
            "presence_penalty": 0,
            "stream": False,
            "response_format": {"type": "text"},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
            if self._config.parallel_tool_calls:
                payload["parallel_tool_calls"] = True
        else:
            payload["tools"] = None
            payload["tool_choice"] = "none"

        if self._is_deepseek_compatible():
            payload["thinking"] = {"type": "disabled"}
            payload["stop"] = None
            payload["stream_options"] = None
            payload["logprobs"] = False
            payload["top_logprobs"] = None
        return payload

    def _normalize_chat_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize message list for chat.completions-compatible providers.

        Some providers (e.g. DeepSeek) strictly require every `tool` role message
        to be preceded by an assistant message containing matching `tool_calls`.
        Since this runtime stores tool execution in separate turns without raw
        assistant tool_call payload, we convert historical tool observations into
        assistant-readable text notes instead of dropping them outright.
        """
        normalized: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role == "tool":
                tool_name = str(item.get("name") or "tool").strip() or "tool"
                compact = _compact_tool_content(item.get("content"))
                if compact:
                    normalized.append(
                        {
                            "role": "assistant",
                            "content": f"[Tool result: {tool_name}] {compact}",
                        }
                    )
                continue
            if role not in {"user", "assistant", "system"}:
                continue
            content = item.get("content")
            if content is None:
                text = ""
            elif isinstance(content, str):
                text = content
            else:
                text = str(content)
            normalized.append({"role": role, "content": text})
        return normalized

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
        if isinstance(args_raw, str):
            args = _safe_json_loads(args_raw)
        elif isinstance(args_raw, dict):
            args = args_raw
        else:
            args = {}
        return ModelToolCall(call_id=str(call_id), name=name, arguments=args)

    def _error_response(self, message: str) -> ModelResponse:
        return ModelResponse(text=f"error: {message}", tool_calls=[], reasoning_items=[])

    def _is_deepseek_compatible(self) -> bool:
        base = self._config.base_url.strip().lower()
        model = self._config.model.strip().lower()
        return "deepseek" in base or model.startswith("deepseek")

    def _prefer_chat_completions(self) -> bool:
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
        if choice not in {"auto", "required", "none"}:
            return "auto"
        if choice == "none":
            return "auto"
        return choice

    def _log_request_summary(
        self,
        *,
        active_subagent: str,
        tool_choice: str,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> None:
        tool_names = self._tool_names(tools)
        message_preview = self._message_preview(messages)
        logger.info(
            "llm.request provider_pref=%s model=%s subagent=%s tool_choice=%s tools=%s messages=%s instruction_preview=%s",
            "chat_completions" if self._prefer_chat_completions() else "responses",
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
            "llm.response provider=%s response_id=%s tool_calls=%s has_text=%s reasoning_items=%s tool_names=%s text_preview=%s",
            provider,
            response.response_id,
            len(response.tool_calls),
            bool(response.text),
            len(response.reasoning_items),
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

    def _format_error(self, value: str | None) -> str:
        if not value:
            return "unknown"
        compact = " ".join(value.split())
        if len(compact) <= 280:
            return compact
        return compact[:277] + "..."
