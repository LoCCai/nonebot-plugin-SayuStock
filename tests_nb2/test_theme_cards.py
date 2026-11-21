"""Theme bridge and generated-card contracts for the NoneBot2 branch."""

from __future__ import annotations

import sys
import copy
import types
import asyncio
from typing import Any, Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from nonebot_plugin_sayustock.ai import tools
from nonebot_plugin_sayustock.services import theme as theme_service
from nonebot_plugin_sayustock.ai.contracts import ToolContext
from nonebot_plugin_sayustock.services.charts import (
    news_card,
    overview_html,
    dashboard_html,
    chart_from_page,
    build_report_html,
)

HOST_CONTEXT: dict[str, Any] = {
    "post_theme": "qiqi-light",
    "post_mode": "light",
    "post_theme_css": ":root{--host-card:1;}",
}
HOST_PALETTE: dict[str, str] = {
    "background": "#fafafa",
    "surface": "#ffffff",
    "text": "#111111",
    "muted": "#666666",
    "accent": "#0055ff",
    "secondary": "#aa7700",
    "pink": "#cc3388",
    "border": "#dddddd",
}


@pytest.fixture(autouse=True)
def _reset_host_theme_globals() -> Any:
    context = dict(HOST_CONTEXT)
    palette = dict(HOST_PALETTE)
    yield
    HOST_CONTEXT.clear()
    HOST_CONTEXT.update(context)
    HOST_PALETTE.clear()
    HOST_PALETTE.update(palette)


def _install_fake_host(monkeypatch: pytest.MonkeyPatch) -> None:
    source = types.ModuleType("src")
    source.__path__ = []  # type: ignore[attr-defined]
    utils = types.ModuleType("src.utils")
    utils.__path__ = []  # type: ignore[attr-defined]
    report = types.ModuleType("src.utils.report_theme")
    report.post_theme_context = lambda: dict(HOST_CONTEXT)
    report.graph_palette = lambda context: dict(HOST_PALETTE)
    monkeypatch.setitem(sys.modules, "src", source)
    monkeypatch.setitem(sys.modules, "src.utils", utils)
    monkeypatch.setitem(sys.modules, "src.utils.report_theme", report)


def _remove_fake_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "src.utils.report_theme", None)


def _tool_context(platform: str = "onebot11") -> ToolContext:
    return ToolContext(platform=platform, user_id="u1", session_id="test:u1", bot_id="b1")


class _FakeRuntime:
    def __init__(self, news: Mapping[str, Any]) -> None:
        self.market = types.SimpleNamespace(news=self._news)
        self._news_data = news
        self.render_lock = asyncio.Lock()
        self.render_enabled = True

    async def _news(self, *, limit: int = 20) -> Mapping[str, Any]:
        return self._news_data

    def render_scale(self) -> float:
        return 1.0


def _news_payload() -> dict[str, Any]:
    return {
        "source": "eastmoney",
        "source_label": "东方财富",
        "items": [
            {"title": "标题A", "published_ms": 1_700_000_000_000, "important": True},
            {"title": " 标题A ", "published_ms": 1_700_000_060_000},
            {"title": "<script>alert(1)</script>", "published_ms": 1_700_000_120_000},
            {"title": "", "text": "只有正文的有效标题。后续内容", "published_ms": 1_700_000_180_000},
            {"title": "", "text": ""},
        ],
    }


def test_bundled_theme_falls_back_without_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _remove_fake_host(monkeypatch)
    context = theme_service.resolve_theme()
    assert context["host"] is False
    assert context["post_mode"] == "dark"
    assert context["post_theme"]
    assert theme_service.graph_palette() == theme_service.FALLBACK_PALETTE
    assert set(theme_service.graph_palette()) == set(theme_service.PALETTE_KEYS)


def test_host_theme_is_read_each_render_and_palette_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_host(monkeypatch)
    context = theme_service.resolve_theme()
    assert context["post_theme"] == "qiqi-light"
    assert context["post_mode"] == "light"
    assert context["post_theme_css"] == HOST_CONTEXT["post_theme_css"]
    assert theme_service.graph_palette(context) == HOST_PALETTE
    assert set(theme_service.graph_palette(context)) == set(theme_service.PALETTE_KEYS)

    first = overview_html({"as_of": "2026-11-21"})
    assert 'data-post-mode="light"' in first
    assert "#fafafa" in first
    assert HOST_CONTEXT["post_theme_css"] in first

    HOST_CONTEXT["post_mode"] = "dark"
    HOST_PALETTE["background"] = "#101010"
    second = overview_html({"as_of": "2026-11-21"})
    assert 'data-post-mode="dark"' in second
    assert "#101010" in second
    assert second != first


@pytest.mark.asyncio
async def test_overview_report_and_chart_share_host_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_host(monkeypatch)
    from SayuStock.render.chart_page import Panel, ChartPage, Rectangle

    panel = Panel("价格", 200, [])
    panel.rects.append(Rectangle(10, 10, 20, 30, "#f6465d"))
    page = ChartPage("测试", "行情", "副标题", [panel], "来源")
    chart = chart_from_page(page)
    assert chart is not None
    assert 'data-post-theme="qiqi-light"' in chart
    assert 'data-post-mode="light"' in chart
    assert 'role="img"' in chart
    assert HOST_PALETTE["accent"] in chart

    report = await build_report_html("测试报告", "# 正文", meta={"来源": "fake"})
    assert 'data-post-theme="qiqi-light"' in report
    assert HOST_PALETTE["secondary"] in report
    assert "测试报告" in report


def test_news_card_escapes_dedupes_and_keeps_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_host(monkeypatch)
    payload = _news_payload()
    before = copy.deepcopy(payload)
    html, text = news_card(payload, 10)
    assert payload == before
    assert "东方财富" in text
    assert "来源：<b>东方财富</b>" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert html.count("标题A") == 1
    assert "只有正文的有效标题" in html
    assert html.count("只有正文的有效标题") == 1
    assert "重要" in html
    assert text.index("标题A") < text.index("只有正文的有效标题")
    expected_time = datetime.fromtimestamp(1_700_000_000, ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
    assert expected_time in html
    assert expected_time in text
    assert 'data-post-mode="light"' in html
    assert HOST_PALETTE["background"] in html

    limited, limited_text = news_card(payload, 2)
    assert limited.count('article class="news-item') == 2
    assert "只有正文的有效标题" not in limited_text


def test_news_card_empty_and_missing_fields() -> None:
    html, text = news_card({}, 5)
    assert "暂无有效快讯" in html
    assert "暂无有效快讯" in text
    assert "来源：多源快讯" in text


@pytest.mark.asyncio
async def test_ai_news_tool_returns_web_html_and_group_image(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_runtime = _FakeRuntime(_news_payload())
    monkeypatch.setattr(tools, "get_runtime", lambda: fake_runtime)

    async def fake_rasterize(html: str, *, width: int = 1280, scale: float = 1.5) -> bytes:
        return b"PNG"

    monkeypatch.setattr(tools, "rasterize", fake_rasterize)
    group = await tools.run_tool("sayustock_news", {"limit": 3}, _tool_context("onebot11"))
    assert group.images == [b"PNG"]
    assert any(item.mime.startswith("text/html") for item in group.artifacts)
    assert any(item.mime == "image/png" for item in group.artifacts)
    assert "东方财富" in group.text

    web = await tools.run_tool("sayustock_news", {"limit": 3}, _tool_context("qweb"))
    assert web.images == []
    assert any(item.mime.startswith("text/html") for item in web.artifacts)


@pytest.mark.asyncio
async def test_dashboard_tool_theme_and_image(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_host(monkeypatch)
    fake_runtime = _FakeRuntime(_news_payload())
    monkeypatch.setattr(tools, "get_runtime", lambda: fake_runtime)

    async def fake_rasterize(html: str, *, width: int = 1280, scale: float = 1.5) -> bytes:
        return b"PNG"

    monkeypatch.setattr(tools, "rasterize", fake_rasterize)
    outcome = await tools.run_tool(
        "sayustock_dashboard",
        {
            "title": "复盘",
            "metrics": [{"label": "上证", "value": 3200, "change_pct": 1.2}],
            "tables": [{"columns": ["代码", "涨幅"], "rows": [["600519", 2.1]]}],
        },
        _tool_context("onebot11"),
    )
    assert outcome.images == [b"PNG"]
    assert any(item.mime.startswith("text/html") for item in outcome.artifacts)
    assert "复盘" in outcome.text

    html = dashboard_html(
        {
            "title": "复盘",
            "metrics": [{"label": "上证", "value": 3200, "change_pct": 1.2}],
            "tables": [{"columns": ["代码"], "rows": [["600519"]]}],
        }
    )
    assert 'data-post-mode="light"' in html
    assert HOST_PALETTE["accent"] in html
    assert "复盘" in html
