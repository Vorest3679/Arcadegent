"""Unified tool registry with schema validation and execution dispatch."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from app.agent.tools.builtin.db_query_tool import DBQueryTool
from app.agent.tools.builtin.geo_resolve_tool import GeoResolveTool
from app.agent.tools.builtin.route_plan_tool import RoutePlanTool
from app.agent.tools.builtin.select_next_subagent_tool import SelectNextSubagentTool
from app.agent.tools.builtin.summary_tool import SummaryTool
from app.agent.tools.mcp_gateway import MCPToolGateway
from app.agent.tools.permission import ToolPermissionChecker, ToolPermissionError
from app.agent.tools.schemas import (
    DBQueryArgs,
    GeoResolveArgs,
    RoutePlanArgs,
    SelectNextSubagentArgs,
    SummaryArgs,
    TOOL_ARG_MODELS,
    build_tool_definitions,
)
from app.infra.observability.logger import get_logger
from app.protocol.messages import Location, RouteSummaryDto

_REGION_CODE_PATTERN = re.compile(r"^\d{12}$")
logger = get_logger(__name__)


def _as_region_code_or_name(
    code_value: str | None,
    name_value: str | None,
) -> tuple[str | None, str | None]:
    code = code_value.strip() if isinstance(code_value, str) else None
    name = name_value.strip() if isinstance(name_value, str) else None
    if code and not _REGION_CODE_PATTERN.fullmatch(code):
        if not name:
            name = code
        code = None
    return code or None, name or None


def _short(text: str | None, *, limit: int = 80) -> str:
    if not isinstance(text, str):
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)].rstrip()}..."


@dataclass(frozen=True)
class ToolExecutionResult:
    """Normalized tool execution output."""

    call_id: str
    tool_name: str
    status: str
    output: dict[str, Any]
    error_message: str | None = None


class ToolRegistry:
    """Schema-first runtime entrypoint for builtin tools."""

    def __init__(
        self,
        *,
        db_query_tool: DBQueryTool,
        geo_resolve_tool: GeoResolveTool,
        route_plan_tool: RoutePlanTool,
        summary_tool: SummaryTool,
        select_next_subagent_tool: SelectNextSubagentTool,
        permission_checker: ToolPermissionChecker,
        mcp_tool_gateway: MCPToolGateway | None = None,
        strict_schema: bool = True,
    ) -> None:
        self._db_query_tool = db_query_tool
        self._geo_resolve_tool = geo_resolve_tool
        self._route_plan_tool = route_plan_tool
        self._summary_tool = summary_tool
        self._select_next_subagent_tool = select_next_subagent_tool
        self._permission_checker = permission_checker
        self._mcp_tool_gateway = mcp_tool_gateway or MCPToolGateway()
        self._strict_schema = strict_schema

    def tool_definitions(self, *, allowed_tools: list[str]) -> list[dict[str, Any]]:
        builtin = build_tool_definitions(allowed_tools, strict=self._strict_schema)
        dynamic = self._mcp_tool_gateway.build_tool_definitions(
            allowed_tools=allowed_tools,
            strict=self._strict_schema,
        )
        return builtin + dynamic

    def refresh_mcp_tools(self) -> None:
        self._mcp_tool_gateway.refresh()

    def mcp_health(self) -> dict[str, Any]:
        return self._mcp_tool_gateway.health()

    def execute(
        self,
        *,
        call_id: str,
        tool_name: str,
        raw_arguments: dict[str, Any],
        allowed_tools: list[str],
    ) -> ToolExecutionResult:
        try:
            self._permission_checker.ensure_allowed(tool_name=tool_name, allowed_tools=allowed_tools)
            if self._mcp_tool_gateway.has_tool(tool_name):
                mcp_result = self._mcp_tool_gateway.execute(
                    tool_name=tool_name,
                    raw_arguments=raw_arguments,
                )
                return ToolExecutionResult(
                    call_id=call_id,
                    tool_name=tool_name,
                    status=mcp_result.status,
                    output=mcp_result.output,
                    error_message=mcp_result.error_message,
                )
            validated = self._validate_arguments(tool_name=tool_name, raw_arguments=raw_arguments)
            output = self._dispatch(tool_name=tool_name, validated=validated)
            return ToolExecutionResult(
                call_id=call_id,
                tool_name=tool_name,
                status="completed",
                output=output,
            )
        except ToolPermissionError as exc:
            return self._failed(
                call_id=call_id,
                tool_name=tool_name,
                error_type="permission_error",
                message=str(exc),
            )
        except ValidationError as exc:
            return self._failed(
                call_id=call_id,
                tool_name=tool_name,
                error_type="validation_error",
                message=str(exc),
                details=exc.errors(),
            )
        except Exception as exc:  # pragma: no cover
            return self._failed(
                call_id=call_id,
                tool_name=tool_name,
                error_type="runtime_error",
                message=str(exc),
            )

    def _validate_arguments(self, *, tool_name: str, raw_arguments: dict[str, Any]) -> Any:
        model = TOOL_ARG_MODELS.get(tool_name)
        if model is None:
            raise ValueError(f"unknown_tool:{tool_name}")
        return model.model_validate(raw_arguments)

    def _dispatch(self, *, tool_name: str, validated: Any) -> dict[str, Any]:
        if tool_name == "db_query_tool":
            args = validated if isinstance(validated, DBQueryArgs) else DBQueryArgs.model_validate(validated)
            if args.shop_id is not None:
                shop = self._db_query_tool.get_shop(args.shop_id)
                return {"shop": shop}

            province_code, province_name = _as_region_code_or_name(
                args.province_code,
                args.province_name,
            )
            city_code, city_name = _as_region_code_or_name(
                args.city_code,
                args.city_name,
            )
            county_code, county_name = _as_region_code_or_name(
                args.county_code,
                args.county_name,
            )
            sort_title_name = args.sort_title_name
            if args.sort_by == "title_quantity" and not (sort_title_name or "").strip():
                keyword = (args.keyword or "").strip()
                if keyword:
                    parts = [part for part in re.split(r"\s+", keyword) if part]
                    if parts:
                        sort_title_name = parts[-1]

            rows, total = self._db_query_tool.search_shops(
                keyword=args.keyword,
                province_code=province_code,
                city_code=city_code,
                county_code=county_code,
                province_name=province_name,
                city_name=city_name,
                county_name=county_name,
                has_arcades=args.has_arcades,
                page=args.page,
                page_size=args.page_size,
                sort_by=args.sort_by,
                sort_order=args.sort_order,
                sort_title_name=sort_title_name,
            )
            logger.info(
                "db_query_tool.filters keyword=%s province_code=%s city_code=%s county_code=%s province_name=%s city_name=%s county_name=%s has_arcades=%s sort_by=%s sort_order=%s sort_title_name=%s page=%s page_size=%s total=%s",
                _short(args.keyword),
                province_code,
                city_code,
                county_code,
                province_name,
                city_name,
                county_name,
                args.has_arcades,
                args.sort_by,
                args.sort_order,
                _short(sort_title_name),
                args.page,
                args.page_size,
                total,
            )
            return {
                "shops": rows,
                "total": total,
                "query": {
                    "keyword": args.keyword,
                    "province_code": province_code,
                    "city_code": city_code,
                    "county_code": county_code,
                    "province_name": province_name,
                    "city_name": city_name,
                    "county_name": county_name,
                    "has_arcades": args.has_arcades,
                    "sort_by": args.sort_by,
                    "sort_order": args.sort_order,
                    "sort_title_name": sort_title_name,
                    "page": args.page,
                    "page_size": args.page_size,
                },
            }

        if tool_name == "geo_resolve_tool":
            args = validated if isinstance(validated, GeoResolveArgs) else GeoResolveArgs.model_validate(validated)
            provider = self._geo_resolve_tool.resolve_provider(args.province_code)
            return {"provider": provider}

        if tool_name == "route_plan_tool":
            args = validated if isinstance(validated, RoutePlanArgs) else RoutePlanArgs.model_validate(validated)
            origin = Location(lng=args.origin.lng, lat=args.origin.lat)
            destination = Location(lng=args.destination.lng, lat=args.destination.lat)
            route = None
            if args.provider == "amap":
                route = self._mcp_tool_gateway.plan_amap_route(
                    mode=args.mode,
                    origin=origin,
                    destination=destination,
                )
            if route is None:
                route = self._route_plan_tool.plan_route(
                    provider=args.provider,
                    mode=args.mode,
                    origin=origin,
                    destination=destination,
                )
            return {"route": route.model_dump(mode="json")}

        if tool_name == "summary_tool":
            args = validated if isinstance(validated, SummaryArgs) else SummaryArgs.model_validate(validated)
            if args.topic == "navigation":
                route_payload = args.route or {}
                route = RouteSummaryDto.model_validate(route_payload)
                reply = self._summary_tool.summarize_navigation(
                    shop_name=args.shop_name or "target arcade",
                    route=route,
                )
            else:
                reply = self._summary_tool.summarize_search(
                    keyword=args.keyword,
                    total=int(args.total or 0),
                    shops=args.shops or [],
                    sort_by=args.sort_by,
                    sort_order=args.sort_order,
                    sort_title_name=args.sort_title_name,
                )
            return {"reply": reply}

        if tool_name == "select_next_subagent":
            args = (
                validated
                if isinstance(validated, SelectNextSubagentArgs)
                else SelectNextSubagentArgs.model_validate(validated)
            )
            return self._select_next_subagent_tool.select_next_subagent(
                current_subagent=args.current_subagent,
                intent=args.intent,
                tool_name=args.tool_name,
                tool_status=args.tool_status,
                has_route=args.has_route,
                has_shops=args.has_shops,
            )

        raise ValueError(f"unknown_tool:{tool_name}")

    def _failed(
        self,
        *,
        call_id: str,
        tool_name: str,
        error_type: str,
        message: str,
        details: list[ErrorDetails] | list[dict[str, Any]] | None = None,
    ) -> ToolExecutionResult:
        payload: dict[str, Any] = {
            "error": {
                "type": error_type,
                "message": message,
            }
        }
        if details is not None:
            payload["error"]["details"] = details
        return ToolExecutionResult(
            call_id=call_id,
            tool_name=tool_name,
            status="failed",
            output=payload,
            error_message=message,
        )
