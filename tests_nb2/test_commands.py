from __future__ import annotations

import pytest

from nonebot_plugin_sayustock.commands import split_command


def test_split_command_prefix_and_alias():
    assert split_command("/大盘概览")[0] == "overview"
    assert split_command("个股 日k 贵州茅台") == ("stock", "日k 贵州茅台", "个股")
    assert split_command("添加自选 600519") == ("add", "600519", "添加自选")
    assert split_command("随便聊聊")[0] == ""
    assert split_command("股票菜单")[0] == "help"
    from nonebot_plugin_sayustock import dispatcher, help_matcher
    from nonebot.rule import CommandRule

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
