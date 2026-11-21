"""Shared catalogue for the upstream stock workflows available in Qiqi."""

from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from dataclasses import dataclass

from .platform import UnifiedEvent
from .feature_data import DOMAIN_FEATURES


@dataclass(frozen=True)
class Feature:
    command: str
    aliases: tuple[str, ...]
    usage: str
    writes: bool
    module: str
    category: str


DOMAINS = (
    ("fundwork", "基金与市场图表"),
    ("studywork", "策略研究"),
    ("newswork", "日历与资讯"),
    ("paperwork", "模拟盘与宏观"),
)


@lru_cache(maxsize=1)
def catalog() -> dict[str, Feature]:
    features = {}
    for module, category in DOMAINS:
        for name, (aliases, usage, writes) in DOMAIN_FEATURES.get(module, {}).items():
            if name in features:
                raise ValueError(f"股票功能名重复：{name}")
            features[name] = Feature(name, tuple(a for a in aliases if a != name), usage, writes, module, category)
    return features


@lru_cache(maxsize=1)
def command_index() -> dict[str, Feature]:
    index = {}
    for feature in catalog().values():
        for alias in (feature.command, *feature.aliases):
            key = alias.casefold()
            if key in index and index[key] != feature:
                raise ValueError(f"股票指令有多种含义：{alias}")
            index[key] = feature
    return index


def lookup(name: str) -> Feature | None:
    return command_index().get(name.casefold())


async def invoke(feature: Feature, argument: str, event: UnifiedEvent):
    if not event.user_id or len(argument) > 2000:
        raise ValueError("请核对身份与输入长度")
    module = import_module(f".services.{feature.module}", __package__)
    return await module.dispatch(feature.command, argument.strip(), event)


async def web_event(qq: str, command: str, group_id: str | None) -> UnifiedEvent:
    if group_id:
        from types import SimpleNamespace

        from src.qi_web.services.chat_actions import group_context

        from .platform import from_event

        if not (group_id.isascii() and group_id.isdigit() and 1 <= len(group_id) <= 12):
            raise ValueError("群号无效")
        bot, event, node = await group_context(SimpleNamespace(qq=int(qq)), int(group_id), "/" + command)
        if node.get("plugin") != "nonebot_plugin_sayustock":
            raise ValueError("这个群未开放该股票指令")
        unified = from_event(bot, event, text=command)
        unified._extra["qweb"] = True
        return unified
    return UnifiedEvent(platform="qweb", bot_id="qweb", user_id=qq, text=command)
