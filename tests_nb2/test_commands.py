from __future__ import annotations

from nonebot_plugin_sayustock import config
from nonebot_plugin_sayustock.commands import split_command


def test_split_command_prefix_and_alias():
    assert split_command("/大盘概览")[0] == "overview"
    assert split_command("个股 日k 贵州茅台") == ("stock", "日k 贵州茅台", "个股")
    assert split_command("添加自选 600519") == ("add", "600519", "添加自选")
    assert split_command("随便聊聊")[0] == ""


def test_llm_config_env(monkeypatch):
    monkeypatch.setenv("SAYUSTOCK_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("SAYUSTOCK_LLM_MODEL", "demo-model")
    config.load_config(force=True)
    assert config.get_value("llm.base_url") == "https://example.test/v1"
    assert config.get_value("llm.model") == "demo-model"
