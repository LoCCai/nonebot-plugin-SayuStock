from __future__ import annotations

from nonebot_plugin_sayustock import config


def test_deep_merge_preserves_nested_defaults():
    merged = config.deep_merge(
        {"render": {"scale": 1.5, "enabled": True}, "web": {"enabled": True}},
        {"render": {"scale": 2.0}},
    )
    assert merged["render"]["scale"] == 2.0
    assert merged["render"]["enabled"] is True
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
    config.save_config({"render": {"scale": 2.0}})
    assert path.is_file()
    loaded = config.load_config(force=True)
    assert loaded["render"]["scale"] == 2.0
    assert loaded["render"]["timeout_seconds"] == 45


def test_no_plugin_owned_model_or_token_keys(monkeypatch):
    monkeypatch.delenv("SAYUSTOCK_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SAYUSTOCK_WEB_TOKEN", raising=False)
    config.load_config(force=True)
    assert config.get_value("llm", None) is None
    assert config.get_value("web.token", None) is None
