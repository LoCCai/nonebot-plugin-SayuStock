"""Use the host chat model to call the stock tools and collect owned artifacts."""

from __future__ import annotations

import asyncio

from .config import get_value
from .ai.tools import TOOLS, run_tool
from .artifacts import get_artifact
from .ai.contracts import ToolContext
from .services.market import MarketServiceError


def definitions():
    if not get_value("web.enabled", True):
        return {}
    return {name: tool for name, tool in TOOLS.items() if tool.permission == "user"}


async def call(name, arguments, *, qq, thread, is_admin, confirmed):
    tool = definitions()[name]
    if not qq or (tool.effect == "mutating") != confirmed:
        raise ValueError("请核对绑定 QQ 和操作")
    ctx = ToolContext(platform="qweb", user_id=qq, session_id=thread, bot_id="qweb", confirmed=confirmed)
    try:
        result = await run_tool(name, arguments, ctx)
    except MarketServiceError as error:
        if name not in ("sayustock_kline", "sayustock_technical_analysis"):
            raise
        query = arguments["query"]
        period = arguments.get("period", "日k")
        raise ValueError(f"{query} · {period} 未取得 K 线，本次查询无法计算技术指标。{error}") from error
    media = []
    for item in result.artifacts:
        if not item.mime.startswith("text/html"):
            continue
        owned = get_artifact(item.id, owner=qq)
        if not owned or owned[1].stat().st_size > 18000000:
            raise ValueError("无法读取本次生成的图表")
        html = await asyncio.to_thread(owned[1].read_text, encoding="utf-8")
        media.append((item.title, "html", html))
    if not media:
        media.extend((result.text[:80] or "股票图表", "image", image) for image in result.images)
    return result.text, result.structured, media
