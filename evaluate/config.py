"""Explicit evaluation-only dotenv configuration; never import production secrets."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
import yaml

from app.agent.llm.llm_config import LLMConfig
from app.protocol.messages import ChatRequest

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent


class ConfigError(ValueError):
    """A diagnostic containing field names only, safe to print."""


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    location: dict | None = None
    shop_ids: list[int] | None = None
    required_shop_ids: list[int] = Field(default_factory=list)
    forbidden_shop_ids: list[int] = Field(default_factory=list)
    ordered_prefix: list[int] = Field(default_factory=list)
    ordered: bool = False
    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    route_mode: Literal["walking", "driving"] | None = None
    route_origin: list[float] | None = None
    route_destination: list[float] | None = None
    forbid_route: bool = True
    answer_contains: list[str] = Field(default_factory=list)
    answer_not_contains: list[str] = Field(default_factory=list)
    tool_argument_assertions: list["ToolArgumentAssertion"] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_turn(self):
        ChatRequest(message=self.message, location=self.location)
        if self.route_mode:
            if self.forbid_route:
                raise ValueError("route case requires forbid_route=false")
            for point in [self.route_origin, self.route_destination]:
                if not point or len(point) != 2 or not all(math.isfinite(v) for v in point) or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                    raise ValueError("route case requires valid GCJ02 origin and destination [lng, lat]")
        if len(set(self.required_shop_ids)) != len(self.required_shop_ids) or len(set(self.forbidden_shop_ids)) != len(self.forbidden_shop_ids):
            raise ValueError("duplicate required or forbidden shop IDs")
        if set(self.required_shop_ids) & set(self.forbidden_shop_ids):
            raise ValueError("a shop cannot be both required and forbidden")
        if self.ordered_prefix and not set(self.ordered_prefix) <= (set(self.shop_ids or []) | set(self.required_shop_ids)):
            raise ValueError("ordered_prefix must be part of expected shops")
        return self


class ToolArgumentAssertion(BaseModel):
    """A partial match against one proposed/prepared tool call, not an exact trace."""
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(min_length=1)
    stage: Literal["raw", "prepared"] = "prepared"
    contains: dict[str, object] = Field(min_length=1)


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    group: Literal["retrieval", "navigation", "robustness"]
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    turns: list[Turn] = Field(min_length=1)


@dataclass
class Config:
    values: dict[str, str]
    models: dict[str, LLMConfig]
    cases: list[Case]
    data: Path
    repeat: int
    max_requests: int
    per_attempt: int
    max_tools: int
    wall_s: float
    interval_s: float
    judge: LLMConfig | None
    judge_max_requests: int
    quality_threshold: float
    map_mode: str

    def ready(self, model: LLMConfig) -> bool:
        return bool(model.api_key and model.base_url and model.model)


def path(value: str) -> Path:
    return (BASE / value).resolve()


def number(values, key, default, *, integer=False, minimum=0):
    raw = values.get(key, str(default))
    try:
        value = int(raw) if integer else float(raw)
    except (ValueError, TypeError):
        raise ConfigError(f"{key} must be a number") from None
    if not math.isfinite(value) or value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}")
    return value


def boolean(values, key, default=True):
    value = values.get(key, str(default)).lower()
    if value not in {"true", "false", "1", "0"}:
        raise ConfigError(f"{key} must be true/false")
    return value in {"true", "1"}


def profile(values, prefix):
    url = values.get(prefix + "BASE_URL", "").rstrip("/")
    if url:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ConfigError(f"{prefix}BASE_URL must be an HTTP(S) endpoint without credentials/query")
    mode = values.get(prefix + "API_MODE", "chat_completions")
    if mode not in {"responses", "chat_completions"}:
        raise ConfigError(f"{prefix}API_MODE must be responses/chat_completions")
    try:
        extra = json.loads(values.get(prefix + "EXTRA_PARAMETERS", "{}"))
    except ValueError:
        raise ConfigError(f"{prefix}EXTRA_PARAMETERS must be valid JSON") from None
    if not isinstance(extra, dict):
        raise ConfigError(f"{prefix}EXTRA_PARAMETERS must be a JSON object")
    if set(extra) & {"model", "messages", "input", "tools", "tool_choice", "stream", "instructions", "headers"}:
        raise ConfigError(f"{prefix}EXTRA_PARAMETERS cannot override protocol controls")
    return LLMConfig(
        api_key=values.get(prefix + "API_KEY", ""), base_url=url, model=values.get(prefix + "MODEL", ""),
        api_mode=mode, timeout_seconds=number(values, "EVAL_REQUEST_TIMEOUT_S", 45, minimum=0.1),
        temperature=number(values, prefix + "TEMPERATURE", 0.2),
        max_tokens=number(values, "EVAL_MAX_OUTPUT_TOKENS", 1024, integer=True, minimum=1),
        auth_header=values.get(prefix + "AUTH_HEADER", "Authorization"),
        send_temperature=boolean(values, prefix + "SEND_TEMPERATURE"),
        token_limit_parameter=values.get(prefix + "TOKEN_PARAMETER", "max_tokens"), extra_parameters=extra,
    )


def load(env_file=BASE / ".env", models=None, cases_file=None, repeat=None) -> Config:
    env_file = Path(env_file)
    # No global environment mutation or interpolation of unrelated production variables.
    values = {k: v for k, v in dotenv_values(env_file, interpolate=False).items() if k.startswith("EVAL_") and v is not None} if env_file.exists() else {}
    values.update({k: v for k, v in os.environ.items() if k.startswith("EVAL_")})
    names = [name.strip() for name in (models or values.get("EVAL_MODELS", "default")).split(",")]
    if not names or len(set(names)) != len(names) or any(not re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in names):
        raise ValueError("EVAL_MODELS must contain unique lowercase profile names")
    selected = {name: profile(values, "EVAL_LLM_" if name == "default" else f"EVAL_{name.upper()}_") for name in names}
    raw = yaml.safe_load(path(str(cases_file or values.get("EVAL_CASES", "datasets/public/smoke.yaml"))).read_text())
    if not isinstance(raw, list) or not raw:
        raise ValueError("cases must be a nonempty YAML list")
    cases = [Case.model_validate(item) for item in raw]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("duplicate case IDs")
    data = path(values.get("EVAL_DATA", "fixtures/arcades/synthetic.jsonl"))
    rows = [json.loads(line) for line in data.read_text().splitlines() if line.strip()]
    if not rows or any(not isinstance(row, dict) or any(row.get(k) in (None, "") for k in
                      ["source", "source_id", "source_url", "name"]) or type(row.get("source_id")) is not int for row in rows):
        raise ConfigError("EVAL_DATA requires nonempty rows with source, integer source_id, source_url and name")
    ids = {row["source_id"] for row in rows}
    if len(ids) != len(rows):
        raise ConfigError("EVAL_DATA contains duplicate source_id values")
    for case in cases:
        for turn in case.turns:
            expected_ids = set(turn.shop_ids or []) | set(turn.required_shop_ids) | set(turn.forbidden_shop_ids) | set(turn.ordered_prefix)
            if expected_ids and not expected_ids <= ids:
                raise ValueError(f"{case.id}: oracle IDs absent from dataset")
    judge = profile(values, "EVAL_JUDGE_") if boolean(values, "EVAL_JUDGE_ENABLED", False) else None
    map_mode = values.get("EVAL_MAP_MODE", "disabled")
    if map_mode not in {"disabled", "live"}:
        raise ValueError("EVAL_MAP_MODE must be disabled/live")
    config = Config(values, selected, cases, data,
        int(repeat) if repeat is not None else number(values, "EVAL_REPEAT", 1, integer=True, minimum=1),
        number(values, "EVAL_MAX_REQUESTS", 40, integer=True, minimum=1),
        number(values, "EVAL_MAX_CALLS_PER_ATTEMPT", 40, integer=True, minimum=1),
        number(values, "EVAL_MAX_TOOL_CALLS", 40, integer=True, minimum=1),
        number(values, "EVAL_ATTEMPT_TIMEOUT_S", 120, minimum=0.1),
        number(values, "EVAL_REQUEST_INTERVAL_S", 1), judge,
        number(values, "EVAL_JUDGE_MAX_REQUESTS", 20, integer=True, minimum=1),
        number(values, "EVAL_QUALITY_THRESHOLD", 75), map_mode)
    if config.repeat < 1 or config.quality_threshold > 100:
        raise ValueError("invalid repeat or quality threshold")
    return config
