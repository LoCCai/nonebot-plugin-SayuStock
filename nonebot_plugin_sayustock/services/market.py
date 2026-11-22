"""Async market-data service backed by the original SayuStock market layer."""

from __future__ import annotations

import time
import base64
import asyncio
from typing import Any
from datetime import date, datetime
from zoneinfo import ZoneInfo
from dataclasses import asdict, is_dataclass

from nonebot.log import logger

from ..compat import bootstrap

bootstrap.install()

from SayuStock.utils.market import (  # noqa: E402
    Quote,
    RankBy,
    IpoMarket,
    ValueKind,
    KlinePeriod,
    BoardSnapshot,
    MarketTurnover,
    NorthboundFlow,
    get_market,
    breadth_counts,
    breadth_up_down,
    is_market_error,
)
from SayuStock.utils.sparkline import sparkline_from_series  # noqa: E402
from SayuStock.utils.market.models import (  # noqa: E402
    Bar,
    BreadthBar,
    KlineSeries,
    IntradaySeries,
)
from SayuStock.utils.market.display import source_label  # noqa: E402

INDEX_SPECS: tuple[tuple[str, str], ...] = (
    ("上证指数", "1.000001"),
    ("深证成指", "0.399001"),
    ("创业板指", "0.399006"),
    ("沪深300", "1.000300"),
    ("中证500", "1.000905"),
    ("科创50", "1.000688"),
    ("中证1000", "1.000852"),
    ("中证A500", "1.000510"),
    ("中证全指", "1.000985"),
    ("科创综指", "1.000680"),
    ("中证2000", "2.932000"),
    ("北证50", "0.899050"),
)
OPTIONAL_INDEX_SPECS: tuple[tuple[str, str], ...] = (
    ("黄金9999", "118.AU9999"),
    ("三十债主连", "220.TLM"),
)

PERIOD_ALIASES: dict[str, KlinePeriod] = {
    "5k": KlinePeriod.M5,
    "5m": KlinePeriod.M5,
    "15k": KlinePeriod.M15,
    "30k": KlinePeriod.M30,
    "60k": KlinePeriod.M60,
    "60m": KlinePeriod.M60,
    "日k": KlinePeriod.D1,
    "日线": KlinePeriod.D1,
    "day": KlinePeriod.D1,
    "周k": KlinePeriod.W1,
    "周线": KlinePeriod.W1,
    "week": KlinePeriod.W1,
    "月k": KlinePeriod.MON1,
    "月线": KlinePeriod.MON1,
    "month": KlinePeriod.MON1,
    "季k": KlinePeriod.Q1,
    "半年k": KlinePeriod.H1,
    "半年线": KlinePeriod.H1,
    "年k": KlinePeriod.Y1,
    "year": KlinePeriod.Y1,
}


class MarketServiceError(RuntimeError):
    """A user-presentable market data failure."""


def normalize_period(raw: str) -> KlinePeriod:
    key = (raw or "").strip().lower()
    if not key:
        return KlinePeriod.D1
    if key in PERIOD_ALIASES:
        return PERIOD_ALIASES[key]
    try:
        return KlinePeriod(key)
    except ValueError as error:
        raise MarketServiceError(f"不支持的周期：{raw}") from error


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def quote_dict(quote: Quote) -> dict[str, Any]:
    return {
        "code": quote.symbol.code,
        "name": quote.symbol.name,
        "display_name": quote.symbol.display_name,
        "asset_class": quote.symbol.asset_class.value,
        "exchange": quote.symbol.exchange,
        "price": quote.price,
        "open": quote.open,
        "high": quote.high,
        "low": quote.low,
        "prev_close": quote.prev_close,
        "change_pct": quote.change_pct,
        "change_amount": quote.change_amount,
        "volume": quote.volume,
        "amount": quote.amount,
        "turnover_rate": quote.turnover_rate,
        "pe": quote.pe,
        "pb": quote.pb,
        "market_cap": quote.market_cap,
        "float_market_cap": quote.float_market_cap,
        "industry": quote.industry,
        "limit_up": quote.limit_up,
        "limit_down": quote.limit_down,
        "as_of": _jsonable(quote.as_of),
        "provider": quote.provider,
    }


class _TTLCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, key: str, ttl: float, loader: Any) -> Any:
        now = time.monotonic()
        hit = self._data.get(key)
        if hit is not None and now - hit[0] <= ttl:
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            hit = self._data.get(key)
            if hit is not None and now - hit[0] <= ttl:
                return hit[1]
            value = await loader()
            self._data[key] = (time.monotonic(), value)
            if len(self._data) > 512:
                cutoff = time.monotonic() - 600
                self._data = {k: v for k, v in self._data.items() if v[0] >= cutoff} or dict(
                    list(self._data.items())[-128:]
                )
            return value

    def clear(self) -> None:
        self._data.clear()


class MarketService:
    """High-level, protocol-agnostic market API."""

    def __init__(self) -> None:
        self.market = get_market()
        self._cache = _TTLCache()

    async def resolve(self, query: str) -> Any:
        try:
            return await self.market.resolve(query)
        except Exception as error:
            logger.warning("[SayuStock] 标的解析失败 {}: {}", query, error)
            return None

    async def quote(self, query: str) -> dict[str, Any]:
        async def load() -> dict[str, Any]:
            result = await self.market.quote(query)
            if is_market_error(result):
                raise MarketServiceError(result.message)
            return quote_dict(result)

        try:
            return await self._cache.get(f"quote:{query}", 8, load)
        except MarketServiceError:
            raise
        except Exception as error:
            raise MarketServiceError(f"行情获取失败：{error}") from error

    async def quotes(self, queries: list[str]) -> list[dict[str, Any] | None]:
        results = await asyncio.gather(*(self.quote(query) for query in queries), return_exceptions=True)
        return [None if isinstance(item, BaseException) else item for item in results]

    async def intraday(self, query: str, *, ndays: int = 1) -> dict[str, Any]:
        result = await self.market.intraday(query, ndays=ndays)
        if is_market_error(result):
            raise MarketServiceError(result.message)
        assert isinstance(result, IntradaySeries)
        return {
            "symbol": _jsonable(result.symbol),
            "ndays": result.ndays,
            "provider": result.provider,
            "quote": quote_dict(result.quote) if result.quote else None,
            "points": [
                {
                    "ts": point.ts.isoformat(timespec="minutes"),
                    "price": point.price,
                    "avg_price": point.avg_price,
                    "volume": point.volume,
                    "amount": point.amount,
                }
                for point in result.points
            ],
        }

    async def kline(self, query: str, period: str | KlinePeriod = KlinePeriod.D1) -> dict[str, Any]:
        _, data = await self.kline_snapshot(query, period)
        return data

    async def kline_snapshot(
        self, query: str, period: str | KlinePeriod
    ) -> tuple[KlineSeries, dict[str, Any]]:
        resolved = period if isinstance(period, KlinePeriod) else normalize_period(period)

        async def load() -> tuple[KlineSeries, dict[str, Any]]:
            result = await self.market.kline(query, resolved)
            if is_market_error(result):
                raise MarketServiceError(result.message)
            assert isinstance(result, KlineSeries)
            if not result.bars:
                raise MarketServiceError(f"{query} 的所选周期暂无 K 线数据")
            frame = self._kline_frame(result)
            from SayuStock.utils.indicators import compute_indicators

            indicators = compute_indicators(frame)
            return result, {
                "symbol": _jsonable(result.symbol),
                "period": result.period.value,
                "as_of": result.as_of,
                "adjusted": result.adjusted,
                "provider": result.provider,
                "bars": [
                    {
                        "ts": bar.ts.isoformat(timespec="minutes"),
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "amount": bar.amount,
                        "change_pct": bar.change_pct,
                        "turnover_rate": bar.turnover_rate,
                    }
                    for bar in result.bars
                ],
                "indicators": _jsonable(indicators),
            }

        cache_key = f"kline:{query}:{resolved.value}"
        return await self._cache.get(cache_key, 20, load)

    @staticmethod
    def _kline_frame(series: KlineSeries) -> Any:
        import pandas as pd

        bars: tuple[Bar, ...] = series.bars
        return pd.DataFrame(
            {
                "open": [bar.open for bar in bars],
                "high": [bar.high for bar in bars],
                "low": [bar.low for bar in bars],
                "close": [bar.close for bar in bars],
                "volume": [bar.volume for bar in bars],
                "amount": [bar.amount for bar in bars],
                "turnover_rate": [bar.turnover_rate for bar in bars],
            }
        )

    async def technical(self, query: str, period: str = "日k") -> dict[str, Any]:
        from .studywork import technical_report

        series, data = await self.kline_snapshot(query, period)
        indicators = data["indicators"]
        score, signals, risks = self._score(indicators)
        report = technical_report(series)
        return {
            "technical_report": None if isinstance(report, str) else report.to_dict(),
            "ok": True,
            "symbol": data["symbol"],
            "period": data["period"],
            "as_of": data["as_of"],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "adjusted": data["adjusted"],
            "price": data["bars"][-1]["close"] if data["bars"] else None,
            "indicators": indicators,
            "score": score,
            "signals": signals,
            "risks": risks,
            "bars": data["bars"][-120:],
            "provider": data["provider"],
        }

    @staticmethod
    def _score(ind: dict[str, Any]) -> tuple[float, list[str], list[str]]:
        score = 0.0
        signals: list[str] = []
        risks: list[str] = []
        close = ind.get("last_close")
        ma20 = ind.get("ma20")
        ma60 = ind.get("ma60")
        if close is not None and ma20 is not None:
            if close > ma20:
                score += 0.18
                signals.append("收盘价站上 MA20")
            else:
                score -= 0.18
                risks.append("收盘价跌破 MA20")
        if ma20 is not None and ma60 is not None:
            if ma20 > ma60:
                score += 0.14
                signals.append("MA20 位于 MA60 上方（中期多头）")
            else:
                score -= 0.14
                risks.append("MA20 位于 MA60 下方（中期空头）")
        if ind.get("macd_golden_cross_in_3d"):
            score += 0.16
            signals.append("MACD 三日内有金叉")
        if ind.get("macd_death_cross_in_3d"):
            score -= 0.16
            risks.append("MACD 三日内有死叉")
        if ind.get("kdj_golden_cross_in_3d"):
            score += 0.12
            signals.append("KDJ 三日内有金叉")
        if ind.get("kdj_death_cross_in_3d"):
            score -= 0.12
            risks.append("KDJ 三日内有死叉")
        rsi = ind.get("rsi12")
        if isinstance(rsi, (int, float)):
            if rsi >= 80:
                score -= 0.12
                risks.append(f"RSI12={rsi:.1f}，处于超买区")
            elif rsi <= 20:
                score += 0.10
                signals.append(f"RSI12={rsi:.1f}，处于超卖区")
        cmf = ind.get("cmf20")
        if isinstance(cmf, (int, float)):
            if cmf > 0:
                score += 0.10
                signals.append("CMF20 为正，资金面偏流入")
            else:
                score -= 0.10
                risks.append("CMF20 为负，资金面偏流出")
        atr = ind.get("atr_pct")
        if isinstance(atr, (int, float)) and atr > 5:
            risks.append(f"ATR={atr:.2f}%，波动率偏高")
        return round(max(-1.0, min(1.0, score)), 3), signals, risks

    async def _overview_index(self, spec: str) -> dict[str, Any] | None:
        try:
            series = await self.market.intraday(spec, ndays=1)
        except Exception as error:
            logger.debug("[SayuStock] 指数分时 {}: {}", spec, error)
            series = None
        quote = series.quote if isinstance(series, IntradaySeries) else None
        if not isinstance(quote, Quote):
            quote = await self.market.quote(spec)
        if not isinstance(quote, Quote):
            return None
        spark = ""
        if isinstance(series, IntradaySeries):
            spark = await asyncio.to_thread(sparkline_from_series, series, width=170, height=46)
        return {**quote_dict(quote), "spark": spark}

    async def main_flow_uri(self) -> str:
        from aiohttp import ClientError

        from SayuStock.utils.market.adapters.composite import CompositeMarketData

        if not isinstance(self.market, CompositeMarketData):
            return ""
        async def load() -> str:
            try:
                png = await asyncio.wait_for(self.market.main_flow_image(), timeout=5)
            except (ClientError, OSError, ValueError, asyncio.TimeoutError) as error:
                logger.debug("[SayuStock] 主力资金图片暂缺: {}", error)
                return ""
            return "data:image/png;base64," + base64.b64encode(png).decode("ascii") if png else ""
        return await self._cache.get("market-flow-png", 90, load)

    async def overview(self, *, include_flow: bool = False) -> dict[str, Any]:
        snapshot = self._cache.get("market-overview", 45, self._overview_snapshot)
        if not include_flow:
            return await snapshot
        data, flow = await asyncio.gather(snapshot, self.main_flow_uri())
        return {**data, "main_flow_uri": flow}

    async def _overview_snapshot(self) -> dict[str, Any]:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        specs = (*INDEX_SPECS, *OPTIONAL_INDEX_SPECS)
        jobs = [self._overview_index(spec) for _, spec in specs]
        for name in ("行业板块", "概念板块"):
            jobs.extend(self.market.board(name, limit=20, sort_asc=asc) for asc in (False, True))
        results = await asyncio.gather(
            *jobs, self.market.breadth(), self.market.market_turnover(), self.market.northbound(),
            return_exceptions=True,
        )
        warnings = []
        indices = []
        for (label, spec), result in zip(specs, results[:len(specs)], strict=True):
            if isinstance(result, dict):
                indices.append({"label": label, "spec": spec, **result})
            else:
                warnings.append(f"{label}报价暂缺")
        rise, fall, concept_rise, concept_fall, bar, turnover, north = results[len(specs):]
        breadth = self._breadth_dict(bar)
        for section, value, required in (
            ("行业领涨", rise, BoardSnapshot), ("行业领跌", fall, BoardSnapshot),
            ("概念领涨", concept_rise, BoardSnapshot), ("概念领跌", concept_fall, BoardSnapshot),
            ("成交额", turnover, MarketTurnover), ("北向资金", north, NorthboundFlow),
        ):
            if not isinstance(value, required) or (isinstance(value, BoardSnapshot) and not value.rows):
                warnings.append(f"{section}数据暂缺")
        if not breadth:
            warnings.append("涨跌分布数据暂缺")
        sources = {}
        groups = {
            "指数": [item["provider"] for item in indices],
            "板块": [b.provider for b in (rise, fall, concept_rise, concept_fall) if isinstance(b, BoardSnapshot)],
            "分布": [bar.provider] if breadth else [],
            "成交额": [turnover.provider] if isinstance(turnover, MarketTurnover) else [],
            "北向": [north.provider] if isinstance(north, NorthboundFlow) else [],
        }
        for section, providers in groups.items():
            if any(providers):
                sources[section] = source_label(*providers)
        trade_date = turnover.last_trade_date if isinstance(turnover, MarketTurnover) else None
        session_label = "行情交易日暂缺"
        if trade_date:
            session_label = f"行情交易日 {trade_date:%Y-%m-%d}"
            if trade_date.date() < now.date():
                session_label += "（最近交易日）"
        note = ""
        if isinstance(turnover, MarketTurnover) and turnover.prev_amount is not None:
            delta = turnover.amount - turnover.prev_amount
            note = ("放量" if delta > 0 else "缩量") + f" {abs(delta) / 1e8:,.0f} 亿" if delta else "成交额持平"
        return {
            "ok": bool(indices or breadth or any(isinstance(b, BoardSnapshot) and b.rows for b in
                                                (rise, fall, concept_rise, concept_fall)) or
                       isinstance(turnover, MarketTurnover) or isinstance(north, NorthboundFlow)),
            "as_of": now.isoformat(timespec="seconds"), "session_label": session_label,
            "turnover_note": note, "indices": indices, "sources": sources,
            "industries": {"rise": self._board_rows(rise), "fall": self._board_rows(fall)},
            "concepts": {"rise": self._board_rows(concept_rise), "fall": self._board_rows(concept_fall)},
            "breadth": breadth,
            "turnover": _jsonable(turnover) if isinstance(turnover, MarketTurnover) else None,
            "northbound": _jsonable(north) if isinstance(north, NorthboundFlow) else None,
            "warnings": warnings, "provider": sources.get("指数"),
        }

    @staticmethod
    def _breadth_dict(bar: Any) -> dict[str, Any] | None:
        if not isinstance(bar, BreadthBar) or not bar.buckets:
            return None
        counts = breadth_counts(bar)
        up, down = breadth_up_down(counts)
        return {"counts": counts, "up": up, "down": down, "flat": counts["平"], "limit_up": counts["涨停"],
                "limit_down": counts["跌停"], "provider": bar.provider}

    @staticmethod
    def _board_rows(snapshot: Any) -> list[dict[str, Any]]:
        if not isinstance(snapshot, BoardSnapshot):
            return []
        return [
            {
                "code": row.code,
                "name": row.name,
                "price": row.price,
                "change_pct": row.change_pct,
                "amount": row.amount,
                "lead_name": row.lead_name,
                "lead_change_pct": row.lead_change_pct,
                "fall_name": row.fall_name,
                "fall_change_pct": row.fall_change_pct,
                "up_count": row.extras.up_count if row.extras else None,
                "down_count": row.extras.down_count if row.extras else None,
            }
            for row in snapshot.rows
        ]

    async def board(self, kind: str = "industry", *, limit: int = 20, falling: bool = False) -> dict[str, Any]:
        snapshot = await self.market.board(kind, limit=limit, sort_asc=falling)
        if is_market_error(snapshot):
            raise MarketServiceError(snapshot.message)
        assert isinstance(snapshot, BoardSnapshot)
        return {
            "ok": True,
            "kind": snapshot.kind.value,
            "title": snapshot.title,
            "provider": snapshot.provider,
            "rows": self._board_rows(snapshot),
        }

    async def rank(self, rank_by: str, *, limit: int = 20, high_first: bool | None = None) -> dict[str, Any]:
        normalized = RankBy(rank_by) if rank_by in {item.value for item in RankBy} else rank_by
        snapshot = await self.market.rank_list(normalized, limit=limit, high_first=high_first)
        if is_market_error(snapshot):
            raise MarketServiceError(snapshot.message)
        return _jsonable(snapshot)

    async def financial(self, query: str) -> dict[str, Any]:
        symbol = await self.market.resolve(query)
        if symbol is None:
            raise MarketServiceError(f"未找到标的：{query}")
        snapshot = await self.market.financial_snapshot(symbol.code)
        if is_market_error(snapshot):
            raise MarketServiceError(snapshot.message)
        quote = await self.market.quote(query)
        return {
            "symbol": _jsonable(symbol),
            "quote": None if is_market_error(quote) else quote_dict(quote),
            "financial": _jsonable(snapshot),
        }

    async def valuation(self, query: str, kind: str = "pe", *, limit: int = 240) -> dict[str, Any]:
        try:
            value_kind = ValueKind(kind.lower())
        except ValueError as error:
            raise MarketServiceError(f"不支持的估值类型：{kind}") from error
        series = await self.market.valuation_series(query, value_kind)
        if is_market_error(series):
            raise MarketServiceError(series.message)
        points = [
            {"day": point.day.isoformat(), "value": point.value} for point in series.points[-max(1, min(limit, 1000)) :]
        ]
        return {
            "symbol": _jsonable(series.symbol),
            "kind": series.kind.value,
            "provider": series.provider,
            "points": points,
            "last": points[-1] if points else None,
        }

    async def ipo_calendar(self, market: str = "cn") -> dict[str, Any]:
        normalized = IpoMarket(str(market).lower())
        events = await self.market.ipo_calendar(normalized)
        if is_market_error(events):
            raise MarketServiceError(events.message)
        return {
            "market": normalized.value,
            "events": [_jsonable(item) for item in events[:100]],
            "count": len(events),
        }

    async def news(self, *, limit: int = 20) -> dict[str, Any]:
        from SayuStock.utils.news import source_label, get_news_port, is_news_error

        feed = await get_news_port().latest(limit=max(1, min(limit, 50)))
        if is_news_error(feed):
            raise MarketServiceError(feed.message)
        return {
            "source": feed.source,
            "source_label": source_label(feed.source),
            "items": [
                {
                    "id": item.id,
                    "title": item.title,
                    "text": item.text,
                    "published_ms": item.published_ms,
                    "important": item.important,
                    "url": item.url,
                }
                for item in feed.items
            ],
        }

    async def compare(self, queries: list[str], period: str = "日k", *, start: date | None = None,
                      end: date | None = None) -> dict[str, Any]:
        from SayuStock.stock_stockinfo.data import CloudMapDataService

        if not 1 <= len(queries) <= 13:
            raise MarketServiceError("请输入最多 13 个标的或板块")
        expanded = await CloudMapDataService(self.market).expand_for_compare(queries)
        if len(expanded) < 2:
            raise MarketServiceError("对比需要至少两个标的，或一个含成分股的板块")
        resolved_period = normalize_period(period)
        if resolved_period == KlinePeriod.D1:
            resolved_period = KlinePeriod.D1_YEAR
        fetched = await asyncio.gather(*(self.market.kline(query, resolved_period, start=start, end=end)
                                         for query in expanded), return_exceptions=True)
        traces: list[dict[str, Any]] = []
        failures: list[str] = []
        series_list = []
        for query, result in zip(expanded, fetched, strict=True):
            if not isinstance(result, KlineSeries) or not result.bars or not result.bars[0].close:
                failures.append(f"{query}：{getattr(result, 'message', '缺少可比较的行情')}")
                continue
            base = result.bars[0].close
            series_list.append(result)
            step = max(1, len(result.bars) // 240)
            sampled = result.bars[::step]
            if sampled[-1] is not result.bars[-1]:
                sampled = [*sampled, result.bars[-1]]
            traces.append(
                {
                    "code": result.symbol.code,
                    "name": result.symbol.name,
                    "provider": result.provider,
                    "points": [
                        {
                            "ts": bar.ts.isoformat(timespec="minutes"),
                            "pct": round((bar.close / base - 1) * 100, 3),
                            "close": bar.close,
                        }
                        for bar in sampled
                    ],
                }
            )
        if not traces:
            raise MarketServiceError("；".join(failures) or "暂无可比较的行情")
        return {
            "ok": True,
            "period": resolved_period.value,
            "as_of": datetime.now().isoformat(timespec="seconds"),
            "failures": failures,
            "traces": traces,
            "series": series_list,
        }

    async def screen(
        self,
        *,
        min_change: float | None = None,
        max_change: float | None = None,
        max_pe: float | None = None,
        min_market_cap_yi: float | None = None,
        max_market_cap_yi: float | None = None,
        industry: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        snapshot = await self.market.board("沪深A股", limit=3000, sort_asc=False)
        if is_market_error(snapshot):
            raise MarketServiceError(snapshot.message)
        assert isinstance(snapshot, BoardSnapshot)
        rows: list[dict[str, Any]] = []
        for row in snapshot.rows:
            change = row.change_pct
            pe = row.extras.pe if row.extras else None
            cap = row.market_cap
            if min_change is not None and (change is None or change < min_change):
                continue
            if max_change is not None and (change is None or change > max_change):
                continue
            if max_pe is not None and (pe is None or pe <= 0 or pe > max_pe):
                continue
            if min_market_cap_yi is not None and (cap is None or cap / 1e8 < min_market_cap_yi):
                continue
            if max_market_cap_yi is not None and (cap is None or cap / 1e8 > max_market_cap_yi):
                continue
            if industry and industry not in (row.industry or ""):
                continue
            rows.append(
                {
                    "code": row.code,
                    "name": row.name,
                    "price": row.price,
                    "change_pct": change,
                    "pe": pe,
                    "market_cap_yi": round(cap / 1e8, 2) if cap else None,
                    "industry": row.industry,
                }
            )
            if len(rows) >= max(1, min(limit, 100)):
                break
        return {
            "ok": True,
            "as_of": datetime.now().isoformat(timespec="seconds"),
            "count": len(rows),
            "rows": rows,
            "universe": "沪深A股（按市值排序，最多扫描 3000 只）",
            "provider": snapshot.provider,
        }

    def clear_cache(self) -> None:
        self._cache.clear()


__all__ = [
    "INDEX_SPECS",
    "MarketService",
    "MarketServiceError",
    "normalize_period",
    "quote_dict",
]
