from __future__ import annotations

import sys
import types

import pytest

from tests_nb2.test_protocol import FakeBot, FakeEvent, FakeAdapter
from nonebot_plugin_sayustock.ai import moellm_bridge
from nonebot_plugin_sayustock.runtime import Runtime, set_runtime
from nonebot_plugin_sayustock.services.market import MarketService


class FakeToolRegistry:
    def __init__(self) -> None:
        self.items = {}

    def get(self, name):
        return self.items.get(name)

    def unregister(self, name):
        self.items.pop(name, None)


class FakeToolContext:
    def __init__(self, *, event=None, bot=None, confirmed=False, request_id=None):
        self.event = event
        self.bot = bot
        self.confirmed = confirmed
        self.request_id = request_id


@pytest.fixture
def fake_moellm(monkeypatch):
    registry = FakeToolRegistry()
    recorded = []

    class ToolEffect:
        READ_ONLY = "read_only"
        MUTATING = "mutating"

    class ToolSpec:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def register_tool(spec):
        registry.items[spec.name] = spec
        recorded.append(spec)

    module = types.ModuleType("nonebot_plugin_moellmchats")
    module.ToolEffect = ToolEffect
    module.ToolSpec = ToolSpec
    module.ToolResult = lambda **kwargs: kwargs
    module.register_tool = register_tool
    contracts = types.ModuleType("nonebot_plugin_moellmchats.tool_contracts")
    contracts.tool_registry = registry
    module.tool_contracts = contracts
    monkeypatch.setitem(sys.modules, "nonebot_plugin_moellmchats", module)
    monkeypatch.setitem(sys.modules, "nonebot_plugin_moellmchats.tool_contracts", contracts)
    monkeypatch.setattr(moellm_bridge, "_REGISTERED", False)
    monkeypatch.setattr(moellm_bridge, "_TOOLSPEC_NAMES", [])
    monkeypatch.setattr(moellm_bridge, "_NATIVE_NAMES", [])
    return recorded, registry


@pytest.fixture
def native_moellm(monkeypatch):
    class FakeToolManager:
        def __init__(self):
            self.custom_tools = {}

    manager = FakeToolManager()
    module = types.ModuleType("nonebot_plugin_moellmchats")
    module.__path__ = []
    tool_manager_module = types.ModuleType("nonebot_plugin_moellmchats.tool_manager")
    tool_manager_module.tool_manager = manager
    module.tool_manager = tool_manager_module
    monkeypatch.setitem(sys.modules, "nonebot_plugin_moellmchats", module)
    monkeypatch.setitem(sys.modules, "nonebot_plugin_moellmchats.tool_manager", tool_manager_module)
    monkeypatch.setattr(moellm_bridge, "_REGISTERED", False)
    monkeypatch.setattr(moellm_bridge, "_TOOLSPEC_NAMES", [])
    monkeypatch.setattr(moellm_bridge, "_NATIVE_NAMES", [])
    return manager


def test_registers_all_tools(fake_moellm):
    recorded, registry = fake_moellm
    assert moellm_bridge.register() is True
    assert len(recorded) >= 12
    names = {spec.name for spec in recorded}
    assert "sayustock_market_overview" in names
    assert "sayustock_generate_chart" in names
    assert registry.get("sayustock_quote").effect == "read_only"
    assert registry.get("sayustock_watchlist_add").effect == "mutating"


def test_register_is_idempotent(fake_moellm):
    recorded, _ = fake_moellm
    assert moellm_bridge.register() is True
    count = len(recorded)
    assert moellm_bridge.register() is True
    assert len(recorded) == count
    moellm_bridge.unregister()


@pytest.mark.asyncio
async def test_mutating_tool_needs_host_confirmation(fake_moellm):
    _, registry = fake_moellm
    assert moellm_bridge.register() is True
    event = FakeEvent()
    bot = FakeBot(FakeAdapter("OneBot V11"))
    unconfirmed = await registry.get("sayustock_watchlist_add").handler(
        _tool_context=FakeToolContext(event=event, bot=bot),
        query="贵州茅台",
    )
    assert unconfirmed["structured"]["requires_confirmation"] is True
    assert unconfirmed["metadata"]["requires_confirmation"] is True


@pytest.mark.asyncio
async def test_tool_identity_comes_from_event_not_arguments(fake_moellm, fake_market):
    _, registry = fake_moellm
    assert moellm_bridge.register() is True
    set_runtime(Runtime(market=MarketService()))
    try:
        event = FakeEvent(user_id="42")
        bot = FakeBot(FakeAdapter("OneBot V11"))
        context = moellm_bridge._context_from_tool(FakeToolContext(event=event, bot=bot, confirmed=True))
        assert context.user_id == "42"
        assert context.platform == "onebot11"
        assert context.session_id == "onebot11:10000:99"
    finally:
        set_runtime(None)


def test_native_fallback_registers_read_only_tools(native_moellm):
    assert moellm_bridge.register() is True
    names = set(native_moellm.custom_tools)
    assert "sayustock_quote" in names
    assert "sayustock_generate_report" in names
    assert "sayustock_watchlist_add" not in names
    assert "sayustock_watchlist_remove" not in names
    moellm_bridge.unregister()
    assert native_moellm.custom_tools == {}


@pytest.mark.asyncio
async def test_native_handler_uses_event_identity_for_artifact_owner(native_moellm, fake_market):
    assert moellm_bridge.register() is True
    set_runtime(Runtime(market=MarketService()))
    try:
        event = FakeEvent(user_id="42")
        bot = FakeBot(FakeAdapter("OneBot V11"))
        handler = native_moellm.custom_tools["sayustock_generate_report"]["func"]
        result = await handler(
            _event=event,
            _bot=bot,
            title="归属测试",
            markdown="# 归属测试",
        )
        artifacts = result["metadata"]["artifacts"]
        assert artifacts
        from nonebot_plugin_sayustock.artifacts import get_artifact

        found = get_artifact(artifacts[0]["id"], owner="42")
        assert found is not None
        assert get_artifact(artifacts[0]["id"], owner="99") is None
    finally:
        set_runtime(None)
