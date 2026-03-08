"""Context assembly for ReAct runtime turns."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.context.context_payload import (
    ContextBlockKey,
    ContextBlockRefDto,
    ContextDirectoryDto,
    QueryContextDto,
    RouteContextDto,
    RuntimeContextPayloadDto,
    SearchCatalogContextDto,
    SearchCatalogShopDto,
    ShopArcadeContextDto,
    ShopBasicContextDto,
    ShopCommentContextDto,
    ShopDetailContextDto,
    ShopTransportContextDto,
)
from app.agent.runtime.session_state import AgentSessionState, AgentTurn
from app.agent.subagents.subagent_builder import SubAgentProfile
from app.protocol.messages import ChatRequest


@dataclass(frozen=True)
class BuiltContext:
    """Prepared prompt payload for provider adapter."""

    instructions: str
    messages: list[dict[str, Any]]


class ContextBuilder:
    """Build model instructions and messages from session history."""

    def __init__(
        self,
        *,
        prompt_root: Path,
        history_turn_limit: int,
        skill_root: Path | None = None,
    ) -> None:
        self._prompt_root = prompt_root
        self._skill_root = skill_root
        self._history_turn_limit = max(4, history_turn_limit)
        self._prompt_cache: dict[str, str] = {}
        self._skill_cache: dict[str, str] = {}

    def build(
        self,
        *,
        session_state: AgentSessionState,
        request: ChatRequest,
        subagent: SubAgentProfile,
    ) -> BuiltContext:
        base_prompt = self._load_prompt("system_base.md").strip()
        subagent_prompt = self._load_prompt(subagent.prompt_file).strip()
        skill_block = self._build_skill_block(subagent.skill_files)
        context_payload = self._build_context_payload(
            session_state=session_state,
            request=request,
            subagent=subagent,
        )
        runtime_hint = {
            "session_id": session_state.session_id,
            "turn_index": session_state.turn_index,
            "active_subagent": session_state.active_subagent,
            "intent": session_state.intent,
            "request": request.model_dump(mode="json"),
            "memory_summary": {
                "has_shops": bool(session_state.working_memory.get("shops")),
                "has_route": bool(session_state.working_memory.get("route")),
            },
            "context_payload": self._compact_value(
                context_payload.model_dump(mode="json", exclude_none=True)
            ),
        }

        instruction_parts = [base_prompt]
        if skill_block:
            instruction_parts.append(skill_block)
        instruction_parts.extend(
            (
                subagent_prompt,
                "Runtime state (JSON):",
                json.dumps(runtime_hint, ensure_ascii=False),
            )
        )
        instructions = "\n\n".join(part for part in instruction_parts if part)
        messages = [self._to_model_message(turn) for turn in self._tail_turns(session_state.turns)]
        return BuiltContext(instructions=instructions, messages=messages)

    def _tail_turns(self, turns: list[AgentTurn]) -> list[AgentTurn]:
        if len(turns) <= self._history_turn_limit:
            return turns
        return turns[-self._history_turn_limit :]

    def _to_model_message(self, turn: AgentTurn) -> dict[str, Any]:
        if turn.role == "tool":
            payload: dict[str, Any] = {
                "role": "tool",
                "content": turn.content,
            }
            if turn.name:
                payload["name"] = turn.name
            if turn.call_id:
                payload["tool_call_id"] = turn.call_id
            return payload
        return {"role": turn.role, "content": turn.content}

    def _build_skill_block(self, skill_files: list[str]) -> str:
        sections: list[str] = []
        for filename in skill_files:
            content = self._load_skill(filename).strip()
            if not content:
                continue
            sections.append(f"Skill reference: {filename}\n{content}")
        if not sections:
            return ""
        return "\n\n".join(sections)

    def _build_context_payload(
        self,
        *,
        session_state: AgentSessionState,
        request: ChatRequest,
        subagent: SubAgentProfile,
    ) -> RuntimeContextPayloadDto:
        query = self._build_query_context(session_state=session_state, request=request)
        search_catalog = self._build_search_catalog(session_state=session_state)
        shop_details = self._build_shop_details(session_state=session_state)
        route = self._build_route_context(session_state=session_state)
        directory = self._build_directory(
            session_state=session_state,
            subagent=subagent,
            query=query,
            search_catalog=search_catalog,
            shop_details=shop_details,
            route=route,
        )
        return RuntimeContextPayloadDto(
            directory=directory,
            query=query,
            search_catalog=search_catalog,
            shop_details=shop_details,
            route=route,
        )

    def _build_directory(
        self,
        *,
        session_state: AgentSessionState,
        subagent: SubAgentProfile,
        query: QueryContextDto | None,
        search_catalog: SearchCatalogContextDto | None,
        shop_details: list[ShopDetailContextDto],
        route: RouteContextDto | None,
    ) -> ContextDirectoryDto:
        available_blocks: list[ContextBlockRefDto] = []
        reading_order: list[ContextBlockKey] = []

        if route is not None:
            available_blocks.append(
                ContextBlockRefDto(
                    block="route",
                    purpose="Primary navigation facts for the final answer.",
                    primary_fields=["destination_name", "mode", "distance_m", "duration_s", "hint"],
                )
            )
            reading_order.append("route")
        if search_catalog is not None:
            available_blocks.append(
                ContextBlockRefDto(
                    block="search_catalog",
                    purpose="Top-level matched shop count and ranking preview.",
                    primary_fields=["total", "top_shops"],
                )
            )
            reading_order.append("search_catalog")
        if query is not None:
            available_blocks.append(
                ContextBlockRefDto(
                    block="query",
                    purpose="Structured filters and sort conditions behind the current result.",
                    primary_fields=["keyword", "region", "sort_by", "sort_order", "sort_title_name"],
                )
            )
            reading_order.append("query")
        if shop_details:
            available_blocks.append(
                ContextBlockRefDto(
                    block="shop_details",
                    purpose="Per-shop detail sections such as transport, arcades, and comments.",
                    primary_fields=["basic", "transport", "arcades", "comment"],
                )
            )
            reading_order.append("shop_details")

        # Keep detail blocks after primary catalog/route blocks unless route is the only answer anchor.
        if route is not None and "shop_details" in reading_order:
            reading_order = ["route", "search_catalog", "query", "shop_details"]
            reading_order = [item for item in reading_order if item in {block.block for block in available_blocks}]
        elif search_catalog is not None and "shop_details" in reading_order:
            reading_order = ["search_catalog", "query", "shop_details"]
            reading_order = [item for item in reading_order if item in {block.block for block in available_blocks}]

        return ContextDirectoryDto(
            active_intent=session_state.intent,
            active_subagent=subagent.name,
            available_blocks=available_blocks,
            reading_order=reading_order,
            focus=self._build_focus_text(search_catalog=search_catalog, route=route),
            top_shop_ids=[
                int(item.source_id)
                for item in (search_catalog.top_shops if search_catalog is not None else [])
                if item.source_id is not None
            ],
        )

    def _build_focus_text(
        self,
        *,
        search_catalog: SearchCatalogContextDto | None,
        route: RouteContextDto | None,
    ) -> str:
        if route is not None:
            return "Use route as the main answer. Add destination detail only when it improves the reply."
        total = search_catalog.total if search_catalog is not None else None
        if isinstance(total, int) and total <= 0:
            return "State that no shop matched the current filters, then suggest another keyword or region."
        if isinstance(total, int) and total > 0:
            return "Answer with matched count first, then mention top shops. Use detail sections only when relevant."
        return "Ask for the minimum missing input and avoid guessing unavailable facts."

    def _build_query_context(
        self,
        *,
        session_state: AgentSessionState,
        request: ChatRequest,
    ) -> QueryContextDto | None:
        memory = session_state.working_memory
        query_meta = memory.get("last_db_query")
        request_page_size = request.page_size if request.page_size != 5 else None
        query = QueryContextDto(
            keyword=self._string_or_none(
                self._first_non_empty(
                    (query_meta.get("keyword") if isinstance(query_meta, dict) else None),
                    memory.get("keyword"),
                    request.keyword,
                )
            ),
            province_code=self._string_or_none(
                self._first_non_empty(
                    (query_meta.get("province_code") if isinstance(query_meta, dict) else None),
                    request.province_code,
                )
            ),
            province_name=self._string_or_none(
                query_meta.get("province_name") if isinstance(query_meta, dict) else None
            ),
            city_code=self._string_or_none(
                self._first_non_empty(
                    (query_meta.get("city_code") if isinstance(query_meta, dict) else None),
                    request.city_code,
                )
            ),
            city_name=self._string_or_none(query_meta.get("city_name") if isinstance(query_meta, dict) else None),
            county_code=self._string_or_none(
                self._first_non_empty(
                    (query_meta.get("county_code") if isinstance(query_meta, dict) else None),
                    request.county_code,
                )
            ),
            county_name=self._string_or_none(query_meta.get("county_name") if isinstance(query_meta, dict) else None),
            has_arcades=self._bool_or_none(query_meta.get("has_arcades") if isinstance(query_meta, dict) else None),
            page=self._int_or_none(query_meta.get("page") if isinstance(query_meta, dict) else None),
            page_size=self._int_or_none(
                self._first_non_empty(
                    (query_meta.get("page_size") if isinstance(query_meta, dict) else None),
                    request_page_size,
                )
            ),
            sort_by=self._string_or_none(query_meta.get("sort_by") if isinstance(query_meta, dict) else None),
            sort_order=self._string_or_none(query_meta.get("sort_order") if isinstance(query_meta, dict) else None),
            sort_title_name=self._string_or_none(
                query_meta.get("sort_title_name") if isinstance(query_meta, dict) else None
            ),
        )
        payload = self._compact_value(query.model_dump(mode="json", exclude_none=True))
        if not payload:
            return None
        return QueryContextDto.model_validate(payload)

    def _build_search_catalog(
        self,
        *,
        session_state: AgentSessionState,
    ) -> SearchCatalogContextDto | None:
        memory = session_state.working_memory
        total = self._int_or_none(memory.get("total"))
        rows = self._shop_rows_from_memory(memory)
        if total is None and not rows:
            return None

        top_shops = [
            SearchCatalogShopDto(
                source_id=self._int_or_none(row.get("source_id")),
                name=self._string_or_none(row.get("name")),
                city_name=self._string_or_none(row.get("city_name")),
                county_name=self._string_or_none(row.get("county_name")),
                arcade_count=self._int_or_none(row.get("arcade_count")),
                detail_sections=self._detail_sections(row),
            )
            for row in rows[:5]
        ]
        payload = SearchCatalogContextDto(total=total, top_shops=top_shops)
        compact = self._compact_value(payload.model_dump(mode="json", exclude_none=True))
        if not compact:
            return None
        return SearchCatalogContextDto.model_validate(compact)

    def _build_shop_details(
        self,
        *,
        session_state: AgentSessionState,
    ) -> list[ShopDetailContextDto]:
        detail_rows = self._shop_rows_from_memory(session_state.working_memory)[:3]
        details: list[ShopDetailContextDto] = []
        for row in detail_rows:
            detail = ShopDetailContextDto(
                source_id=self._int_or_none(row.get("source_id")),
                basic=ShopBasicContextDto(
                    source_id=self._int_or_none(row.get("source_id")),
                    name=self._string_or_none(row.get("name")),
                    province_name=self._string_or_none(row.get("province_name")),
                    city_name=self._string_or_none(row.get("city_name")),
                    county_name=self._string_or_none(row.get("county_name")),
                    address=self._string_or_none(row.get("address")),
                    arcade_count=self._int_or_none(row.get("arcade_count")),
                ),
                transport=self._build_transport_context(row),
                arcades=self._build_arcade_details(row),
                comment=self._build_comment_context(row),
            )
            compact = self._compact_value(detail.model_dump(mode="json", exclude_none=True))
            if compact:
                details.append(ShopDetailContextDto.model_validate(compact))
        return details

    def _build_route_context(
        self,
        *,
        session_state: AgentSessionState,
    ) -> RouteContextDto | None:
        memory = session_state.working_memory
        route = memory.get("route")
        if not isinstance(route, dict):
            return None
        destination = self._primary_destination(memory)
        payload = RouteContextDto(
            destination_source_id=self._int_or_none(destination.get("source_id") if destination else None),
            destination_name=self._string_or_none(destination.get("name") if destination else None),
            provider=self._string_or_none(route.get("provider")),
            mode=self._string_or_none(route.get("mode")),
            distance_m=self._int_or_none(route.get("distance_m")),
            duration_s=self._int_or_none(route.get("duration_s")),
            hint=self._string_or_none(route.get("hint")),
        )
        compact = self._compact_value(payload.model_dump(mode="json", exclude_none=True))
        if not compact:
            return None
        return RouteContextDto.model_validate(compact)

    def _build_transport_context(self, row: dict[str, Any]) -> ShopTransportContextDto | None:
        transport = self._string_or_none(row.get("transport"))
        if transport is None:
            return None
        return ShopTransportContextDto(summary=transport)

    def _build_comment_context(self, row: dict[str, Any]) -> ShopCommentContextDto | None:
        comment = self._string_or_none(row.get("comment"))
        if comment is None:
            return None
        return ShopCommentContextDto(summary=comment)

    def _build_arcade_details(self, row: dict[str, Any]) -> list[ShopArcadeContextDto]:
        items: list[ShopArcadeContextDto] = []
        for raw in row.get("arcades") or []:
            if not isinstance(raw, dict):
                continue
            item = ShopArcadeContextDto(
                title_name=self._string_or_none(raw.get("title_name")),
                quantity=self._int_or_none(raw.get("quantity")),
                version=self._string_or_none(raw.get("version")),
                comment=self._string_or_none(raw.get("comment")),
            )
            compact = self._compact_value(item.model_dump(mode="json", exclude_none=True))
            if compact:
                items.append(ShopArcadeContextDto.model_validate(compact))
            if len(items) >= 12:
                break
        return items

    def _detail_sections(self, row: dict[str, Any]) -> list[str]:
        sections: list[str] = ["basic"]
        if self._string_or_none(row.get("transport")):
            sections.append("transport")
        if isinstance(row.get("arcades"), list) and row.get("arcades"):
            sections.append("arcades")
        if self._string_or_none(row.get("comment")):
            sections.append("comment")
        return sections

    def _shop_rows_from_memory(self, memory: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        for raw in [memory.get("shop"), *(memory.get("shops") or [])]:
            if not isinstance(raw, dict):
                continue
            source_id = raw.get("source_id")
            if source_id is not None:
                key = ("source_id", str(source_id))
            else:
                key = ("name", str(raw.get("name") or "").strip().lower())
            if key[1] == "" or key in seen_keys:
                continue
            seen_keys.add(key)
            rows.append(raw)
        return rows

    def _primary_destination(self, memory: dict[str, Any]) -> dict[str, Any] | None:
        rows = self._shop_rows_from_memory(memory)
        if not rows:
            return None
        return rows[0]

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text or None

    def _int_or_none(self, value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _bool_or_none(self, value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        return None

    def _first_non_empty(self, *values: Any) -> Any:
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
        return None

    def _compact_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            compact: dict[str, Any] = {}
            for key, item in value.items():
                normalized = self._compact_value(item)
                if normalized in (None, "", [], {}):
                    continue
                compact[str(key)] = normalized
            return compact
        if isinstance(value, list):
            compact_list = [self._compact_value(item) for item in value]
            return [item for item in compact_list if item not in (None, "", [], {})]
        return value

    def _load_prompt(self, filename: str) -> str:
        return self._load_markdown(
            filename=filename,
            root=self._prompt_root,
            cache=self._prompt_cache,
        )

    def _load_skill(self, filename: str) -> str:
        if self._skill_root is None:
            return ""
        return self._load_markdown(
            filename=filename,
            root=self._skill_root,
            cache=self._skill_cache,
        )

    def _load_markdown(
        self,
        *,
        filename: str,
        root: Path,
        cache: dict[str, str],
    ) -> str:
        if filename in cache:
            return cache[filename]
        path = root / filename
        if not path.exists():
            content = ""
        else:
            content = path.read_text(encoding="utf-8")
        cache[filename] = content
        return content
