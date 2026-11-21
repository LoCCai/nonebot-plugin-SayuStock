"""SayuStock data/analysis tools exposed to LLMs and the web chat."""

from __future__ import annotations

import json
from typing import Any

from nonebot.log import logger

from ..runtime import get_runtime, artifact_url
from .contracts import ToolContext, ToolOutcome, ToolDefinition
from ..artifacts import create_png, create_html
from ..watchlist import owner_key
from ..services.theme import apply_theme
from ..services.charts import (
    ChartRenderError,
    news_card,
    rasterize,
    overview_html,
    dashboard_html,
    build_kline_html,
    build_report_html,
    build_compare_html,
    build_intraday_html,
    compare_series_html,
)
from ..services.market import MarketServiceError
from ..services.dataviz import (
    table_html,
    correlation,
    bar_chart_html,
    pie_chart_html,
    series_summary,
    line_chart_html,
)

MARKET_PERIODS = ["5k", "15k", "30k", "60k", "日k", "周k", "月k", "季k", "年k"]


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:+.2f}%"


def _summary_overview(data: dict[str, Any]) -> str:
    lines = [f"【A股大盘概览】{data.get('as_of', '')}"]
    for item in (data.get("indices") or [])[:8]:
        lines.append(f"{item.get('label')}: {item.get('price')} ({_pct(item.get('change_pct'))})")
    breadth = data.get("breadth") or {}
    if breadth:
        lines.append(
            f"涨跌家数: 上涨 {breadth.get('up', 0)} / 下跌 {breadth.get('down', 0)} / "
            f"平 {breadth.get('flat', 0)}；涨停 {breadth.get('limit_up', 0)}，"
            f"跌停 {breadth.get('limit_down', 0)}"
        )
    turnover = data.get("turnover") or {}
    if isinstance(turnover.get("amount"), (int, float)):
        lines.append(f"两市成交额: {float(turnover['amount']) / 1e8:,.0f} 亿")
    north = data.get("northbound") or {}
    if north:
        lines.append(f"北向净流入: {float(north.get('sh_net_yi', 0)) + float(north.get('sz_net_yi', 0)):,.2f} 亿")
    rise = (data.get("industries") or {}).get("rise") or []
    fall = (data.get("industries") or {}).get("fall") or []
    if rise:
        lines.append("行业领涨: " + "、".join(f"{r['name']} {_pct(r.get('change_pct'))}" for r in rise[:5]))
    if fall:
        lines.append("行业领跌: " + "、".join(f"{r['name']} {_pct(r.get('change_pct'))}" for r in fall[:5]))
    for warning in (data.get("warnings") or [])[:4]:
        lines.append(f"⚠ {warning}")
    return "\n".join(lines)


def _summary_quote(data: dict[str, Any]) -> str:
    return (
        f"【{data.get('display_name') or data.get('name')}】{data.get('code')}\n"
        f"最新 {data.get('price')}，涨跌 {_pct(data.get('change_pct'))}，"
        f"今开 {data.get('open')} / 最高 {data.get('high')} / 最低 {data.get('low')}\n"
        f"成交额 {data.get('amount')}，换手 {data.get('turnover_rate')}%，"
        f"PE {data.get('pe')}，PB {data.get('pb')}\n"
        f"数据源 {data.get('provider') or '未标注'}，时间 {data.get('as_of') or '—'}"
    )


def _summary_technical(data: dict[str, Any]) -> str:
    symbol = data.get("symbol") or {}
    ind = data.get("indicators") or {}
    lines = [
        f"【技术分析】{symbol.get('name')}({symbol.get('code')}) · {data.get('period')}",
        f"评分 {data.get('score'):+.3f}（-1 空 ~ +1 多），收盘 {data.get('price')}",
        f"MA5/10/20/60: {ind.get('ma5')} / {ind.get('ma10')} / {ind.get('ma20')} / {ind.get('ma60')}",
        f"MACD DIF/DEA/BAR: {ind.get('macd_dif')} / {ind.get('macd_dea')} / {ind.get('macd_bar')}",
        f"RSI6/12/24: {ind.get('rsi6')} / {ind.get('rsi12')} / {ind.get('rsi24')}",
        f"KDJ K/D/J: {ind.get('kdj_k')} / {ind.get('kdj_d')} / {ind.get('kdj_j')}",
        f"BOLL 中/上/下: {ind.get('boll20_mid')} / {ind.get('boll20_upper')} / {ind.get('boll20_lower')}",
        f"ATR% {ind.get('atr_pct')}，CMF20 {ind.get('cmf20')}，量比 {ind.get('volume_ratio')}",
        "多头信号: " + ("、".join(data.get("signals") or []) or "暂无"),
        "风险信号: " + ("、".join(data.get("risks") or []) or "暂无"),
    ]
    return "\n".join(lines)


async def _chart_artifacts(
    *,
    title: str,
    html: str,
    width: int,
    ctx: ToolContext,
    with_png: bool = True,
    kind: str = "chart",
) -> tuple[bytes | None, list[Any]]:
    artifacts = [
        create_html(
            title,
            html,
            kind=kind,
            owner=ctx.user_id,
            meta={"session_id": ctx.session_id},
        )
    ]
    image: bytes | None = None
    if with_png and ctx.platform != "qweb" and get_runtime().render_enabled:
        try:
            async with get_runtime().render_lock:
                image = await rasterize(html, width=width, scale=get_runtime().render_scale())
            artifacts.append(create_png(title, image, owner=ctx.user_id))
        except ChartRenderError as error:
            logger.info("[SayuStock] 图片渲染不可用，保留 HTML 制品: {}", error)
        except Exception as error:  # pragma: no cover - optional backend
            logger.warning("[SayuStock] 图片渲染异常: {}", error)
    return image, artifacts


def _artifact_note(ctx: ToolContext, artifacts: list[Any]) -> str:
    if ctx.platform != "qweb" or not artifacts:
        return ""
    from ..config import get_value

    base = str(get_value("web.public_base_url", "") or "")
    links = [artifact_url(item, public_base_url=base) for item in artifacts]
    return "\n图表/报告链接: " + " ".join(links)


async def market_overview(*, include_chart: bool = False, _tool_context: ToolContext) -> ToolOutcome:
    data = await get_runtime().market.overview()
    html = overview_html(data)
    image = None
    artifacts: list[Any] = []
    if include_chart:
        image, artifacts = await _chart_artifacts(title="A股大盘概览", html=html, width=1280, ctx=_tool_context)
    text = _summary_overview(data) + _artifact_note(_tool_context, artifacts)
    return ToolOutcome(
        text=text,
        structured={"overview": data},
        images=[image] if image else [],
        artifacts=artifacts,
        metadata={"provider": data.get("provider"), "as_of": data.get("as_of")},
    )


async def quote_tool(*, query: str, _tool_context: ToolContext) -> ToolOutcome:
    runtime = get_runtime()
    data = await runtime.market.quote(query)
    return ToolOutcome(
        text=_summary_quote(data),
        structured={"quote": data},
        metadata={"provider": data.get("provider")},
    )


async def kline_tool(
    *,
    query: str,
    period: str = "日k",
    include_chart: bool = True,
    _tool_context: ToolContext,
) -> ToolOutcome:
    runtime = get_runtime()
    data = await runtime.market.kline(query, period)
    symbol = data["symbol"]
    indicators = data["indicators"]
    bars = data["bars"]
    text = (
        f"【{symbol.get('name')}({symbol.get('code')}) · {period}】"
        f"共 {len(bars)} 根，最新收盘 {bars[-1]['close'] if bars else '—'}，"
        f"MACD {indicators.get('macd_bar')}，RSI12 {indicators.get('rsi12')}，"
        f"MA20 {indicators.get('ma20')}。"
    )
    image = None
    artifacts: list[Any] = []
    if include_chart:
        html, width, title = await build_kline_html(runtime.market, query, period)
        image, artifacts = await _chart_artifacts(title=title, html=html, width=width, ctx=_tool_context)
    return ToolOutcome(
        text=text + _artifact_note(_tool_context, artifacts),
        structured={
            "symbol": symbol,
            "period": period,
            "indicators": indicators,
            "recent_bars": bars[-40:],
        },
        images=[image] if image else [],
        artifacts=artifacts,
        metadata={"provider": data.get("provider")},
    )


async def technical_tool(*, query: str, period: str = "日k", _tool_context: ToolContext) -> ToolOutcome:
    data = await get_runtime().market.technical(query, period)
    return ToolOutcome(
        text=_summary_technical(data),
        structured=data,
        metadata={"provider": data.get("provider")},
    )


async def sector_tool(
    *,
    kind: str = "industry",
    limit: int = 15,
    _tool_context: ToolContext,
) -> ToolOutcome:
    normalized = "concept" if kind in {"concept", "概念"} else "industry"
    data = await get_runtime().market.board(normalized, limit=max(1, min(limit, 50)))
    rows = data.get("rows") or []
    title = "行业板块" if normalized == "industry" else "概念板块"
    lines = [f"【{title}涨幅榜】来源 {data.get('provider') or '多源'}"]
    lines.extend(
        f"{index}. {row.get('name')} {_pct(row.get('change_pct'))} "
        f"成交额 {row.get('amount')} 领涨 {row.get('lead_name') or '—'}"
        for index, row in enumerate(rows[: max(1, min(limit, 30))], 1)
    )
    return ToolOutcome(
        text="\n".join(lines),
        structured={"kind": normalized, "rows": rows},
        metadata={"provider": data.get("provider")},
    )


async def compare_tool(
    *,
    queries: list[str],
    period: str = "日k",
    include_chart: bool = True,
    _tool_context: ToolContext,
) -> ToolOutcome:
    runtime = get_runtime()
    data = await runtime.market.compare(queries, period)
    traces = data.get("traces") or []
    lines = [f"【个股走势对比】{period}"]
    for trace in traces:
        points = trace.get("points") or []
        last = points[-1].get("pct") if points else None
        lines.append(f"{trace.get('name')}({trace.get('code')}): {_pct(last)}")
    image = None
    artifacts: list[Any] = []
    if include_chart:
        html, width, title = compare_series_html(data["series"])
        image, artifacts = await _chart_artifacts(title=title, html=html, width=width, ctx=_tool_context)
    return ToolOutcome(
        text="\n".join(lines) + _artifact_note(_tool_context, artifacts),
        structured={"period": period, "traces": traces},
        images=[image] if image else [],
        artifacts=artifacts,
    )


async def screen_tool(
    *,
    min_change: float | None = None,
    max_change: float | None = None,
    max_pe: float | None = None,
    min_market_cap_yi: float | None = None,
    max_market_cap_yi: float | None = None,
    industry: str = "",
    limit: int = 20,
    _tool_context: ToolContext,
) -> ToolOutcome:
    data = await get_runtime().market.screen(
        min_change=min_change,
        max_change=max_change,
        max_pe=max_pe,
        min_market_cap_yi=min_market_cap_yi,
        max_market_cap_yi=max_market_cap_yi,
        industry=industry,
        limit=limit,
    )
    rows = data.get("rows") or []
    lines = [f"【条件选股】命中 {len(rows)} 只（{data.get('universe')}）"]
    lines.extend(
        f"{row.get('code')} {row.get('name')} {_pct(row.get('change_pct'))} "
        f"PE {row.get('pe')} 市值 {row.get('market_cap_yi')} 亿"
        for row in rows[:30]
    )
    return ToolOutcome(text="\n".join(lines), structured=data)


async def watchlist_list_tool(*, _tool_context: ToolContext) -> ToolOutcome:
    ctx = _tool_context
    key = owner_key(platform=ctx.platform, bot_id=ctx.bot_id or "web", user_id=ctx.user_id)
    items = await get_runtime().watchlist.list(key)
    text = (
        "自选为空。"
        if not items
        else "当前自选：\n"
        + "\n".join(f"{index}. {item.get('name')}({item.get('code')})" for index, item in enumerate(items, 1))
    )
    return ToolOutcome(text=text, structured={"watchlist": items})


async def watchlist_add_tool(*, query: str, _tool_context: ToolContext) -> ToolOutcome:
    ctx = _tool_context
    runtime = get_runtime()
    symbol = await runtime.market.resolve(query)
    if symbol is None:
        raise MarketServiceError(f"未找到标的：{query}")
    key = owner_key(platform=ctx.platform, bot_id=ctx.bot_id or "web", user_id=ctx.user_id)
    added = await runtime.watchlist.add(key, symbol.code, symbol.name, query)
    return ToolOutcome(
        text=(f"已加入自选：{symbol.name}({symbol.code})" if added else f"{symbol.name}({symbol.code}) 已在自选中"),
        structured={"added": added, "code": symbol.code, "name": symbol.name},
        metadata={"effect": "mutating"},
    )


async def watchlist_remove_tool(*, query: str, _tool_context: ToolContext) -> ToolOutcome:
    ctx = _tool_context
    runtime = get_runtime()
    items = await runtime.watchlist.list(
        owner_key(platform=ctx.platform, bot_id=ctx.bot_id or "web", user_id=ctx.user_id)
    )
    target = None
    for item in items:
        if query in {item.get("code"), item.get("name"), item.get("query")}:
            target = item
            break
    if target is None:
        symbol = await runtime.market.resolve(query)
        target = {"code": symbol.code} if symbol else None
    if target is None:
        raise MarketServiceError(f"自选中没有：{query}")
    removed = await runtime.watchlist.remove(
        owner_key(platform=ctx.platform, bot_id=ctx.bot_id or "web", user_id=ctx.user_id),
        str(target.get("code")),
    )
    return ToolOutcome(
        text=f"已移除 {query}" if removed else f"{query} 不在自选中",
        structured={"removed": removed, "code": target.get("code")},
        metadata={"effect": "mutating"},
    )


async def generate_chart_tool(
    *,
    chart_type: str = "kline",
    query: str = "上证指数",
    period: str = "日k",
    ndays: int = 1,
    queries: list[str] | None = None,
    _tool_context: ToolContext,
) -> ToolOutcome:
    runtime = get_runtime()
    kind = (chart_type or "kline").lower()
    if kind in {"overview", "market", "大盘"}:
        data = await runtime.market.overview()
        html, width, title = overview_html(data), 1280, "A股大盘概览"
        structured = {"chart_type": "overview", "overview": data}
    elif kind in {"intraday", "分时"}:
        if queries and len(queries) > 1:
            from ..services.charts import build_multi_intraday_html

            html, width, title = await build_multi_intraday_html(
                runtime.market, queries, ndays=max(1, min(int(ndays), 5))
            )
        else:
            html, width, title = await build_intraday_html(runtime.market, query, ndays=max(1, min(int(ndays), 5)))
        structured = {"chart_type": "intraday", "query": query, "ndays": ndays}
    elif kind in {"compare", "对比"}:
        html, width, title = await build_compare_html(runtime.market, queries or [query, "沪深300"], period)
        structured = {"chart_type": "compare", "queries": queries or [query], "period": period}
    else:
        html, width, title = await build_kline_html(runtime.market, query, period)
        structured = {"chart_type": "kline", "query": query, "period": period}
    image, artifacts = await _chart_artifacts(title=title, html=html, width=width, ctx=_tool_context)
    if image is None:
        text = f"已生成 HTML 图表：{title}" + _artifact_note(_tool_context, artifacts)
    else:
        text = f"图表已生成：{title}" + _artifact_note(_tool_context, artifacts)
    return ToolOutcome(
        text=text,
        structured=structured,
        images=[image] if image else [],
        artifacts=artifacts,
    )


async def generate_report_tool(
    *,
    title: str,
    markdown: str,
    meta: dict[str, Any] | None = None,
    _tool_context: ToolContext,
) -> ToolOutcome:
    html = await build_report_html(title, markdown, meta=meta)
    image, artifacts = await _chart_artifacts(
        title=title,
        html=html,
        width=960,
        ctx=_tool_context,
        kind="report",
    )
    return ToolOutcome(
        text=f"研究报告：{title}" + _artifact_note(_tool_context, artifacts),
        structured={"title": title, "markdown": markdown},
        images=[image] if image else [],
        artifacts=artifacts,
    )


async def list_artifacts_tool(*, _tool_context: ToolContext) -> ToolOutcome:
    from ..artifacts import list_artifacts

    items = list_artifacts(20, owner=_tool_context.user_id)
    text = (
        "暂无制品。"
        if not items
        else "最近制品：\n" + "\n".join(f"{item['id']} · {item['title']} · {item['kind']}" for item in items)
    )
    return ToolOutcome(text=text, structured={"artifacts": items})


async def rank_tool(
    *,
    rank_by: str = "main_inflow",
    limit: int = 10,
    _tool_context: ToolContext,
) -> ToolOutcome:
    data = await get_runtime().market.rank(rank_by, limit=max(1, min(limit, 50)))
    rows = data.get("rows") or []
    lines = [f"【{data.get('rank_by_label') or rank_by}】{data.get('unit_hint') or ''}"]
    lines.extend(
        f"{row.get('rank')}. {row.get('name')}({row.get('code')}) {row.get('metric_label') or ''}={row.get('metric')}"
        for row in rows[:limit]
    )
    lines.append(str(data.get("caveat") or "榜单仅作线索，不构成买卖依据。"))
    return ToolOutcome(text="\n".join(lines), structured=data)


async def financial_tool(*, query: str, _tool_context: ToolContext) -> ToolOutcome:
    data = await get_runtime().market.financial(query)
    financial = data.get("financial") or {}
    quote = data.get("quote") or {}
    name = (data.get("symbol") or {}).get("name") or query
    lines = [
        f"【{name} 财务快照】报告期 {financial.get('report_date')}",
        f"ROE {financial.get('roe')}%，营收同比 {financial.get('revenue_yoy')}%，"
        f"净利同比 {financial.get('profit_yoy')}%",
        f"毛利率 {financial.get('gross_margin')}%，净利率 {financial.get('net_margin')}%，"
        f"负债率 {financial.get('debt_ratio')}%",
        f"EPS {financial.get('eps')}，BPS {financial.get('bps')}，PE {quote.get('pe')}，PB {quote.get('pb')}",
    ]
    if financial.get("missing_fields"):
        lines.append("缺失字段：" + "、".join(financial["missing_fields"]))
    return ToolOutcome(text="\n".join(lines), structured=data)


async def valuation_tool(
    *,
    query: str,
    kind: str = "pe",
    _tool_context: ToolContext,
) -> ToolOutcome:
    data = await get_runtime().market.valuation(query, kind)
    points = data.get("points") or []
    values = [point.get("value") for point in points]
    from ..services.dataviz import describe

    stats = describe(values)
    name = (data.get("symbol") or {}).get("name") or query
    last = data.get("last") or {}
    last_value = last.get("value") if isinstance(last, dict) else last
    text = (
        f"【{name} {kind.upper()} 估值】共 {len(points)} 个交易日\n"
        f"最新 {last_value}，均值 {stats.get('mean')}，"
        f"区间 {stats.get('min')} ~ {stats.get('max')}，分位参考见 structured"
    )
    return ToolOutcome(text=text, structured=data)


async def ipo_tool(*, market: str = "cn", _tool_context: ToolContext) -> ToolOutcome:
    data = await get_runtime().market.ipo_calendar(market)
    events = data.get("events") or []
    lines = [f"【IPO 日历 · {data.get('market')}】共 {data.get('count')} 条"]
    lines.extend(
        f"{event.get('name') or event.get('code')} | 申购 {event.get('apply_date') or '—'} | "
        f"上市 {event.get('listing_date') or '—'} | 发行价 {event.get('issue_price') or '—'}"
        for event in events[:20]
    )
    return ToolOutcome(text="\n".join(lines), structured=data)


async def news_tool(*, limit: int = 12, _tool_context: ToolContext) -> ToolOutcome:
    data = await get_runtime().market.news(limit=max(1, min(limit, 50)))
    html, text = news_card(data, limit)
    image, artifacts = await _chart_artifacts(
        title="财经快讯",
        html=html,
        width=880,
        ctx=_tool_context,
        kind="news",
    )
    return ToolOutcome(
        text=text + _artifact_note(_tool_context, artifacts),
        structured=data,
        images=[image] if image else [],
        artifacts=artifacts,
        metadata={"provider": data.get("source"), "count": len(data.get("items") or [])},
    )


async def plot_series_tool(
    *,
    title: str,
    series: list[dict[str, Any]],
    labels: list[str] | None = None,
    chart_type: str = "line",
    y_label: str = "",
    note: str = "",
    _tool_context: ToolContext,
) -> ToolOutcome:
    if not series:
        raise MarketServiceError("至少需要一组 series")
    normalized = [
        {"name": str(item.get("name") or f"series{index}"), "values": item.get("values") or []}
        for index, item in enumerate(series[:8])
    ]
    count = max((len(item["values"]) for item in normalized), default=0)
    if count == 0:
        raise MarketServiceError("series.values 不能为空")
    x_labels = list(labels or [str(index + 1) for index in range(count)])[:count]
    kind = "bar" if chart_type.lower() in {"bar", "柱状", "柱状图"} else "line"
    html = (
        bar_chart_html(title=title, labels=x_labels, series=normalized, y_label=y_label, note=note)
        if kind == "bar"
        else line_chart_html(title=title, labels=x_labels, series=normalized, y_label=y_label, note=note)
    )
    html = apply_theme(html, recolor=True)
    image, artifacts = await _chart_artifacts(title=title, html=html, width=1100, ctx=_tool_context)
    summary = series_summary(normalized)
    lines = [f"【{title}】共 {len(normalized)} 组、{count} 个数据点"]
    for item in summary:
        lines.append(
            f"{item['name']}: 均值 {item.get('mean')}，区间 {item.get('min')} ~ "
            f"{item.get('max')}，变化 {item.get('change_pct')}%"
        )
    return ToolOutcome(
        text="\n".join(lines) + _artifact_note(_tool_context, artifacts),
        structured={"series": summary, "labels": x_labels, "chart_type": kind},
        images=[image] if image else [],
        artifacts=artifacts,
    )


async def pie_chart_tool(
    *,
    title: str,
    labels: list[str],
    values: list[float],
    note: str = "",
    _tool_context: ToolContext,
) -> ToolOutcome:
    html = apply_theme(pie_chart_html(title=title, labels=labels, values=values, note=note), recolor=True)
    image, artifacts = await _chart_artifacts(title=title, html=html, width=900, ctx=_tool_context)
    total = sum(value for value in values if isinstance(value, (int, float)))
    lines = [f"【{title}】合计 {total:,.2f}"]
    for label, value in zip(labels, values, strict=False):
        pct = value / total * 100 if total else 0
        lines.append(f"{label}: {value:,.2f}（{pct:.2f}%）")
    return ToolOutcome(
        text="\n".join(lines) + _artifact_note(_tool_context, artifacts),
        structured={"labels": labels, "values": values, "total": total},
        images=[image] if image else [],
        artifacts=artifacts,
    )


async def data_table_tool(
    *,
    title: str,
    columns: list[str],
    rows: list[list[Any]],
    note: str = "",
    _tool_context: ToolContext,
) -> ToolOutcome:
    if not columns:
        raise MarketServiceError("columns 不能为空")
    normalized_rows = [list(row) for row in rows[:300]]
    html = apply_theme(table_html(title=title, columns=columns, rows=normalized_rows, note=note), recolor=True)
    image, artifacts = await _chart_artifacts(
        title=title,
        html=html,
        width=1100,
        ctx=_tool_context,
        kind="table",
    )
    return ToolOutcome(
        text=f"【{title}】共 {len(normalized_rows)} 行" + _artifact_note(_tool_context, artifacts),
        structured={"columns": columns, "rows": normalized_rows},
        images=[image] if image else [],
        artifacts=artifacts,
    )


async def dashboard_tool(
    *,
    title: str,
    metrics: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    note: str = "",
    _tool_context: ToolContext,
) -> ToolOutcome:
    data = {
        "title": title,
        "metrics": metrics,
        "tables": tables,
        "notes": [note] if note else [],
    }
    html = dashboard_html(data, title=title)
    image, artifacts = await _chart_artifacts(
        title=title,
        html=html,
        width=1100,
        ctx=_tool_context,
        kind="dashboard",
    )
    metrics_count = len(metrics)
    rows_count = sum(len(table.get("rows") or []) for table in tables if isinstance(table, dict))
    text = f"【{title}】{metrics_count} 个指标、{rows_count} 行数据" + _artifact_note(_tool_context, artifacts)
    return ToolOutcome(
        text=text,
        structured={"title": title, "metrics": metrics, "tables": tables},
        images=[image] if image else [],
        artifacts=artifacts,
    )


async def analyze_series_tool(
    *,
    series: list[dict[str, Any]],
    title: str = "数据分析",
    include_chart: bool = False,
    _tool_context: ToolContext,
) -> ToolOutcome:
    normalized = [
        {"name": str(item.get("name") or f"series{index}"), "values": item.get("values") or []}
        for index, item in enumerate(series[:8])
    ]
    summary = series_summary(normalized)
    corr = correlation(normalized)
    lines = [f"【{title}】"]
    for item in summary:
        lines.append(
            f"{item['name']}: n={item.get('count')} 均值={item.get('mean')} "
            f"中位数={item.get('median')} 波动率={item.get('stdev')} "
            f"最大回撤={item.get('max_drawdown_pct')}%"
        )
    if corr["matrix"]:
        lines.append("相关系数矩阵（按最短序列对齐）:")
        for name, row in zip(corr["names"], corr["matrix"], strict=False):
            lines.append(f"{name}: " + " / ".join("—" if value is None else f"{value:+.3f}" for value in row))
    image = None
    artifacts: list[Any] = []
    if include_chart and normalized:
        count = max((len(item["values"]) for item in normalized), default=0)
        html = apply_theme(
            line_chart_html(
                title=title,
                labels=[str(index + 1) for index in range(count)],
                series=normalized,
            ),
            recolor=True,
        )
        image, artifacts = await _chart_artifacts(title=title, html=html, width=1100, ctx=_tool_context)
    return ToolOutcome(
        text="\n".join(lines) + _artifact_note(_tool_context, artifacts),
        structured={"summary": summary, "correlation": corr},
        images=[image] if image else [],
        artifacts=artifacts,
    )


def _empty_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


TOOLS: dict[str, ToolDefinition] = {}


def register(definition: ToolDefinition) -> None:
    if definition.name in TOOLS:
        raise ValueError(f"duplicate tool {definition.name}")
    TOOLS[definition.name] = definition


register(
    ToolDefinition(
        name="sayustock_market_overview",
        description=(
            "获取 A 股大盘概览：核心指数实时点位/涨跌幅、涨跌家数、两市成交额、北向资金、"
            "行业与概念领涨领跌。用户问“今天大盘怎么样/市场情绪/涨跌分布”时先调用。"
            "include_chart=true 会同时生成大盘概览图表。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "include_chart": {
                    "type": "boolean",
                    "description": "是否生成图表（默认 false，仅数据）",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=market_overview,
        timeout_seconds=45,
    )
)
register(
    ToolDefinition(
        name="sayustock_quote",
        description=(
            "查询单只股票/ETF/指数/加密货币的实时行情：最新价、涨跌幅、开高低、成交额、"
            "换手率、PE/PB、市值。用户给出具体标的时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 80}},
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=quote_tool,
        timeout_seconds=30,
    )
)
register(
    ToolDefinition(
        name="sayustock_kline",
        description=(
            "获取股票/ETF/指数的 K 线数据与技术指标（MA/MACD/RSI/KDJ/BOLL/ATR/CMF），"
            "并可按需生成 K 线图。period 取 5k/15k/30k/60k/日k/周k/月k/季k/年k。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 80},
                "period": {"type": "string", "enum": MARKET_PERIODS, "default": "日k"},
                "include_chart": {"type": "boolean", "default": True},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=kline_tool,
        timeout_seconds=60,
    )
)
register(
    ToolDefinition(
        name="sayustock_technical_analysis",
        description=(
            "对股票/ETF 做多周期技术面评分：趋势、动量、量能、位置、支撑压力与风险信号，"
            "返回 -1~+1 评分及多空信号。用户问“技术面/能不能买/走势如何”时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 80},
                "period": {"type": "string", "enum": MARKET_PERIODS, "default": "日k"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=technical_tool,
        timeout_seconds=45,
    )
)
register(
    ToolDefinition(
        name="sayustock_sector_ranking",
        description=(
            "查询行业板块或概念板块涨幅榜/跌幅榜，含板块涨跌幅、成交额、领涨股。kind=industry/concept，limit 1~50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["industry", "concept"], "default": "industry"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 15},
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=sector_tool,
        timeout_seconds=40,
    )
)
register(
    ToolDefinition(
        name="sayustock_compare",
        description=("对比 2~6 只股票/ETF/指数的区间涨跌幅，并可按需生成对比图。queries 是标的列表，period 默认日k。"),
        parameters={
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 80},
                    "minItems": 2,
                    "maxItems": 6,
                },
                "period": {"type": "string", "enum": MARKET_PERIODS, "default": "日k"},
                "include_chart": {"type": "boolean", "default": True},
            },
            "required": ["queries"],
            "additionalProperties": False,
        },
        handler=compare_tool,
        timeout_seconds=60,
    )
)
register(
    ToolDefinition(
        name="sayustock_screen",
        description=(
            "按条件筛选沪深 A 股：涨跌幅区间、PE 上限、市值区间、行业名称。"
            "返回命中股票列表；股票池为按市值排序的前 3000 只，不构成投资建议。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "min_change": {"type": "number", "minimum": -30, "maximum": 30},
                "max_change": {"type": "number", "minimum": -30, "maximum": 30},
                "max_pe": {"type": "number", "minimum": 0, "maximum": 500},
                "min_market_cap_yi": {"type": "number", "minimum": 0},
                "max_market_cap_yi": {"type": "number", "minimum": 0},
                "industry": {"type": "string", "maxLength": 40},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=screen_tool,
        timeout_seconds=60,
    )
)
register(
    ToolDefinition(
        name="sayustock_watchlist_list",
        description="查看当前用户的自选股列表（按平台与用户隔离）。",
        parameters=_empty_schema(),
        handler=watchlist_list_tool,
        timeout_seconds=10,
    )
)
register(
    ToolDefinition(
        name="sayustock_watchlist_add",
        description="把一只股票/ETF 加入当前用户自选。query 可为代码或名称。",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 80}},
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=watchlist_add_tool,
        effect="mutating",
        timeout_seconds=30,
    )
)
register(
    ToolDefinition(
        name="sayustock_watchlist_remove",
        description="从当前用户自选中移除一只股票/ETF。",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 80}},
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=watchlist_remove_tool,
        effect="mutating",
        timeout_seconds=30,
    )
)
register(
    ToolDefinition(
        name="sayustock_generate_chart",
        description=(
            "生成可直接查看的图表（K线/分时/对比/大盘概览）。返回 PNG（若渲染后端可用）"
            "与 HTML 制品链接。用户要求“画图/出图/看图表”时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": ["kline", "intraday", "compare", "overview"],
                    "default": "kline",
                },
                "query": {"type": "string", "maxLength": 80, "default": "上证指数"},
                "period": {"type": "string", "enum": MARKET_PERIODS, "default": "日k"},
                "ndays": {"type": "integer", "minimum": 1, "maximum": 5, "default": 1},
                "queries": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 80},
                    "maxItems": 6,
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=generate_chart_tool,
        timeout_seconds=90,
    )
)
register(
    ToolDefinition(
        name="sayustock_generate_report",
        description=(
            "把一段 Markdown 研究内容渲染为可独立查看的 HTML 报告，返回制品链接。"
            "适合输出长篇研报、复盘、持仓分析、数据表格。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 120},
                "markdown": {"type": "string", "minLength": 1, "maxLength": 60000},
                "meta": {"type": "object", "properties": {}, "additionalProperties": {"type": "string"}},
            },
            "required": ["title", "markdown"],
            "additionalProperties": False,
        },
        handler=generate_report_tool,
        timeout_seconds=20,
    )
)
register(
    ToolDefinition(
        name="sayustock_market_ranking",
        description=(
            "查询沪深 A 股资金流/换手/成交额/成交量/ROE/净利润增速榜单。"
            "rank_by 取 main_inflow/main_outflow/turnover/roe/amount/volume/profit_yoy。"
            "榜单只作扫描线索，不能单独作为买卖依据。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "rank_by": {
                    "type": "string",
                    "enum": [
                        "main_inflow",
                        "main_outflow",
                        "turnover",
                        "roe",
                        "amount",
                        "volume",
                        "profit_yoy",
                    ],
                    "default": "main_inflow",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=rank_tool,
        timeout_seconds=60,
    )
)
register(
    ToolDefinition(
        name="sayustock_financials",
        description=(
            "查询个股财务快照：ROE、营收/净利同比、毛利率、净利率、负债率、EPS/BPS，"
            "并附带当前 PE/PB。用户问“基本面/财报/估值”时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 80}},
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=financial_tool,
        timeout_seconds=45,
    )
)
register(
    ToolDefinition(
        name="sayustock_valuation",
        description=(
            "查询个股 PE/PB/股息率历史估值序列（kind=pe/pb/dy），返回最新值与描述统计。适合判断估值所处历史区间。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 80},
                "kind": {"type": "string", "enum": ["pe", "pb", "dy"], "default": "pe"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=valuation_tool,
        timeout_seconds=45,
    )
)
register(
    ToolDefinition(
        name="sayustock_ipo_calendar",
        description="查询 A股/港股/美股 IPO 日历（申购日、上市日、发行价等）。market=cn/hk/us。",
        parameters={
            "type": "object",
            "properties": {"market": {"type": "string", "enum": ["cn", "hk", "us"], "default": "cn"}},
            "required": [],
            "additionalProperties": False,
        },
        handler=ipo_tool,
        timeout_seconds=45,
    )
)
register(
    ToolDefinition(
        name="sayustock_news",
        description=(
            "获取最新财经快讯（东财/华尔街见闻/新浪/金十自动换源），含重要标记与来源。"
            "用于解释盘中异动、宏观事件或个股催化。"
        ),
        parameters={
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 12}},
            "required": [],
            "additionalProperties": False,
        },
        handler=news_tool,
        timeout_seconds=40,
    )
)
register(
    ToolDefinition(
        name="sayustock_plot_series",
        description=(
            "把任意分析序列画成折线图或柱状图（不限于行情）：传入 labels 与 series，"
            "自动计算均值/区间/变化率并返回 HTML/PNG。适合 AI 输出自定义图表。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 120},
                "chart_type": {"type": "string", "enum": ["line", "bar"], "default": "line"},
                "labels": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 40},
                    "maxItems": 240,
                },
                "series": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "maxLength": 60},
                            "values": {
                                "type": "array",
                                "items": {"type": "number"},
                                "maxItems": 240,
                            },
                        },
                        "required": ["name", "values"],
                        "additionalProperties": False,
                    },
                },
                "y_label": {"type": "string", "maxLength": 40, "default": ""},
                "note": {"type": "string", "maxLength": 500, "default": ""},
            },
            "required": ["title", "series"],
            "additionalProperties": False,
        },
        handler=plot_series_tool,
        timeout_seconds=90,
    )
)
register(
    ToolDefinition(
        name="sayustock_pie_chart",
        description="把一组占比数据画成饼图并返回 HTML/PNG；适合行业构成、仓位分布等。",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 120},
                "labels": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 40},
                    "minItems": 1,
                    "maxItems": 30,
                },
                "values": {
                    "type": "array",
                    "items": {"type": "number", "minimum": 0},
                    "minItems": 1,
                    "maxItems": 30,
                },
                "note": {"type": "string", "maxLength": 500, "default": ""},
            },
            "required": ["title", "labels", "values"],
            "additionalProperties": False,
            "description": "labels 与 values 按下标一一对应",
        },
        handler=pie_chart_tool,
        timeout_seconds=90,
    )
)
register(
    ToolDefinition(
        name="sayustock_data_table",
        description=("把结构化数据渲染为可独立查看的 HTML 表格制品，适合选股结果、财务对比、持仓明细等 AI 分析输出。"),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 120},
                "columns": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 40},
                    "minItems": 1,
                    "maxItems": 20,
                },
                "rows": {
                    "type": "array",
                    "maxItems": 300,
                    "items": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {"type": ["string", "number", "boolean", "null"]},
                    },
                },
                "note": {"type": "string", "maxLength": 500, "default": ""},
            },
            "required": ["title", "columns", "rows"],
            "additionalProperties": False,
        },
        handler=data_table_tool,
        timeout_seconds=30,
    )
)
register(
    ToolDefinition(
        name="sayustock_dashboard",
        description=(
            "把多组指标卡和数据表组合成一张股票看板 HTML，网页可打开同一制品，群聊可发 PNG。"
            "适合每日复盘、持仓概览、选股结果与财务对比；不执行交易。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 120},
                "metrics": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "maxLength": 40},
                            "value": {"type": ["string", "number"]},
                            "change_pct": {"type": "number"},
                            "note": {"type": "string", "maxLength": 120},
                        },
                        "required": ["label", "value"],
                        "additionalProperties": False,
                    },
                },
                "tables": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "maxLength": 60},
                            "columns": {
                                "type": "array",
                                "items": {"type": "string", "maxLength": 40},
                                "minItems": 1,
                                "maxItems": 20,
                            },
                            "rows": {
                                "type": "array",
                                "maxItems": 300,
                                "items": {
                                    "type": "array",
                                    "maxItems": 20,
                                    "items": {"type": ["string", "number", "boolean", "null"]},
                                },
                            },
                            "note": {"type": "string", "maxLength": 500},
                        },
                        "required": ["columns", "rows"],
                        "additionalProperties": False,
                    },
                },
                "note": {"type": "string", "maxLength": 500, "default": ""},
            },
            "required": ["title", "metrics", "tables"],
            "additionalProperties": False,
        },
        handler=dashboard_tool,
        timeout_seconds=45,
    )
)
register(
    ToolDefinition(
        name="sayustock_analyze_series",
        description=(
            "对一组或多组数值序列做描述统计与 Pearson 相关分析（均值/中位数/波动/"
            "最大回撤/相关系数矩阵），可选同时生成折线图。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 120, "default": "数据分析"},
                "series": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "maxLength": 60},
                            "values": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 1,
                                "maxItems": 500,
                            },
                        },
                        "required": ["name", "values"],
                        "additionalProperties": False,
                    },
                },
                "include_chart": {"type": "boolean", "default": False},
            },
            "required": ["series"],
            "additionalProperties": False,
        },
        handler=analyze_series_tool,
        timeout_seconds=60,
    )
)
register(
    ToolDefinition(
        name="sayustock_artifact_list",
        description="列出最近生成的图表/报告制品，便于在网页中重新打开。",
        parameters=_empty_schema(),
        handler=list_artifacts_tool,
        timeout_seconds=10,
    )
)


def tool_schemas() -> list[dict[str, Any]]:
    return [definition.schema() for definition in TOOLS.values()]


async def run_tool(name: str, arguments: dict[str, Any] | None, context: ToolContext) -> ToolOutcome:
    definition = TOOLS.get(name)
    if definition is None:
        raise KeyError(f"unknown tool {name}")
    args = dict(arguments or {})
    args["_tool_context"] = context
    return await definition.handler(**args)


__all__ = ["TOOLS", "ToolDefinition", "run_tool", "tool_schemas"]
