"""News subscriptions, scheduled pushes and the IPO calendar (isolated)."""

from __future__ import annotations

import types
import asyncio
from pathlib import Path
from datetime import date, datetime, timedelta

import pytest

from SayuStock.utils.news import NewsFeed, NewsItem, NewsError, set_news_port
from SayuStock.utils.market import IpoEvent, IpoMarket, MarketError
from nonebot_plugin_sayustock.platform import UnifiedEvent
from nonebot_plugin_sayustock.services import newssub, newswork


class _FakePort:
    def __init__(self, items: tuple[NewsItem, ...] = (), source: str = "eastmoney") -> None:
        self.items = items
        self.source = source
        self.calls = 0
        self.error: NewsError | None = None

    async def latest(self, *, limit: int = 50, cover_ms: int = 0) -> NewsFeed | NewsError:
        self.calls += 1
        if self.error is not None:
            return self.error
        return NewsFeed(source=self.source, items=self.items)


class _FakeMarket:
    def __init__(self, *, fail: frozenset[str] = frozenset(), events: tuple[IpoEvent, ...] = ()) -> None:
        self.fail = fail
        self.events = events

    async def ipo_calendar(self, market: str) -> list[IpoEvent] | MarketError:
        if market in self.fail:
            return MarketError(code="upstream", message="数据源不可用", provider="fake")
        return [event for event in self.events if event.market.value == market]


class _FakeRuntime:
    def __init__(self, market: _FakeMarket) -> None:
        self.market = market
        self.render_enabled = False
        self.render_lock = asyncio.Lock()

    def render_scale(self) -> float:
        return 1.0


@pytest.fixture
def store(tmp_path: Path):
    backend = newssub.NewsSubscriptionStore(tmp_path / "news.json")
    newssub.set_subscription_store(backend)
    yield backend
    newssub.set_subscription_store(None)


def _group_event(*, group_id: str = "90001", admin: bool = True, user: str = "u1") -> UnifiedEvent:
    return UnifiedEvent(
        platform="onebot11",
        bot_id="1",
        user_id=user,
        text="",
        group_id=group_id,
        is_admin=admin,
    )


def _feed(count: int = 4, *, source: str = "eastmoney", base: int = 100) -> _FakePort:
    return _FakePort(
        tuple(
            NewsItem(
                source=source,
                id=str(base + index),
                text=f"快讯{index}",
                published_ms=1_700_000_000_000 + index * 60_000,
                important=True,
            )
            for index in range(count)
        ),
        source=source,
    )


def _install_runtime(monkeypatch: pytest.MonkeyPatch, market: _FakeMarket) -> None:
    from nonebot_plugin_sayustock import runtime

    monkeypatch.setattr(runtime, "get_runtime", lambda: _FakeRuntime(market))


def test_features_are_qiqi_names_only() -> None:
    names = set(newswork.FEATURES)
    assert names == {"订阅股票新闻", "取消订阅股票新闻", "新闻订阅状态", "IPO日历"}
    for name, (aliases, usage, writes) in newswork.FEATURES.items():
        assert isinstance(aliases, tuple) and aliases
        assert isinstance(usage, str) and usage
        assert isinstance(writes, bool)
        assert name not in aliases
        assert all("早柚" not in alias and "SayuStock" not in alias for alias in aliases)
    assert newswork.FEATURES["订阅股票新闻"][2] is True
    assert newswork.FEATURES["IPO日历"][2] is False


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        ("", newssub.CATEGORY_REALTIME),
        ("实时", newssub.CATEGORY_REALTIME),
        ("小时", newssub.CATEGORY_HOURLY),
        ("每小时汇总", newssub.CATEGORY_HOURLY),
        ("交易时段", newssub.CATEGORY_TRADING),
        ("每日", newssub.CATEGORY_DAILY),
        ("hourly", newssub.CATEGORY_HOURLY),
    ],
)
def test_parse_category(argument: str, expected: str) -> None:
    assert newswork.parse_category(argument) == expected


def test_parse_category_rejects_unknown() -> None:
    assert newswork.parse_category("随便") is None


def test_parse_ipo_markets() -> None:
    assert newswork.parse_ipo_markets("") is None
    assert newswork.parse_ipo_markets("A股 港股") == [IpoMarket.CN, IpoMarket.HK]
    assert newswork.parse_ipo_markets("美股") == [IpoMarket.US]
    assert newswork.parse_ipo_markets("全部") is None


@pytest.mark.asyncio
async def test_subscribe_requires_group_and_admin(store: newssub.NewsSubscriptionStore) -> None:
    private = await newswork.dispatch(
        "订阅股票新闻", "小时", UnifiedEvent(platform="onebot11", bot_id="1", user_id="u1", text="")
    )
    assert "群聊" in private.text
    assert private.structured["reason"] == "permission"
    member = await newswork.dispatch("订阅股票新闻", "小时", _group_event(admin=False))
    assert "管理员" in member.text
    assert await store.list_all() == []


@pytest.mark.asyncio
async def test_subscribe_is_idempotent_and_category_is_updatable(store: newssub.NewsSubscriptionStore) -> None:
    first = await newswork.dispatch("订阅股票新闻", "小时", _group_event())
    assert first.structured == {"ok": True, "category": newssub.CATEGORY_HOURLY}
    rows = await store.list_all()
    assert len(rows) == 1 and rows[0].category == newssub.CATEGORY_HOURLY

    again = await newswork.dispatch("订阅股票新闻", "小时", _group_event())
    assert "保持不变" in again.text
    assert len(await store.list_all()) == 1

    changed = await newswork.dispatch("订阅股票新闻", "每日", _group_event())
    assert "更新" in changed.text
    rows = await store.list_all()
    assert len(rows) == 1 and rows[0].category == newssub.CATEGORY_DAILY


@pytest.mark.asyncio
async def test_subscribe_unknown_category_and_unsubscribe(store: newssub.NewsSubscriptionStore) -> None:
    unknown = await newswork.dispatch("订阅股票新闻", "乱写", _group_event())
    assert unknown.structured["reason"] == "category"
    assert await store.list_all() == []

    await newswork.dispatch("订阅股票新闻", "实时", _group_event())
    removed = await newswork.dispatch("取消订阅股票新闻", "", _group_event())
    assert "已取消" in removed.text
    assert await store.list_all() == []
    missing = await newswork.dispatch("取消订阅股票新闻", "", _group_event())
    assert "没有订阅" in missing.text


@pytest.mark.asyncio
async def test_status_reports_category_and_watermark(store: newssub.NewsSubscriptionStore) -> None:
    await newswork.dispatch("订阅股票新闻", "交易时段", _group_event())
    row = (await store.list_all())[0]
    await store.put(
        newssub.NewsSubscription(
            platform=row.platform,
            bot_id=row.bot_id,
            group_id=row.group_id,
            category=row.category,
            watermark="jin10:777",
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    )
    result = await newswork.dispatch("新闻订阅状态", "", _group_event())
    assert "交易时段汇总" in result.text
    assert "jin10:777" in result.text
    assert "金十快讯" in result.text


@pytest.mark.asyncio
async def test_store_roundtrip_and_owner_isolation(tmp_path: Path) -> None:
    backend = newssub.NewsSubscriptionStore(tmp_path / "news.json")
    first = newssub.NewsSubscription("onebot11", "1", "100", "hourly", "", 1.0, 1.0)
    second = newssub.NewsSubscription("onebot11", "1", "200", "daily", "eastmoney:5", 2.0, 2.0)
    assert await backend.subscribe(first) is True
    assert await backend.subscribe(second) is True
    assert await backend.subscribe(first) is False
    assert (await backend.get(first.owner_key)).category == "hourly"  # type: ignore[union-attr]
    assert {row.group_id for row in await backend.list_all()} == {"100", "200"}
    assert await backend.remove(first.owner_key) is True
    assert await backend.remove(first.owner_key) is False


@pytest.mark.asyncio
async def test_subscribe_seeds_watermark_from_newest(store: newssub.NewsSubscriptionStore) -> None:
    set_news_port(_feed(3))
    try:
        result = await newswork.dispatch("订阅股票新闻", "实时", _group_event())
    finally:
        set_news_port(None)
    assert "已订阅" in result.text
    assert (await store.list_all())[0].watermark == "eastmoney:102"


@pytest.mark.asyncio
async def test_subscribe_reports_unavailable_source_without_faking(
    store: newssub.NewsSubscriptionStore,
) -> None:
    port = _FakePort()
    port.error = NewsError(code="network", message="上游不可用", provider="eastmoney")
    set_news_port(port)
    try:
        result = await newswork.dispatch("订阅股票新闻", "实时", _group_event())
    finally:
        set_news_port(None)
    assert "已订阅" in result.text
    assert "新闻源暂不可用（network）" in result.text
    assert (await store.list_all())[0].watermark == ""


@pytest.mark.asyncio
async def test_realtime_push_advances_watermark_and_dedupes(store: newssub.NewsSubscriptionStore) -> None:
    set_news_port(_feed(3, base=100))
    try:
        await newswork.dispatch("订阅股票新闻", "实时", _group_event())
        # New items after subscribing are the ones that may be pushed.
        set_news_port(_feed(3, base=200))
        sent: list[list[str]] = []

        async def deliver(subscription: newssub.NewsSubscription, texts: list[str]) -> int:
            sent.append(list(texts))
            return len(texts)

        scheduler = newswork.NewsPushScheduler(store=store, deliver=deliver, send_gap=(0.0, 0.0))
        await scheduler.tick(datetime(2026, 5, 1, 10, 30, tzinfo=newswork.BJT))
        assert len(sent) == 1 and len(sent[0]) == 3
        row = (await store.list_all())[0]
        assert row.watermark == "eastmoney:202"
        # The same feed must not be resent before the next interval.
        await scheduler.tick(datetime(2026, 5, 1, 10, 31, tzinfo=newswork.BJT))
        assert len(sent) == 1
        # A different source rebases instead of replaying the backlog.
        set_news_port(_feed(2, source="jin10", base=1))
        scheduler._last_realtime = 0.0  # simulate the next 5-minute window
        await scheduler.tick(datetime(2026, 5, 1, 10, 36, tzinfo=newswork.BJT))
        assert len(sent) == 1
        assert (await store.list_all())[0].watermark == "jin10:2"
    finally:
        set_news_port(None)


@pytest.mark.asyncio
async def test_failed_delivery_keeps_watermark(store: newssub.NewsSubscriptionStore) -> None:
    set_news_port(_feed(2, base=100))
    try:
        await newswork.dispatch("订阅股票新闻", "实时", _group_event())
        set_news_port(_feed(2, base=300))
        scheduler = newswork.NewsPushScheduler(
            store=store,
            deliver=lambda subscription, texts: _zero(),
            send_gap=(0.0, 0.0),
        )
        await scheduler.tick(datetime(2026, 5, 1, 10, 30, tzinfo=newswork.BJT))
        row = (await store.list_all())[0]
        assert row.watermark == "eastmoney:101"
    finally:
        set_news_port(None)


async def _zero() -> int:
    return 0


@pytest.mark.asyncio
async def test_hourly_digest_is_batched_and_once_per_slot(store: newssub.NewsSubscriptionStore) -> None:
    set_news_port(_feed(3, base=100))
    try:
        await newswork.dispatch("订阅股票新闻", "小时", _group_event())
        set_news_port(_feed(3, base=200))
        batches: list[list[str]] = []

        async def deliver(subscription: newssub.NewsSubscription, texts: list[str]) -> int:
            batches.append(list(texts))
            return len(texts)

        scheduler = newswork.NewsPushScheduler(store=store, deliver=deliver, send_gap=(0.0, 0.0))
        await scheduler.tick(datetime(2026, 5, 1, 11, 0, tzinfo=newswork.BJT))
        assert len(batches) == 1
        assert batches[0][0].startswith("📰 东财7x24 · 小时汇总")
        assert len(batches[0]) == 4  # header + 3 items
        await scheduler.tick(datetime(2026, 5, 1, 11, 1, tzinfo=newswork.BJT))
        assert len(batches) == 1
        assert (await store.list_all())[0].watermark == "eastmoney:202"
    finally:
        set_news_port(None)


@pytest.mark.asyncio
async def test_news_source_failure_does_not_advance_or_raise(store: newssub.NewsSubscriptionStore) -> None:
    port = _FakePort()
    port.error = NewsError(code="network", message="上游不可用", provider="eastmoney")
    set_news_port(port)
    try:
        await newswork.dispatch("订阅股票新闻", "小时", _group_event())
        sent: list[list[str]] = []

        async def deliver(subscription: newssub.NewsSubscription, texts: list[str]) -> int:
            sent.append(texts)
            return len(texts)

        scheduler = newswork.NewsPushScheduler(store=store, deliver=deliver, send_gap=(0.0, 0.0))
        assert await scheduler.tick(datetime(2026, 5, 1, 11, 0, tzinfo=newswork.BJT)) == []
        assert sent == []
        assert (await store.list_all())[0].watermark == ""
    finally:
        set_news_port(None)


@pytest.mark.asyncio
async def test_ipo_calendar_builds_text_and_html(store: newssub.NewsSubscriptionStore, monkeypatch) -> None:
    events = (
        IpoEvent(
            market=IpoMarket.CN,
            code="001234",
            name="测试新股",
            apply_date=date.today(),
            listing_date=date.today() + timedelta(days=3),
        ),
        IpoEvent(
            market=IpoMarket.HK,
            code="09999",
            name="港股测试",
            apply_end_date=date.today() + timedelta(days=1),
            issue_price_text="10-12",
            currency="HKD",
        ),
    )
    _install_runtime(monkeypatch, _FakeMarket(events=events))
    result = await newswork.dispatch("IPO日历", "A股 港股", _group_event())
    assert "IPO日历" in result.text
    assert "测试新股" in result.text
    assert "港股测试" in result.text
    assert result.structured["markets"] == ["cn", "hk"]
    assert result.structured["count"] == 2
    assert result.artifacts and result.artifacts[0].mime.startswith("text/html")


@pytest.mark.asyncio
async def test_ipo_calendar_all_sources_failed_is_a_real_error(store, monkeypatch) -> None:
    _install_runtime(monkeypatch, _FakeMarket(fail=frozenset({"cn", "hk", "us"})))
    result = await newswork.dispatch("IPO日历", "", _group_event())
    assert "获取失败" in result.text
    assert result.image is None
    assert result.artifacts == []
    assert result.structured["failed"] == ["A股", "港股", "美股"]


@pytest.mark.asyncio
async def test_ipo_calendar_partial_failure_is_labelled(store, monkeypatch) -> None:
    events = (
        IpoEvent(market=IpoMarket.US, code="ACME", name="Acme Inc", listing_date=date.today() + timedelta(days=2)),
    )
    _install_runtime(monkeypatch, _FakeMarket(fail=frozenset({"cn", "hk"}), events=events))
    result = await newswork.dispatch("IPO日历", "", _group_event())
    assert "ACME" in result.text
    assert "⚠ A股、港股市场数据源暂时不可用" in result.text
    assert result.structured["failed"] == ["A股", "港股"]


@pytest.mark.asyncio
async def test_delivery_checks_host_group_permission(monkeypatch) -> None:
    calls: list[tuple[object, int]] = []
    delivered: list[dict[str, object]] = []

    async def command_target_access(bot: object, group_id: int) -> tuple[bool, str]:
        calls.append((bot, group_id))
        return False, "已退群"

    async def call_api(api: str, **data: object) -> None:
        delivered.append({"api": api, **data})

    module = types.ModuleType("src.utils.push_permissions")
    module.command_target_access = command_target_access
    monkeypatch.setitem(__import__("sys").modules, "src.utils.push_permissions", module)
    sender = types.SimpleNamespace(self_id=1, call_api=call_api)
    monkeypatch.setattr(newswork, "_select_bot", _select(sender))
    subscription = newssub.NewsSubscription("onebot11", "1", "90001", "realtime", "", 0.0, 0.0)
    assert await newswork.default_deliver(subscription, ["不应发送"]) == 0
    assert calls == [(sender, 90001)]
    assert delivered == []


def _select(bot: object):
    async def select(bot_id: str) -> object:
        return bot

    return select


async def _unexpected_send(*args: object, **kwargs: object) -> None:
    raise AssertionError("revoked groups must not receive messages")


class _PgCursor:
    def __init__(self, rows: list[tuple[object, ...]], rowcount: int) -> None:
        self._rows = rows
        self.rowcount = rowcount

    async def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class _PgTransaction:
    async def __aenter__(self) -> "_PgTransaction":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _PgConnection:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        self.statements: list[str] = []

    async def __aenter__(self) -> "_PgConnection":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def transaction(self) -> _PgTransaction:
        return _PgTransaction()

    async def execute(self, sql: str, params: tuple[object, ...] | None = None) -> _PgCursor:
        self.statements.append(" ".join(sql.split()))
        if "pg_advisory_xact_lock" in sql:
            return _PgCursor([], 0)
        if sql.startswith("INSERT INTO"):
            key, record = params or ("", {})
            self.rows[str(key)] = dict(record)  # type: ignore[arg-type]
            return _PgCursor([], 1)
        if sql.startswith("SELECT record"):
            if "FOR UPDATE" in sql:
                key = str((params or ("",))[0])
                row = self.rows.get(key)
                return _PgCursor([(row,)] if row is not None else [], 1 if row else 0)
            if "WHERE owner_key" in sql:
                key = str((params or ("",))[0])
                row = self.rows.get(key)
                return _PgCursor([(row,)] if row is not None else [], 1 if row else 0)
            return _PgCursor([(row,) for _, row in sorted(self.rows.items())], len(self.rows))
        if sql.startswith("DELETE FROM"):
            key = str((params or ("",))[0])
            existed = self.rows.pop(key, None) is not None
            return _PgCursor([], 1 if existed else 0)
        raise AssertionError(f"意外的 SQL: {sql}")


class _PgPool:
    def __init__(self) -> None:
        self.connection_obj = _PgConnection()

    def connection(self) -> _PgConnection:
        return self.connection_obj


@pytest.mark.asyncio
async def test_postgres_store_upserts_idempotently(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _PgPool()

    async def connection_pool() -> _PgPool:
        return pool

    monkeypatch.setattr(newssub, "postgres_enabled", lambda: True)
    monkeypatch.setattr(newssub, "async_connection_pool", connection_pool)
    monkeypatch.setattr(newssub, "_jsonb", lambda value: value)
    backend = newssub.NewsSubscriptionStore()
    first = newssub.NewsSubscription("onebot11", "1", "100", "hourly", "eastmoney:9", 1.0, 1.0)
    assert await backend.subscribe(first) is True
    assert await backend.subscribe(first) is False
    rows = await backend.list_all()
    assert [row.group_id for row in rows] == ["100"]
    assert rows[0].watermark == "eastmoney:9"
    assert await backend.update("missing:key", lambda current: current) is None
    assert await backend.remove(first.owner_key) is True
    assert await backend.remove(first.owner_key) is False
    joined = "\n".join(pool.connection_obj.statements)
    assert newssub.SUBSCRIPTION_TABLE in joined
    assert "ON CONFLICT (owner_key) DO UPDATE SET record = EXCLUDED.record" in joined


def test_configured_category_follows_legacy_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Value:
        def __init__(self, data: object) -> None:
            self.data = data

    class _Config:
        def get_config(self, key: str) -> _Value:
            values = {
                "news_push_hourly_groups": ["100"],
                "news_push_trading_session_groups": ["100", "200"],
                "news_push_daily_groups": ["300"],
            }
            return _Value(values.get(key, []))

    module = types.ModuleType("SayuStock.stock_config.stock_config")
    module.STOCK_CONFIG = _Config()  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "SayuStock.stock_config.stock_config", module)
    assert newswork.configured_category("100") == newssub.CATEGORY_HOURLY
    assert newswork.configured_category("200") == newssub.CATEGORY_TRADING
    assert newswork.configured_category("300") == newssub.CATEGORY_DAILY
    assert newswork.configured_category("999") == newssub.CATEGORY_REALTIME
    assert newswork.configured_category(None) == newssub.CATEGORY_REALTIME


@pytest.mark.asyncio
async def test_subscribe_rejects_unauthorized_host_group(
    store: newssub.NewsSubscriptionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def command_target_access(bot: object, group_id: int) -> tuple[bool, str]:
        return False, "群未授权"

    module = types.ModuleType("src.utils.push_permissions")
    module.command_target_access = command_target_access  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "src.utils.push_permissions", module)
    event = _group_event()
    event.bot = types.SimpleNamespace(self_id=1)
    result = await newswork.dispatch("订阅股票新闻", "小时", event)
    assert result.structured["reason"] == "host_permission"
    assert "群未授权" in result.text
    assert await store.list_all() == []


@pytest.mark.asyncio
async def test_ipo_calendar_qweb_returns_html_without_image(store, monkeypatch) -> None:
    events = (IpoEvent(market=IpoMarket.HK, code="09999", name="港股测试", apply_end_date=date.today()),)
    _install_runtime(monkeypatch, _FakeMarket(events=events))
    result = await newswork.dispatch(
        "IPO日历", "港股", UnifiedEvent(platform="qweb", bot_id="qweb", user_id="90001", text="")
    )
    assert result.image is None
    assert result.artifacts and result.artifacts[0].mime.startswith("text/html")
    assert result.structured["markets"] == ["hk"]
