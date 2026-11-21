# ruff: noqa: E501
"""HTML/PNG chart production for bots, LLM tools and the web console."""

from __future__ import annotations

import re
import html as html_lib
from typing import Any, Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from nonebot.log import logger

from ..compat import bootstrap

bootstrap.install()

from SayuStock.render.kline import prepare_kline  # noqa: E402
from SayuStock.render.compare import prepare_compare  # noqa: E402
from SayuStock.render.intraday import (  # noqa: E402
    prepare_multi_intraday,
    prepare_single_intraday,
)
from SayuStock.render.chart_page import page_html  # noqa: E402
from SayuStock.utils.market.models import KlineSeries, IntradaySeries  # noqa: E402

from .theme import (  # noqa: E402
    ThemeContext,
    ThemePalette,
    apply_theme,
    resolve_theme,
    theme_palette,
)
from .market import MarketService, MarketServiceError  # noqa: E402
from ..compat.gsuid_core.utils.html_render import render_html_to_bytes  # noqa: E402


class ChartRenderError(RuntimeError):
    """HTML exists but the optional browser backend could not rasterize it."""


def _theme_context(context: Mapping[str, Any] | None = None) -> ThemeContext:
    if isinstance(context, ThemeContext):
        return context
    if context is None:
        return resolve_theme()
    return ThemeContext(context)


def _theme(context: Mapping[str, Any] | None = None) -> ThemePalette:
    return theme_palette(_theme_context(context))


def _esc(value: Any) -> str:
    return html_lib.escape(str(value if value is not None else ""), quote=True)


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _esc(value)
    return f"{number:,.{digits}f}{suffix}"


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def _tone(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "flat"
    if number > 0:
        return "up"
    if number < 0:
        return "down"
    return "flat"


def chart_from_page(page: Any, *, context: Mapping[str, Any] | None = None) -> str | None:
    """Convert a prepared ``ChartPage`` (or an error string) to full HTML.

    The same themed HTML is stored as the web artifact and rasterized for the
    group image, so both surfaces share one source.
    """
    if isinstance(page, str):
        return None
    resolved = _theme_context(context)
    return apply_theme(page_html(page), resolved, recolor=True)


async def build_kline_html(service: MarketService, query: str, period: str = "日k") -> tuple[str, int, str]:
    from .market import normalize_period

    result = await service.market.kline(query, normalize_period(period))
    if not isinstance(result, KlineSeries):
        raise MarketServiceError(getattr(result, "message", "K 线获取失败"))
    page = prepare_kline(result)
    html = chart_from_page(page)
    if html is None:
        raise MarketServiceError(str(page))
    title = f"{result.symbol.display_name} · {period}"
    return html, page.width, title


async def build_intraday_html(service: MarketService, query: str, ndays: int = 1) -> tuple[str, int, str]:
    result = await service.market.intraday(query, ndays=ndays)
    if not isinstance(result, IntradaySeries):
        raise MarketServiceError(getattr(result, "message", "分时获取失败"))
    page = prepare_single_intraday(result)
    html = chart_from_page(page)
    if html is None:
        raise MarketServiceError(str(page))
    return html, page.width, f"{result.symbol.display_name} · 分时"


async def build_multi_intraday_html(service: MarketService, queries: list[str], ndays: int = 1) -> tuple[str, int, str]:
    results = []
    for query in queries:
        result = await service.market.intraday(query, ndays=ndays)
        if not isinstance(result, IntradaySeries):
            raise MarketServiceError(f"{query}: {getattr(result, 'message', '分时获取失败')}")
        results.append(result)
    page = prepare_multi_intraday(results)
    html = chart_from_page(page)
    if html is None:
        raise MarketServiceError(str(page))
    return html, page.width, "多股分时对比"


async def build_compare_html(service: MarketService, queries: list[str], period: str = "日k") -> tuple[str, int, str]:
    from .market import normalize_period

    resolved = normalize_period(period)
    results = []
    for query in queries:
        result = await service.market.kline(query, resolved)
        if not isinstance(result, KlineSeries):
            raise MarketServiceError(f"{query}: {getattr(result, 'message', 'K 线获取失败')}")
        results.append(result)
    return compare_series_html(results)


def compare_series_html(series: list[KlineSeries]) -> tuple[str, int, str]:
    page = prepare_compare(series)
    html = chart_from_page(page)
    if html is None:
        raise MarketServiceError(str(page))
    return html, page.width, "个股走势对比"


def overview_html(data: dict[str, Any], *, width: int = 1280, context: Mapping[str, Any] | None = None) -> str:
    """Render the market overview as a responsive, self-contained page."""
    ctx = _theme_context(context)
    palette = theme_palette(ctx)
    indices = data.get("indices") or []
    industry_rise = (data.get("industries") or {}).get("rise") or []
    industry_fall = (data.get("industries") or {}).get("fall") or []
    concept_rise = (data.get("concepts") or {}).get("rise") or []
    concept_fall = (data.get("concepts") or {}).get("fall") or []
    breadth = data.get("breadth") or {}
    turnover = data.get("turnover") or {}
    northbound = data.get("northbound") or {}
    warnings = data.get("warnings") or []

    index_cards = "".join(
        f"""
        <article class="index-card {_tone(item.get("change_pct"))}">
          <div class="index-name">{_esc(item.get("label"))}</div>
          <div class="index-price">{_fmt(item.get("price"))}</div>
          <div class="index-change">{_pct(item.get("change_pct"))}</div>
          <div class="spark">{item.get("spark") or ""}</div>
          <div class="index-meta">{_esc(item.get("code"))} · {_esc(item.get("provider") or "")}</div>
        </article>"""
        for item in indices[:12]
    )

    total = int(breadth.get("up", 0)) + int(breadth.get("down", 0)) + int(breadth.get("flat", 0))
    up_ratio = (int(breadth.get("up", 0)) / total * 100) if total else 0
    down_ratio = (int(breadth.get("down", 0)) / total * 100) if total else 0
    breadth_bar = f"""
      <div class="breadth-bar" title="上涨 {breadth.get("up", 0)} / 下跌 {breadth.get("down", 0)}">
        <span class="up-bar" style="width:{up_ratio:.1f}%"></span>
        <span class="down-bar" style="width:{down_ratio:.1f}%"></span>
        <span class="flat-bar" style="width:{max(0, 100 - up_ratio - down_ratio):.1f}%"></span>
      </div>
      <div class="breadth-legend">
        <span class="up">上涨 {breadth.get("up", 0)}</span>
        <span class="flat">平盘 {breadth.get("flat", 0)}</span>
        <span class="down">下跌 {breadth.get("down", 0)}</span>
        <span class="limit">涨停 {breadth.get("limit_up", 0)} / 跌停 {breadth.get("limit_down", 0)}</span>
      </div>"""

    def sector_rows(rows: list[dict[str, Any]]) -> str:
        return "".join(
            f"<tr><td>{_esc(row.get('name'))}</td>"
            f"<td class='num {_tone(row.get('change_pct'))}'>{_pct(row.get('change_pct'))}</td>"
            f"<td>{_esc(row.get('lead_name') or '')}</td>"
            f"<td class='num'>{_pct(row.get('lead_change_pct'))}</td></tr>"
            for row in rows[:10]
        )

    turnover_amount = turnover.get("amount")
    turnover_yi = f"{float(turnover_amount) / 1e8:,.0f} 亿" if isinstance(turnover_amount, (int, float)) else "—"
    north = "—"
    if isinstance(northbound, dict) and northbound:
        try:
            north = f"{float(northbound.get('sh_net_yi', 0)) + float(northbound.get('sz_net_yi', 0)):,.2f} 亿"
        except (TypeError, ValueError):
            north = "—"

    warnings_html = (
        "<div class='warn'><b>部分数据缺失</b><br>" + "<br>".join(_esc(item) for item in warnings) + "</div>"
        if warnings
        else ""
    )

    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SayuStock 大盘概览</title>
<style>
:root{{--bg:{palette.background};--panel:{palette.surface};--panel2:{palette.surface_alt};
--text:{palette.text};--muted:{palette.muted};--up:{palette.up};--down:{palette.down};--flat:{palette.flat};
--line:{palette.border};--gold:{palette.secondary};--dim:{palette.dim};--accent-soft:{palette.accent_soft};
--warn-bg:{palette.surface_alt};--warn-border:{palette.warning};--warn-text:{palette.warning}}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(1200px 600px at 15% -10%,{palette.page_top} 0,var(--bg) 55%);
color:var(--text);font:14px/1.55 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}}
.wrap{{max-width:{width}px;margin:0 auto;padding:28px 22px 48px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:22px}}
h1{{font-size:26px;margin:0;letter-spacing:.5px}}h1 span{{color:var(--gold)}}
.sub{{color:var(--muted);font-size:12px}}.up{{color:var(--up)}}.down{{color:var(--down)}}.flat{{color:var(--flat)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}}
.index-card{{background:linear-gradient(160deg,var(--panel2),var(--panel));border:1px solid var(--line);
border-radius:14px;padding:14px 15px;position:relative;overflow:hidden}}
.index-card:before{{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--flat)}}
.index-card.up:before{{background:var(--up)}}.index-card.down:before{{background:var(--down)}}
.index-name{{color:var(--muted);font-size:12px}}.index-price{{font-size:25px;font-weight:700;margin-top:2px}}
.index-change{{font-size:13px;margin-bottom:4px}}.spark svg{{width:100%;height:44px;display:block}}
.index-meta{{color:var(--dim);font-size:10px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;margin-top:18px}}
.panel h2{{font-size:16px;margin:0 0 12px}}.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.stat{{background:var(--panel2);border-radius:12px;padding:12px}}.stat b{{display:block;font-size:20px}}
.stat span{{color:var(--muted);font-size:12px}}
.breadth-bar{{display:flex;height:16px;border-radius:8px;overflow:hidden;background:{palette.surface_alt};
margin:12px 0 8px}}
.breadth-bar span{{display:block;height:100%}}.up-bar{{background:var(--up)}}.down-bar{{background:var(--down)}}
.flat-bar{{background:var(--flat)}}.breadth-legend{{display:flex;gap:18px;flex-wrap:wrap;font-size:12px}}
.tables{{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:7px 6px;border-bottom:1px solid var(--line);text-align:left}}
th{{color:var(--muted);font-weight:500}}.num{{text-align:right;font-variant-numeric:tabular-nums}}
.warn{{margin-top:16px;padding:12px 14px;border-radius:12px;border:1px solid var(--warn-border);
background:var(--warn-bg);color:var(--warn-text);font-size:12px}}
@media(max-width:760px){{.tables{{grid-template-columns:1fr}}.stats{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<header><div><h1>SayuStock <span>大盘概览</span></h1>
<div class="sub">数据时间 {_esc(data.get("as_of"))} · 来源 {_esc(data.get("provider") or "多源聚合")}</div></div>
<div class="sub">仅供研究，不构成投资建议</div></header>
<section class="grid">{index_cards}</section>
<div class="panel"><h2>市场宽度</h2>{breadth_bar}</div>
<div class="panel stats">
  <div class="stat"><b>{turnover_yi}</b><span>两市成交额</span></div>
  <div class="stat"><b>{north}</b><span>北向资金净流入</span></div>
  <div class="stat"><b>{up_ratio:.1f}%</b><span>上涨占比</span></div>
</div>
<div class="panel tables">
  <div><h2>行业领涨</h2><table><thead><tr><th>板块</th><th class="num">涨跌幅</th><th>领涨</th><th class="num">涨幅</th></tr></thead>
  <tbody>{sector_rows(industry_rise)}</tbody></table></div>
  <div><h2>行业领跌</h2><table><thead><tr><th>板块</th><th class="num">涨跌幅</th><th>领跌</th><th class="num">跌幅</th></tr></thead>
  <tbody>{sector_rows(industry_fall)}</tbody></table></div>
  <div><h2>概念领涨</h2><table><thead><tr><th>板块</th><th class="num">涨跌幅</th><th>领涨</th><th class="num">涨幅</th></tr></thead>
  <tbody>{sector_rows(concept_rise)}</tbody></table></div>
  <div><h2>概念领跌</h2><table><thead><tr><th>板块</th><th class="num">涨跌幅</th><th>领跌</th><th class="num">跌幅</th></tr></thead>
  <tbody>{sector_rows(concept_fall)}</tbody></table></div>
</div>
{warnings_html}
<div class="sub" style="margin-top:22px">SayuStock · NoneBot2 branch · {datetime.now():%Y-%m-%d %H:%M}</div>
</div></body></html>"""
    return apply_theme(document, ctx, recolor=True)


def _clean_news_text(value: Any) -> str:
    raw = "" if value is None else str(value)
    raw = "".join(char for char in raw if char >= " " or char in "\n\t")
    return re.sub(r"\s+", " ", raw).strip()


def _news_title(item: Mapping[str, Any]) -> str:
    title = _clean_news_text(item.get("title"))
    if title:
        return title[:500]
    detail = _clean_news_text(item.get("text"))
    if not detail:
        return ""
    # 详情才是有效标题时，取第一句，避免把整段正文塞进标题。
    first = re.split(r"(?<=[。！？!?；;])", detail, maxsplit=1)[0]
    return first[:500]


def _news_time(item: Mapping[str, Any]) -> str:
    raw = item.get("published_ms")
    if isinstance(raw, (int, float)) and raw > 0:
        seconds = raw / 1000 if raw > 10_000_000_000 else float(raw)
        try:
            return datetime.fromtimestamp(seconds, ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
        except (OSError, OverflowError, ValueError):
            return "—"
    for key in ("published_at", "published", "time"):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo:
            parsed = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
        return parsed.strftime("%m-%d %H:%M")
    return "—"


def _is_important(item: Mapping[str, Any]) -> bool:
    value = item.get("important")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "重要", "高"}
    return bool(value)


def _news_dedupe_key(title: str) -> str:
    return re.sub(r"[\s\u3000，。！？!?；;：:、,.·\-—_]+", "", title).casefold()


def news_card(data: Mapping[str, Any], limit: int = 12) -> tuple[str, str]:
    """Return a themed news card and its plain-text twin.

    Items keep their natural order.  Blank titles are skipped; a blank title
    with a usable detail falls back to the first detail sentence.  Duplicate
    titles are removed first-occurrence-first.  Every untrusted field is
    HTML-escaped in the card and normalised in the plain text.
    """
    try:
        maximum = max(0, min(int(limit), 50))
    except (TypeError, ValueError):
        maximum = 12
    payload: Mapping[str, Any] = data if isinstance(data, Mapping) else {}
    source = _clean_news_text(payload.get("source_label") or payload.get("source") or "多源快讯")
    raw_items = payload.get("items")
    items: list[Mapping[str, Any]] = [item for item in (raw_items or []) if isinstance(item, Mapping)]
    rows: list[str] = []
    plain_rows: list[str] = []
    seen: set[str] = set()
    for item in items:
        if len(rows) >= maximum:
            break
        title = _news_title(item)
        if not title:
            continue
        key = _news_dedupe_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        timestamp = _news_time(item)
        important = _is_important(item)
        flag = '<span class="flag">重要</span>' if important else ""
        rows.append(
            f"""<article class="news-item {"important" if important else ""}">
  <div class="news-meta"><time datetime="{_esc(timestamp)}">{_esc(timestamp)}</time>
  {flag}</div>
  <h3 class="news-title">{_esc(title)}</h3>
</article>"""
        )
        plain_flag = "[重要] " if important else ""
        plain_rows.append(f"{timestamp} {plain_flag}{title}")
    if not rows:
        rows.append('<div class="news-empty">暂无有效快讯。</div>')
        plain_rows.append("暂无有效快讯。")
    ctx = resolve_theme()
    palette = theme_palette(ctx)
    count = len(plain_rows) if plain_rows and plain_rows[0] != "暂无有效快讯。" else 0
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>财经快讯 · {_esc(source)}</title>
<style>
:root{{--bg:{palette.background};--panel:{palette.surface};--text:{palette.text};--muted:{palette.muted};
--line:{palette.border};--accent:{palette.accent};--warning:{palette.warning};--up:{palette.up};--down:{palette.down}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);
font:14px/1.55 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}}
.wrap{{max-width:860px;margin:0 auto;padding:24px 22px 40px}}
.news-head{{display:flex;align-items:flex-end;justify-content:space-between;gap:14px;
border-bottom:2px solid var(--line);padding-bottom:14px;margin-bottom:14px}}
.news-head h1{{margin:0;font-size:24px;letter-spacing:.5px}}
.news-source{{color:var(--muted);font-size:12px}}.news-source b{{color:var(--accent)}}
.news-count{{color:var(--muted);font-size:12px;white-space:nowrap}}
.news-item{{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--line);
border-radius:12px;padding:12px 15px;margin-bottom:10px}}
.news-item.important{{border-left-color:var(--warning)}}
.news-meta{{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:11px;
font-variant-numeric:tabular-nums;margin-bottom:5px}}
.news-item.important .news-meta{{color:var(--warning)}}
.flag{{border:1px solid currentColor;border-radius:999px;padding:0 7px;font-size:10px}}
.news-title{{margin:0;font-size:15px;line-height:1.6;font-weight:650;overflow-wrap:anywhere}}
.news-empty{{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:18px;color:var(--muted);text-align:center}}
.foot{{color:var(--muted);font-size:11px;margin-top:18px}}
</style></head><body><div class="wrap">
<header class="news-head"><div><h1>财经快讯</h1>
<div class="news-source">来源：<b>{_esc(source)}</b></div></div>
<div class="news-count">共 {count} 条</div></header>
<section class="news-list">{"".join(rows)}</section>
<div class="foot">七七 · {datetime.now():%Y-%m-%d %H:%M}</div>
</div></body></html>"""
    plain = "\n".join([f"【财经快讯】来源：{source} · {count} 条", *plain_rows])
    return apply_theme(document, ctx), plain


def _table_html(title: str, columns: list[str], rows: list[list[Any]], note: str = "") -> str:
    head = "".join(f"<th>{_esc(column)}</th>" for column in columns)
    body_rows: list[str] = []
    for row in rows[:300]:
        cells = []
        for value in row[: len(columns)]:
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            if numeric:
                cells.append(f"<td class='num'>{value:,.2f}</td>")
            else:
                cells.append(f"<td>{_esc(value)}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    note_html = f"<div class='dash-note'>{_esc(note)}</div>" if note else ""
    return f"""<section class="dash-panel"><h2>{_esc(title)}</h2>
<table><thead><tr>{head}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>{note_html}</section>"""


def dashboard_html(
    data: Mapping[str, Any],
    *,
    title: str = "AI 股票看板",
    width: int = 1100,
    context: Mapping[str, Any] | None = None,
) -> str:
    """Render an AI dashboard: metric cards plus escaped data tables.

    ``data`` is read-only: the caller's dictionaries/lists are never mutated.
    """
    ctx = _theme_context(context)
    palette = theme_palette(ctx)
    payload: Mapping[str, Any] = data if isinstance(data, Mapping) else {}
    resolved_title = _esc(payload.get("title") or title)
    metrics = payload.get("metrics") or payload.get("cards") or []
    tables = payload.get("tables") or []
    single = payload.get("table")
    if isinstance(single, Mapping):
        tables = [single, *tables]
    cards: list[str] = []
    for metric in metrics[:12]:
        if not isinstance(metric, Mapping):
            continue
        change = metric.get("change_pct", metric.get("change"))
        tone = _tone(change)
        value = metric.get("value")
        note = metric.get("note") or metric.get("detail") or ""
        cards.append(
            f"""<article class="dash-card {tone}">
  <div class="dash-label">{_esc(metric.get("label") or metric.get("name"))}</div>
  <div class="dash-value">{_esc(value) if not isinstance(value, (int, float)) else _fmt(value)}</div>
  <div class="dash-change">{_pct(change)}</div>
  {f'<div class="dash-detail">{_esc(note)}</div>' if note else ""}
</article>"""
        )
    table_blocks: list[str] = []
    for table in tables[:8]:
        if not isinstance(table, Mapping):
            continue
        columns = [str(column) for column in (table.get("columns") or [])]
        rows = [list(row) for row in (table.get("rows") or [])[:300] if isinstance(row, (list, tuple))]
        if not columns:
            continue
        table_blocks.append(
            _table_html(
                _clean_news_text(table.get("title") or "数据表"),
                columns,
                rows,
                _clean_news_text(table.get("note")),
            )
        )
    notes = [str(item) for item in (payload.get("notes") or payload.get("warnings") or []) if str(item).strip()]
    note_html = "<div class='dash-warning'>" + "<br>".join(_esc(item) for item in notes) + "</div>" if notes else ""
    as_of = _esc(payload.get("as_of") or datetime.now().strftime("%Y-%m-%d %H:%M"))
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{resolved_title}</title>
<style>
:root{{--bg:{palette.background};--panel:{palette.surface};--panel2:{palette.surface_alt};--text:{palette.text};
--muted:{palette.muted};--line:{palette.border};--accent:{palette.accent};--warning:{palette.warning};
--up:{palette.up};--down:{palette.down};--flat:{palette.flat}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);
font:14px/1.55 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}}
.wrap{{max-width:{width}px;margin:0 auto;padding:26px 24px 44px}}
.dash-head{{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:18px}}
.dash-head h1{{margin:0;font-size:25px}}.dash-sub{{color:var(--muted);font-size:12px}}
.dash-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
.dash-card{{background:linear-gradient(160deg,var(--panel2),var(--panel));border:1px solid var(--line);
border-left:3px solid var(--flat);border-radius:13px;padding:13px 14px}}
.dash-card.up{{border-left-color:var(--up)}}.dash-card.down{{border-left-color:var(--down)}}
.dash-card.up .dash-change{{color:var(--up)}}.dash-card.down .dash-change{{color:var(--down)}}
.dash-label{{color:var(--muted);font-size:12px}}.dash-value{{font-size:23px;font-weight:750;
font-variant-numeric:tabular-nums;margin-top:2px}}.dash-change{{font-size:12px}}
.dash-detail{{color:var(--muted);font-size:11px;margin-top:3px}}
.dash-panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:16px}}
.dash-panel h2{{font-size:15px;margin:0 0 10px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left}}
th{{color:var(--muted);font-weight:600}}.num{{text-align:right;font-variant-numeric:tabular-nums}}
.dash-note{{margin-top:10px;padding:8px 10px;border-left:3px solid var(--accent);
background:var(--panel2);color:var(--muted);font-size:11px}}
.dash-warning{{margin-top:16px;padding:11px 13px;border-radius:10px;border:1px solid var(--warning);
color:var(--warning);font-size:12px}}.foot{{color:var(--muted);font-size:11px;margin-top:20px}}
@media(max-width:720px){{.dash-head{{display:block}}}}
</style></head><body><div class="wrap">
<header class="dash-head"><div><h1>{resolved_title}</h1>
<div class="dash-sub">更新时间 {as_of} · 网页与群图片同源</div></div>
<div class="dash-sub">仅供研究，不构成投资建议</div></header>
<section class="dash-grid">{"".join(cards)}</section>
{"".join(table_blocks)}
{note_html}
<div class="foot">SayuStock · AI 数据看板</div>
</div></body></html>"""
    return apply_theme(document, ctx)


async def rasterize(html: str, *, width: int = 1280, scale: float = 1.5) -> bytes:
    """Rasterize HTML to PNG via the optional htmlrender backend."""
    try:
        image = await render_html_to_bytes(html, max_width=width * scale, root_max_width=width, dpi=96 * scale)
    except (OSError, RuntimeError, ValueError) as error:
        raise ChartRenderError(str(error)) from error
    if isinstance(image, str):
        raise ChartRenderError(image)
    return image


async def build_overview(
    service: MarketService,
) -> tuple[dict[str, Any], str, int]:
    data = await service.overview()
    return data, overview_html(data), 1280


async def build_report_html(
    title: str,
    markdown_body: str,
    *,
    meta: dict[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> str:
    """Build a print-friendly HTML research report from plain text/markdown."""
    ctx = _theme_context(context)
    palette = theme_palette(ctx)
    try:
        import markdown as md

        body = md.markdown(markdown_body, extensions=["tables", "fenced_code"])
    except Exception:
        body = "<pre>" + _esc(markdown_body) + "</pre>"
    meta_html = "".join(f"<span>{_esc(k)}：{_esc(v)}</span>" for k, v in (meta or {}).items())
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_esc(title)}</title>
<style>:root{{--paper:{palette.background};--panel:{palette.surface};--text:{palette.text};
--muted:{palette.muted};--line:{palette.border};--accent:{palette.secondary};--accent-soft:{palette.accent_soft};
--up:{palette.up};--down:{palette.down};--flat:{palette.flat}}}
body{{margin:0;background:var(--paper);color:var(--text);
font:15px/1.7 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}}
.wrap{{max-width:920px;margin:0 auto;padding:36px 30px 60px;background:var(--panel);min-height:100vh;
border:1px solid var(--line)}}h1{{font-size:28px;margin:0 0 6px}}
.meta{{color:var(--muted);font-size:12px;display:flex;gap:14px;flex-wrap:wrap;margin-bottom:22px}}
h2{{border-left:4px solid var(--accent);padding-left:10px;margin-top:28px}}
h3{{color:var(--text)}}table{{border-collapse:collapse;width:100%;margin:14px 0}}
th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left}}th{{background:var(--accent-soft)}}
pre{{background:var(--paper);color:var(--text);padding:14px;border-radius:10px;overflow:auto;
border:1px solid var(--line)}}
blockquote{{border-left:3px solid var(--line);margin:0;padding:4px 14px;color:var(--muted)}}
a{{color:var(--accent)}}.up{{color:var(--up)}}.down{{color:var(--down)}}.flat{{color:var(--flat)}}
.foot{{color:var(--muted);font-size:12px}}
@media print{{body{{background:#fff}}}}
</style></head>
<body><div class="wrap"><h1>{_esc(title)}</h1><div class="meta">{meta_html}</div>{body}
<hr><p class="foot">SayuStock · 数据仅供研究，不构成投资建议</p></div></body></html>"""
    return apply_theme(document, ctx)


logger.debug("[SayuStock] chart service loaded")
