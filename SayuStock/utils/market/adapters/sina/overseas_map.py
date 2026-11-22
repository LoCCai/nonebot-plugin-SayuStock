"""港美股云图的备用样本。面积统一取市值，覆盖范围随数据返回。"""

from __future__ import annotations

import math
import asyncio

from .client import _get_json
from ...enums import BoardKind
from ...errors import MarketError, empty_error, network_error
from ...models import BoardRow, BoardSnapshot
from ....stock.utils import async_file_cache
from ..tencent.client import fetch_qt_lines

_US = "https://stock.finance.sina.com.cn/usstock/api/json_v2.php/US_CategoryService.getList"
_HK = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHKStockData"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


@async_file_cache(market="us-cloud", sector="{page}", suffix="json", minutes=5)
async def _us_page(page: int) -> list[dict] | str:
    raw = await _get_json(_US, {"page": str(page), "num": "20", "sort": "mktcap", "asc": "0"}, timeout_s=6)
    if isinstance(raw, MarketError):
        return raw.message
    rows = raw.get("data") if isinstance(raw, dict) else None
    return rows if isinstance(rows, list) and rows else "新浪美股市值榜暂不可用"


@async_file_cache(market="hk-cloud", sector="active-equities", suffix="json", minutes=5)
async def _hk_quotes() -> dict[str, str] | str:
    params = {"node": "qbgg_hk", "num": "60", "page": "1", "sort": "amount", "asc": "0"}
    rank = await _get_json(_HK, params, timeout_s=6)
    if not isinstance(rank, list):
        return "新浪港股成交额榜暂不可用"
    symbols = [
        f"r_hk{r['symbol']}"
        for r in rank
        if isinstance(r, dict) and isinstance(r.get("symbol"), str) and len(r["symbol"]) == 5 and r["symbol"].isdigit()
    ]
    quotes = await fetch_qt_lines(symbols)
    if isinstance(quotes, MarketError):
        return quotes.message
    return {key: quotes[key] for key in symbols if key in quotes} or "腾讯港股行情暂不可用"


async def overseas_map(market: str) -> BoardSnapshot | MarketError:
    rows: dict[str, BoardRow] = {}
    if market == "美股":
        provider = "sina"
        coverage = "新浪市值榜前 100 · 有效样本"
        pages = await asyncio.gather(*(_us_page(n) for n in range(1, 6)))
        for page in pages:
            if isinstance(page, str):
                return network_error(page, provider=provider)
            for item in page:
                if not isinstance(item, dict):
                    continue
                code = item.get("symbol")
                cap, pct = _number(item.get("mktcap")), _number(item.get("chg"))
                if isinstance(code, str) and code and cap and cap > 0 and pct is not None:
                    rows[code] = BoardRow(
                        code,
                        item.get("cname") or code,
                        _number(item.get("price")),
                        pct,
                        None,
                        cap,
                        item.get("category") or market,
                        None,
                        None,
                    )
    else:
        provider = "sina+tencent"
        coverage = "新浪成交额榜前 60 · 港元股票有效样本"
        quotes = await _hk_quotes()
        if isinstance(quotes, str):
            return network_error(quotes, provider=provider)
        for key, line in quotes.items():
            fields = line.split("~")
            if len(fields) <= 75 or fields[63] != "GP" or fields[75] != "HKD" or key != f"r_hk{fields[2]}":
                continue
            cap, pct = _number(fields[45]), _number(fields[32])
            if cap and cap > 0 and pct is not None:
                rows[fields[2]] = BoardRow(
                    fields[2], fields[1], _number(fields[3]), pct, _number(fields[37]), cap * 1e8, market, None, None
                )
    if not rows:
        return empty_error(f"{market}暂缺有效市值与涨跌幅", provider=provider)
    return BoardSnapshot(BoardKind.HOTMAP, market, tuple(rows.values()), provider, coverage)
