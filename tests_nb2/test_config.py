from __future__ import annotations

from nonebot_plugin_sayustock import config


def test_deep_merge_preserves_nested_defaults():
    merged = config.deep_merge(
        {"llm": {"model": "a", "base_url": ""}, "web": {"enabled": True}},
        {"llm": {"model": "b"}},
    )
    assert merged["llm"]["model"] == "b"
    assert merged["llm"]["base_url"] == ""
    assert merged["web"]["enabled"] is True


def test_env_override_is_typed(monkeypatch):
    monkeypatch.setenv("SAYUSTOCK_WEB_ENABLED", "false")
    monkeypatch.setenv("SAYUSTOCK_MARKET_CHAIN", "东方财富, 新浪财经")
    config.load_config(force=True)
    loaded = config.load_config(force=True)
    assert loaded["web"]["enabled"] is False
    assert loaded["market"]["provider_chain"] == ["东方财富", "新浪财经"]


def test_config_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    config.save_config({"llm": {"model": "demo"}})
    assert path.is_file()
    loaded = config.load_config(force=True)
    assert loaded["llm"]["model"] == "demo"
    assert loaded["llm"]["temperature"] == 0.4
