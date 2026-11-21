# ruff: noqa: E501
"""Small, dependency-free SVG data-visualisation helpers for AI outputs.

The market charts reuse the upstream SayuStock renderer.  These helpers cover
the other half of the requirement: letting the model turn arbitrary analysis
results (screening tables, correlations, custom series) into a viewable HTML
chart or PNG without inventing a second market-data path.
"""

from __future__ import annotations

import html as html_lib
import math
import statistics
from typing import Any, Sequence

PALETTE = (
    "#5aa9ff",
    "#ffc857",
    "#20d47a",
    "#ff5b5b",
    "#c084fc",
    "#38bdf8",
    "#fb923c",
    "#a3e635",
)


def _esc(value: Any) -> str:
    return html_lib.escape(str(value if value is not None else ""), quote=True)


def _finite(values: Sequence[Any]) -> list[float]:
    result: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def describe(values: Sequence[Any]) -> dict[str, Any]:
    """Descriptive statistics with a stable JSON shape."""
    numbers = _finite(values)
    if not numbers:
        return {"count": 0}
    result: dict[str, Any] = {
        "count": len(numbers),
        "mean": round(statistics.fmean(numbers), 6),
        "median": round(statistics.median(numbers), 6),
        "min": round(min(numbers), 6),
        "max": round(max(numbers), 6),
        "stdev": round(statistics.pstdev(numbers), 6) if len(numbers) > 1 else 0.0,
        "first": round(numbers[0], 6),
        "last": round(numbers[-1], 6),
    }
    if numbers[0]:
        result["change_pct"] = round((numbers[-1] / numbers[0] - 1) * 100, 4)
    peak = numbers[0]
    max_drawdown = 0.0
    for value in numbers:
        peak = max(peak, value)
        if peak:
            max_drawdown = min(max_drawdown, (value / peak - 1) * 100)
    result["max_drawdown_pct"] = round(max_drawdown, 4)
    return result


def correlation(series: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Pairwise Pearson correlation for aligned series (shortest length wins)."""
    names = [str(item.get("name") or f"series{i}") for i, item in enumerate(series)]
    columns = [_finite(item.get("values") or []) for item in series]
    length = min((len(column) for column in columns), default=0)
    matrix: list[list[float | None]] = []
    for left in columns:
        row: list[float | None] = []
        for right in columns:
            if length < 2:
                row.append(None)
                continue
            a = left[:length]
            b = right[:length]
            try:
                corr = statistics.correlation(a, b)
            except (statistics.StatisticsError, ValueError):
                corr = None
            row.append(round(corr, 4) if corr is not None else None)
        matrix.append(row)
    return {"names": names, "matrix": matrix, "points": length}


def _base_html(title: str, body: str, *, width: int = 1100, subtitle: str = "") -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_esc(title)}</title>
<style>
:root{{--bg:#0b1220;--panel:#131e32;--line:#22324f;--text:#e8eefb;--muted:#8ea2c4;--gold:#ffc857}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(1000px 500px at 10% -10%,#1c3156,var(--bg) 60%);
color:var(--text);font:14px/1.55 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}}
.wrap{{max-width:{width}px;margin:0 auto;padding:28px 24px 40px}}
h1{{font-size:24px;margin:0 0 4px}}.sub{{color:var(--muted);font-size:12px;margin-bottom:18px}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px}}
svg{{display:block;width:100%;height:auto}}.legend{{display:flex;gap:16px;flex-wrap:wrap;margin-top:10px;font-size:12px;color:var(--muted)}}
.legend i{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:8px 8px;border-bottom:1px solid var(--line);text-align:left}}
th{{color:var(--muted);font-weight:600}}.num{{text-align:right;font-variant-numeric:tabular-nums}}
.note{{margin-top:14px;padding:10px 12px;border-left:3px solid var(--gold);background:rgba(255,200,87,.06);
color:#ffe6ad;font-size:12px}}.footer{{color:#63769a;font-size:10px;margin-top:18px}}
</style></head><body><div class="wrap"><h1>{_esc(title)}</h1>
<div class="sub">{_esc(subtitle)}</div>{body}
<div class="footer">SayuStock · 数据仅供研究，不构成投资建议</div></div></body></html>"""


def line_chart_html(
    *,
    title: str,
    labels: Sequence[str],
    series: Sequence[dict[str, Any]],
    y_label: str = "",
    note: str = "",
) -> str:
    width, height = 1080, 460
    left, right, top, bottom = 64, 24, 24, 58
    all_values = [value for item in series for value in _finite(item.get("values") or [])]
    if not all_values:
        return _base_html(title, "<div class='panel'>没有可绘制的数值。</div>")
    lo, hi = min(all_values), max(all_values)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    plot_w, plot_h = width - left - right, height - top - bottom
    count = max(len(labels), *(len(_finite(item.get("values") or [])) for item in series), 2)
    step = plot_w / max(count - 1, 1)

    def x_at(index: int) -> float:
        return left + index * step

    def y_at(value: float) -> float:
        return top + (hi - value) / (hi - lo) * plot_h

    grid = []
    for tick in range(5):
        value = lo + (hi - lo) * tick / 4
        y = y_at(value)
        grid.append(
            f"<line x1='{left}' y1='{y:.2f}' x2='{width - right}' y2='{y:.2f}' "
            f"stroke='#22324f' stroke-width='1'/>"
            f"<text x='{left - 8}' y='{y + 4:.2f}' fill='#8ea2c4' font-size='11' text-anchor='end'>{value:,.2f}</text>"
        )
    label_step = max(1, count // 8)
    x_labels = "".join(
        f"<text x='{x_at(index):.2f}' y='{height - 20}' fill='#8ea2c4' font-size='11' text-anchor='middle'>"
        f"{_esc(labels[index] if index < len(labels) else index + 1)}</text>"
        for index in range(0, count, label_step)
    )
    traces = []
    for index, item in enumerate(series):
        values = _finite(item.get("values") or [])
        color = str(item.get("color") or PALETTE[index % len(PALETTE)])
        points = " ".join(f"{x_at(i):.2f},{y_at(value):.2f}" for i, value in enumerate(values))
        traces.append(
            f"<polyline fill='none' stroke='{color}' stroke-width='2.2' "
            f"stroke-linejoin='round' points='{points}'/>"
            + "".join(
                f"<circle cx='{x_at(i):.2f}' cy='{y_at(value):.2f}' r='2.6' fill='{color}'/>"
                for i, value in enumerate(values)
                if i in {0, len(values) - 1}
            )
        )
    legend = "".join(
        f"<span><i style='background:{item.get('color') or PALETTE[i % len(PALETTE)]}'></i>"
        f"{_esc(item.get('name') or f'series{i}')}</span>"
        for i, item in enumerate(series)
    )
    svg = (
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{_esc(title)}'>"
        + "".join(grid)
        + "".join(traces)
        + x_labels
        + (f"<text x='{left}' y='14' fill='#8ea2c4' font-size='11'>{_esc(y_label)}</text>" if y_label else "")
        + "</svg>"
    )
    note_html = f"<div class='note'>{_esc(note)}</div>" if note else ""
    return _base_html(
        title,
        f"<div class='panel'>{svg}<div class='legend'>{legend}</div>{note_html}</div>",
        subtitle="自定义序列 · SVG 渲染",
    )


def bar_chart_html(
    *,
    title: str,
    labels: Sequence[str],
    series: Sequence[dict[str, Any]],
    y_label: str = "",
    note: str = "",
) -> str:
    width, height = 1080, 460
    left, right, top, bottom = 64, 24, 24, 62
    all_values = [value for item in series for value in _finite(item.get("values") or [])]
    if not all_values:
        return _base_html(title, "<div class='panel'>没有可绘制的数值。</div>")
    lo = min(0.0, min(all_values))
    hi = max(0.0, max(all_values))
    if lo == hi:
        hi = lo + 1
    plot_w, plot_h = width - left - right, height - top - bottom
    count = max(len(labels), *(len(_finite(item.get("values") or [])) for item in series), 1)
    group_w = plot_w / count
    bar_w = max(3.0, group_w * 0.7 / max(len(series), 1))

    def y_at(value: float) -> float:
        return top + (hi - value) / (hi - lo) * plot_h

    zero = y_at(0)
    bars = []
    for s_index, item in enumerate(series):
        values = _finite(item.get("values") or [])
        color = str(item.get("color") or PALETTE[s_index % len(PALETTE)])
        for i, value in enumerate(values):
            x = left + i * group_w + group_w * 0.15 + s_index * bar_w
            y = y_at(value)
            bars.append(
                f"<rect x='{x:.2f}' y='{min(y, zero):.2f}' width='{bar_w:.2f}' "
                f"height='{abs(y - zero):.2f}' fill='{color}' opacity='0.88' rx='2'/>"
            )
    label_step = max(1, count // 10)
    x_labels = "".join(
        f"<text x='{left + index * group_w + group_w / 2:.2f}' y='{height - 22}' fill='#8ea2c4' "
        f"font-size='11' text-anchor='middle'>{_esc(labels[index] if index < len(labels) else index + 1)}</text>"
        for index in range(0, count, label_step)
    )
    grid = "".join(
        f"<line x1='{left}' y1='{y_at(value):.2f}' x2='{width - right}' y2='{y_at(value):.2f}' "
        f"stroke='#22324f' stroke-width='1'/>"
        for value in (lo, (lo + hi) / 2, hi)
    )
    legend = "".join(
        f"<span><i style='background:{item.get('color') or PALETTE[i % len(PALETTE)]}'></i>"
        f"{_esc(item.get('name') or f'series{i}')}</span>"
        for i, item in enumerate(series)
    )
    svg = (
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{_esc(title)}'>"
        + grid
        + "".join(bars)
        + x_labels
        + (f"<text x='{left}' y='14' fill='#8ea2c4' font-size='11'>{_esc(y_label)}</text>" if y_label else "")
        + "</svg>"
    )
    note_html = f"<div class='note'>{_esc(note)}</div>" if note else ""
    return _base_html(
        title,
        f"<div class='panel'>{svg}<div class='legend'>{legend}</div>{note_html}</div>",
        subtitle="自定义分类数据 · SVG 渲染",
    )


def pie_chart_html(*, title: str, labels: Sequence[str], values: Sequence[Any], note: str = "") -> str:
    numbers = [max(0.0, value) for value in _finite(values)]
    labels = list(labels)[: len(numbers)]
    total = sum(numbers)
    if not total:
        return _base_html(title, "<div class='panel'>没有可绘制的数值。</div>")
    cx = cy = 190.0
    radius = 160.0
    paths = []
    angle = -math.pi / 2
    for index, value in enumerate(numbers):
        sweep = value / total * math.tau
        x1 = cx + radius * math.cos(angle)
        y1 = cy + radius * math.sin(angle)
        angle += sweep
        x2 = cx + radius * math.cos(angle)
        y2 = cy + radius * math.sin(angle)
        large = 1 if sweep > math.pi else 0
        color = PALETTE[index % len(PALETTE)]
        paths.append(
            f"<path d='M {cx} {cy} L {x1:.2f} {y1:.2f} A {radius} {radius} 0 {large} 1 {x2:.2f} {y2:.2f} Z' "
            f"fill='{color}' stroke='#0b1220' stroke-width='2'/>"
        )
    legend_rows = "".join(
        f"<tr><td><i style='display:inline-block;width:9px;height:9px;border-radius:50%;"
        f"background:{PALETTE[i % len(PALETTE)]};margin-right:7px'></i>{_esc(labels[i] if i < len(labels) else i + 1)}</td>"
        f"<td class='num'>{value:,.2f}</td><td class='num'>{value / total * 100:.2f}%</td></tr>"
        for i, value in enumerate(numbers)
    )
    note_html = f"<div class='note'>{_esc(note)}</div>" if note else ""
    body = f"""<div class="panel" style="display:grid;grid-template-columns:420px 1fr;gap:20px;align-items:center">
<svg viewBox="0 0 380 380" role="img" aria-label="{_esc(title)}">{"".join(paths)}
<circle cx="190" cy="190" r="72" fill="#131e32"/></svg>
<div><table><thead><tr><th>项目</th><th class="num">数值</th><th class="num">占比</th></tr></thead>
<tbody>{legend_rows}</tbody></table>{note_html}</div></div>"""
    return _base_html(title, body, subtitle="自定义占比数据 · SVG 渲染")


def table_html(*, title: str, columns: Sequence[str], rows: Sequence[Sequence[Any]], note: str = "") -> str:
    head = "".join(f"<th>{_esc(column)}</th>" for column in columns)
    body_rows = []
    for row in rows[:300]:
        cells = []
        for value in row[: len(columns)]:
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            cells.append(f"<td class='num'>{value:,.2f}</td>" if numeric else f"<td>{_esc(value)}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    note_html = f"<div class='note'>{_esc(note)}</div>" if note else ""
    body = f"""<div class="panel"><table><thead><tr>{head}</tr></thead>
<tbody>{"".join(body_rows)}</tbody></table>{note_html}</div>"""
    return _base_html(title, body, subtitle=f"共 {len(rows)} 行")


def series_summary(series: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": str(item.get("name") or f"series{index}"),
            **describe(item.get("values") or []),
        }
        for index, item in enumerate(series)
    ]


__all__ = [
    "bar_chart_html",
    "correlation",
    "describe",
    "line_chart_html",
    "pie_chart_html",
    "series_summary",
    "table_html",
]
