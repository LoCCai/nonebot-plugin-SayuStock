from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock

import pytest

from nonebot_plugin_sayustock.commands import split_command


def test_split_command_prefix_and_alias():
    assert split_command("/大盘概览")[0] == "overview"
    assert split_command("个股 日k 贵州茅台") == ("stock", "日k 贵州茅台", "个股")
    assert split_command("添加自选 600519") == ("add", "600519", "添加自选")
    assert split_command("随便聊聊")[0] == ""
    assert split_command("股票菜单")[0] == "help"
    from nonebot.rule import CommandRule

    from nonebot_plugin_sayustock import dispatcher, help_matcher

    names = {cmd[0] for matcher in (dispatcher, help_matcher) for checker in matcher.rule.checkers
             if isinstance(checker.call, CommandRule) for cmd in checker.call.cmds}
    assert names == {"股票", "股票菜单", "股票帮助"}
    for removed in ("早柚股票", "SayuStock帮助", "sayustock帮助", "SayuStock", "sayustock"):
        assert split_command(removed)[0] == ""


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("财务 600519", "financial"),
        ("估值 pb 600519", "valuation"),
        ("股票资讯", "news"),
        ("资金排行", "rank"),
    ],
)
def test_menu_commands_are_dispatchable(text, intent):
    assert split_command(text)[0] == intent


@pytest.mark.asyncio
async def test_stock_menu_uses_visible_host_menu_and_original_recipient(monkeypatch):
    from nonebot_plugin_sayustock import _help, _dispatch

    module = ModuleType("src.plugins.nonebot_plugin_picmenu_next.__main__")
    menu = AsyncMock()
    module.render_menu = AsyncMock(return_value=(menu, None, None))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    bot, event = AsyncMock(), object()
    await _help(bot, event)
    await _dispatch(bot, event, "")
    assert menu.send.await_count == 2
    menu.send.assert_awaited_with(target=event, bot=bot)
    module.render_menu.assert_awaited_with(bot, event, plugin_id="nonebot_plugin_sayustock")
    bot.send.assert_not_awaited()

    module.render_menu.return_value = (None, None, None)
    await _help(bot, event)
    bot.send.assert_awaited_once_with(event, "当前没有可显示的股票菜单。")
