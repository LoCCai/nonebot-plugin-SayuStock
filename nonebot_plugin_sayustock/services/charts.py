# ruff: noqa: E501
"""HTML/PNG chart production for bots, LLM tools and the web console."""

from __future__ import annotations

import html as html_lib
from typing import Any
from datetime import datetime

from nonebot.log import logger

from ..compat import bootstrap

bootstrap.install()

from SayuStock.render import RenderError, render_html  # noqa: E402
from SayuStock.render.kline import prepare_kline  # noqa: E402
from SayuStock.render.compare import prepare_compare  # noqa: E402
from SayuStock.render.intraday import (  # noqa: E402
    prepare_multi_intraday,
    prepare_single_intraday,
)
from SayuStock.render.chart_page import page_html  # noqa: E402
from SayuStock.utils.market.models import KlineSeries, IntradaySeries  # noqa: E402

from .market import MarketService, MarketServiceError  # noqa: E402


class ChartRenderError(RuntimeError):
    """HTML exists but the optional browser backend could not rasterize it."""


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


def chart_from_page(page: Any) -> str | None:
    """Convert a prepared ``ChartPage`` (or an error string) to full HTML."""
    if isinstance(page, str):
        return None
    return page_html(page)


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
    page = prepare_compare(results)
    html = chart_from_page(page)
    if html is None:
        raise MarketServiceError(str(page))
    return html, page.width, "个股走势对比"


def overview_html(data: dict[str, Any], *, width: int = 1280) -> str:
    """Render the market overview as a responsive, self-contained page."""
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

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SayuStock 大盘概览</title>
<style>
:root{{--bg:#0b1220;--panel:#131e32;--panel2:#182743;--text:#e8eefb;--muted:#8ea2c4;
--up:#ff5b5b;--down:#20d47a;--flat:#8ea2c4;--line:#22324f;--gold:#ffc857}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(1200px 600px at 15% -10%,#1c3156 0,var(--bg) 55%);
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
.index-meta{{color:#63769a;font-size:10px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;margin-top:18px}}
.panel h2{{font-size:16px;margin:0 0 12px}}.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.stat{{background:var(--panel2);border-radius:12px;padding:12px}}.stat b{{display:block;font-size:20px}}
.stat span{{color:var(--muted);font-size:12px}}
.breadth-bar{{display:flex;height:16px;border-radius:8px;overflow:hidden;background:#23304a;margin:12px 0 8px}}
.breadth-bar span{{display:block;height:100%}}.up-bar{{background:var(--up)}}.down-bar{{background:var(--down)}}
.flat-bar{{background:#5d6b82}}.breadth-legend{{display:flex;gap:18px;flex-wrap:wrap;font-size:12px}}
.tables{{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:7px 6px;border-bottom:1px solid var(--line);text-align:left}}
th{{color:var(--muted);font-weight:500}}.num{{text-align:right;font-variant-numeric:tabular-nums}}
.warn{{margin-top:16px;padding:12px 14px;border-radius:12px;border:1px solid #8a6d1f;background:#2a2414;color:#ffd278;font-size:12px}}
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


async def rasterize(html: str, *, width: int = 1280, scale: float = 1.5) -> bytes:
    """Rasterize HTML to PNG via the optional htmlrender backend."""
    try:
        image = await render_html(html, width=width, scale=scale, label="SayuStock")
    except RenderError as error:
        raise ChartRenderError(str(error)) from error
    if isinstance(image, str):
        raise ChartRenderError(image)
    return image


async def build_overview(
    service: MarketService,
) -> tuple[dict[str, Any], str, int]:
    data = await service.overview()
    return data, overview_html(data), 1280


async def build_report_html(title: str, markdown_body: str, *, meta: dict[str, Any] | None = None) -> str:
    """Build a print-friendly HTML research report from plain text/markdown."""
    try:
        import markdown as md

        body = md.markdown(markdown_body, extensions=["tables", "fenced_code"])
    except Exception:
        body = "<pre>" + _esc(markdown_body) + "</pre>"
    meta_html = "".join(f"<span>{_esc(k)}：{_esc(v)}</span>" for k, v in (meta or {}).items())
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_esc(title)}</title>
<style>body{{margin:0;background:#f5f7fb;color:#172033;font:15px/1.7 "Microsoft YaHei",system-ui,sans-serif}}
.wrap{{max-width:920px;margin:0 auto;padding:36px 30px 60px;background:#fff;min-height:100vh;
box-shadow:0 0 40px rgba(20,35,70,.08)}}h1{{font-size:28px;margin:0 0 6px}}
.meta{{color:#5b6b88;font-size:12px;display:flex;gap:14px;flex-wrap:wrap;margin-bottom:22px}}
h2{{border-left:4px solid #d9a441;padding-left:10px;margin-top:28px}}table{{border-collapse:collapse;width:100%;margin:14px 0}}
th,td{{border:1px solid #dce3ef;padding:8px 10px;text-align:left}}th{{background:#f0f4fa}}
pre{{background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}
blockquote{{border-left:3px solid #c9d3e4;margin:0;padding:4px 14px;color:#5b6b88}}</style></head>
<body><div class="wrap"><h1>{_esc(title)}</h1><div class="meta">{meta_html}</div>{body}
<hr><p style="color:#8794aa;font-size:12px">SayuStock · 数据仅供研究，不构成投资建议</p></div></body></html>"""


logger.debug("[SayuStock] chart service loaded")
