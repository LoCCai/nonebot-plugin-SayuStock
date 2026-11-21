from __future__ import annotations

import sys
import types

import pytest

from nonebot_plugin_sayustock.ai import moellm_bridge


class FakeToolRegistry:
    def __init__(self) -> None:
        self.items = {}

    def get(self, name):
        return self.items.get(name)

    def unregister(self, name):
        self.items.pop(name, None)


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
    monkeypatch.setattr(moellm_bridge, "_NAMES", [])
    return recorded, registry


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
