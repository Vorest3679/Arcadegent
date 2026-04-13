"""Execution and result normalization for MCP-backed tools."""

from __future__ import annotations

# 这个模块定义了 MCPDispatcher 类，用于执行 MCP 工具并规范化其响应，
# 以供运行时使用。
# 以下是一些辅助函数，用于处理工具执行的输入和输出：
# - `_coerce_int()`: 将任意值尝试转换为整数，如果无法转换则返回 None。
# - `_serialize_json_safe()`: 尝试将值序列化为 JSON，如果失败则返回字符串表示。
# - `_serialize_content_block()`: 将工具执行结果中的内容块序列化为字典格式。
# - `_extract_text_from_content()`: 从工具执行结果的内容中提取文本信息。
# - `_coerce_geo_point()`: 根据提供的经纬度和坐标系信息构造 GeoPoint 对象。
# - `_parse_polyline()`: 将字符串格式的路径信息解析为 GeoPoint 对象列表。
# - `_normalize_polyline()`: 将不同格式的路径信息规范化为 Location 对象列表。
# - `_infer_mode()`: 根据工具的远程名称和输入参数推断出路径规划的模式。
# - `_fallback_polyline()`: 从输入参数中提取路径信息作为备选方案。
# - `_extract_route_from_mapping()`: 从工具执行结果的不同层级中提取路径信息并构建 RouteSummaryDto。
# - `maybe_extract_route_payload()`: 尝试从工具执行结果中提取路径信息。
# - `pick_route_descriptor()`: 从工具描述符列表中选择一个最适合路径规划的工具描述符。
# - `build_route_arguments()`: 根据工具描述符和路径规划输入参数构建工具执行参数字典。

import json
from typing import Any

from app.agent.tools.mcp.client_manager import MCPClientManager
from app.agent.tools.mcp.discovery import coerce_str
from app.agent.tools.mcp.models import MCPExecutionResult, MCPServerConfig, MCPToolDescriptor
from app.protocol.messages import GeoPoint, Location, RouteSummaryDto


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _serialize_json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _serialize_content_block(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        try:
            dumped = block.model_dump(mode="json")  # type: ignore[attr-defined]
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    block_type = getattr(block, "type", None)
    text = getattr(block, "text", None)
    payload: dict[str, Any] = {"type": block_type or type(block).__name__}
    if isinstance(text, str):
        payload["text"] = text
    else:
        payload["value"] = str(block)
    return payload


def _extract_text_from_content(content: list[dict[str, Any]]) -> str | None:
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    merged = "\n".join(chunks).strip()
    return merged or None


def _coerce_geo_point(
    *,
    lng: float,
    lat: float,
    coord_system: str,
    source: str,
    precision: str = "approx",
) -> GeoPoint:
    return GeoPoint(
        lng=lng,
        lat=lat,
        coord_system=coord_system,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        precision=precision,  # type: ignore[arg-type]
    )


def _parse_polyline(polyline: str) -> list[GeoPoint]:
    points: list[GeoPoint] = []
    for point in polyline.split(";"):
        raw = point.strip()
        if not raw:
            continue
        lng_lat = raw.split(",")
        if len(lng_lat) != 2:
            continue
        try:
            lng = float(lng_lat[0])
            lat = float(lng_lat[1])
        except ValueError:
            continue
        points.append(
            _coerce_geo_point(
                lng=lng,
                lat=lat,
                coord_system="gcj02",
                source="route",
            )
        )
    return points


def _normalize_polyline(value: Any) -> list[GeoPoint]:
    if isinstance(value, str):
        return _parse_polyline(value)
    if not isinstance(value, list):
        return []
    points: list[GeoPoint] = []
    for item in value:
        if isinstance(item, dict):
            lng = item.get("lng", item.get("lon", item.get("longitude")))
            lat = item.get("lat", item.get("latitude"))
            try:
                if lng is None or lat is None:
                    continue
                points.append(
                    _coerce_geo_point(
                        lng=float(lng),
                        lat=float(lat),
                        coord_system=str(item.get("coord_system") or "gcj02"),
                        source=str(item.get("source") or "route"),
                        precision=str(item.get("precision") or "approx"),
                    )
                )
            except (TypeError, ValueError):
                continue
    return points


def _infer_mode(*, remote_name: str, arguments: dict[str, Any]) -> str:
    mode = coerce_str(arguments.get("mode"))
    if mode in {"walking", "driving"}:
        return mode
    normalized = remote_name.lower()
    if "walk" in normalized or "walking" in normalized:
        return "walking"
    if "drive" in normalized or "driving" in normalized:
        return "driving"
    return "walking"


def _fallback_point(arguments: dict[str, Any], key: str) -> GeoPoint | None:
    raw = arguments.get(key)
    if not isinstance(raw, dict):
        return None
    lng = raw.get("lng")
    lat = raw.get("lat")
    try:
        if lng is None or lat is None:
            return None
        if key == "origin":
            return _coerce_geo_point(
                lng=float(lng),
                lat=float(lat),
                coord_system=str(raw.get("coord_system") or "wgs84"),
                source=str(raw.get("source") or "client"),
            )
        return _coerce_geo_point(
            lng=float(lng),
            lat=float(lat),
            coord_system=str(raw.get("coord_system") or "gcj02"),
            source=str(raw.get("source") or "route"),
        )
    except (TypeError, ValueError):
        return None


def _fallback_polyline(arguments: dict[str, Any]) -> list[GeoPoint]:
    points: list[GeoPoint] = []
    for key in ("origin", "destination"):
        point = _fallback_point(arguments, key)
        if point is not None:
            points.append(point)
    return points

"""
从工具执行结果的不同层级中提取路径信息并构建RouteSummaryDto对象。
这个函数会递归地检查工具执行结果的不同部分，以寻找可能包含路径信息的字段。
一旦找到相关信息，就会尝试构建一个RouteSummaryDto对象来表示路径规划的结果。"""
def _extract_route_from_mapping(
    payload: dict[str, Any],
    *,
    remote_name: str,
    raw_arguments: dict[str, Any],
    depth: int,
) -> RouteSummaryDto | None:
    if depth > 2: # 限制递归深度，避免过度嵌套导致性能问题
        return None

    mode = _infer_mode(remote_name=remote_name, arguments=raw_arguments)

    if "provider" in payload and "mode" in payload:
        try:
            return RouteSummaryDto.model_validate(payload)
        except Exception:
            pass

    distance_m = _coerce_int(payload.get("distance_m", payload.get("distance")))
    duration_s = _coerce_int(payload.get("duration_s", payload.get("duration")))
    polyline = _normalize_polyline(payload.get("polyline"))
    hint = coerce_str(payload.get("hint"))

    if distance_m is not None or duration_s is not None:
        return RouteSummaryDto(
            provider="amap",
            mode=mode,
            distance_m=distance_m,
            duration_s=duration_s,
            origin=_fallback_point(raw_arguments, "origin"),
            destination=_fallback_point(raw_arguments, "destination"),
            polyline=polyline or _fallback_polyline(raw_arguments),
            hint=hint,
        )
    # 尝试在更深层级中寻找路径信息
    route_obj = payload.get("route")
    if isinstance(route_obj, dict):
        route = _extract_route_from_mapping(
            route_obj,
            remote_name=remote_name,
            raw_arguments=raw_arguments,
            depth=depth + 1,
        )
        if route is not None:
            return route
    # 找到了路径信息的常见格式，继续解析
    paths = payload.get("paths")
    if isinstance(paths, list) and paths:
        first = paths[0] if isinstance(paths[0], dict) else None
        if isinstance(first, dict):
            distance_m = _coerce_int(first.get("distance"))
            duration_s = _coerce_int(first.get("duration"))
            points: list[GeoPoint] = []
            steps = first.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    polyline_text = step.get("polyline")
                    if isinstance(polyline_text, str):
                        points.extend(_parse_polyline(polyline_text))
            return RouteSummaryDto(
                provider="amap",
                mode=mode,
                distance_m=distance_m,
                duration_s=duration_s,
                origin=_fallback_point(raw_arguments, "origin"),
                destination=_fallback_point(raw_arguments, "destination"),
                polyline=points or _fallback_polyline(raw_arguments),
                hint=hint,
            )

    for nested_key in ("data", "result", "payload"):
        nested = payload.get(nested_key)
        if not isinstance(nested, dict):
            continue
        route = _extract_route_from_mapping(
            nested,
            remote_name=remote_name,
            raw_arguments=raw_arguments,
            depth=depth + 1,
        )
        if route is not None:
            return route
    return None


def maybe_extract_route_payload(
    *,
    descriptor: MCPToolDescriptor,
    raw_arguments: dict[str, Any],
    structured_content: Any,
    data: Any,
) -> RouteSummaryDto | None:
    candidates: list[Any] = []
    if isinstance(structured_content, dict):
        candidates.append(structured_content)
    if isinstance(data, dict) and data is not structured_content:
        candidates.append(data)
    for candidate in candidates:
        route = _extract_route_from_mapping(
            candidate,
            remote_name=descriptor.remote_name,
            raw_arguments=raw_arguments,
            depth=0,
        )
        if route is not None:
            return route
    return None

"""
从工具描述符列表中选择一个最适合路径规划的工具描述符。
这个函数会根据工具的远程名称、描述和输入参数等信息，对候选工具进行打分和排序，
以选择出最可能是路径规划工具的描述符。"""
def pick_route_descriptor(
    *,
    descriptors: list[MCPToolDescriptor],
    server_name: str,
    mode: str,
) -> MCPToolDescriptor | None:
    candidates = [item for item in descriptors if item.server_name == server_name]
    if not candidates:
        return None
    ranked: list[tuple[int, MCPToolDescriptor]] = []
    for descriptor in candidates:
        text = " ".join(
            [
                descriptor.remote_name.lower(),
                descriptor.local_name.lower(),
                descriptor.description.lower(),
                json.dumps(descriptor.input_schema, ensure_ascii=False).lower(),
            ]
        )
        score = 0
        if "route" in text or "direction" in text or "路径" in text or "路线" in text or "导航" in text:
            score += 4
        if mode == "walking" and ("walk" in text or "walking" in text or "步行" in text):
            score += 3
        if mode == "driving" and ("drive" in text or "driving" in text or "驾车" in text or "开车" in text):
            score += 3
        if "origin" in text and "destination" in text:
            score += 1
        if score > 0:
            ranked.append((score, descriptor))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1].local_name))
    return ranked[0][1]


def build_route_arguments(
    *,
    descriptor: MCPToolDescriptor,
    origin: Location,
    destination: Location,
    mode: str,
) -> dict[str, Any]:
    properties = descriptor.input_schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    origin_string = f"{origin.lng},{origin.lat}"
    destination_string = f"{destination.lng},{destination.lat}"
    origin_object = {"lng": origin.lng, "lat": origin.lat}
    destination_object = {"lng": destination.lng, "lat": destination.lat}

    def _match_value(name: str) -> Any:
        schema = properties.get(name)
        schema_type = schema.get("type") if isinstance(schema, dict) else None
        if name in {"origin", "from", "start"}:
            return origin_object if schema_type == "object" else origin_string
        if name in {"destination", "to", "end"}:
            return destination_object if schema_type == "object" else destination_string
        if name == "mode":
            return mode
        return None

    arguments: dict[str, Any] = {}
    for key in properties:
        value = _match_value(key)
        if value is not None:
            arguments[key] = value

    if not arguments:
        arguments = {
            "origin": origin_string,
            "destination": destination_string,
        }
    return arguments


class MCPDispatcher:
    """Execute MCP tools and normalize responses for the runtime."""

    def __init__(self, *, client_manager: MCPClientManager | None = None) -> None:
        self._client_manager = client_manager or MCPClientManager()

    async def execute(
        self,
        *,
        config: MCPServerConfig,
        descriptor: MCPToolDescriptor,
        raw_arguments: dict[str, Any],
    ) -> MCPExecutionResult:
        if not isinstance(raw_arguments, dict):
            return MCPExecutionResult(
                status="failed",
                output={"error": {"type": "validation_error", "message": "tool arguments must be an object"}},
                error_message="tool arguments must be an object",
            )

        try:
            result = await self._client_manager.call_tool(
                config=config,
                remote_name=descriptor.remote_name,
                arguments=raw_arguments,
            )
        except Exception as exc:
            message = str(exc)
            return MCPExecutionResult(
                status="failed",
                output={"error": {"type": "runtime_error", "message": message}},
                error_message=message,
            )

        content = [_serialize_content_block(item) for item in list(getattr(result, "content", []) or [])]
        structured_content = getattr(result, "structured_content", None)
        data = getattr(result, "data", None)
        is_error = bool(getattr(result, "is_error", False))
        payload: dict[str, Any] = {
            "server": descriptor.server_name,
            "tool": descriptor.remote_name,
            "content": content,
            "structured_content": _serialize_json_safe(structured_content),
            "data": _serialize_json_safe(data),
            "is_error": is_error,
        }
        route = maybe_extract_route_payload(
            descriptor=descriptor,
            raw_arguments=raw_arguments,
            structured_content=structured_content,
            data=data,
        )
        if route is not None:
            payload["route"] = route.model_dump(mode="json")

        if is_error:
            message = (
                _extract_text_from_content(content)
                or coerce_str(payload.get("data"))
                or f"mcp tool '{descriptor.remote_name}' returned is_error=true"
            )
            payload["error"] = {"type": "runtime_error", "message": message}
            return MCPExecutionResult(status="failed", output=payload, error_message=message)
        return MCPExecutionResult(status="completed", output=payload)
