"""User-facing command service shared by matchers, web chat and tests."""

from __future__ import annotations

import re
from typing import Any
from dataclasses import field, dataclass

from nonebot.log import logger

from .menu import USAGE
from .config import get_value
from .runtime import get_runtime, artifact_url
from .features import invoke, lookup, catalog
from .platform import UnifiedEvent, send_text, send_image
from .artifacts import Artifact, create_html
from .watchlist import owner_key
from .platform.bridge import send_group_result
from .services.charts import (
    ChartRenderError,
    rasterize,
    overview_html,
    build_report_html,
    compare_series_html,
)
from .services.market import MarketServiceError, normalize_period
from .services.overview import summary as overview_summary


@dataclass(slots=True)
class CommandResult:
    text: str
    image: bytes | None = None
    artifacts: list[Artifact] = field(default_factory=list)

    extra_images: list[bytes] = field(default_factory=list)
    image_only: bool = False
    structured: dict = field(default_factory=dict)

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


async def help_result() -> CommandResult:
    lines = [
        "【SayuStock · NoneBot2】",
        USAGE,
        *(
            "、".join(f.command for f in catalog().values() if f.category == category)
            for category in dict.fromkeys(f.category for f in catalog().values())
        ),
        "",
        "提示：默认命令前缀由 NoneBot 的 COMMAND_START 决定；也支持直接发送“大盘概览”。",
        "支持 OneBot v11 / OneBot v12 / QQ 官方机器人。",
    ]
    return CommandResult("\n".join(lines))


async def overview_result(*, owner: str = "", with_image: bool = True) -> CommandResult:
    runtime = get_runtime()
    data = await runtime.market.overview(include_flow=True)
    html = overview_html(data)
    image = await _render(html, 1280) if with_image else None
    text = overview_summary(data)
    artifact = create_html("A股大盘概览", html, kind="chart", owner=owner, meta={"command": "overview"})
    return CommandResult(text, image, [artifact])


async def stock_result(raw: str, *, owner: str = "", with_image: bool = True) -> CommandResult:
    from SayuStock.utils import render_text
    from SayuStock.utils.utils import get_vix_name
    from SayuStock.render.kline import prepare_kline
    from SayuStock.render.compare import prepare_compare
    from SayuStock.render.intraday import prepare_multi_intraday, prepare_single_intraday
    from SayuStock.utils.stock_period import parse_stock_img_request, intraday_ndays_from_sector
    from SayuStock.stock_stockinfo.data import CloudMapDataService

    from .services.charts import chart_from_page

    content, sector = parse_stock_img_request(raw)
    if not content:
        return CommandResult("请后跟股票代码或名称，例如：个股 600519 / 个股 日k 贵州茅台")
    if get_vix_name(content) and sector != "single-stock":
        return CommandResult("VIX 仅支持当日分时")
    data = await CloudMapDataService(get_runtime().market.market).fetch(content, sector, None, None)
    values = data.raw_data
    if isinstance(values, str):
        return CommandResult(values)
    if data.sector == "compare-stock":
        page = prepare_compare(data.raw_datas)
        text = render_text.compare_text(data.raw_datas)
    elif sector.startswith("single-stock-kline-"):
        page = prepare_kline(values)
        text = render_text.kline_text(values, sector)
    else:
        page = prepare_multi_intraday(data.raw_datas) if data.raw_datas else prepare_single_intraday(values)
        text = render_text.single_stock_text(data.raw_datas or values, ndays=intraday_ndays_from_sector(sector) or 1)
    if isinstance(page, str):
        return CommandResult(page)
    html = chart_from_page(page)
    artifact = create_html(page.title, html, kind="chart", owner=owner)
    image = await _render(html, page.width) if with_image else None
    if data.failures:
        text += "\n未取得行情：" + "；".join(data.failures)
    return CommandResult(text, image, [artifact], structured={"failures": list(data.failures)})


async def compare_result(
    raw: str, *, event: UnifiedEvent | None = None, owner: str = "", with_image: bool = True
) -> CommandResult:
    runtime = get_runtime()
    from SayuStock.utils.time_range import parse_time_range

    start, end, content = parse_time_range(raw)
    tokens = content.replace("，", " ").replace(",", " ").split()
    period = "日k"
    if tokens:
        try:
            normalize_period(tokens[0])
            period = tokens.pop(0)
        except MarketServiceError:
            pass
    if not tokens and event is not None:
        tokens = [item["code"] for item in await runtime.watchlist.list(_watch_key(event))][:13]
    if not tokens:
        return CommandResult("用法：对比个股 贵州茅台 五粮液 沪深300")
    data = await runtime.market.compare(
        tokens, period, start=start.date() if start else None, end=end.date() if end else None
    )
    html, width, title = compare_series_html(data["series"])
    image = await _render(html, width) if with_image else None
    lines = [f"【{title}】{period}"]
    for trace in data.get("traces") or []:
        points = trace.get("points") or []
        last = points[-1].get("pct") if points else None
        lines.append(
            f"{trace.get('name')}: {last:+.2f}%" if isinstance(last, (int, float)) else f"{trace.get('name')}: —"
        )
    lines.extend(data["failures"])
    text = "\n".join(lines)
    artifact = create_html(title, html, kind="chart", owner=owner)
    return CommandResult(text, image, [artifact])


async def news_result(limit: int = 10, *, owner: str = "", with_image: bool = True) -> CommandResult:
    from .services.charts import news_card

    limit = max(1, min(limit, 30))
    data = await get_runtime().market.news(limit=limit)
    html, text = news_card(data, limit)
    artifact = create_html("财经快讯", html, kind="news", owner=owner)
    image = await _render(html, 880) if with_image else None
    return CommandResult(text, image, [artifact], image_only=True)


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
    labels = {
        "主力净流入": "main_inflow",
        "主力净流出": "main_outflow",
        "换手率": "turnover",
        "成交额": "amount",
        "成交量": "volume",
        "净利增速": "profit_yoy",
        "ROE": "roe",
    }
    data = await get_runtime().market.rank(labels.get(rank_by, rank_by.lower()), limit=10)
    rows = data.get("rows") or []
    lines = [f"【{data.get('rank_by_label') or rank_by}】"]
    for row in rows:
        lines.append(
            f"{row.get('rank')}. {row.get('name')}({row.get('code')}) "
            f"{row.get('metric_label') or ''}={row.get('metric')}"
        )
    lines.append("榜单仅作线索，不构成买卖依据。")
    return CommandResult("\n".join(lines))


async def sector_result(kind: str, limit: int = 20, *, falling: bool = False) -> CommandResult:
    data = await get_runtime().market.board(kind, limit=limit, falling=falling)
    rows = data.get("rows") or []
    title = "行业板块" if kind == "industry" else "概念板块"
    direction = "领跌" if falling else "领涨"
    member = "fall_name" if falling else "lead_name"
    lines = [f"【{title}{direction}榜】"]
    for index, row in enumerate(rows[:limit], 1):
        pct = row["change_pct"]
        change = f"{pct:+.2f}%" if pct is not None else "—"
        lines.append(f"{index}. {row['name']} {change} {direction} {row[member] or '—'}")
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

    def number(key: str) -> str:
        value = (data.get("indicators") or {}).get(key)
        return f"{value:.3f}" if isinstance(value, (int, float)) else "—"

    lines = [
        f"【技术分析】{symbol.get('name')}({symbol.get('code')}) · {period}",
        f"评分 {data.get('score'):+.3f}，收盘 {data.get('price')}",
        f"MA20 {number('ma20')} / MA60 {number('ma60')}，"
        f"MACD {number('macd_bar')}，RSI12 {number('rsi12')}，KDJ {number('kdj_j')}",
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
    from SayuStock.stock_info.watch_view import load_watch_view, watch_index_quotes

    from .services.theme import themed_upstream_page
    from .services.market import quote_dict

    market = get_runtime().market.market
    codes = [str(item["code"]) for item in items]
    view = await load_watch_view(codes, market, await watch_index_quotes(market))
    html = themed_upstream_page(view.html, "我的自选")
    artifact = create_html("我的自选", html, kind="chart", owner=ue.user_id)
    image = await _render(html, view.width) if ue.platform != "qweb" and not ue._extra.get("qweb") else None
    return CommandResult(
        view.text,
        image,
        [artifact],
        structured={
            "watchlist": items,
            "quotes": [quote_dict(q) for q, _ in view.quotes],
            "failures": [{"code": f.label, "reason": f.reason} for f in view.failures],
        },
    )


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
    image = await _render(html, 960) if ue.platform != "qweb" and not ue._extra.get("qweb") else None
    text = f"【{symbol} 研究报告】技术评分 {technical.get('score'):+.3f}"
    if image is None and ue.platform != "qweb":
        text = markdown
    return CommandResult(text, image, [artifact])


_COMMAND_ALIASES = {
    "help": ("股票帮助", "股票菜单"),
    "overview": ("大盘概览", "大盘概况", "大盘", "股票大盘", "市场概览"),
    "stock": ("个股", "行情", "查行情"),
    "compare": ("对比个股", "个股对比", "走势对比"),
    "sector": ("板块", "行业板块", "概念板块", "板块排行"),
    "technical": ("技术分析", "股票分析"),
    "screen": ("自动选股", "选股", "条件选股"),
    "news": ("股票资讯", "财经快讯", "快讯"),
    "financial": ("财务", "财务分析", "基本面"),
    "valuation": ("估值", "估值分析"),
    "rank": ("资金排行", "排行榜", "榜单"),
    "add": ("添加自选", "添加个股", "加入自选", "添加股票", "添加持仓"),
    "remove": ("删除自选", "删除个股", "移除自选", "删除股票", "删除持仓"),
    "mine": ("我的自选", "我的持仓", "我的股票"),
    "clear": ("清空自选", "清空我的自选"),
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
    tokens = text.strip().lstrip("/").split(maxsplit=1)
    feature = lookup(tokens[0]) if tokens else None
    if feature is not None:
        return await invoke(feature, tokens[1] if len(tokens) > 1 else "", ue)
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
        return await compare_result(arg, event=ue, owner=ue.user_id, with_image=ue.platform != "qweb")
    if intent == "sector":
        if tokens[0] in {"行业板块", "概念板块"} and arg and arg not in {"涨幅", "跌幅", "领涨", "领跌"}:
            name = "概念云图" if tokens[0] == "概念板块" else "板块云图"
            return await invoke(catalog()[name], arg, ue)
        kind = "concept" if "概念" in text else "industry"
        return await sector_result(kind, falling="跌" in arg)
    if intent == "technical":
        return await technical_result(arg)
    if intent == "screen":
        return await screen_result(arg)
    if intent == "news":
        return await news_result(owner=ue.user_id, with_image=ue.platform != "qweb")
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
    images = ([result.image] if result.image else []) + result.extra_images
    if ue.group_id or ue.guild_id or ue.channel_id:
        await send_group_result(ue, "" if images and result.image_only else result.text, images)
        return
    for image in images:
        await send_image(ue, image)
    await send_text(ue, result.with_links())


__all__ = [
    "CommandResult",
    "deliver",
    "execute",
    "help_result",
    "parse_screen",
    "split_command",
]
