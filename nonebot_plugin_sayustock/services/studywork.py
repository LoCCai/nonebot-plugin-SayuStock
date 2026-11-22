"""Qiqi/NoneBot2 stock research domain: analysis, cards, screening and Kronos.

The upstream plugin exposes these workflows through GsCore ``SV`` triggers and
``@ai_tools``.  This branch keeps the original algorithms as the single source
of truth and only rebuilds the transport:

* the original ``stock_analysis`` technical/card/screener/portfolio builders
  produce the HTML, so the scoring rules do not fork;
* the original ``stock_stockinfo`` data service keeps sector expansion for
  ``个股`` / ``对比个股``;
* watchlist charts reuse the original ``my_stock_html`` layout on top of the
  shared market port instead of the GsCore ``SsBind`` repository;
* Kronos predictions import the vendored ``stock_ai.draw_ai_map`` algorithm and
  render its ``ChartPage`` through the Qiqi theme bridge.  The Kronos weights
  stay independent from the host LLM; the LLM is only used, when the host
  provides it, to narrate the deterministic holdings fact pack.

Every write-like action is refused without a trusted ``UnifiedEvent`` identity;
the host keeps the two-phase confirmation for data-changing operations.
"""

from __future__ import annotations

import re
import sys
import math
import time
import types
import asyncio
from typing import TYPE_CHECKING, Any
from pathlib import Path
from datetime import date, datetime

from nonebot.log import logger

from .theme import themed_upstream_page
from ..compat import bootstrap
from ..platform import UnifiedEvent
from ..artifacts import create_html

if TYPE_CHECKING:
    from SayuStock.utils.market import KlineSeries
    from SayuStock.stock_analysis.technical import TechnicalReport

bootstrap.install()

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_SAYU_ROOT = _PLUGIN_ROOT / "SayuStock"
_SHELLS: tuple[tuple[str, str], ...] = (
    ("SayuStock.stock_analysis", "stock_analysis"),
    ("SayuStock.stock_holdings_analysis", "stock_holdings_analysis"),
    ("SayuStock.stock_stockinfo", "stock_stockinfo"),
    ("SayuStock.stock_ai", "stock_ai"),
)

_HOST_AI_NOTE = "未接宿主模型，以下为规则化数据摘要（不含模型生成的判断）。"


def _core_available() -> bool:
    """A real GsCore provides ``gsuid_core.sv``; then keep its own registration."""
    try:
        import gsuid_core.sv  # noqa: F401
    except ImportError:
        return False
    return True


def _install_shells() -> None:
    """Import the reused domain packages without their GsCore registration.

    Their ``__init__.py`` calls ``SV(...)``; the NoneBot2 branch only needs the
    submodules.  The path-only shell is the same mechanism ``compat.legacy``
    uses for papertrade/macro.
    """
    if _core_available():
        return
    import SayuStock  # noqa: F401  # real package; no registration without Core

    for name, directory in _SHELLS:
        existing = sys.modules.get(name)
        if existing is not None and getattr(existing, "__path__", None) is not None:
            continue
        path = _SAYU_ROOT / directory
        module = types.ModuleType(name)
        module.__path__ = [str(path)]  # type: ignore[attr-defined]
        module.__package__ = name
        module.__file__ = str(path / "__init__.py")
        sys.modules[name] = module


_install_shells()


from ..feature_data import STUDYWORK as FEATURES  # noqa: E402

_ALIASES: dict[str, str] = {
    alias.casefold(): name for name, (aliases, _usage, _writes) in FEATURES.items() for alias in (name, *aliases)
}

_KRONOS_BUSY: set[str] = set()
_KRONOS_LOCK = asyncio.Lock()
# Same spirit as the upstream PNG cache: one prediction per 150 minutes per
# symbol/model/device, so repeated requests do not re-run the 3-minute inference.
_KRONOS_TTL = 150 * 60
_KRONOS_CACHE: dict[str, tuple[float, str, str, str, dict[str, Any], int]] = {}
_DATE_RE = re.compile(r"^(\d{4})([-./])(\d{1,2})\2(\d{1,2})$|^(\d{4})(\d{2})(\d{2})$")


def _result() -> Any:
    """Import ``CommandResult`` at call time so the catalogue stays cycle-free."""
    from ..commands import CommandResult

    return CommandResult


def _watch_key(event: UnifiedEvent) -> str:
    from ..watchlist import owner_key

    return owner_key(platform=event.platform, bot_id=event.bot_id or event.platform, user_id=event.user_id)


async def _rasterize(html: str, width: int) -> bytes | None:
    from .charts import ChartRenderError, rasterize
    from ..runtime import get_runtime

    runtime = get_runtime()
    if not runtime.render_enabled:
        return None
    try:
        async with runtime.render_lock:
            return await rasterize(html, width=width, scale=runtime.render_scale())
    except ChartRenderError as error:
        logger.info("[SayuStock] 图片渲染不可用，保留 HTML 制品: {}", error)
    except Exception as error:  # pragma: no cover - optional browser backend
        logger.warning("[SayuStock] 图片渲染异常: {}", error)
    return None


async def _html_result(
    command: str,
    title: str,
    html: str,
    text: str,
    event: UnifiedEvent,
    structured: dict[str, Any],
    *,
    kind: str = "chart",
    width: int = 1100,
    themed: bool = True,
    with_image: bool = True,
) -> Any:
    if themed:
        html = themed_upstream_page(html, title)
    artifact = create_html(title, html, kind=kind, owner=event.user_id, meta={"command": command})
    hosted = event.platform == "qweb" or bool(event._extra.get("qweb"))
    image = await _rasterize(html, width) if with_image and not hosted else None
    return _result()(text, image, [artifact], structured=structured)


def _clean_json(value: Any) -> Any:
    """Replace NaN/Inf with ``None`` so the web JSON payload stays valid."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(item) for item in value]
    return value


def _resolve(command: str) -> str:
    return _ALIASES.get((command or "").strip().casefold(), command)


def _usage(name: str) -> str:
    return f"用法：{FEATURES[name][1]}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def technical_report(series: KlineSeries) -> TechnicalReport | str:
    from SayuStock.utils.market import kline_to_df
    from SayuStock.stock_analysis.technical import build_technical_report

    return build_technical_report(
        name=series.symbol.name,
        code=series.symbol.code,
        period_code=series.period.value,
        ohlcv_df=kline_to_df(series),
        source_ids=(series.provider,) if series.provider else (),
    )


async def _technical(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.utils.market import KlinePeriod, get_market, is_market_error
    from SayuStock.stock_analysis.render import build_technical_html
    from SayuStock.stock_analysis.technical import report_to_text, parse_period_and_query

    period, query = parse_period_and_query(argument)
    if not query:
        return _result()(_usage("技术分析"))
    series = await get_market().kline(query, KlinePeriod(period))
    if is_market_error(series):
        return _result()(series.message, structured={"ok": False})
    report = technical_report(series)
    if isinstance(report, str):
        return _result()(report, structured={"ok": False})
    text = report_to_text(report)
    structured = report.to_dict()
    structured["provider"] = series.provider
    structured["as_of"] = series.as_of
    title = f"技术分析 · {report.name}（{report.code}）· {report.period_label}"
    return await _html_result("技术分析", title, build_technical_html(report), text, event, structured)


async def _stock_card(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.stock_analysis.card import build_trade_card
    from SayuStock.stock_analysis.render import build_trade_card_html
    from SayuStock.stock_analysis.service import _card_summary

    query = argument.strip()
    if not query:
        return _result()(_usage("股票卡片"))
    card = await build_trade_card(query)
    if isinstance(card, str):
        return _result()(card, structured={"ok": False})
    text = _card_summary(card)
    structured = {
        "code": card.code,
        "name": card.name,
        "price": card.price,
        "change_pct": card.pct,
        "industry": card.industry,
        "pe": card.pe,
        "pb": card.pb,
        "market_cap": card.mv,
        "finance": card.finance,
        "source_ids": list(card.source_ids),
    }
    return await _html_result("股票卡片", f"{card.name} 股票卡片", build_trade_card_html(card), text, event, structured)


async def _screen(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.stock_analysis.render import build_screener_html
    from SayuStock.stock_analysis.service import _screener_summary
    from SayuStock.stock_analysis.screener import run_screener

    text = argument.strip()
    if not text:
        return _result()(_usage("自动选股"))
    result = await run_screener(text)
    if result.error:
        # Only a pool-source failure falls back to the port screen; a usage
        # error (行业+概念 / 无条件) must keep the original DSL message.
        if "股票池为空" not in result.error:
            return _result()(result.error, structured={"ok": False})
        from ..commands import parse_screen, screen_result

        if len(parse_screen(text)) > 1:
            fallback = await screen_result(text)
            fallback.structured = {"fallback": "market.screen", "query": text, "reason": result.error}
            return fallback
        return _result()(result.error, structured={"ok": False})
    summary = _screener_summary(result)
    structured = {
        "scope": result.scope,
        "total_pool": result.total_pool,
        "matched": result.matched,
        "shown": result.shown,
        "filters": result.filters_desc,
        "source_ids": list(result.source_ids),
        "rows": result.df.to_dict("records"),
    }
    return await _html_result(
        "自动选股",
        "自动选股",
        build_screener_html(result),
        summary,
        event,
        structured,
        width=990,
    )


async def _portfolio(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.stock_analysis.render import build_portfolio_html
    from SayuStock.stock_analysis.portfolio import analyze_portfolio

    codes = [token for token in argument.replace("，", " ").replace(",", " ").split() if token]
    if not codes:
        items = await _watchlist_items(event)
        codes = [str(item["code"]) for item in items]
    if not codes:
        return _result()("请先添加自选，或：组合体检 600519 000001", structured={"ok": False})
    requested = min(len(codes), 30)
    report = await analyze_portfolio(codes)
    if isinstance(report, str):
        return _result()(report, structured={"ok": False})
    messages = list(report.messages)
    if len(report.holdings) < requested:
        messages.insert(0, f"仅覆盖 {len(report.holdings)}/{requested} 只标的，按已获取行情的标的计算。")
    structured = report.to_dict()
    structured["requested_count"] = requested
    return await _html_result(
        "组合体检",
        "组合行业体检",
        build_portfolio_html(report, requested_count=requested),
        "\n".join(messages),
        event,
        structured,
        width=840,
    )


async def _watchlist_items(event: UnifiedEvent) -> list[dict[str, str]]:
    from ..runtime import get_runtime

    return await get_runtime().watchlist.list(_watch_key(event))


async def _holding_snapshot(symbol: str) -> dict[str, Any]:
    from SayuStock.utils.market import KlinePeriod, get_market, kline_to_df, is_market_error
    from SayuStock.stock_analysis.technical import build_technical_report

    market = get_market()
    quote = await market.quote(symbol)
    row: dict[str, Any] = {"symbol": symbol, "quote": None, "technical": None, "financial": None, "gap": ""}
    if is_market_error(quote):
        row["gap"] = quote.message
        return row
    row["quote"] = quote
    series = await market.kline(symbol, KlinePeriod.D1)
    if not is_market_error(series):
        report = build_technical_report(
            name=series.symbol.name,
            code=series.symbol.code,
            period_code="101",
            ohlcv_df=kline_to_df(series),
            source_ids=(series.provider,) if series.provider else (),
        )
        if not isinstance(report, str):
            row["technical"] = report
    code6 = quote.symbol.code.split(".")[-1]
    if code6.isdigit() and len(code6) <= 6:
        snapshot = await market.financial_snapshot(code6)
        if not is_market_error(snapshot):
            row["financial"] = snapshot
    return row


def _holding_markdown(rows: list[dict[str, Any]], portfolio: Any) -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 持仓分析数据包",
        "",
        f"生成时间：{today}　标的：{len(rows)} 只（自选/手填，非模拟盘仓位）",
        "",
        "| 代码 | 名称 | 现价 | 涨跌 | 技术分 | 趋势 | 信号/风险 | ROE | 营收同比 | 净利同比 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        quote = row["quote"]
        technical = row["technical"]
        finance = row["financial"]
        if quote is None:
            lines.append(f"| — | {row['symbol']} | — | — | — | — | {row['gap'] or '行情缺失'} | — | — | — |")
            continue
        flags = "、".join(
            [*(technical.signals[:2] if technical else []), *(technical.risk_flags[:2] if technical else [])]
        )
        lines.append(
            f"| {quote.symbol.code} | {quote.symbol.display_name or quote.symbol.name} | {_num(quote.price)} "
            f"| {_pct(quote.change_pct)} | {technical.score if technical else '—'} "
            f"| {technical.trend if technical else '—'} | {flags or '—'} "
            f"| {_num(finance.roe) if finance else '—'} "
            f"| {_num(finance.revenue_yoy) if finance else '—'} "
            f"| {_num(finance.profit_yoy) if finance else '—'} |"
        )
    lines.extend(["", "## 逐票要点"])
    for row in rows:
        quote = row["quote"]
        technical = row["technical"]
        finance = row["financial"]
        if quote is None:
            lines.append(f"- {row['symbol']}：数据缺口 {row['gap'] or '行情缺失'}")
            continue
        name = quote.symbol.display_name or quote.symbol.name
        lines.append(
            f"- **{name}({quote.symbol.code})**：现价 {_num(quote.price)}，涨跌 {_pct(quote.change_pct)}；"
            f"技术分 {technical.score if technical else '—'}，趋势 {technical.trend if technical else '—'}。"
        )
        if technical is not None:
            lines.append(
                f"  - 信号：{'、'.join(technical.signals) or '无'}；风险：{'、'.join(technical.risk_flags) or '无'}。"
            )
        if finance is not None:
            lines.append(
                f"  - 财务：ROE {_num(finance.roe)}%，营收同比 {_num(finance.revenue_yoy)}%，"
                f"净利同比 {_num(finance.profit_yoy)}%，负债率 {_num(finance.debt_ratio)}%。"
            )
    if portfolio is not None:
        lines.extend(["", "## 组合层面"])
        lines.extend(f"- {message}" for message in portfolio.messages)
    lines.extend(["", "> 数据来自行情/财务接口，仅供研究，不构成投资建议。"])
    return "\n".join(lines)


def host_ai_available() -> bool:
    """True when the Qiqi host model is reachable; never configure one here."""
    from .paperwork_lifecycle import host_ai_available as available

    return available()


async def host_ai(prompt: str, system_prompt: str) -> str:
    from .paperwork_lifecycle import host_ai as call

    return await call(prompt, system_prompt)


async def _holdings_with_host_ai(markdown: str) -> tuple[str, bool, str]:
    """Let the Qiqi host model narrate the fact pack; never fabricate on failure."""
    if not host_ai_available():
        return markdown, False, _HOST_AI_NOTE
    prompt = (
        "下面是行情/财务工具得到的持仓数据包。请只基于这些数据写一份中文持仓分析，"
        "覆盖宏观定调（若数据包没有宏观数据就明确写缺口）、逐票技术/财务要点、组合集中度与风险。"
        "禁止编造数据包中没有的数值，缺口必须写明。\n\n" + markdown
    )
    try:
        narrative = await host_ai(prompt, "你是严谨的股票研究代理，不承诺收益，不编造数据。")
    except Exception as error:
        logger.warning("[SayuStock] 持仓分析宿主模型调用失败: {}", error)
        return markdown, False, f"宿主模型调用失败：{type(error).__name__}: {error}；以下为规则化数据摘要。"
    if not narrative.strip():
        return markdown, False, "宿主模型返回空结果；以下为规则化数据摘要。"
    return narrative.strip(), True, ""


async def _holdings(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.stock_analysis.portfolio import analyze_portfolio
    from SayuStock.stock_holdings_analysis.parse import build_symbol_list

    from .charts import build_report_html

    items = await _watchlist_items(event) if not argument.strip() else []
    watchlist = [str(item["code"]) for item in items]
    resolved = build_symbol_list(manual_text=argument, watchlist=watchlist)
    if resolved is None:
        return _result()(
            "您还没有自选，也没有在命令后写股票代码/名称。请先「添加自选」，或发送：持仓分析 600519 茅台（最多 8 只）",
            structured={"ok": False},
        )
    # The original charged one run per user/day because it drove two capability
    # agents.  Only the host-model path can be that expensive here; the
    # deterministic fallback must stay retryable and free.
    quota_claimed = False
    quota_key = event.user_id
    quota_bot = event.bot_id or event.platform
    model_tool = bool(event._extra.get("host_model_tool"))
    if not model_tool and host_ai_available():
        from SayuStock.stock_holdings_analysis.quota import try_claim_quota, is_unlimited_user

        if not is_unlimited_user(quota_key):
            if not try_claim_quota(quota_key, quota_bot):
                return _result()(
                    "今日持仓分析已用过，请明天再试（按自然日 0 点重置；失败会释放额度）。",
                    structured={"ok": False, "quota": "exhausted"},
                )
            quota_claimed = True
    used_ai = False
    try:
        rows = await asyncio.gather(*(_holding_snapshot(symbol) for symbol in resolved.symbols))
        portfolio = await analyze_portfolio(list(resolved.symbols))
        fact_pack = _holding_markdown(list(rows), None if isinstance(portfolio, str) else portfolio)
        if model_tool:
            text, used_ai, note = fact_pack, False, ""
        else:
            text, used_ai, note = await _holdings_with_host_ai(fact_pack)
    finally:
        if quota_claimed and not used_ai:
            from SayuStock.stock_holdings_analysis.quota import release_quota

            release_quota(quota_key, quota_bot)
    prefix = [note] if note else []
    if resolved.cap_warning:
        prefix.append(resolved.cap_warning)
    text = "\n".join([*prefix, text])
    # The artifact must show the same body as the message; when the host model
    # narrated the pack, render that narrative instead of the raw table.
    html = await build_report_html(
        "持仓分析数据包",
        text if used_ai else fact_pack,
        meta={
            "标的数": str(len(resolved.symbols)),
            "来源": resolved.source,
            "模型": "宿主模型" if used_ai else "规则化",
        },
    )
    structured = {
        "symbols": resolved.symbols,
        "source": resolved.source,
        "truncated": resolved.truncated,
        "ai": used_ai,
        "portfolio": None if isinstance(portfolio, str) else portfolio.to_dict(),
        "rows": [
            {
                "symbol": row["symbol"],
                "code": row["quote"].symbol.code if row["quote"] is not None else "",
                "gap": row["gap"],
            }
            for row in rows
        ],
    }
    return await _html_result(
        "持仓分析",
        "持仓分析数据包",
        html,
        text,
        event,
        structured,
        kind="report",
        width=960,
        themed=False,
    )


def _kronos_text(page: Any, series: Any, config: Any) -> str:
    lines = [
        f"【{series.symbol.display_name or series.symbol.name} Kronos 模型预测】",
        f"模型权重：{config.model}　Tokenizer：{config.tokenizer}　设备：{config.device}",
        "说明：Kronos 使用独立权重推理，不调用宿主 LLM。",
        f"输入：{len(series.bars)} 根 30 分钟 K 线，最新收盘 {series.bars[-1].close if series.bars else '—'}",
    ]
    for legend in page.legend:
        detail = f"（{legend.detail}）" if legend.detail else ""
        lines.append(f"{legend.label}：{legend.value or '—'}{detail}")
    if series.bars:
        lines.append("")
        lines.append("最近5条K线：")
        for bar in series.bars[-5:]:
            change = f"（{bar.change_pct:+.2f}%）" if bar.change_pct is not None else ""
            lines.append(f"  {bar.ts} 收:{bar.close}{change}")
    if page.notes:
        lines.extend(f"⚠ {note}" for note in page.notes)
    lines.append("预测结果仅供研究，不构成投资建议。")
    return "\n".join(lines)


def _load_kronos() -> Any:
    import importlib

    return importlib.import_module("SayuStock.stock_ai.draw_ai_map")


async def _kronos(event: UnifiedEvent, argument: str) -> Any:
    import importlib.util

    from SayuStock.utils.market import KlinePeriod, get_market, is_market_error

    from .charts import chart_from_page

    query = argument.strip()
    if not query:
        return _result()(_usage("模型预测"))
    if importlib.util.find_spec("torch") is None:
        return _result()("模型预测需要 PyTorch，请先安装适合当前设备的 torch。", structured={"ok": False})
    key = query.casefold()
    async with _KRONOS_LOCK:
        if _KRONOS_BUSY:
            return _result()(
                f"当前队列中还有 {len(_KRONOS_BUSY)} 只股票在预测中，请稍后提交。",
                structured={"ok": False, "busy": True},
            )
        _KRONOS_BUSY.add(key)
    worker = None
    try:
        series = await get_market().kline(query, KlinePeriod.M30)
        if is_market_error(series):
            return _result()(series.message, structured={"ok": False})
        try:
            module = _load_kronos()
        except ImportError as error:
            return _result()(f"预测组件缺少 {error.name}，请安装 forecast 依赖。", structured={"ok": False})
        config = module.kronos_run_config()
        last = series.bars[-1].ts.isoformat(timespec="minutes") if series.bars else "none"
        cache_key = f"{series.symbol.code}|{last}|{len(series.bars)}|{config.model}|{config.tokenizer}|{config.device}"
        cached = _KRONOS_CACHE.get(cache_key)
        if cached is not None and cached[0] > time.monotonic():
            _, title, cached_html, cached_text, structured, width = cached
            return await _html_result(
                "模型预测", title, cached_html, cached_text, event, structured, width=width, themed=False
            )
        frame = module.fill_kline_by_kronos(series)
        if frame is None or frame.empty:
            return _result()("无有效K线数据", structured={"ok": False})
        worker = asyncio.create_task(asyncio.to_thread(module.gdf, frame, series, config))
        page = await asyncio.shield(worker)
        if isinstance(page, str):
            return _result()(page, structured={"ok": False, "model": config.model})
        html = chart_from_page(page)
        if html is None:
            return _result()("预测图生成失败", structured={"ok": False})
        text = _kronos_text(page, series, config)
        structured = {
            "symbol": series.symbol.code,
            "name": series.symbol.display_name or series.symbol.name,
            "period": "30m",
            "bars": len(series.bars),
            "last_close": series.bars[-1].close if series.bars else None,
            "model": config.model,
            "tokenizer": config.tokenizer,
            "device": config.device,
            "weights": "kronos-independent",
            "provider": series.provider,
            "legend": [{"label": item.label, "value": item.value, "detail": item.detail} for item in page.legend],
        }
        _KRONOS_CACHE[cache_key] = (
            time.monotonic() + _KRONOS_TTL,
            page.title,
            html,
            text,
            structured,
            page.width,
        )
        if len(_KRONOS_CACHE) > 32:
            now = time.monotonic()
            for expired in [k for k, value in _KRONOS_CACHE.items() if value[0] <= now] or list(_KRONOS_CACHE)[:8]:
                _KRONOS_CACHE.pop(expired, None)
        return await _html_result(
            "模型预测",
            page.title,
            html,
            text,
            event,
            structured,
            width=page.width,
            themed=False,
        )
    finally:
        if worker is not None and not worker.done():

            def release(completed: asyncio.Task) -> None:
                if not completed.cancelled():
                    completed.exception()
                _KRONOS_BUSY.discard(key)

            worker.add_done_callback(release)
        else:
            _KRONOS_BUSY.discard(key)


async def _stock(event: UnifiedEvent, argument: str):
    from ..commands import stock_result

    return await stock_result(
        argument, owner=event.user_id, with_image=event.platform != "qweb" and not event._extra.get("qweb")
    )


async def _compare(event: UnifiedEvent, argument: str):
    from ..commands import compare_result

    return await compare_result(
        argument, event=event, owner=event.user_id, with_image=event.platform != "qweb" and not event._extra.get("qweb")
    )


async def _watchlist(event: UnifiedEvent, argument: str):
    from ..commands import my_watch

    return await my_watch(event)


async def _my_intraday(event: UnifiedEvent, argument: str):
    items = await _watchlist_items(event)
    if not items:
        return _result()("请先添加自选股。")
    return await _stock(event, " ".join(item["code"] for item in items[:5]))


def _parse_day(token: str) -> date | None:
    """Parse ``2024-01-02`` / ``2024.1.2`` / ``20240102``; never a bare code."""
    match = _DATE_RE.fullmatch(token.strip())
    if match is None:
        return None
    if match.group(5) is not None:
        year, month, day = int(match.group(5)), int(match.group(6)), int(match.group(7))
    else:
        year, month, day = int(match.group(1)), int(match.group(3)), int(match.group(4))
    try:
        return date(year, month, day)
    except ValueError:
        return None


async def _range_change(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.utils.market import KlinePeriod, get_market, is_market_error
    from SayuStock.utils.time_range import parse_time_range

    if not argument.strip():
        return _result()(_usage("区间涨幅"))
    try:
        start_time, end_time, cleaned = parse_time_range(argument)
    except ValueError as error:
        return _result()(str(error), structured={"ok": False})
    query_parts: list[str] = []
    days: list[date] = []
    for token in cleaned.replace("，", " ").replace(",", " ").split():
        day = _parse_day(token)
        if day is None:
            query_parts.append(token)
        elif len(days) < 2:
            days.append(day)
    query = " ".join(query_parts)
    if not query:
        return _result()(_usage("区间涨幅"))
    if start_time is not None:
        start, end = start_time.date(), (end_time.date() if end_time is not None else date.today())
    elif len(days) == 1:
        start, end = days[0], date.today()
    elif len(days) >= 2:
        start, end = days[0], days[1]
    else:
        start, end = date(date.today().year, 1, 1), date.today()
    if start > end:
        return _result()("开始日期不能晚于结束日期", structured={"ok": False})
    series = await get_market().kline(query, KlinePeriod.D1, start=start, end=end)
    if is_market_error(series):
        return _result()(series.message, structured={"ok": False})
    in_range = [bar for bar in series.bars if start <= bar.ts.date() <= end]
    if not in_range:
        actual = f"{series.bars[0].ts:%Y-%m-%d}~{series.bars[-1].ts:%Y-%m-%d}" if series.bars else "无"
        return _result()(
            f"在指定时间范围内未找到{series.symbol.name}的K线数据（实际数据范围: {actual}）", structured={"ok": False}
        )
    first, last = in_range[0], in_range[-1]
    change = (last.close / first.open - 1) * 100 if first.open else None
    lines = [
        f"【{series.symbol.name} 涨跌幅分析】",
        f"时间范围: {start:%Y-%m-%d} ~ {end:%Y-%m-%d}（实际 {first.ts:%Y-%m-%d} ~ {last.ts:%Y-%m-%d}）",
        f"起始开盘价: {_num(first.open, 3)}",
        f"结束收盘价: {_num(last.close, 3)}",
        f"区间涨跌幅: {_pct(change)}",
        f"数据源: {series.provider or '未标注'}",
    ]
    structured = {
        "symbol": series.symbol.code,
        "name": series.symbol.name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "start_open": first.open,
        "end_close": last.close,
        "change_pct": change,
        "bars": len(in_range),
        "provider": series.provider,
    }
    return _result()("\n".join(lines), structured=structured)


_VIX_MAP: dict[str, tuple[str, str]] = {
    "300": ("300VIX", "沪深300 VIX"),
    "50": ("50VIX", "上证50 VIX"),
    "1000": ("1000VIX", "中证1000 VIX"),
    "kcb": ("科创板VIX", "科创板 VIX"),
    "cyb": ("创业板VIX", "创业板 VIX"),
}


async def _vix(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.utils.market import get_market, is_market_error

    key = argument.strip().casefold() or "300"
    query, label = _VIX_MAP.get(key, _VIX_MAP["300"])
    quote = await get_market().quote(query)
    if is_market_error(quote):
        return _result()(quote.message, structured={"ok": False})
    text = f"【{label}】\n当前: {_num(quote.price)}  涨跌: {_pct(quote.change_pct)}"
    structured = {
        "kind": key,
        "query": query,
        "label": label,
        "price": quote.price,
        "change_pct": quote.change_pct,
        "provider": quote.provider,
    }
    return _result()(text, structured=structured)


async def _symbol_lookup(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.utils.market import get_market

    query = argument.strip()
    if not query:
        return _result()(_usage("代码解析"))
    symbol = await get_market().resolve(query)
    if symbol is None:
        return _result()(f"未找到 '{query}'", structured={"ok": False})
    text = f"{symbol.code}: {symbol.name} ({symbol.sec_type or symbol.asset_class.value})"
    structured = {
        "code": symbol.code,
        "name": symbol.name,
        "display_name": symbol.display_name,
        "exchange": symbol.exchange,
        "asset_class": symbol.asset_class.value,
        "provider_symbol": symbol.provider_symbol,
    }
    return _result()(text, structured=structured)


async def _sector_scan(event: UnifiedEvent, argument: str):
    import json

    from SayuStock.utils.market_research import sector_scan

    concept = "概念" in argument
    data = json.loads(await sector_scan(15, "concept" if concept else "industry", not concept or "全部" in argument))
    lines = []
    for label, key in (("领涨", "top_rise"), ("领跌", "top_fall")):
        lines.append(label + "：" + "、".join(str(row["name"]) for row in data[key]))
    if data.get("_error"):
        lines.append("板块数据暂缺：" + str(data["_error"]))
    return _result()("\n".join(lines), structured=data)


async def _crypto(event: UnifiedEvent, argument: str):
    from SayuStock.utils.market_research import crypto_snapshot

    return _result()(await crypto_snapshot())


_HANDLERS = {
    "板块强弱扫描": _sector_scan,
    "主流币行情": _crypto,
    "技术分析": _technical,
    "股票卡片": _stock_card,
    "自动选股": _screen,
    "组合体检": _portfolio,
    "持仓分析": _holdings,
    "模型预测": _kronos,
    "个股": _stock,
    "对比个股": _compare,
    "我的自选": _watchlist,
    "我的个股": _my_intraday,
    "区间涨幅": _range_change,
    "VIX指数": _vix,
    "代码解析": _symbol_lookup,
}


async def dispatch(command: str, argument: str, event: UnifiedEvent) -> Any:
    """Run one research feature for the trusted event identity."""
    name = _resolve(command)
    if not event.user_id:
        return _result()("无法确认当前账户身份，已拒绝执行。", structured={"ok": False, "error": "unauthenticated"})
    if len(argument) > 2000:
        return _result()("参数过长，请简化后重试。", structured={"ok": False, "error": "argument_too_long"})
    handler = _HANDLERS.get(name)
    if handler is None:
        return _result()(f"未实现的研究指令：{command}", structured={"ok": False})
    try:
        result = await handler(event, argument.strip())
        if result.structured:
            result.structured = _clean_json(result.structured)
        return result
    except Exception as error:
        logger.exception("[SayuStock] {} 执行失败: {}", name, error)
        return _result()(f"{name}失败：{error}", structured={"ok": False, "error": type(error).__name__})


__all__ = ["FEATURES", "dispatch"]
