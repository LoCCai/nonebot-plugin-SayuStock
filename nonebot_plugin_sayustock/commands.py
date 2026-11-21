"""User-facing command service shared by matchers, web chat and tests."""

from __future__ import annotations

import re
from typing import Any
from dataclasses import field, dataclass

from nonebot.log import logger

from .menu import USAGE
from .config import get_value
from .runtime import get_runtime, artifact_url
from .platform import UnifiedEvent, send_text, send_image
from .artifacts import Artifact, create_html
from .watchlist import owner_key
from .services.charts import (
    ChartRenderError,
    rasterize,
    overview_html,
    build_kline_html,
    build_report_html,
    build_compare_html,
    build_intraday_html,
    build_multi_intraday_html,
)
from .services.market import MarketServiceError, normalize_period


@dataclass(slots=True)
class CommandResult:
    text: str
    image: bytes | None = None
    artifacts: list[Artifact] = field(default_factory=list)

    def with_links(self) -> str:
        if not self.artifacts:
            return self.text
        base = str(get_value("web.public_base_url", "") or "")
        links = [artifact_url(item, public_base_url=base) for item in self.artifacts]
        return self.text + "\n网页查看: " + " ".join(links)


def _watch_key(ue: UnifiedEvent) -> str:
    return owner_key(platform=ue.platform, bot_id=ue.bot_id or ue.platform, user_id=ue.user_id)


async def _render(html: str, width: int) -> bytes | None:
    runtime = get_runtime()
    if not runtime.render_enabled:
        return None
    try:
        async with runtime.render_lock:
            return await rasterize(html, width=width, scale=runtime.render_scale())
    except ChartRenderError as error:
        logger.info("[SayuStock] 图片渲染不可用: {}", error)
        return None
    except Exception as error:
        logger.warning("[SayuStock] 图片渲染异常: {}", error)
        return None


def _artifact_link_note(artifacts: list[Artifact]) -> str:
    if not artifacts:
        return ""
    base = str(get_value("web.public_base_url", "") or "")
    return "\n网页查看: " + " ".join(artifact_url(item, public_base_url=base) for item in artifacts)


async def help_result() -> CommandResult:
    lines = [
        "【SayuStock · NoneBot2】",
        USAGE,
        "",
        "提示：默认命令前缀由 NoneBot 的 COMMAND_START 决定；也支持直接发送“大盘概览”。",
        "支持 OneBot v11 / OneBot v12 / QQ 官方机器人。",
    ]
    return CommandResult("\n".join(lines))


async def overview_result(*, owner: str = "", with_image: bool = True) -> CommandResult:
    runtime = get_runtime()
    data = await runtime.market.overview()
    html = overview_html(data)
    image = await _render(html, 1280) if with_image else None
    lines = [f"【A股大盘概览】{data.get('as_of', '')}"]
    for item in (data.get("indices") or [])[:6]:
        pct = item.get("change_pct")
        sign = "+" if isinstance(pct, (int, float)) and pct > 0 else ""
        lines.append(
            f"{item.get('label')}: {item.get('price')} ({sign}{pct:.2f}%)"
            if isinstance(pct, (int, float))
            else f"{item.get('label')}: {item.get('price')}"
        )
    breadth = data.get("breadth") or {}
    if breadth:
        lines.append(
            f"上涨 {breadth.get('up', 0)} / 下跌 {breadth.get('down', 0)} / "
            f"涨停 {breadth.get('limit_up', 0)} / 跌停 {breadth.get('limit_down', 0)}"
        )
    for warning in (data.get("warnings") or [])[:3]:
        lines.append(f"⚠ {warning}")
    text = "\n".join(lines)
    artifact = create_html("A股大盘概览", html, kind="chart", owner=owner, meta={"command": "overview"})
    if image is None:
        return CommandResult(text + _artifact_link_note([artifact]), None, [artifact])
    return CommandResult(text + _artifact_link_note([artifact]), image, [artifact])


async def stock_result(raw: str, *, owner: str = "", with_image: bool = True) -> CommandResult:
    runtime = get_runtime()
    from SayuStock.utils.stock_period import parse_stock_img_request

    content, sector = parse_stock_img_request(raw)
    if not content:
        return CommandResult("请后跟股票代码或名称，例如：个股 600519 / 个股 日k 贵州茅台")
    if sector.startswith("single-stock-kline-"):
        period_code = sector.rsplit("-", 1)[-1]
        period_map = {
            "5": "5k",
            "15": "15k",
            "30": "30k",
            "60": "60k",
            "100": "日k",
            "101": "日k",
            "102": "周k",
            "103": "月k",
            "104": "季k",
            "105": "半年k",
            "106": "年k",
        }
        period = period_map.get(period_code, "日k")
        html, width, title = await build_kline_html(runtime.market, content, period)
        image = await _render(html, width) if with_image else None
        artifact = create_html(title, html, kind="chart", owner=owner)
        text = f"【{title}】已生成 K 线图。" + _artifact_link_note([artifact])
        return CommandResult(text, image, [artifact])
    if sector.startswith("single-stock-ndays-"):
        ndays = int(sector.rsplit("-", 1)[-1])
    else:
        ndays = 1
    parts = content.split()
    if len(parts) > 1:
        html, width, title = await build_multi_intraday_html(runtime.market, parts, ndays)
    else:
        html, width, title = await build_intraday_html(runtime.market, content, ndays)
    image = await _render(html, width) if with_image else None
    artifact = create_html(title, html, kind="chart", owner=owner)
    text = f"【{title}】" + _artifact_link_note([artifact])
    return CommandResult(text, image, [artifact])


async def compare_result(raw: str, *, owner: str = "", with_image: bool = True) -> CommandResult:
    runtime = get_runtime()
    tokens = raw.replace("，", " ").split()
    period = "日k"
    if tokens:
        try:
            normalize_period(tokens[0])
            period = tokens.pop(0)
        except MarketServiceError:
            pass
    if len(tokens) < 2:
        return CommandResult("用法：对比个股 贵州茅台 五粮液 沪深300")
    html, width, title = await build_compare_html(runtime.market, tokens, period)
    image = await _render(html, width) if with_image else None
    data = await runtime.market.compare(tokens, period)
    lines = [f"【{title}】{period}"]
    for trace in data.get("traces") or []:
        points = trace.get("points") or []
        last = points[-1].get("pct") if points else None
        lines.append(
            f"{trace.get('name')}: {last:+.2f}%" if isinstance(last, (int, float)) else f"{trace.get('name')}: —"
        )
    text = "\n".join(lines)
    if image is None:
        artifact = create_html(title, html, kind="chart", owner=owner)
        return CommandResult(text + _artifact_link_note([artifact]), None, [artifact])
    return CommandResult(text, image)


async def news_result(limit: int = 10) -> CommandResult:
    data = await get_runtime().market.news(limit=max(1, min(limit, 30)))
    items = data.get("items") or []
    lines = [f"【{data.get('source_label') or data.get('source')} 财经快讯】"]
    for item in items[:limit]:
        prefix = "🔴" if item.get("important") else "·"
        lines.append(f"{prefix} {item.get('title') or item.get('text')}")
    return CommandResult("\n".join(lines))


async def financial_result(raw: str) -> CommandResult:
    query = raw.strip()
    if not query:
        return CommandResult("用法：财务 600519")
    data = await get_runtime().market.financial(query)
    financial = data.get("financial") or {}
    name = (data.get("symbol") or {}).get("name") or query
    lines = [
        f"【{name} 财务快照】报告期 {financial.get('report_date')}",
        f"ROE {financial.get('roe')}% / 营收同比 {financial.get('revenue_yoy')}% / "
        f"净利同比 {financial.get('profit_yoy')}%",
        f"毛利率 {financial.get('gross_margin')}% / 净利率 {financial.get('net_margin')}% / "
        f"负债率 {financial.get('debt_ratio')}%",
        f"EPS {financial.get('eps')} / BPS {financial.get('bps')}",
    ]
    return CommandResult("\n".join(lines))


async def valuation_result(raw: str) -> CommandResult:
    tokens = raw.split()
    kind = "pe"
    if tokens and tokens[0].lower() in {"pe", "pb", "dy"}:
        kind = tokens.pop(0).lower()
    query = " ".join(tokens).strip()
    if not query:
        return CommandResult("用法：估值 600519 或 估值 pb 600519")
    data = await get_runtime().market.valuation(query, kind)
    points = data.get("points") or []
    values = [point.get("value") for point in points]
    from .services.dataviz import describe

    stats = describe(values)
    name = (data.get("symbol") or {}).get("name") or query
    last = data.get("last") or {}
    last_value = last.get("value") if isinstance(last, dict) else last
    return CommandResult(
        f"【{name} {kind.upper()} 估值】{len(points)} 个交易日\n"
        f"最新 {last_value}，均值 {stats.get('mean')}，区间 {stats.get('min')} ~ {stats.get('max')}"
    )


async def rank_result(rank_by: str = "main_inflow") -> CommandResult:
    data = await get_runtime().market.rank(rank_by, limit=10)
    rows = data.get("rows") or []
    lines = [f"【{data.get('rank_by_label') or rank_by}】"]
    for row in rows:
        lines.append(
            f"{row.get('rank')}. {row.get('name')}({row.get('code')}) "
            f"{row.get('metric_label') or ''}={row.get('metric')}"
        )
    lines.append("榜单仅作线索，不构成买卖依据。")
    return CommandResult("\n".join(lines))


async def sector_result(kind: str, limit: int = 20) -> CommandResult:
    data = await get_runtime().market.board(kind, limit=limit)
    rows = data.get("rows") or []
    title = "行业板块" if kind == "industry" else "概念板块"
    lines = [f"【{title}涨幅榜】"]
    lines.extend(
        f"{index}. {row.get('name')} {row.get('change_pct'):+.2f}% 领涨 {row.get('lead_name') or '—'}"
        for index, row in enumerate(rows[:limit], 1)
    )
    return CommandResult("\n".join(lines))


async def technical_result(raw: str) -> CommandResult:
    tokens = raw.split()
    period = "日k"
    if tokens:
        try:
            normalize_period(tokens[0])
            period = tokens.pop(0)
        except MarketServiceError:
            pass
    query = " ".join(tokens).strip()
    if not query:
        return CommandResult("用法：技术分析 600519 或 技术分析 周k 贵州茅台")
    data = await get_runtime().market.technical(query, period)
    symbol = data.get("symbol") or {}
    ind = data.get("indicators") or {}
    lines = [
        f"【技术分析】{symbol.get('name')}({symbol.get('code')}) · {period}",
        f"评分 {data.get('score'):+.3f}，收盘 {data.get('price')}",
        f"MA20 {ind.get('ma20')} / MA60 {ind.get('ma60')}，"
        f"MACD {ind.get('macd_bar')}，RSI12 {ind.get('rsi12')}，KDJ {ind.get('kdj_j')}",
        "多头信号: " + ("、".join(data.get("signals") or []) or "暂无"),
        "风险信号: " + ("、".join(data.get("risks") or []) or "暂无"),
    ]
    return CommandResult("\n".join(lines))


_SCREEN_PATTERNS = {
    "min_market_cap_yi": re.compile(r"(?:市值|流通市值)\s*[>≥]\s*([0-9.]+)"),
    "max_market_cap_yi": re.compile(r"(?:市值|流通市值)\s*[<≤]\s*([0-9.]+)"),
}


def parse_screen(text: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    range_match = re.search(r"(?:市值|流通市值)\s*([0-9.]+)\s*-\s*([0-9.]+)", text)
    if range_match:
        args["min_market_cap_yi"] = float(range_match.group(1))
        args["max_market_cap_yi"] = float(range_match.group(2))
    for key, pattern in _SCREEN_PATTERNS.items():
        match = pattern.search(text)
        if match and key not in args:
            args[key] = float(match.group(1))
    pe_match = re.search(r"(?:PE|市盈率)\s*[<≤]\s*([0-9.]+)", text, re.I)
    if pe_match:
        args["max_pe"] = float(pe_match.group(1))
    change_match = re.search(r"(?:涨跌幅|涨幅)\s*[>≥]\s*(-?[0-9.]+)", text)
    if change_match:
        args["min_change"] = float(change_match.group(1))
    change_max = re.search(r"(?:涨跌幅|涨幅)\s*[<≤]\s*(-?[0-9.]+)", text)
    if change_max:
        args["max_change"] = float(change_max.group(1))
    industry = re.search(r"行业\s*([^\s]+)", text)
    if industry:
        args["industry"] = industry.group(1)
    args["limit"] = 20
    return args


async def screen_result(raw: str) -> CommandResult:
    if not raw.strip():
        return CommandResult("用法：自动选股 市值50-200 PE<30 涨跌幅>2 行业 半导体")
    args = parse_screen(raw)
    if len(args) <= 1:
        return CommandResult("没有识别到筛选条件。示例：自动选股 市值50-200 PE<30 涨跌幅>2")
    data = await get_runtime().market.screen(**args)
    rows = data.get("rows") or []
    lines = [f"【条件选股】命中 {len(rows)} 只（{data.get('universe')}）"]
    lines.extend(
        f"{row.get('code')} {row.get('name')} {row.get('change_pct'):+.2f}% "
        f"PE {row.get('pe')} 市值 {row.get('market_cap_yi')} 亿"
        for row in rows[:20]
    )
    return CommandResult("\n".join(lines))


async def add_watch(ue: UnifiedEvent, raw: str) -> CommandResult:
    runtime = get_runtime()
    tokens = [token for token in raw.replace("，", " ").split() if token]
    if not tokens:
        return CommandResult("用法：添加自选 600519 贵州茅台")
    added: list[str] = []
    failed: list[str] = []
    key = _watch_key(ue)
    for token in tokens[:5]:
        symbol = await runtime.market.resolve(token)
        if symbol is None:
            failed.append(token)
            continue
        try:
            if await runtime.watchlist.add(key, symbol.code, symbol.name, token):
                added.append(f"{symbol.name}({symbol.code})")
            else:
                failed.append(f"{token}（已存在）")
        except ValueError as error:
            failed.append(str(error))
            break
    lines = ["✅ 已加入自选：" + "、".join(added)] if added else []
    if failed:
        lines.append("未加入：" + "、".join(failed))
    return CommandResult("\n".join(lines) or "没有可加入的标的")


async def remove_watch(ue: UnifiedEvent, raw: str) -> CommandResult:
    runtime = get_runtime()
    tokens = [token for token in raw.replace("，", " ").split() if token]
    if not tokens:
        return CommandResult("用法：删除自选 600519")
    items = await runtime.watchlist.list(_watch_key(ue))
    removed: list[str] = []
    for token in tokens[:10]:
        target = next(
            (item for item in items if token in {item.get("code"), item.get("name"), item.get("query")}),
            None,
        )
        code = target.get("code") if target else None
        if code is None:
            symbol = await runtime.market.resolve(token)
            code = symbol.code if symbol else None
        if code and await runtime.watchlist.remove(_watch_key(ue), code):
            removed.append(token)
    return CommandResult("✅ 已移除：" + "、".join(removed) if removed else "没有找到要删除的自选")


async def my_watch(ue: UnifiedEvent) -> CommandResult:
    items = await get_runtime().watchlist.list(_watch_key(ue))
    if not items:
        return CommandResult("您还未添加自选。发送“添加自选 600519”即可。")
    lines = [f"【我的自选】共 {len(items)} 只"]
    for index, item in enumerate(items, 1):
        lines.append(f"{index}. {item.get('name')}({item.get('code')})")
    return CommandResult("\n".join(lines))


async def clear_watch(ue: UnifiedEvent) -> CommandResult:
    count = await get_runtime().watchlist.clear(_watch_key(ue))
    return CommandResult(f"✅ 已清空自选（{count} 只）" if count else "自选本来就是空的。")


async def report_result(ue: UnifiedEvent, raw: str) -> CommandResult:
    query = raw.strip()
    if not query:
        items = await get_runtime().watchlist.list(_watch_key(ue))
        if not items:
            return CommandResult("用法：股票报告 600519，或先添加自选")
        query = items[0].get("code") or ""
    runtime = get_runtime()
    quote = await runtime.market.quote(query)
    technical = await runtime.market.technical(query, "日k")
    symbol = quote.get("display_name") or quote.get("name")
    ind = technical.get("indicators") or {}
    markdown = f"""# {symbol} 研究报告

## 行情快照

| 项目 | 数值 |
| --- | --- |
| 最新价 | {quote.get("price")} |
| 涨跌幅 | {quote.get("change_pct")}% |
| 成交额 | {quote.get("amount")} |
| 换手率 | {quote.get("turnover_rate")}% |
| PE / PB | {quote.get("pe")} / {quote.get("pb")} |
| 数据源 | {quote.get("provider")} |

## 技术指标

| 指标 | 数值 |
| --- | --- |
| MA5 / MA20 / MA60 | {ind.get("ma5")} / {ind.get("ma20")} / {ind.get("ma60")} |
| MACD BAR | {ind.get("macd_bar")} |
| RSI6 / RSI12 | {ind.get("rsi6")} / {ind.get("rsi12")} |
| KDJ K/D/J | {ind.get("kdj_k")} / {ind.get("kdj_d")} / {ind.get("kdj_j")} |
| ATR% | {ind.get("atr_pct")} |

## 结论

技术评分 **{technical.get("score"):+.3f}**（-1 空 ~ +1 多）。

多头信号：{"、".join(technical.get("signals") or []) or "暂无"}

风险信号：{"、".join(technical.get("risks") or []) or "暂无"}

> 数据仅供研究，不构成投资建议。
"""
    html = await build_report_html(
        f"{symbol} 研究报告",
        markdown,
        meta={"生成时间": technical.get("as_of", ""), "数据源": quote.get("provider", "")},
    )
    artifact = create_html(
        f"{symbol} 研究报告",
        html,
        kind="report",
        owner=ue.user_id,
        meta={"session_id": ue.session_id},
    )
    return CommandResult(
        f"【{symbol} 研究报告】技术评分 {technical.get('score'):+.3f}" + _artifact_link_note([artifact]),
        None,
        [artifact],
    )


_COMMAND_ALIASES = {
    "help": ("股票帮助", "SayuStock帮助", "早柚股票"),
    "overview": ("大盘概览", "大盘", "市场概览"),
    "stock": ("个股", "行情", "查行情"),
    "compare": ("对比个股", "个股对比"),
    "sector": ("板块", "行业板块", "概念板块", "板块排行"),
    "technical": ("技术分析", "股票分析"),
    "screen": ("自动选股", "选股", "条件选股"),
    "news": ("股票资讯", "财经快讯", "快讯"),
    "financial": ("财务", "财务分析", "基本面"),
    "valuation": ("估值", "估值分析"),
    "rank": ("资金排行", "排行榜", "榜单"),
    "add": ("添加自选", "添加个股", "加入自选"),
    "remove": ("删除自选", "删除个股", "移除自选"),
    "mine": ("我的自选", "我的个股"),
    "clear": ("清空自选",),
    "report": ("股票报告", "研报", "股票研报"),
}


def split_command(text: str) -> tuple[str, str, str]:
    """Return ``(intent, argument, matched_alias)`` for a plain text message."""
    cleaned = text.strip().lstrip("/").strip()
    for intent, aliases in _COMMAND_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if cleaned == alias:
                return intent, "", alias
            if cleaned.startswith(alias):
                rest = cleaned[len(alias) :]
                if rest[:1].isspace():
                    return intent, rest.strip(), alias
    return "", cleaned, ""


async def execute(text: str, ue: UnifiedEvent) -> CommandResult:
    """Execute a command or a natural-language alias."""
    intent, arg, _ = split_command(text)
    if not intent:
        return CommandResult("未识别该指令。发送“股票帮助”查看用法；网页 AI 由 QWeb 宿主提供。")
    if intent == "help":
        return await help_result()
    if intent == "overview":
        return await overview_result(owner=ue.user_id, with_image=ue.platform != "qweb")
    if intent == "stock":
        return await stock_result(arg, owner=ue.user_id, with_image=ue.platform != "qweb")
    if intent == "compare":
        return await compare_result(arg, owner=ue.user_id, with_image=ue.platform != "qweb")
    if intent == "sector":
        kind = "concept" if "概念" in text else "industry"
        return await sector_result(kind)
    if intent == "technical":
        return await technical_result(arg)
    if intent == "screen":
        return await screen_result(arg)
    if intent == "news":
        return await news_result()
    if intent == "financial":
        return await financial_result(arg)
    if intent == "valuation":
        return await valuation_result(arg)
    if intent == "rank":
        return await rank_result(arg or "main_inflow")
    if intent == "add":
        return await add_watch(ue, arg)
    if intent == "remove":
        return await remove_watch(ue, arg)
    if intent == "mine":
        return await my_watch(ue)
    if intent == "clear":
        return await clear_watch(ue)
    if intent == "report":
        return await report_result(ue, arg)
    return CommandResult("未实现的指令。")


async def deliver(ue: UnifiedEvent, result: CommandResult) -> None:
    """Deliver a command result to the originating chat."""
    text = result.with_links()
    if result.image is not None:
        sent = await send_image(ue, result.image, caption="")
        if sent:
            if text:
                await send_text(ue, text)
            return
    if text:
        await send_text(ue, text)


__all__ = [
    "CommandResult",
    "deliver",
    "execute",
    "help_result",
    "parse_screen",
    "split_command",
]
