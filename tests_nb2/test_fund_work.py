"""Fund / ETF / cloud-map / all-weather / valuation domain contract tests."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from nonebot_plugin_sayustock.services import fundwork


def _event(platform: str = "onebot11"):
    from nonebot_plugin_sayustock.platform import UnifiedEvent

    return UnifiedEvent(platform=platform, bot_id="bot", user_id="10001", text="")


def test_features_are_group_commands_with_real_usage():
    assert "ETF找股" in fundwork.FEATURES
    for name, (aliases, usage, writes) in fundwork.FEATURES.items():
        assert isinstance(name, str) and name
        assert isinstance(aliases, tuple) and all(isinstance(alias, str) and alias for alias in aliases)
        assert "早柚" not in name and all("早柚" not in alias for alias in aliases)
        assert isinstance(usage, str) and usage
        assert writes is False
    assert fundwork.resolve_feature("场内找股") == "ETF找股"
    assert fundwork.resolve_feature("行业云图") == "板块云图"
    assert fundwork.resolve_feature("不存在") is None


@pytest.mark.asyncio
async def test_cloudmap_dispatch_builds_themed_owned_artifact(fake_market) -> None:
    result = await fundwork.dispatch("大盘云图", "", _event())
    assert result.text.startswith("【大盘云图 · A股】")
    assert result.image is None
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.owner == "10001" and artifact.mime.startswith("text/html")
    assert result.structured["market"] == "大盘云图"
    assert result.structured["total"] > 0 and result.structured["rows"]
    assert result.structured["provider"] == "fake"

    from nonebot_plugin_sayustock.artifacts import get_artifact

    owned = get_artifact(artifact.id, owner="10001")
    assert owned is not None
    document = owned[1].read_text(encoding="utf-8")
    assert "data-post-theme=" in document and "sayustock-post-theme" in document
    assert 'class="stock-heading"' in document and "<!--stock-heading-art-->" not in document


@pytest.mark.asyncio
async def test_qweb_cloudmap_skips_png_but_keeps_html(fake_market, monkeypatch) -> None:
    from SayuStock.utils.market.enums import BoardKind
    from SayuStock.utils.market.errors import network_error
    from nonebot_plugin_sayustock.artifacts import get_artifact

    for argument, label in (("", "A股"), ("hk", "港股"), ("美股", "美股")):
        result = await fundwork.dispatch("大盘云图", argument, _event("qweb"))
        title = f"大盘云图 · {label}"
        assert result.image is None
        artifact = result.artifacts[0]
        assert artifact.owner == "10001" and artifact.title == title
        document = get_artifact(artifact.id, owner="10001")[1].read_text()
        assert f"<h1>{title}</h1>" in document and result.text.startswith(f"【{title}】")
        if label != "A股":
            assert "涨停" not in result.text

    async def failed_board(kind, *, sector, limit):
        assert kind == BoardKind.HOTMAP and sector == "美股"
        return network_error("东方财富: -400016", provider="eastmoney")

    monkeypatch.setattr(fake_market, "board", failed_board)
    failure = await fundwork.dispatch("大盘云图", "美股", _event("qweb"))
    assert not failure.artifacts and "【大盘云图 · 美股】" in failure.text
    assert "-400016" not in failure.text and "-400016" in failure.structured["error"]


@pytest.mark.asyncio
async def test_all_weather_dispatch_returns_sections_and_text(fake_market) -> None:
    result = await fundwork.dispatch("全天候", "", _event())
    assert "【全天候板块】" in result.text
    assert result.artifacts and result.structured["sections"]
    for section in result.structured["sections"]:
        assert section["title"] and section["items"]
        assert section["items"][0]["name"]


@pytest.mark.asyncio
async def test_unknown_or_missing_argument_is_a_real_error(fake_market) -> None:
    unknown = await fundwork.dispatch("没有这个功能", "", _event())
    assert "未知股票功能" in unknown.text and unknown.structured["error"] == "unknown_command"
    missing = await fundwork.dispatch("ETF找股", "", _event())
    assert "用法" in missing.text and missing.structured["error"] == "missing_query"
    no_search = await fundwork.dispatch("ETF展开", "食品饮料", _event())
    assert "ETF找股" in no_search.text and no_search.structured["error"] == "no_recent_search"
    no_identity = await fundwork.dispatch("大盘云图", "", _event("qweb"))
    assert no_identity.artifacts or no_identity.text
    too_long = await fundwork.dispatch("基金持仓", "0" * 2001, _event())
    assert too_long.structured["error"] == "argument_too_long"


def test_basket_structured_keeps_weights_and_failures() -> None:
    from datetime import datetime

    from SayuStock.utils.warn_box import Failure
    from SayuStock.stock_info.etf_compare import FundValue, TargetStock, BasketResult

    stamp = datetime(2026, 11, 20, 10, 0)
    fund = FundValue(
        code="515170",
        name="食品饮料ETF",
        stocks={"600519": ("贵州茅台", 60.0), "000858": ("五粮液", 40.0)},
        industries={"白酒": 100.0},
        first_quote=stamp,
        last_quote=stamp,
        zero_stocks=("600887",),
        index_name="中证食品饮料",
        providers=("tencent",),
    )
    result = BasketResult((fund,), (Failure("159843", "缺当日报价"),))
    payload = fundwork._basket_json(result, TargetStock("600519", "贵州茅台"))
    assert payload["target"] == {"code": "600519", "name": "贵州茅台"}
    assert payload["funds"][0]["stocks"][0] == {"code": "600519", "name": "贵州茅台", "weight": 60.0}
    assert payload["funds"][0]["industries"] == [{"name": "白酒", "weight": 100.0}]
    assert payload["failures"] == [{"label": "159843", "reason": "缺当日报价"}]
    assert payload["funds"][0]["providers"] == ["tencent"]


def test_fund_info_view_renders_warning_and_keeps_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from SayuStock.utils.warn_box import Failure
    from SayuStock.utils.fund_holdings_html import FundHoldingRow
    from SayuStock.stock_info.draw_fund_info import FundInfoView, fund_info_text

    view = FundInfoView(
        fund_name="示例基金",
        fund_code="1.000005",
        rows=(
            FundHoldingRow(code="600519", name="贵州茅台", weight="8.50", price=1500.0, change_pct=2.0),
            FundHoldingRow(code="300750", name="宁德时代", weight="6.20", failed=True, reason="数据源未返回行情"),
        ),
        sparks={},
        failures=(Failure("宁德时代(300750)", "数据源未返回行情"),),
        average=2.0,
        meta="持仓 2 只 · 前十占比 14.70%",
        report_period="报告期 2026-09-30",
        sources=("fake",),
    )
    text = fund_info_text(view)
    assert "示例基金(1.000005)" in text and "贵州茅台" in text and "宁德时代" in text
    assert "1 只行情获取失败" in text and "报告期 2026-09-30" in text

    import asyncio

    async def _no_index(_names: list[str]) -> list[object]:
        return []

    monkeypatch.setattr("SayuStock.stock_info.draw_fund_info.quote_index_items", _no_index)
    html = asyncio.run(
        __import__("SayuStock.stock_info.draw_fund_info", fromlist=["fund_info_html"]).fund_info_html(view)
    )
    assert "贵州茅台" in html and "宁德时代" in html and "warn-inline" in html


def test_value_comparison_text_uses_only_real_series() -> None:
    from SayuStock.utils.market.enums import ValueKind
    from SayuStock.utils.market.models import SymbolRef, ValueSeries
    from SayuStock.utils.market.models.value import ValuePoint
    from SayuStock.stock_sina.eastmoney_value import ValueComparison, value_comparison_text

    symbol = SymbolRef("600519", "贵州茅台", "equity", "SSE", "1.600519")
    series = ValueSeries(
        symbol=symbol,
        kind=ValueKind.PE,
        points=(
            ValuePoint(day=date(2025, 1, 2), value=20.0),
            ValuePoint(day=date(2026, 1, 5), value=25.0),
        ),
        provider="eastmoney",
    )
    view = ValueComparison("pe", "市盈率", (series,), ("测试缺源股",))
    text = value_comparison_text(view)
    assert "贵州茅台(600519)" in text and "25.00" in text and "数据来源：东方财富" in text
    assert "未获取到数据: 测试缺源股" in text
    assert datetime.now().year >= 2025
