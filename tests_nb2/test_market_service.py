from __future__ import annotations

from dataclasses import replace

import pytest

from nonebot_plugin_sayustock.services.market import MarketService, MarketServiceError


@pytest.mark.asyncio
async def test_overview_aggregates_indices_and_breadth(fake_market):
    service = MarketService()
    data = await service.overview()
    assert data["ok"] is True
    assert data["indices"]
    assert data["breadth"]["up"] > 0
    assert data["breadth"]["down"] > 0
    assert data["industries"]["rise"]


@pytest.mark.asyncio
async def test_quote_and_kline(fake_market):
    service = MarketService()
    quote = await service.quote("贵州茅台")
    assert quote["name"] == "贵州茅台"
    assert quote["price"] == 1500.0
    kline = await service.kline("贵州茅台", "日k")
    assert len(kline["bars"]) == 140
    assert "ma20" in kline["indicators"]


@pytest.mark.asyncio
async def test_technical_score_and_screen(fake_market):
    service = MarketService()
    technical = await service.technical("贵州茅台", "日k")
    assert -1 <= technical["score"] <= 1
    assert technical["signals"] or technical["risks"]
    screened = await service.screen(max_pe=40, min_market_cap_yi=1, limit=5)
    assert screened["ok"] is True
    assert screened["count"] <= 5


@pytest.mark.asyncio
async def test_compare_requires_two_symbols(fake_market):
    service = MarketService()
    with pytest.raises(MarketServiceError):
        await service.compare(["贵州茅台"])
    result = await service.compare(["贵州茅台", "五粮液"])
    assert len(result["traces"]) == 2
    assert result["traces"][0]["points"]


@pytest.mark.asyncio
async def test_overview_keeps_distribution_and_declining_member(fake_market, monkeypatch):
    from nonebot_plugin_sayustock.services.charts import overview_html
    from nonebot_plugin_sayustock.services.overview import breadth_svg

    original_board = fake_market.board

    async def board(*args, **kwargs):
        snapshot = await original_board(*args, **kwargs)
        rows = tuple(replace(r, lead_name="领涨样本", lead_change_pct=6, fall_name="领跌样本", fall_change_pct=-7)
                     for r in snapshot.rows)
        return replace(snapshot, rows=rows)

    monkeypatch.setattr(fake_market, "board", board)
    first = await MarketService().overview()
    assert {"北证50", "科创综指", "科创50", "中证2000", "中证全指"} <= {i["label"] for i in first["indices"]}
    breadth = first["breadth"]
    svg = breadth_svg(breadth)
    assert svg.count("data-band=") == 13
    swapped = dict(breadth["counts"])
    swapped["0~1"], swapped["5~10"] = swapped["5~10"], swapped["0~1"]
    assert breadth_svg({**breadth, "counts": swapped}) != svg
    assert first["industries"]["fall"][0]["fall_name"] == "领跌样本"
    html = overview_html(first)
    assert "领跌样本</td><td class='num'>-7.00%" in html
    assert "放量 2,000 亿" in html and "2026-11-20" in first["session_label"]


@pytest.mark.asyncio
async def test_overview_survives_partial_provider_failures(fake_market, monkeypatch):
    import json

    from SayuStock.utils.market import BreadthBar
    from nonebot_plugin_sayustock.services.charts import overview_html

    async def unavailable(*args, **kwargs):
        raise RuntimeError("fixture provider is offline")

    async def empty():
        return BreadthBar(buckets=())

    monkeypatch.setattr(fake_market, "intraday", unavailable)
    monkeypatch.setattr(fake_market, "breadth", empty)
    monkeypatch.setattr(fake_market, "market_turnover", unavailable)
    data = await MarketService().overview()
    assert data["indices"] and all(not i["spark"] for i in data["indices"])
    assert data["breadth"] is None and data["turnover"] is None
    assert "暂缺涨跌分布" in overview_html(data)
    assert "成交额数据暂缺" in data["warnings"]
    json.dumps(data, allow_nan=False)
