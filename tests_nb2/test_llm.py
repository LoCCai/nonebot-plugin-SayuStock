from __future__ import annotations

import pytest

from nonebot_plugin_sayustock import config
from nonebot_plugin_sayustock.ai import llm
from nonebot_plugin_sayustock.runtime import Runtime, set_runtime
from nonebot_plugin_sayustock.ai.contracts import ToolContext
from nonebot_plugin_sayustock.services.market import MarketService


@pytest.fixture
def llm_env(fake_market, monkeypatch):
    monkeypatch.setenv("SAYUSTOCK_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("SAYUSTOCK_LLM_MODEL", "demo")
    monkeypatch.setenv("SAYUSTOCK_LLM_API_KEY", "key")
    config.load_config(force=True)
    set_runtime(Runtime(market=MarketService()))
    yield
    set_runtime(None)


def ctx() -> ToolContext:
    return ToolContext(platform="web", user_id="u", session_id="web:u")


@pytest.mark.asyncio
async def test_tool_loop_executes_tool_then_answers(llm_env, monkeypatch):
    calls = {"count": 0}

    async def fake_complete(messages, *, tools=None):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call1",
                                    "type": "function",
                                    "function": {
                                        "name": "sayustock_quote",
                                        "arguments": '{"query": "贵州茅台"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"total_tokens": 10},
            }
        return {
            "choices": [{"message": {"role": "assistant", "content": "贵州茅台现价 1500。"}}],
            "usage": {"total_tokens": 20},
        }

    monkeypatch.setattr(llm, "_complete", fake_complete)
    result = await llm.chat("茅台现在多少钱", context=ctx())
    assert result["ok"] is True
    assert "1500" in result["reply"]
    assert result["tool_calls"][0]["name"] == "sayustock_quote"
    assert result["steps"] == 2


@pytest.mark.asyncio
async def test_unconfigured_raises(llm_env, monkeypatch):
    monkeypatch.setenv("SAYUSTOCK_LLM_BASE_URL", "")
    config.load_config(force=True)
    with pytest.raises(llm.LLMError):
        await llm.chat("你好", context=ctx())
