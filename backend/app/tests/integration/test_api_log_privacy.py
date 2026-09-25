"""Ensure normal API and agent execution never puts request content in logs."""

from __future__ import annotations

import asyncio
import io
import logging
from types import SimpleNamespace

import pytest

from app.agent.llm.llm_config import LLMConfig
from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall, ProviderAdapter
from app.core.lifecycle import on_startup
from app.infra.observability.logger import PrivacyFormatter, log_ref
from backend.app.tests.integration._api_test_support import _build_client


def test_external_log_and_exception_body_are_not_rendered() -> None:
    secret = "synthetic-secret-do-not-log"
    record = logging.LogRecord(
        "httpx",
        logging.WARNING,
        __file__,
        1,
        "request_url=https://example.invalid/?key=%s",
        (secret,),
        (RuntimeError, RuntimeError(secret), None),
    )

    rendered = PrivacyFormatter("%(name)s | %(message)s").format(record)

    assert rendered == "external | external_log_event exception_type=RuntimeError"
    assert secret not in rendered


def test_provider_logs_counts_without_message_or_error_content() -> None:
    secret = "synthetic-prompt-and-token-secret"
    adapter = ProviderAdapter(LLMConfig(
        api_key="synthetic-test-key",
        base_url="https://example.invalid/v1",
        model=secret,
        timeout_seconds=1.0,
        temperature=0.0,
        max_tokens=16,
    ))
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(PrivacyFormatter("%(message)s"))
    logger = logging.getLogger("app.agent.llm.provider_adapter")
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        adapter._log_request_summary(
            active_subagent=secret,
            tool_choice=secret,
            instructions=secret,
            messages=[{"role": "user", "content": secret}],
            tools=[{"type": "function", "function": {"name": secret}}],
            protocol="responses",
        )
        adapter._log_response_summary(
            provider="responses",
            response=ModelResponse(
                text=secret,
                response_id=secret,
                error={"message": secret},
                usage={"total_tokens": 17, secret: secret},
            ),
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    output = stream.getvalue()
    assert secret not in output
    assert "instruction_chars=" in output
    assert "text_chars=" in output
    assert log_ref(secret) in output


def test_startup_logs_only_allowlisted_numeric_health_counts() -> None:
    secret = "synthetic-health-secret-521"

    class Store:
        def health(self):
            return {"loaded_rows": 7, "bad_lines": 1, "url": secret, "total_lines": secret}

    class Registry:
        async def refresh_tools(self):
            return None

        def provider_health(self):
            return {"mcp": {"last_error": secret}}

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("app.core.lifecycle")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        asyncio.run(on_startup(SimpleNamespace(store=Store(), tool_registry=Registry())))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    output = stream.getvalue()
    assert secret not in output
    assert "'loaded_rows': 7" in output
    assert "'bad_lines': 1" in output
    assert "total_lines" not in output


def test_chat_and_access_logs_exclude_payload_query_and_reply(tmp_path) -> None:
    secret = "synthetic-private-message-719"
    client = _build_client(tmp_path)
    calls = 0

    async def fake_complete(*, instructions, messages, tools, runtime_hints=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(tool_calls=[ModelToolCall("call-1", "db_query_tool", {
                "keyword": secret,
                "page": 1,
                "page_size": 5,
            })])
        return ModelResponse(text=secret)

    client.app.state.container.react_runtime._provider_adapter.complete = fake_complete
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(PrivacyFormatter("%(name)s | %(message)s"))
    root = logging.getLogger()
    assert any(isinstance(item.formatter, PrivacyFormatter) for item in root.handlers)
    root.addHandler(handler)
    try:
        listing = client.get("/api/arcades", params={"keyword": secret, "token": secret})
        response = client.post("/api/chat", json={"message": secret, "keyword": secret})
        missing = client.get(f"/unmatched/{secret}?token={secret}")
    finally:
        root.removeHandler(handler)

    assert listing.status_code == 200
    assert response.status_code == 200
    assert response.json()["reply"] == secret
    assert missing.status_code == 404
    output = stream.getvalue()
    assert secret not in output
    assert "GET /api/arcades" in output
    assert "POST /api/chat" in output
    assert "GET <unmatched>" in output
    assert "tool.call" in output
    assert "tool_ref=" in output
    assert "tool=db_query_tool" not in output
    assert "reply_chars=" in output


def test_runtime_failure_logs_exception_type_without_message(tmp_path) -> None:
    secret = "synthetic-provider-error-482"
    client = _build_client(tmp_path)

    async def fail_complete(**kwargs):
        raise RuntimeError(secret)

    client.app.state.container.react_runtime._provider_adapter.complete = fail_complete
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(PrivacyFormatter("%(name)s | %(message)s"))
    root = logging.getLogger()
    plain_stream = io.StringIO()
    plain_handler = logging.StreamHandler(plain_stream)
    plain_handler.setFormatter(logging.Formatter("%(message)s"))
    runtime_logger = logging.getLogger("app.agent.runtime.react_runtime")
    root.addHandler(handler)
    runtime_logger.addHandler(plain_handler)
    try:
        with pytest.raises(RuntimeError):
            client.post("/api/chat", json={"message": secret})
    finally:
        root.removeHandler(handler)
        runtime_logger.removeHandler(plain_handler)

    output = stream.getvalue()
    assert secret not in output
    assert "chat.failed" in output
    assert "exception_type=RuntimeError" in output
    assert "agent/runtime/react_runtime.py:" in output
    assert __file__ not in output
    assert secret not in plain_stream.getvalue()
    assert "app_frames=agent/runtime/react_runtime.py:" in plain_stream.getvalue()
