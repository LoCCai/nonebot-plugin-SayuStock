"""Fund, ETF-basket, all-weather, cloud-map and valuation workflows.

Every feature here is read-only.  Data comes from the original SayuStock
services; this module only orchestrates them, applies the Qiqi theme and
returns the same :class:`CommandResult` for group commands, QWeb and AI tools.
"""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING, Any, Callable, Awaitable
from datetime import datetime, timedelta
from itertools import count
from collections import OrderedDict
from dataclasses import dataclass

from nonebot.log import logger

from ..compat import bootstrap
from ..feature_data import FUNDWORK as FEATURES

bootstrap.install()

from SayuStock.utils import render_text  # noqa: E402
from SayuStock.render.treemap import treemap_html, prepare_treemap  # noqa: E402
from SayuStock.utils.warn_box import failure_lines  # noqa: E402
from SayuStock.utils.time_range import parse_time_range  # noqa: E402
from SayuStock.render.chart_page import WIDTH as CHART_WIDTH  # noqa: E402
from SayuStock.stock_cloudmap.data import CLOUDMAP_DATA_SERVICE  # noqa: E402
from SayuStock.utils.market.models import BoardSnapshot  # noqa: E402
from SayuStock.stock_info.draw_future import (  # noqa: E402
    CSS_WIDTH as WEATHER_WIDTH,
    AllWeatherView,
    all_weather_html,
    all_weather_text,
    load_all_weather,
)
from SayuStock.stock_info.etf_compare import (  # noqa: E402
    MAX_COMPARE,
    FundValue,
    TargetStock,
    BasketResult,
    SearchResult,
    fund_theme,
    load_baskets,
    resolve_target,
    resolve_etf_code,
    expand_fund_theme,
    search_listed_funds,
    latest_etf_trading_day,
)
from SayuStock.stock_config.stock_config import STOCK_CONFIG  # noqa: E402
from SayuStock.stock_info.draw_fund_info import (  # noqa: E402
    PAGE_W,
    FundInfoView,
    fund_info_html,
    fund_info_text,
    load_fund_info,
)
from SayuStock.stock_sina.eastmoney_value import (  # noqa: E402
    VALUE_NAME_MAP,
    ValueType,
    ValueComparison,
    load_value_comparison,
    value_comparison_html,
    value_comparison_text,
)
from SayuStock.stock_info.draw_etf_compare import (  # noqa: E402
    WIDTH as ETF_WIDTH,
    build_search_html,
    build_comparison_html,
)

from ..runtime import get_runtime  # noqa: E402
from ..artifacts import Artifact, create_html  # noqa: E402
from ..services.theme import (  # noqa: E402
    ThemePalette,
    resolve_theme,
    theme_palette,
    themed_upstream_page,
)
from ..services.charts import ChartRenderError, rasterize  # noqa: E402

if TYPE_CHECKING:
    from ..commands import CommandResult
    from ..platform import UnifiedEvent


_CLOUD_MARKETS: dict[str, str] = {
    "大盘云图": "大盘云图",
    "板块云图": "行业云图",
    "概念云图": "概念云图",
}

_VALUE_KINDS: dict[str, ValueType] = {
    "市盈率对比": "pe",
    "市净率对比": "pb",
    "股息率对比": "dy",
}

_SEARCH_TTL = 600.0
_SEARCH_LIMIT = 128
_SEARCH_ORDER = count()


@dataclass(frozen=True, slots=True)
class _SearchContext:
    sequence: int
    expires_at: float
    result: SearchResult


_SEARCHES: OrderedDict[tuple[str, str], _SearchContext] = OrderedDict()


def _fund_search_key(event: "UnifiedEvent") -> tuple[str, str]:
    return str(event._extra.get("host_thread") or event.session_id), event.user_id


def _recent_fund_search(event: "UnifiedEvent") -> SearchResult | None:
    key = _fund_search_key(event)
    recent = _SEARCHES[key] if key in _SEARCHES else None
    if recent is not None and monotonic() >= recent.expires_at:
        _SEARCHES.pop(key, None)
        return None
    return recent.result if recent is not None else None


def _remember_search(event: "UnifiedEvent", result: SearchResult) -> None:
    key = _fund_search_key(event)
    sequence = next(_SEARCH_ORDER)
    if key in _SEARCHES and sequence < _SEARCHES[key].sequence:
        return
    _SEARCHES[key] = _SearchContext(sequence, monotonic() + _SEARCH_TTL, result)
    _SEARCHES.move_to_end(key)
    if len(_SEARCHES) > _SEARCH_LIMIT:
        _SEARCHES.popitem(last=False)


# ---------------------------------------------------------------------------
# Result construction (theme, artifact, image, text)
# ---------------------------------------------------------------------------
def _themed(document: str, title: str) -> str:
    """Attach the Qiqi theme, banner and footer to an upstream page."""
    return themed_upstream_page(document, title)


async def _render_image(html: str, width: int) -> bytes | None:
    runtime = get_runtime()
    if not runtime.render_enabled:
        return None
    try:
        async with runtime.render_lock:
            return await rasterize(html, width=width, scale=runtime.render_scale())
    except ChartRenderError as error:
        logger.info("[SayuStock] 图片渲染不可用: {}", error)
        return None
    except Exception as error:  # pragma: no cover - optional backend
        logger.warning("[SayuStock] 图片渲染异常: {}", error)
        return None


async def _finish(
    *,
    command: str,
    title: str,
    html: str,
    text: str,
    structured: dict[str, Any],
    event: "UnifiedEvent",
    width: int,
) -> "CommandResult":
    from ..commands import CommandResult

    themed = _themed(html, title)
    artifact: Artifact = create_html(
        title,
        themed,
        kind="chart",
        owner=event.user_id,
        meta={"command": command, "session_id": event.session_id},
    )
    image = None
    if event.platform != "qweb" and not event._extra.get("qweb"):
        image = await _render_image(themed, width)
    return CommandResult(text, image, [artifact], image_only=image is not None, structured=structured)


# ---------------------------------------------------------------------------
# ETF baskets
# ---------------------------------------------------------------------------
def _fund_json(fund: FundValue, *, stock_limit: int = 40) -> dict[str, Any]:
    ranked = sorted(fund.stocks.items(), key=lambda item: -item[1][1])
    visible = ranked[:stock_limit]
    return {
        "code": fund.code,
        "name": fund.name,
        "index": fund.index_name,
        "stock_count": len(fund.stocks),
        "stocks": [{"code": code, "name": name, "weight": round(weight, 4)} for code, (name, weight) in visible],
        "industries": [
            {"name": name, "weight": round(weight, 4)}
            for name, weight in sorted(fund.industries.items(), key=lambda item: -item[1])
        ],
        "stale_stocks": list(fund.stale_stocks),
        "providers": list(fund.providers),
    }


def _basket_json(result: BasketResult, target: TargetStock | None = None) -> dict[str, Any]:
    return {
        "target": {"code": target.code, "name": target.name} if target is not None else None,
        "funds": [_fund_json(fund) for fund in result.funds],
        "failures": [{"label": item.label, "reason": item.reason} for item in result.failures],
        "membership_only": [{"code": item.code, "stock_count": item.stock_count} for item in result.membership_only],
    }


def _search_json(result: SearchResult) -> dict[str, Any]:
    return {
        "stock": {"code": result.stock.code, "name": result.stock.name},
        "period": result.period.isoformat(),
        "page": result.page,
        "pages": result.pages,
        "planned_pages": result.planned_pages,
        "candidates": [
            {"code": fund.code, "name": fund.name, "theme": fund_theme(fund.name)} for fund in result.candidates
        ],
        "preview": _basket_json(result.preview, result.stock),
    }


async def _etf_search(argument: str, event: "UnifiedEvent") -> "CommandResult":
    from ..commands import CommandResult

    query = argument.strip()
    if not query:
        return CommandResult("用法：ETF找股 工业富联", structured={"error": "missing_query"})
    stock = await resolve_target(query)
    if stock is None:
        return CommandResult("未识别到沪深 A 股，请输入股票名称或六位代码。", structured={"error": "not_found"})
    result = await search_listed_funds(stock, latest_etf_trading_day())
    if isinstance(result, str):
        return CommandResult(result, structured={"error": result})
    html, text = build_search_html(result)
    _remember_search(event, result)
    return await _finish(
        command="ETF找股",
        title=f"{result.stock.name} · ETF 找股",
        html=html,
        text=text,
        structured=_search_json(result),
        event=event,
        width=ETF_WIDTH,
    )


async def _etf_expand(argument: str, event: "UnifiedEvent") -> "CommandResult":
    from ..commands import CommandResult

    theme = "".join(argument.split())
    if not theme:
        return CommandResult("用法：ETF展开 食品饮料", structured={"error": "missing_theme"})
    search = _recent_fund_search(event)
    if search is None:
        return CommandResult(
            "请先发送「ETF找股 股票名称」，再发送「ETF展开 主题名」。",
            structured={"error": "no_recent_search"},
        )
    result = await expand_fund_theme(search, theme, latest_etf_trading_day())
    if isinstance(result, str):
        return CommandResult(result, structured={"error": result, "theme": theme})
    title = f"{theme} ETF 申赎篮子对比"
    html, text = build_comparison_html(result, title, search.stock)
    return await _finish(
        command="ETF展开",
        title=title,
        html=html,
        text=text,
        structured={
            "theme": theme,
            "search_stock": {"code": search.stock.code, "name": search.stock.name},
            **_basket_json(result, search.stock),
        },
        event=event,
        width=ETF_WIDTH,
    )


async def _etf_compare(argument: str, event: "UnifiedEvent") -> "CommandResult":
    from ..commands import CommandResult

    raw_codes, _, query = argument.partition("关注")
    codes: list[str] = []
    for token in raw_codes.replace(",", " ").replace("，", " ").split():
        code = await resolve_etf_code(token)
        if code is None:
            return CommandResult(
                f"「{token}」不是有效的沪深 ETF 代码或名称，请检查后重试。",
                structured={"error": "invalid_code", "token": token},
            )
        if code not in codes:
            codes.append(code)
    if not 2 <= len(codes) <= MAX_COMPARE:
        return CommandResult(
            "用法：ETF对比 515170 516900 159843 159862，可追加「关注 贵州茅台」。",
            structured={"error": "bad_code_count"},
        )
    stock = await resolve_target(query) if query.strip() else None
    if query.strip() and stock is None:
        return CommandResult("未识别到关注的沪深 A 股，请检查名称或代码。", structured={"error": "bad_target"})
    result = await load_baskets(codes, day=latest_etf_trading_day())
    if len(result.funds) < 2:
        lines = failure_lines(result.failures)
        message = "\n".join(["当日可核验的 ETF 不足两只，暂无法对比。", *lines])
        return CommandResult(message, structured=_basket_json(result, stock))
    title = "ETF 申赎篮子对比"
    html, text = build_comparison_html(result, title, stock)
    return await _finish(
        command="ETF对比",
        title=title,
        html=html,
        text=text,
        structured={"codes": codes, **_basket_json(result, stock)},
        event=event,
        width=ETF_WIDTH,
    )


# ---------------------------------------------------------------------------
# Fund holdings
# ---------------------------------------------------------------------------
def _fund_structured(view: FundInfoView) -> dict[str, Any]:
    return {
        "fund": {"code": view.fund_code, "name": view.fund_name},
        "report_period": view.report_period,
        "average_change_pct": view.average,
        "meta": view.meta,
        "rows": [
            {
                "code": row.code,
                "name": row.name,
                "weight": row.weight,
                "price": row.price,
                "change_pct": row.change_pct,
                "turnover_rate": row.turnover_rate,
                "amount": row.amount,
                "provider": row.provider,
                "failed": row.failed,
                "reason": row.reason,
            }
            for row in view.rows
        ],
        "failures": [{"label": item.label, "reason": item.reason} for item in view.failures],
        "sources": [source for source in view.sources if source],
    }


async def _fund_info(argument: str, event: "UnifiedEvent") -> "CommandResult":
    from ..commands import CommandResult

    query = argument.strip()
    if not query:
        return CommandResult("用法：基金持仓 000001", structured={"error": "missing_query"})
    view = await load_fund_info(query)
    if isinstance(view, str):
        return CommandResult(view, structured={"error": view})
    html = await fund_info_html(view)
    return await _finish(
        command="基金持仓",
        title=f"{view.fund_name} · 基金持仓",
        html=html,
        text=fund_info_text(view),
        structured=_fund_structured(view),
        event=event,
        width=PAGE_W,
    )


# ---------------------------------------------------------------------------
# All weather
# ---------------------------------------------------------------------------
def _all_weather_structured(view: AllWeatherView) -> dict[str, Any]:
    return {
        "sections": [
            {
                "title": title,
                "items": [
                    {
                        "code": item.code,
                        "name": item.name,
                        "price": item.price,
                        "change_pct": item.change_pct,
                        "amount": item.amount,
                        "provider": item.provider,
                    }
                    for item in items
                ],
            }
            for title, items in view.sections
        ],
        "warnings": list(view.warnings),
    }


async def _all_weather(argument: str, event: "UnifiedEvent") -> "CommandResult":
    from ..commands import CommandResult

    _ = argument
    view = await load_all_weather()
    if isinstance(view, str):
        return CommandResult(view, structured={"error": view})
    return await _finish(
        command="全天候",
        title="全天候板块",
        html=all_weather_html(view),
        text=all_weather_text(view),
        structured=_all_weather_structured(view),
        event=event,
        width=WEATHER_WIDTH,
    )


# ---------------------------------------------------------------------------
# Cloud maps
# ---------------------------------------------------------------------------
def _cloud_structured(snap: BoardSnapshot, market: str, sector: str | None) -> dict[str, Any]:
    rows = [
        {
            "code": row.code,
            "name": row.name,
            "price": row.price,
            "change_pct": row.change_pct,
            "amount": row.amount,
            "market_cap": row.market_cap,
            "industry": row.industry,
        }
        for row in snap.rows
        if row.name
    ]
    changes = [row["change_pct"] for row in rows if isinstance(row["change_pct"], (int, float))]
    return {
        "market": market,
        "sector": sector,
        "provider": snap.provider,
        "total": len(rows),
        "up": sum(1 for value in changes if value > 0),
        "down": sum(1 for value in changes if value < 0),
        "flat": sum(1 for value in changes if value == 0),
        "average_change_pct": round(sum(changes) / len(changes), 4) if changes else None,
        "rows": rows[:300],
        "rows_truncated": len(rows) > 300,
    }


def _treemap_document(snap: BoardSnapshot, market: str, sector: str | None, palette: ThemePalette) -> tuple[str, int]:
    """Build the treemap in the hosted palette (no post-hoc colour swapping)."""
    width = max(600, int(STOCK_CONFIG.get_config("mapcloud_viewport").data or 1600))
    page = prepare_treemap(
        snap,
        market,
        sector,
        2 if market == "大盘云图" else 1,
        width=width,
        up=palette.up,
        down=palette.down,
        neutral=palette.flat,
    )
    if isinstance(page, str):
        return page, width
    document = treemap_html(page)
    document = (
        document.replace("#000000", palette.background)
        .replace("#111315", palette.surface)
        .replace("#9ca3af", palette.muted)
    )
    return document, page.width


async def _cloudmap(command: str, argument: str, event: Any) -> Any:
    from ..commands import CommandResult

    market = _CLOUD_MARKETS[command]
    sector = argument.strip() or None
    result = await CLOUDMAP_DATA_SERVICE.fetch(market, sector, None, None)
    raw = result.raw_data
    if isinstance(raw, str):
        return CommandResult(raw, structured={"error": raw, "market": market, "sector": result.sector})
    if not isinstance(raw, BoardSnapshot):
        return CommandResult(
            "云图数据不可用，请稍后再试。",
            structured={"error": "empty", "market": market, "sector": result.sector},
        )
    resolved = result.sector or sector
    palette = theme_palette(resolve_theme())
    document, width = _treemap_document(raw, market, resolved, palette)
    if not document.strip().startswith("<"):
        return CommandResult(document, structured={"error": document, "market": market, "sector": resolved})
    text = render_text.cloudmap_text(raw, market, resolved)
    if not text:
        text = f"【{market}{' - ' + resolved if resolved else ''}】暂无可展示的行情。"
    title = f"{market}{' · ' + resolved if resolved and market != '大盘云图' else ''}"
    return await _finish(
        command=command,
        title=title,
        html=document,
        text=text,
        structured=_cloud_structured(raw, market, resolved),
        event=event,
        width=width,
    )


# ---------------------------------------------------------------------------
# PE / PB / DY history comparison
# ---------------------------------------------------------------------------
def _apply_default_range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    current = datetime.now()
    return (start or current - timedelta(days=365 * 3), end or current)


async def _value_compare(command: str, argument: str, event: Any) -> Any:
    from ..commands import CommandResult

    kind = _VALUE_KINDS[command]
    raw = argument.strip()
    if not raw:
        return CommandResult(
            f"用法：{command} 贵州茅台 五粮液，可加时间范围（如 最近一年 / 2024.01.01~2024.12.31）",
            structured={"error": "missing_query"},
        )
    try:
        start, end, cleaned = parse_time_range(raw)
    except ValueError as error:
        return CommandResult(str(error), structured={"error": str(error)})
    start, end = _apply_default_range(start, end)
    view = await load_value_comparison(cleaned, kind, start, end)
    if isinstance(view, str):
        return CommandResult(view, structured={"error": view})
    title = f"{VALUE_NAME_MAP[kind]}历史走势对比"
    document = value_comparison_html(view)
    if not document.lstrip().startswith("<"):
        return CommandResult(document, structured={"error": document})
    return await _finish(
        command=command,
        title=title,
        html=document,
        text=value_comparison_text(view),
        structured=_value_json(view),
        event=event,
        width=CHART_WIDTH,
    )


def _value_json(view: ValueComparison) -> dict[str, Any]:
    series = []
    for item in view.series:
        values = [point.value for point in item.points]
        series.append(
            {
                "code": item.symbol.code,
                "name": item.symbol.display_name or item.symbol.name,
                "provider": item.provider,
                "points": len(values),
                "first": {"day": item.points[0].day.isoformat(), "value": item.points[0].value},
                "latest": {"day": item.points[-1].day.isoformat(), "value": item.points[-1].value},
                "min": min(values),
                "max": max(values),
                "mean": round(sum(values) / len(values), 4),
            }
        )
    return {
        "kind": view.kind,
        "label": view.label,
        "series": series,
        "failures": list(view.failures),
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
_HANDLERS: dict[str, Callable[[str, "UnifiedEvent"], Awaitable["CommandResult"]]] = {
    "ETF找股": _etf_search,
    "ETF展开": _etf_expand,
    "ETF对比": _etf_compare,
    "基金持仓": _fund_info,
    "全天候": _all_weather,
    "大盘云图": lambda argument, event: _cloudmap("大盘云图", argument, event),
    "板块云图": lambda argument, event: _cloudmap("板块云图", argument, event),
    "概念云图": lambda argument, event: _cloudmap("概念云图", argument, event),
    "市盈率对比": lambda argument, event: _value_compare("市盈率对比", argument, event),
    "市净率对比": lambda argument, event: _value_compare("市净率对比", argument, event),
    "股息率对比": lambda argument, event: _value_compare("股息率对比", argument, event),
}

_ALIASES: dict[str, str] = {
    alias.casefold(): name for name, (aliases, _usage, _writes) in FEATURES.items() for alias in (name, *aliases)
}


def resolve_feature(command: str) -> str | None:
    return _ALIASES.get((command or "").strip().casefold())


async def dispatch(command: str, argument: str, event: "UnifiedEvent") -> "CommandResult":
    """Execute one read-only stock feature for the trusted event's identity."""
    from ..commands import CommandResult

    name = resolve_feature(command)
    if name is None:
        return CommandResult(f"未知股票功能：{command}", structured={"error": "unknown_command"})
    if not event.user_id:
        return CommandResult("未取得可信身份，无法查询。", structured={"error": "unauthenticated"})
    text = argument.strip()
    if len(text) > 2000:
        return CommandResult("参数过长，请缩小查询范围。", structured={"error": "argument_too_long"})
    try:
        return await _HANDLERS[name](text, event)
    except Exception as error:
        logger.exception("[SayuStock] {} 执行失败: {}", name, error)
        reason = str(error).strip() or error.__class__.__name__
        return CommandResult(f"{name}查询失败：{reason}", structured={"error": reason})


__all__ = ["FEATURES", "dispatch", "resolve_feature"]
