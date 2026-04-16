"""Deterministic formatter for search/navigation summaries."""

from __future__ import annotations

import re

from app.infra.observability.logger import get_logger
from app.protocol.messages import RouteSummaryDto

logger = get_logger(__name__)


class SummaryTool:
    """Pure formatter kept for compatibility with legacy summary tool calls."""

    def __init__(self, llm_client: object | None = None) -> None:
        _ = llm_client

    @staticmethod
    def _normalize_title_name(value: str | None) -> str:
        if not value:
            return ""
        text = str(value).strip().lower()
        text = re.sub(r"[\s_\-./]+", "", text)
        if "\u821e\u840c" in text or text.startswith("maimai"):
            return "maimai"
        if text.startswith("soundvoltex") or text == "sdvx":
            return "sdvx"
        return text

    def _title_quantity(self, row: dict, title_name: str) -> int:
        needle = self._normalize_title_name(title_name)
        if not needle:
            return 0

        total = 0
        for item in row.get("arcades") or []:
            if not isinstance(item, dict):
                continue
            raw_name = self._normalize_title_name(item.get("title_name"))
            if raw_name != needle:
                continue
            try:
                total += int(item.get("quantity") or 0)
            except (TypeError, ValueError):
                continue
        return total

    def _deterministic_title_quantity_summary(
        self,
        *,
        total: int,
        shops: list[dict],
        sort_order: str | None,
        sort_title_name: str,
    ) -> str:
        title = sort_title_name.strip()
        order = (
            "\u7531\u9ad8\u5230\u4f4e"
            if (sort_order or "desc").strip().lower() != "asc"
            else "\u7531\u4f4e\u5230\u9ad8"
        )

        preview_parts: list[str] = []
        for idx, row in enumerate(shops[:5], start=1):
            qty = self._title_quantity(row, title)
            name = str(row.get("name") or "unknown arcade")
            city = str(row.get("city_name") or "-")
            preview_parts.append(f"{idx}. {name}({city}) {qty}\u53f0")

        prefix = (
            f"\u5171\u627e\u5230 {total} \u5bb6\u673a\u5385\uff0c"
            f"\u6309 {title} \u673a\u53f0\u6570{order}\u6392\u5e8f\u3002"
        )
        if not preview_parts:
            return prefix
        preview_text = "\uff1b".join(preview_parts)
        return f"{prefix} \u5f53\u524d\u9875\u524d{len(preview_parts)}\uff1a{preview_text}\u3002"

    def _default_search_summary(
        self,
        *,
        keyword: str | None,
        total: int,
        shops: list[dict],
        sort_by: str | None,
        sort_order: str | None,
    ) -> str:
        if total <= 0:
            if keyword:
                return (
                    f"\u672a\u627e\u5230\u5339\u914d\u201c{keyword}\u201d\u7684\u673a\u5385\uff0c"
                    "\u8bf7\u5c1d\u8bd5\u5176\u4ed6\u5173\u952e\u8bcd\u6216\u533a\u57df\u3002"
                )
            return "\u672a\u627e\u5230\u7b26\u5408\u6761\u4ef6\u7684\u673a\u5385\u3002"

        summary = f"\u5171\u627e\u5230 {total} \u5bb6\u673a\u5385\u3002"
        normalized_sort = (sort_by or "default").strip().lower()
        normalized_order = (sort_order or "desc").strip().lower()
        if normalized_sort == "updated_at":
            order = "\u6700\u65b0\u5728\u524d" if normalized_order != "asc" else "\u6700\u65e9\u5728\u524d"
            summary = f"{summary} \u7ed3\u679c\u6309\u66f4\u65b0\u65f6\u95f4{order}\u3002"
        elif normalized_sort == "arcade_count":
            order = "\u7531\u9ad8\u5230\u4f4e" if normalized_order != "asc" else "\u7531\u4f4e\u5230\u9ad8"
            summary = f"{summary} \u7ed3\u679c\u6309\u6536\u5f55\u673a\u79cd\u6570{order}\u6392\u5e8f\u3002"
        elif normalized_sort == "distance":
            order = "\u7531\u8fd1\u5230\u8fdc" if normalized_order != "desc" else "\u7531\u8fdc\u5230\u8fd1"
            summary = f"{summary} \u7ed3\u679c\u6309\u76f4\u7ebf\u8ddd\u79bb{order}\u6392\u5e8f\u3002"

        preview_parts: list[str] = []
        for idx, row in enumerate(shops[:3], start=1):
            name = str(row.get("name") or "unknown arcade")
            city = str(row.get("city_name") or row.get("county_name") or "-")
            distance = row.get("distance_m")
            distance_text = f" {distance}\u7c73" if isinstance(distance, int) else ""
            preview_parts.append(f"{idx}. {name}({city}){distance_text}")

        if preview_parts:
            preview_text = "\uff1b".join(preview_parts)
            summary = f"{summary} \u53ef\u5148\u770b\uff1a{preview_text}\u3002"
        return summary

    def summarize_search(
        self,
        keyword: str | None,
        total: int,
        shops: list[dict],
        *,
        sort_by: str | None = None,
        sort_order: str | None = None,
        sort_title_name: str | None = None,
    ) -> str:
        logger.info(
            "summary_tool.search keyword=%s total=%s shops=%s sort_by=%s sort_order=%s sort_title_name=%s",
            " ".join((keyword or "").split())[:64],
            total,
            len(shops),
            sort_by,
            sort_order,
            (sort_title_name or "").strip()[:64],
        )

        if (
            total > 0
            and (sort_by or "").strip().lower() == "title_quantity"
            and isinstance(sort_title_name, str)
            and sort_title_name.strip()
        ):
            return self._deterministic_title_quantity_summary(
                total=total,
                shops=shops,
                sort_order=sort_order,
                sort_title_name=sort_title_name,
            )

        return self._default_search_summary(
            keyword=keyword,
            total=total,
            shops=shops,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    def summarize_navigation(self, shop_name: str, route: RouteSummaryDto) -> str:
        dist = route.distance_m if route.distance_m is not None else 0
        mins = max(1, int((route.duration_s or 0) / 60)) if route.duration_s else 0
        mode = "\u6b65\u884c" if route.mode == "walking" else "\u9a7e\u8f66"
        summary = f"\u524d\u5f80{shop_name}\uff1a{mode}{dist}\u7c73"
        if mins > 0:
            summary = f"{summary}\uff0c\u7ea6{mins}\u5206\u949f"
        summary = f"{summary}\u3002"
        hint = str(route.hint or "").strip()
        if hint:
            summary = f"{summary} {hint}"
        return summary
