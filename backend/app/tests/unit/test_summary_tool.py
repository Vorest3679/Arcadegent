"""Unit tests for deterministic summary tool behavior."""

from __future__ import annotations

from app.domain.arcade.tools.summary_tool import SummaryTool
from app.protocol.messages import RouteSummaryDto


def test_summary_tool_returns_deterministic_zero_result_text() -> None:
    tool = SummaryTool()
    text = tool.summarize_search(
        keyword="unknown",
        total=0,
        shops=[],
    )
    assert text == (
        "\u672a\u627e\u5230\u5339\u914d\u201cunknown\u201d\u7684\u673a\u5385\uff0c"
        "\u8bf7\u5c1d\u8bd5\u5176\u4ed6\u5173\u952e\u8bcd\u6216\u533a\u57df\u3002"
    )


def test_summary_tool_returns_preview_for_search_results() -> None:
    tool = SummaryTool()
    text = tool.summarize_search(
        keyword="maimai",
        total=3,
        shops=[
            {"name": "A", "city_name": "Shanghai"},
            {"name": "B", "city_name": "Shanghai"},
        ],
    )
    assert "\u5171\u627e\u5230 3 \u5bb6\u673a\u5385\u3002" in text
    assert "A(Shanghai)" in text
    assert "B(Shanghai)" in text


def test_summary_tool_uses_deterministic_text_for_title_quantity_sorting() -> None:
    tool = SummaryTool()
    text = tool.summarize_search(
        keyword="maimai",
        total=12,
        shops=[
            {
                "name": "A",
                "city_name": "Shanghai",
                "arcades": [{"title_name": "maimai", "quantity": 3}],
            },
            {
                "name": "B",
                "city_name": "Shanghai",
                "arcades": [{"title_name": "maimai", "quantity": 2}],
            },
        ],
        sort_by="title_quantity",
        sort_order="desc",
        sort_title_name="maimai",
    )
    assert "\u5171\u627e\u5230 12 \u5bb6\u673a\u5385" in text
    assert "maimai" in text
    assert "3\u53f0" in text


def test_summary_tool_title_quantity_uses_alias_matching() -> None:
    tool = SummaryTool()
    text = tool.summarize_search(
        keyword="sdvx",
        total=2,
        shops=[
            {
                "name": "A",
                "city_name": "Shanghai",
                "arcades": [{"title_name": "SOUND VOLTEX EXCEED GEAR", "quantity": 5}],
            },
            {
                "name": "B",
                "city_name": "Shanghai",
                "arcades": [{"title_name": "maimai DX", "quantity": 3}],
            },
        ],
        sort_by="title_quantity",
        sort_order="desc",
        sort_title_name="sdvx",
    )
    assert "5\u53f0" in text


def test_summary_tool_returns_deterministic_navigation_text() -> None:
    tool = SummaryTool()
    text = tool.summarize_navigation(
        "Foo Arcade",
        RouteSummaryDto(
            provider="amap",
            mode="walking",
            distance_m=820,
            duration_s=630,
            hint="\u4ece 2 \u53f7\u53e3\u51fa\u7ad9\u540e\u76f4\u884c\u3002",
        ),
    )
    assert text == (
        "\u524d\u5f80Foo Arcade\uff1a\u6b65\u884c820\u7c73\uff0c\u7ea610\u5206\u949f\u3002 "
        "\u4ece 2 \u53f7\u53e3\u51fa\u7ad9\u540e\u76f4\u884c\u3002"
    )
