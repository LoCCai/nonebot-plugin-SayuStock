"""Framework-independent market scans shared by GsCore and QWeb."""
from datetime import datetime

from .market import get_market, is_market_error
from .get_OKX import get_all_crypto_price


async def sector_scan(top_n: int = 15, sector_type: str = "industry", include_all: bool | None = None) -> str:
    import json as _json
    import asyncio

    market = get_market()
    board_kind = "行业板块" if sector_type == "industry" else "概念板块"
    detail_n = max(int(top_n), 1)
    # 行业默认全表；概念默认两端明细（概念 300+ 防 token 爆炸）
    use_all: bool = (sector_type == "industry") if include_all is None else bool(include_all)
    out: dict[str, object] = {
        "sector_type": sector_type,
        "count": 0,
        "ranked": [],
        "top_rise": [],
        "top_fall": [],
        "hot_stocks": [],
        "detail_n": detail_n,
        "include_all": use_all,
    }

    def _row_compact(r: object) -> dict[str, object]:
        from ..utils.market.models import BoardRow

        assert isinstance(r, BoardRow)
        extras = r.extras
        return {
            "name": r.name,
            "code": r.code,
            "change_pct": r.change_pct if r.change_pct is not None else 0,
            "up_count": extras.up_count if extras is not None else None,
            "down_count": extras.down_count if extras is not None else None,
            "lead_stock": r.lead_name or "",
            "lead_stock_code": extras.lead_code if extras is not None else "",
            "lead_stock_pct": r.lead_change_pct,
        }

    async def _top_codes(board_code: str) -> list[str]:
        snap = await market.board(board_code, limit=3, sort_asc=False)
        if is_market_error(snap):
            return []
        return [r.code for r in snap.rows[:3] if r.code]

    try:
        from ..utils.market.models import BoardRow

        # 一次拉全量（limit=None → provider 分页 is_loop），客户端按涨跌幅排序，
        # 避免旧逻辑只拉 top_n*3 再截两端、中游板块对 agent 不可见。
        full_snap = await market.board(board_kind, limit=None, sort_asc=False)
        if is_market_error(full_snap):
            out["_error"] = str(full_snap)
        else:
            rows_list: list[BoardRow] = [r for r in list(full_snap.rows) if isinstance(r, BoardRow)]
            rows_list.sort(
                key=lambda r: r.change_pct if r.change_pct is not None else 0.0,
                reverse=True,
            )
            compact_all = [_row_compact(r) for r in rows_list]
            out["count"] = len(compact_all)
            if use_all:
                out["ranked"] = compact_all

            rise_rows = [dict(x) for x in compact_all[:detail_n]]
            fall_rows = [dict(x) for x in compact_all[-detail_n:]] if compact_all else []
            fall_rows.reverse()  # 跌幅从深到浅，与旧 top_fall 语义一致

            # 仅两端补成分股 TOP3，避免对全市场每个板块再打 N 次 board
            picked_codes: list[str] = []
            for r in rise_rows + fall_rows:
                if "code" not in r:
                    continue
                code_s = str(r["code"] or "")
                if code_s and code_s not in picked_codes:
                    picked_codes.append(code_s)
            if picked_codes:
                code_lists = await asyncio.gather(*[_top_codes(c) for c in picked_codes])
                code_to_top: dict[str, list[str]] = dict(zip(picked_codes, code_lists))
                for r in rise_rows + fall_rows:
                    code_key = str(r["code"] if "code" in r else "")
                    r["top_stocks"] = code_to_top[code_key] if code_key in code_to_top else []
            out["top_rise"] = rise_rows
            out["top_fall"] = fall_rows
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError, AssertionError) as e:
        out["_error"] = str(e)

    try:
        from ..utils.market import RankBy

        # 真成交额榜（勿对涨跌幅 board 子集再按 amount 排序）
        hot = await market.rank_list(RankBy.AMOUNT, limit=5)
        if not is_market_error(hot):
            out["hot_stocks"] = [
                {
                    "code": r.code,
                    "name": r.name,
                    "price": r.price if r.price is not None else 0,
                    "change_pct": r.change_pct if r.change_pct is not None else 0,
                    "amount_yi": (r.amount or 0) / 1e8,
                }
                for r in hot.rows
            ]
    except (OSError, TimeoutError, RuntimeError, ValueError, TypeError) as e:
        out["_hot_error"] = str(e)

    return _json.dumps(out, ensure_ascii=False, default=str)


async def crypto_snapshot() -> str:
    # 语义化：经 MarketDataPort/OKX adapter
    symbols = ["BTC", "ETH", "SOL", "DOGE", "BNB"]
    market = get_market()
    # [as_of=…] 时效契约标记（框架方案七）：实时币价为新鲜读数
    as_of = datetime.now().strftime("%Y-%m-%d %H:%M")
    result = f"[as_of={as_of}|source=okx]\n【加密货币】\n"
    any_ok = False
    for name in symbols:
        q = await market.quote(name)
        if is_market_error(q):
            continue
        any_ok = True
        chg = f"{q.change_pct}%" if q.change_pct is not None else "N/A"
        result += f"{name}: ${q.price} ({chg})\n"
    if not any_ok:
        data = await get_all_crypto_price()
        if not data or not isinstance(data, dict):
            return "获取加密货币数据失败"
        for name, d in data.items():
            if not isinstance(d, dict):
                continue
            price = d["price"] if "price" in d else "N/A"
            change = d["change_pct"] if "change_pct" in d else "N/A"
            result += f"{name}: ${price} ({change}%)\n"
    return result
