import asyncio
import datetime
from io import BytesIO
from typing import Literal, Optional
from dataclasses import replace, dataclass

from PIL import Image

from gsuid_core.logger import logger
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..render import RenderError
from ..utils.eastmoney import EASTMONEY_REQUESTER, EASTMONEY_VALUE_NAME_MAP, EastMoneyStockItem
from ..render.valuation import prepare_valuation
from ..render.chart_page import page_html, render_chart
from ..utils.market.enums import ValueKind
from ..utils.market.models import ValueSeries
from ..utils.market.display import source_label

ValueType = Literal["pe", "pb", "dy"]
BotSendContent = str | bytes
VALUE_NAME_MAP: dict[ValueType, str] = {
    "pe": EASTMONEY_VALUE_NAME_MAP["pe"],
    "pb": EASTMONEY_VALUE_NAME_MAP["pb"],
    "dy": EASTMONEY_VALUE_NAME_MAP["dy"],
}


@dataclass(frozen=True, slots=True)
class ValueComparison:
    """多标的估值历史对比的全部展示数据。"""

    kind: ValueType
    label: str
    series: tuple[ValueSeries, ...]
    failures: tuple[str, ...]

    @property
    def provider(self) -> str | None:
        providers = [item.provider for item in self.series if item.provider]
        return providers[0] if providers else None


async def load_value_comparison(
    _input: str,
    _type: ValueType = "pe",
    start_time: Optional[datetime.datetime] = None,
    end_time: Optional[datetime.datetime] = None,
) -> ValueComparison | str:
    """抓取多标的估值历史并裁到区间；无数据时返回明确的中文错误。"""
    stock_list = await _parse_stock_input(_input)
    if not stock_list:
        return "❌未识别到有效股票，请输入股票代码或名称，例如：市盈率对比 贵州茅台 五粮液"

    tasks = [fetch_eastmoney_value_series(item, _type) for item in stock_list]
    raw_series = await asyncio.gather(*tasks, return_exceptions=True)

    series_list: list[ValueSeries] = []
    failed: list[str] = []
    for item, result in zip(stock_list, raw_series):
        item_desc = f"{item['name']}({item['code']})"
        if isinstance(result, BaseException):
            logger.warning(f"[SayuStock] 获取{item_desc}估值数据失败: {result}")
            failed.append(item_desc)
            continue
        value_series = result
        if value_series is None or not value_series.points:
            failed.append(item_desc)
            continue
        series_list.append(value_series)

    if not series_list:
        return f"❌未获取到{VALUE_NAME_MAP[_type]}历史数据，可能该标的不支持东方财富估值接口。"

    if start_time is not None or end_time is not None:
        start_day = start_time.date() if start_time is not None else None
        end_day = end_time.date() if end_time is not None else None
        filtered: list[ValueSeries] = []
        for series in series_list:
            points = tuple(
                point
                for point in series.points
                if (start_day is None or point.day >= start_day) and (end_day is None or point.day <= end_day)
            )
            if points:
                filtered.append(replace(series, points=points))
            else:
                failed.append(_series_label(series))
        series_list = filtered
    if not series_list:
        return f"❌在指定时间范围内未获取到{VALUE_NAME_MAP[_type]}历史数据，请尝试更宽的时间范围或不指定时间。"

    return ValueComparison(_type, VALUE_NAME_MAP[_type], tuple(series_list), tuple(failed))


def value_comparison_text(view: ValueComparison) -> str:
    """估值对比的语义文字；AI 与群消息共用同一份。"""
    value_name = view.label
    lines = [
        f"【{value_name}历史走势对比】",
        f"数据来源：{source_label(*(item.provider for item in view.series))}",
        f"对比标的数量：{len(view.series)}",
    ]
    latest_values: list[tuple[str, float]] = []
    for item in view.series:
        values = [point.value for point in item.points]
        first, latest = item.points[0], item.points[-1]
        change = latest.value - first.value
        change_rate = change / first.value * 100 if first.value else 0.0
        label = _series_label(item)
        latest_values.append((label, latest.value))
        lines.append(
            f"- {label}: 最新{value_name}={latest.value:.2f}({latest.day}), "
            f"区间起点={first.value:.2f}({first.day}), 区间变化={change:+.2f}({change_rate:+.2f}%), "
            f"区间最低={min(values):.2f}, 最高={max(values):.2f}, "
            f"均值={sum(values) / len(values):.2f}, 样本数={len(values)}"
        )
    if latest_values:
        ranked = sorted(latest_values, key=lambda item: item[1], reverse=True)
        lines.append("最新估值从高到低：" + "、".join(f"{name} {value:.2f}" for name, value in ranked))
        high_name, high_value = ranked[0]
        low_name, low_value = ranked[-1]
        lines.append(
            f"当前最高：{high_name} {high_value:.2f}；当前最低：{low_name} {low_value:.2f}；"
            f"高低差：{high_value - low_value:.2f}"
        )
    if view.failures:
        lines.append(f"未获取到数据: {'、'.join(view.failures)}")
    return "\n".join(lines)


def value_comparison_html(view: ValueComparison) -> str:
    page = prepare_valuation(list(view.series), ValueKind(view.kind), view.label, failures=list(view.failures))
    if isinstance(page, str):
        return page
    return page_html(page, stock_card=True)


async def get_eastmoney_pepb_compare(
    _input: str,
    _type: ValueType = "pe",
    start_time: Optional[datetime.datetime] = None,
    end_time: Optional[datetime.datetime] = None,
) -> BotSendContent:
    """获取估值历史并渲染对比图。"""
    view = await load_value_comparison(_input, _type, start_time, end_time)
    if isinstance(view, str):
        return view

    ai_return(value_comparison_text(view))
    try:
        image = await draw_value_compare_chart(list(view.series), _type, failures=list(view.failures))
    except RenderError as error:
        logger.error(f"[SayuStock] 估值图绘制失败: {error}")
        return "估值图生成失败，请稍后重试。"
    if isinstance(image, str):
        return image
    with Image.open(BytesIO(image)) as pixels:
        return await convert_img(pixels)


async def _fetch_sector_codes(board_code: str) -> list[str]:
    """板块代码 → 成分股代码（前13）。"""
    from ..utils.market import get_market, is_market_error

    snap = await get_market().board(board_code, limit=13, sort_asc=False)
    if is_market_error(snap):
        return []
    return [r.code for r in snap.rows if r.code]


async def _parse_stock_input(_input: str) -> list[EastMoneyStockItem]:
    from ..utils.market import get_market, is_market_error

    parts = [p.strip() for p in _input.replace("，", " ").replace(",", " ").split() if p.strip()]
    expanded: list[str] = []
    market = get_market()
    for part in parts:
        q = await market.quote(part)
        if not is_market_error(q) and q.industry and "板块" in (q.symbol.name or ""):
            codes = await _fetch_sector_codes(q.symbol.code or part)
            expanded.extend(codes if codes else [part])
        else:
            # 菜单/板块名
            menu = await market.sector_menu("industry")
            if not is_market_error(menu) and part in menu:
                codes = await _fetch_sector_codes(menu[part])
                expanded.extend(codes if codes else [part])
            else:
                menu_c = await market.sector_menu("concept")
                if not is_market_error(menu_c) and part in menu_c:
                    codes = await _fetch_sector_codes(menu_c[part])
                    expanded.extend(codes if codes else [part])
                else:
                    expanded.append(part)

    return await EASTMONEY_REQUESTER.parse_stock_input(" ".join(expanded))


async def fetch_eastmoney_value_series(stock: EastMoneyStockItem, _type: ValueType) -> ValueSeries | None:
    from ..utils.market import get_market, is_market_error

    logger.info(f"[SayuStock] 获取东方财富{VALUE_NAME_MAP[_type]}历史估值: {stock['code']}")
    series = await get_market().valuation_series(stock["code"], ValueKind(_type))
    if is_market_error(series):
        logger.warning(series.message)
        return None
    return replace(series, points=tuple(sorted(series.points, key=lambda point: point.day))) if series.points else None


async def draw_value_compare_chart(
    series_list: list[ValueSeries], _type: ValueType, *, failures: list[str] | None = None
) -> bytes | str:
    page = await asyncio.to_thread(
        prepare_valuation, series_list, ValueKind(_type), VALUE_NAME_MAP[_type], failures=failures
    )
    return await render_chart(page)


def _series_label(series: ValueSeries) -> str:
    return f"{series.symbol.display_name}({series.symbol.code})"


def _ai_return_value_compare(view: ValueComparison) -> None:
    try:
        ai_return(value_comparison_text(view))
    except Exception as error:
        logger.warning(f"[SayuStock] ai_return {view.label}对比数据提取失败: {error}")
