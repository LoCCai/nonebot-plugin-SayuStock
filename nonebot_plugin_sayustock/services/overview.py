"""Presentation shared by the group card, web dashboard and model summary."""

from __future__ import annotations

from SayuStock.utils.market import BREADTH_BANDS, BREADTH_DIRECTION
from SayuStock.render.card_widgets import mood_number


def breadth_svg(breadth: dict | None) -> str:
    if not breadth or not breadth.get("counts"):
        return '<p class="breadth-empty">暂缺涨跌分布</p>'
    counts = breadth["counts"]
    bands = list(reversed(BREADTH_BANDS))
    maximum = max(1, *counts.values())
    parts = ['<svg class="band-chart" viewBox="0 0 780 184" role="img" aria-label="全市场涨跌幅分布，单位家">']
    for index, label in enumerate(bands):
        count = counts[label]
        direction = BREADTH_DIRECTION[label]
        x = index * 60 + 30
        height = 118 * count / maximum
        tone = "up" if direction > 0 else "down" if direction < 0 else "flat"
        caption = label if label in ("平", "涨停", "跌停") else label + "%"
        parts.append(
            f'<g data-band="{label}" class="band-{tone}"><title>{caption}：{count} 家</title>'
            f'<rect x="{x - 15}" y="{144 - height:.2f}" width="30" height="{height:.2f}" rx="3"/>'
            f'<text x="{x}" y="{136 - height:.2f}">{count}</text>'
            f'<text x="{x}" y="171">{caption}</text></g>'
        )
    return "".join(parts) + "</svg>"


def sources_text(data: dict) -> str:
    return " · ".join(f"{section} {name}" for section, name in data.get("sources", {}).items())


def summary(data: dict) -> str:
    lines = [f"【A股大盘概览】{data.get('session_label', '')}"]
    for item in data.get("indices", []):
        change = item["change_pct"]
        pct = "—" if change is None else f"{change:+.2f}%"
        lines.append(f"{item['label']} {item['price']}（{pct}）")
    b = data["breadth"]
    if b:
        lines.append(f"上涨 {b['up']} / 平盘 {b['flat']} / 下跌 {b['down']}")
        lines.append("涨跌分布（家） " + "、".join(f"{label} {n}" for label, n in b["counts"].items()))
    else:
        lines.append("暂缺涨跌分布")
    if data["turnover"]:
        lines.append(f"两市成交额 {data['turnover']['amount'] / 1e8:,.0f} 亿　{data['turnover_note']}")
    flow = data["northbound"]
    if flow:
        lines.append(f"北向净流入 {flow['sh_net_yi'] + flow['sz_net_yi']:+,.2f} 亿")
    for label, name in (("行业", "industries"), ("概念", "concepts")):
        for side, direction in (("rise", "领涨"), ("fall", "领跌")):
            rows = data[name][side][:5]
            if rows:
                lines.append(
                    label
                    + direction
                    + "　"
                    + "、".join(
                        f"{row['name']} {row['change_pct']:+.2f}%" if row["change_pct"] is not None else row["name"]
                        for row in rows
                    )
                )
    if data.get("warnings"):
        lines.extend("⚠ " + warning for warning in data["warnings"])
    return "\n".join(lines)


def market_mood(data: dict) -> tuple[str, str | None]:
    for item in data.get("indices", []):
        if item["label"] == "中证全指" and item["change_pct"] is not None:
            change = item["change_pct"]
            return f"市场情绪 · 中证全指 {change:+.2f}%", mood_number(change)
    return "市场情绪暂缺", None


def model_data(data: dict) -> dict:
    snapshot = {key: value for key, value in data.items() if key != "main_flow_uri"}
    snapshot["indices"] = [{k: v for k, v in row.items() if k != "spark"} for row in data["indices"]]
    return snapshot
