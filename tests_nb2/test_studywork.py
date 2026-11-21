"""策略研究域：FEATURES 契约与命令在 FakeMarket 下的端到端路径。"""

from __future__ import annotations

import pytest

from nonebot_plugin_sayustock.runtime import get_runtime
from nonebot_plugin_sayustock.platform import UnifiedEvent
from nonebot_plugin_sayustock.services import studywork
from nonebot_plugin_sayustock.watchlist import owner_key


def _event(platform: str = "onebot11", user_id: str = "u1") -> UnifiedEvent:
    return UnifiedEvent(platform=platform, bot_id="bot", user_id=user_id, text="")


def test_features_are_qiqi_group_commands() -> None:
    names = set(studywork.FEATURES)
    expected = {
        "技术分析",
        "股票卡片",
        "自动选股",
        "组合体检",
        "持仓分析",
        "模型预测",
        "个股",
        "对比个股",
        "我的自选",
        "我的个股",
        "区间涨幅",
        "VIX指数",
        "代码解析",
        "主流币行情",
        "板块强弱扫描",
    }
    assert names == expected
    for name, (aliases, usage, writes) in studywork.FEATURES.items():
        assert isinstance(aliases, tuple) and all(isinstance(alias, str) and alias for alias in aliases)
        assert isinstance(usage, str) and usage
        assert writes is False
        assert name not in aliases
        assert all("早柚" not in alias for alias in aliases)
    assert studywork._resolve("选股") == "自动选股"
    assert studywork._resolve("AI预测") == "模型预测"
    assert studywork._resolve("不存在") == "不存在"


@pytest.mark.asyncio
async def test_untrusted_identity_is_refused(fake_market) -> None:
    result = await studywork.dispatch("技术分析", "600519", _event(user_id=""))
    assert "身份" in result.text
    assert result.artifacts == []


@pytest.mark.asyncio
async def test_technical_uses_original_report_and_keeps_structured_data(fake_market) -> None:
    result = await studywork.dispatch("技术分析", "日k 600519", _event())
    assert "技术分" in result.text
    assert result.structured["code"] == "600519"
    assert result.structured["score"] >= 0
    assert result.structured["provider"] == "fake"
    assert len(result.artifacts) == 1
    assert result.artifacts[0].kind == "chart"


@pytest.mark.asyncio
async def test_card_and_screener_paths(fake_market) -> None:
    card = await studywork.dispatch("股票卡片", "600519", _event())
    assert "股票卡片" in card.text
    assert card.structured["industry"] == "白酒"

    screened = await studywork.dispatch("自动选股", "行业 白酒 PE<30", _event())
    assert screened.structured["scope"] == "行业·白酒"
    assert screened.structured["matched"] == screened.structured["total_pool"] == 6
    assert screened.artifacts

    rejected = await studywork.dispatch("自动选股", "行业 白酒 概念 人工智能", _event())
    assert "二选一" in rejected.text


@pytest.mark.asyncio
async def test_holdings_fall_back_to_rule_based_pack(fake_market) -> None:
    result = await studywork.dispatch("持仓分析", "600519 000001", _event())
    assert result.structured["source"] == "manual"
    assert result.structured["symbols"] == ["600519", "000001"]
    assert result.structured["ai"] is False
    assert "600519" in result.text and "000001" in result.text


@pytest.mark.asyncio
async def test_portfolio_uses_original_risk_model(fake_market) -> None:
    result = await studywork.dispatch("组合体检", "600519 000001", _event())
    assert result.structured["risk_level"]
    assert 0.0 <= result.structured["hhi"] <= 1.0
    assert result.structured["holdings"]


@pytest.mark.asyncio
async def test_kronos_requires_real_torch(fake_market, monkeypatch) -> None:
    from importlib import util

    original = util.find_spec
    monkeypatch.setattr(util, "find_spec", lambda name, *args: None if name == "torch" else original(name, *args))
    result = await studywork.dispatch("模型预测", "600519", _event())
    assert result.structured.get("ok") is False
    assert "torch" in result.text.lower()


@pytest.mark.asyncio
async def test_watchlist_group_image_and_range_change(fake_market) -> None:
    key = owner_key(platform="onebot11", bot_id="bot", user_id="u1")
    await get_runtime().watchlist.clear(key)
    await get_runtime().watchlist.add(key, "600519", "贵州茅台", "茅台")

    view = await studywork.dispatch("我的自选", "", _event())
    assert view.structured["watchlist"][0]["code"] == "600519"
    assert view.structured["quotes"][0]["code"] == "600519"
    assert view.artifacts

    change = await studywork.dispatch("区间涨幅", "600519 2026-01-01 2026-05-20", _event())
    assert change.structured["start_open"] > 0
    assert change.structured["change_pct"] is not None

    lookup = await studywork.dispatch("代码解析", "茅台", _event())
    assert lookup.structured["code"] == "600519"


@pytest.mark.asyncio
async def test_stock_and_compare_keep_sector_expansion(fake_market) -> None:
    stock = await studywork.dispatch("个股", "日k 600519", _event())
    assert stock.artifacts
    compare = await studywork.dispatch("对比个股", "600519 000001", _event())
    assert compare.artifacts
    assert "对比" in compare.text
