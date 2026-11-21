"""Deterministic market port used by tests."""

from __future__ import annotations

from datetime import datetime, timedelta

from SayuStock.utils.market import (
    Quote,
    BoardRow,
    BoardKind,
    SymbolRef,
    AssetClass,
    BreadthBar,
    BoardExtras,
    KlinePeriod,
    KlineSeries,
    ValueSeries,
    RankSnapshot,
    BoardSnapshot,
    IntradayPoint,
    IntradaySeries,
    MarketTurnover,
    NorthboundFlow,
    FinancialSnapshot,
)
from SayuStock.utils.market.enums import ValueKind
from SayuStock.utils.market.models import BreadthBucket


def _symbol(query: str) -> SymbolRef:
    return SymbolRef(
        code="600519" if "茅台" in query or "600519" in query else "000001",
        name="贵州茅台" if "茅台" in query or "600519" in query else "平安银行",
        asset_class=AssetClass.EQUITY,
        exchange="SH" if "600519" in query else "SZ",
        provider_symbol="1.600519" if "600519" in query else "0.000001",
        sec_type="沪A",
    )


def _quote(query: str) -> Quote:
    return Quote(
        symbol=_symbol(query),
        price=1500.0,
        open=1490.0,
        high=1512.0,
        low=1480.0,
        prev_close=1470.0,
        change_pct=2.04,
        change_amount=30.0,
        volume=1_000_000.0,
        amount=1_500_000_000.0,
        turnover_rate=1.2,
        pe=30.0,
        pb=8.0,
        market_cap=1_800_000_000_000.0,
        float_market_cap=1_800_000_000_000.0,
        industry="白酒",
        limit_up=1617.0,
        limit_down=1323.0,
        as_of=datetime(2026, 11, 20, 15, 0),
        provider="fake",
    )


class FakeMarket:
    async def resolve(self, query: str):
        return _symbol(query)

    async def quote(self, query: str):
        return _quote(query)

    async def quotes(self, queries):
        return [_quote(query) for query in queries]

    async def intraday(self, query: str, *, ndays: int = 1):
        start = datetime(2026, 11, 20, 9, 30)
        points = tuple(
            IntradayPoint(
                ts=start + timedelta(minutes=index),
                price=1490.0 + index * 0.5,
                open=1490.0,
                high=1491.0,
                low=1489.0,
                volume=10_000.0,
                amount=15_000_000.0,
                avg_price=1490.0,
            )
            for index in range(30)
        )
        return IntradaySeries(
            symbol=_symbol(query),
            points=points,
            quote=_quote(query),
            ndays=ndays,
            provider="fake",
        )

    async def kline(self, query: str, period: KlinePeriod, *, start=None, end=None):
        start_at = datetime(2026, 1, 1, 15, 0)
        bars = []
        for index in range(140):
            close = 1000.0 + index * 3.0
            bars.append(
                type(
                    "Bar",
                    (),
                    {
                        "ts": start_at + timedelta(days=index),
                        "open": close - 2,
                        "high": close + 5,
                        "low": close - 6,
                        "close": close,
                        "volume": 1_000_000.0 + index * 1000,
                        "amount": 1e9,
                        "amplitude": 1.1,
                        "change_pct": 0.3,
                        "change_amount": 3.0,
                        "turnover_rate": 1.0,
                    },
                )
            )
        return KlineSeries(
            symbol=_symbol(query),
            period=period,
            bars=tuple(bars),
            adjusted=True,
            provider="fake",
        )

    async def board(self, kind, *, sector=None, limit=None, sort_asc=False):
        names = ["白酒", "半导体", "银行", "医药", "新能源", "煤炭"]
        rows = tuple(
            BoardRow(
                code=f"BK{index:04d}",
                name=name,
                price=1000.0 + index,
                change_pct=3.0 - index * 0.8,
                amount=1e9,
                market_cap=1e12,
                industry=name,
                lead_name=f"龙头{index}",
                lead_change_pct=5.0,
                extras=BoardExtras(pe=20.0, up_count=10, down_count=2),
            )
            for index, name in enumerate(names)
        )
        return BoardSnapshot(
            kind=BoardKind.INDUSTRY,
            title=str(kind),
            rows=rows,
            provider="fake",
        )

    async def rank_list(self, rank_by, *, limit=20, high_first=None):
        return RankSnapshot(
            rank_by=str(rank_by),
            rank_by_label="测试排行",
            unit_hint="",
            high_first=True,
            caveat="测试数据",
            rows=(),
            provider="fake",
        )

    async def hotmap(self):
        return await self.board("hotmap")

    async def sector_menu(self, kind):
        return {"白酒": "BK0001"}

    async def breadth(self):
        counts = {
            "涨停": 10,
            "5~10": 20,
            "3~5": 30,
            "2~3": 40,
            "1~2": 50,
            "0~1": 60,
            "平": 5,
            "0~-1": 70,
            "-1~-2": 80,
            "-2~-3": 90,
            "-3~-5": 100,
            "-5~-10": 110,
            "跌停": 5,
        }
        return BreadthBar(
            buckets=tuple(BreadthBucket(label, count) for label, count in counts.items()),
            provider="fake",
        )

    async def market_turnover(self):
        return MarketTurnover(
            prev_amount=1e12,
            amount=1.2e12,
            last_trade_date=datetime(2026, 11, 20, 15, 0),
            provider="fake",
        )

    async def northbound(self):
        return NorthboundFlow(sh_net_yi=12.5, sz_net_yi=-3.5, provider="fake")

    async def valuation_series(self, query: str, kind):
        kind_value = kind.value if hasattr(kind, "value") else str(kind)
        return ValueSeries(symbol=_symbol(query), kind=ValueKind(kind_value), points=(), provider="fake")

    async def financial_snapshot(self, code: str):
        return FinancialSnapshot(
            code=code,
            report_date="2026-09-30",
            roe=20.0,
            revenue_yoy=10.0,
            profit_yoy=12.0,
            gross_margin=80.0,
            net_margin=50.0,
            debt_ratio=20.0,
            eps=30.0,
            bps=180.0,
            net_interest_margin=None,
            industry_type="standard",
            missing_fields=(),
            provider="fake",
        )

    async def batch_quotes(self, symbols):
        return {"600519": _quote("600519")}

    async def ipo_calendar(self, market):
        return []
