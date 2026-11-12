"""Agent logging helpers are standard-library only and shared with the application logger."""

from __future__ import annotations

from app.agent import _logging as agent_logging
from app.infra.observability import logger as app_logger


def test_application_logger_reuses_the_agent_redaction_helpers() -> None:
    assert app_logger.log_ref is agent_logging.log_ref
    assert app_logger.log_public_label is agent_logging.log_public_label
    assert app_logger.log_exception_frames is agent_logging.log_exception_frames
    # One key: the same id correlates across agent and application log lines.
    assert app_logger.log_ref("session-1") == agent_logging.log_ref("session-1") != "session-1"
    assert agent_logging.log_ref(None) == "-"


def test_public_label_keeps_known_values_only() -> None:
    assert agent_logging.log_public_label("amap", {"amap"}) == "amap"
    assert agent_logging.log_public_label("secret", {"amap"}).startswith("ref:")


def test_exception_frames_are_relative_to_the_application_tree() -> None:
    try:
        raise RuntimeError("detail that must not reach the log")
    except RuntimeError as exc:
        frames = agent_logging.log_exception_frames(exc)

    # The application logger widened the root to backend/app at import time,
    # so test and agent frames keep the historical ``agent/...`` style paths.
    assert "tests/integration/test_agent_logging.py:" in frames
    assert not frames.startswith("/") and "detail" not in frames


def test_agent_logging_imports_no_application_module() -> None:
    source = agent_logging.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        assert "from app." not in handle.read()
