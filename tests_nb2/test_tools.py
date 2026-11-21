from __future__ import annotations

import pytest

from nonebot_plugin_sayustock import config
from nonebot_plugin_sayustock.runtime import Runtime, set_runtime
from nonebot_plugin_sayustock.ai.tools import TOOLS, run_tool
from nonebot_plugin_sayustock.ai.contracts import ToolContext
from nonebot_plugin_sayustock.services.market import MarketService


@pytest.fixture
def tool_runtime(fake_market):
    runtime = Runtime(market=MarketService())
    set_runtime(runtime)
    yield runtime
    set_runtime(None)


def ctx() -> ToolContext:
    return ToolContext(platform="test", user_id="u1", session_id="test:u1", bot_id="b1")


@pytest.mark.asyncio
async def test_tool_registry_has_core_tools():
    names = set(TOOLS)
    assert "sayustock_market_overview" in names
    assert "sayustock_generate_chart" in names
    assert "sayustock_generate_report" in names
    assert "sayustock_watchlist_add" in names


@pytest.mark.asyncio
async def test_market_and_technical_tools(tool_runtime):
    overview = await run_tool("sayustock_market_overview", {}, ctx())
    assert "大盘概览" in overview.text
    quote = await run_tool("sayustock_quote", {"query": "贵州茅台"}, ctx())
    assert "贵州茅台" in quote.text
    technical = await run_tool("sayustock_technical_analysis", {"query": "贵州茅台"}, ctx())
    assert "评分" in technical.text
    sector = await run_tool("sayustock_sector_ranking", {"kind": "industry", "limit": 3}, ctx())
    assert "行业" in sector.text


@pytest.mark.asyncio
async def test_watchlist_tools_roundtrip(tool_runtime):
    added = await run_tool("sayustock_watchlist_add", {"query": "贵州茅台"}, ctx())
    assert added.structured["added"] is True
    listed = await run_tool("sayustock_watchlist_list", {}, ctx())
    assert "贵州茅台" in listed.text
    removed = await run_tool("sayustock_watchlist_remove", {"query": "贵州茅台"}, ctx())
    assert removed.structured["removed"] is True


@pytest.mark.asyncio
async def test_report_tool_creates_artifact(tool_runtime):
    outcome = await run_tool(
        "sayustock_generate_report",
        {"title": "测试报告", "markdown": "# 标题\n\n正文"},
        ctx(),
    )
    assert outcome.artifacts
    assert outcome.artifacts[0].mime.startswith("text/html")


@pytest.mark.asyncio
async def test_dataviz_tools(tool_runtime, monkeypatch):
    monkeypatch.setenv("SAYUSTOCK_RENDER_ENABLED", "false")
    config.load_config(force=True)
    series = [
        {"name": "组合A", "values": [1, 2, 3, 5]},
        {"name": "组合B", "values": [2, 2, 4, 6]},
    ]
    plot = await run_tool(
        "sayustock_plot_series",
        {"title": "自定义曲线", "labels": ["一", "二", "三", "四"], "series": series},
        ctx(),
    )
    assert plot.artifacts
    table = await run_tool(
        "sayustock_data_table",
        {"title": "选股表", "columns": ["代码", "涨幅"], "rows": [["600519", 2.1]]},
        ctx(),
    )
    assert table.artifacts
    analysis = await run_tool("sayustock_analyze_series", {"series": series, "title": "相关性"}, ctx())
    assert analysis.structured["correlation"]["points"] == 4
    assert len(analysis.structured["summary"]) == 2


@pytest.mark.asyncio
async def test_extra_data_tools(tool_runtime):
    rank = await run_tool("sayustock_market_ranking", {"rank_by": "roe"}, ctx())
    assert "榜单" in rank.text or "排行" in rank.text
    financial = await run_tool("sayustock_financials", {"query": "贵州茅台"}, ctx())
    assert "ROE" in financial.text
    valuation = await run_tool("sayustock_valuation", {"query": "贵州茅台"}, ctx())
    assert "PE" in valuation.text
    ipo = await run_tool("sayustock_ipo_calendar", {"market": "cn"}, ctx())
    assert "IPO" in ipo.text
