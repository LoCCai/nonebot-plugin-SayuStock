from __future__ import annotations

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
