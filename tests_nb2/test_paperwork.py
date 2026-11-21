"""模拟盘/宏观域：FEATURES 契约、命令解析与可信事件权限边界。"""

from __future__ import annotations

import pytest

from nonebot_plugin_sayustock.platform import UnifiedEvent
from nonebot_plugin_sayustock.services import paperwork, paperwork_lifecycle as life


def _event(**kwargs) -> UnifiedEvent:
    base = {"platform": "onebot11", "bot_id": "bot", "user_id": "10001", "text": ""}
    base.update(kwargs)
    return UnifiedEvent(**base)


def test_features_catalog_contract():
    assert paperwork.FEATURES
    for name, (aliases, usage, writes) in paperwork.FEATURES.items():
        assert isinstance(name, str) and name
        assert isinstance(aliases, tuple)
        assert all(isinstance(alias, str) and alias and alias != name for alias in aliases)
        assert isinstance(usage, str) and usage
        assert isinstance(writes, bool)
        # 早柚侧群别名不得回流
        assert not any("早柚" in alias for alias in aliases)


def test_features_cover_original_papertrade_and_macro_commands():
    expected = {
        "模拟盘初始化",
        "模拟盘创建",
        "模拟盘列表",
        "模拟盘改名",
        "模拟盘删除",
        "模拟盘停用",
        "模拟盘启用",
        "模拟盘策略列表",
        "模拟盘策略切换",
        "模拟盘推送添加",
        "模拟盘推送删除",
        "模拟盘推送列表",
        "模拟盘查看",
        "模拟盘自选",
        "模拟盘收益",
        "模拟盘记录",
        "模拟盘排行",
        "模拟盘查询",
        "模拟盘对账",
        "模拟盘假期还原",
        "模拟盘假期诊断",
        "模拟盘清盘",
        "宏观事件",
        "宏观事件刷新",
    }
    assert expected <= set(paperwork.FEATURES)


def test_parse_cash_bounds():
    assert paperwork._parse_cash("1000000") == 1_000_000.0
    assert paperwork._parse_cash("1,000,000") == 1_000_000.0
    assert paperwork._parse_cash("9999") is None
    assert paperwork._parse_cash("1000000001") is None
    assert paperwork._parse_cash("abc") is None
    assert paperwork._parse_cash("") is None


@pytest.mark.asyncio
async def test_unknown_command_is_reported():
    result = await paperwork.dispatch("不存在的盘指令", "", _event())
    assert "未识别" in result.text


@pytest.mark.asyncio
async def test_manager_write_requires_trusted_admin():
    member = _event(group_id="900001", is_admin=False)
    denied = await paperwork.dispatch("模拟盘删除", "测试盘", member)
    assert "管理员" in denied.text

    # 群管理员通过权限门后不会因权限被拒（真实 DB 在测试环境可能缺失，错误信息必须是真实原因）
    admin = _event(group_id="900001", is_admin=True)
    allowed = await paperwork.dispatch("模拟盘删除", "不存在的盘", admin)
    assert "不存在名为" in allowed.text


@pytest.mark.asyncio
async def test_qweb_without_authorized_group_cannot_manage():
    qweb = UnifiedEvent(platform="qweb", bot_id="qweb", user_id="alice", text="模拟盘创建 新盘")
    result = await paperwork.dispatch("模拟盘创建", "新盘", qweb)
    assert "网页未提供授权群" in result.text

    created = await paperwork.dispatch("模拟盘初始化", "", qweb)
    assert "网页未提供授权群" in created.text


@pytest.mark.asyncio
async def test_group_only_subscription_commands_reject_private_chat():
    admin = _event(group_id=None, is_admin=True)
    for command in ("模拟盘推送添加", "模拟盘推送删除", "模拟盘推送列表"):
        result = await paperwork.dispatch(command, "默认模拟盘", admin)
        assert "群" in result.text


@pytest.mark.asyncio
async def test_destructive_holiday_restore_refuses_qweb_execution():
    qweb = UnifiedEvent(platform="qweb", bot_id="qweb", user_id="alice", text="模拟盘假期还原 执行")
    result = await paperwork.dispatch("模拟盘假期还原", "执行", qweb)
    assert "网页" in result.text


def test_parse_model_object_accepts_fenced_and_embedded_json():
    assert life.parse_model_object('{"actions": []}') == {"actions": []}
    assert life.parse_model_object('```json\n{"events": [{"slug": "x"}]}\n```') == {"events": [{"slug": "x"}]}
    assert life.parse_model_object('说明文字 {"summary": "ok", "actions": []} 结尾') == {
        "summary": "ok",
        "actions": [],
    }
    assert life.parse_model_object("不是 JSON") == {}
    assert life.parse_model_object("") == {}


def test_lifecycle_host_ai_override_is_used():
    seen: list[tuple[str, str]] = []

    async def fake(prompt: str, system_prompt: str) -> str:
        seen.append((prompt, system_prompt))
        return '{"actions": []}'

    life.set_host_ai(fake)
    try:
        assert life.host_ai_available() is True
        import asyncio

        answer = asyncio.run(life.host_ai("问题", "系统"))
        assert answer == '{"actions": []}'
        assert seen == [("问题", "系统")]
    finally:
        life.set_host_ai(None)


class _Ctx:
    def __init__(self):
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *exc):
        self.exited = True
        return None


class _AccountScope:
    DEFAULT_ACCOUNT_NAME = "默认模拟盘"
    MAX_ENABLED_ACCOUNTS = 5

    def __init__(self):
        self.ctx = _Ctx()
        self.granted: list[int] = []

    def grant_write(self, account_id: int):
        self.granted.append(account_id)
        return self.ctx


class _SnapshotRepo:
    def __init__(self):
        self.written: list[dict] = []

    async def prev_before(self, account_id, trade_date):
        return None

    async def upsert_for_date(self, account_id, trade_date, **kwargs):
        self.written.append({"account_id": account_id, "trade_date": trade_date, **kwargs})


class _AccountRepo:
    def __init__(self, accounts):
        self.accounts = accounts

    async def list_enabled(self):
        return list(self.accounts)

    async def get_by_id(self, account_id):
        return next((item for item in self.accounts if item.id == account_id), None)


class _PositionRepo:
    def __init__(self, positions):
        self.positions = positions

    async def list_by_account(self, account_id):
        return list(self.positions)


class _DecisionRepo:
    def __init__(self):
        self.rows: list[dict] = []

    async def append(self, account_id, **kwargs):
        self.rows.append({"account_id": account_id, **kwargs})


class _TradeRepo:
    def __init__(self):
        self.rows: list[dict] = []

    async def append(self, account_id, **kwargs):
        self.rows.append({"account_id": account_id, **kwargs})


def _fake_domain(monkeypatch, *, accounts, positions, snapshots):
    """Install a fake ``SayuStock.stock_papertrade`` so lifecycle can run without SQLModel."""
    import sys
    import types

    db = types.SimpleNamespace(
        PaperAccountRepo=_AccountRepo(accounts),
        PaperPositionRepo=_PositionRepo(positions),
        PaperSnapshotRepo=snapshots,
        PaperDecisionRepo=_DecisionRepo(),
        PaperTradeRepo=_TradeRepo(),
        parse_strategy_params=lambda raw: {},
    )
    scope = _AccountScope()
    module = types.ModuleType("SayuStock.stock_papertrade")
    module.db = db
    module.account_scope = scope
    module.strategies = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "SayuStock.stock_papertrade", module)
    return db, scope


class _Account:
    def __init__(self, **overrides):
        self.id = 7
        self.name = "默认模拟盘"
        self.strategy_id = "multi_factor"
        self.strategy_params = "{}"
        self.mode = "balanced"
        self.cash = 100_000.0
        self.initial_cash = 100_000.0
        self.enabled = 1
        self.group_id = "900001"
        self.bot_id = "onebot11"
        for key, value in overrides.items():
            setattr(self, key, value)


class _Position:
    def __init__(self):
        self.stock_code = "600519"
        self.stock_name = "贵州茅台"
        self.qty = 100
        self.avg_cost = 1000.0
        self.last_quote_price = None


@pytest.mark.asyncio
async def test_snapshot_writes_equity_from_real_quotes(monkeypatch):
    account = _Account(cash=90_000.0)
    position = _Position()
    snapshots = _SnapshotRepo()
    _fake_domain(monkeypatch, accounts=[account], positions=[position], snapshots=snapshots)

    async def quotes(codes):
        assert codes == ["600519"]
        return {"600519": {"price": 1100.0, "change_pct": 1.0, "name": "贵州茅台", "provider": "fake"}}, []

    monkeypatch.setattr(life, "_quote_map", quotes)
    result = await life.run_snapshot(7)
    assert result["written"] == 1
    written = snapshots.written[0]
    assert written["position_value"] == 110_000.0
    assert written["total_equity"] == 200_000.0
    assert written["total_pnl"] == 100_000.0
    assert written["total_pnl_pct"] == 100.0


@pytest.mark.asyncio
async def test_snapshot_reports_quote_failure_instead_of_faking_price(monkeypatch):
    account = _Account(cash=10_000.0)
    position = _Position()
    snapshots = _SnapshotRepo()
    _fake_domain(monkeypatch, accounts=[account], positions=[position], snapshots=snapshots)

    async def quotes(codes):
        return {}, ["600519 行情不可用"]

    monkeypatch.setattr(life, "_quote_map", quotes)
    result = await life.run_snapshot(7)
    written = snapshots.written[0]
    assert written["position_value"] == 100_000.0  # 成本价兜底，且明确告警
    assert result["warnings"]


@pytest.mark.asyncio
async def test_macro_refresh_is_unavailable_without_host_ai(monkeypatch):
    monkeypatch.setattr(life, "_host_ai", None)
    monkeypatch.setattr(life, "host_ai_available", lambda: False)
    result = await life.run_macro_refresh(force=True)
    assert result["status"] == "unavailable"
    assert "宿主" in result["message"]


@pytest.mark.asyncio
async def test_decision_unavailable_without_host_ai(monkeypatch):
    monkeypatch.setattr(life, "host_ai_available", lambda: False)
    result = await life.run_decision()
    assert result["status"] == "unavailable"


def test_lifecycle_schedule_helpers(monkeypatch):
    from datetime import datetime

    now = datetime(2026, 11, 20, 10, 0)
    import sys
    import types

    import nonebot_plugin_sayustock.services.paperwork_lifecycle as mod

    calendar = types.ModuleType("SayuStock.stock_papertrade.trading_calendar")
    calendar.is_a_share_trading_day = lambda dt=None: True
    calendar.is_trading_time = lambda dt=None: True
    monkeypatch.setitem(sys.modules, "SayuStock.stock_papertrade.trading_calendar", calendar)

    assert mod._decision_due(now, "") is True
    assert mod._decision_due(now, "2026-11-20 10:00") is False
    assert mod._decision_due(now.replace(minute=5), "") is False
    assert mod._snapshot_due(now.replace(hour=15, minute=5), "") is True
    assert mod._snapshot_due(now.replace(hour=15, minute=6), "") is False
    assert mod._macro_due(now.replace(hour=8, minute=40), "") is True
    assert mod._macro_due(now.replace(hour=8, minute=41), "") is False


def test_model_actions_cannot_name_account_or_identity():
    assert life._valid_action({"code": "600519", "side": "buy"}) is True
    assert life._valid_action({"code": "600519", "side": "hold"}) is True
    assert life._valid_action({"code": "600519", "side": "buy", "account": "default"}) is False
    assert life._valid_action({"code": "600519", "side": "buy", "group_id": "123"}) is False
    assert life._valid_action({"code": "abc", "side": "buy"}) is False
    assert life._valid_action({"code": "600519", "side": "short"}) is False


@pytest.mark.asyncio
async def test_lifecycle_hooks_are_idempotent(monkeypatch):
    calls: list[str] = []

    async def fake_stop() -> None:
        calls.append("stop")

    monkeypatch.setattr(life, "start_lifecycle", lambda: calls.append("sync-start") or True)
    monkeypatch.setattr(life, "stop_lifecycle", fake_stop)
    monkeypatch.setattr(life, "host_ai_available", lambda: True)
    await paperwork.on_startup()
    await paperwork.on_shutdown()
    assert calls == ["sync-start", "stop"]


@pytest.mark.asyncio
async def test_macro_records_only_cited_events_and_settles(tmp_path, monkeypatch):
    import time
    from uuid import uuid4
    from unittest.mock import AsyncMock

    from SayuStock.stock_macro.repo import MacroEventRepo
    from nonebot_plugin_sayustock.runtime import get_runtime
    from gsuid_core.utils.database.base_models import rollback_session
    from SayuStock.utils.database.macro_models import MACRO_STATUS_SETTLED

    slug = "review_" + uuid4().hex[:12]
    news = {
        "source_label": "样本",
        "items": [
            {
                "id": "123",
                "title": "复核",
                "text": "今日已公布结果",
                "published_ms": int(time.time() * 1000),
                "url": "https://example.com/123",
            }
        ],
    }
    answer = {
        "events": [
            {"slug": slug, "title": "审计样本", "source_ids": ["123"], "result": "已公布", "status": "settled"},
            {"slug": "unverified", "title": "缺出处", "source_ids": ["404"]},
        ]
    }
    import json

    monkeypatch.setattr(life, "_host_ai", AsyncMock(return_value=json.dumps(answer)))
    monkeypatch.setattr(get_runtime().market, "news", AsyncMock(return_value=news))
    async with rollback_session():
        report = await life.run_macro_refresh(force=True)
        assert report["written"] == [slug] and report["skipped"] == ["缺出处"]
        verified = next(row for row in await MacroEventRepo.list_by_status(MACRO_STATUS_SETTLED) if row.slug == slug)
        assert verified.source_urls == "https://example.com/123"
