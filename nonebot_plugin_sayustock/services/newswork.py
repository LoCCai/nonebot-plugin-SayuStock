"""Qiqi/NoneBot2 news domain: subscriptions, scheduled pushes and the IPO calendar.

The upstream GsCore plugin exposed ``订阅雪球新闻`` / ``取消订阅雪球新闻`` and four
scheduled push categories through ``gs_subscribe`` + APScheduler.  This branch has
neither, so the same behaviour is rebuilt on the shared news port and the Qiqi
PostgreSQL storage:

* subscriptions live in :mod:`nonebot_plugin_sayustock.services.newssub`;
* one supervisor task (started by the host lifecycle) ticks every 20 s and
  fires the four categories at their wall-clock slots;
* every delivery first asks the host ``command_target_access`` whether the bot
  may still push to that group; revoked groups are skipped, never spammed;
* watermarks only advance after a confirmed delivery, so a crash or a failed
  send replays the missed items instead of losing them.

No DDL is executed, no second ledger is written and no network call happens at
import time.
"""

from __future__ import annotations

import re
import time
import random
import asyncio
from typing import Any, Callable, Awaitable
from datetime import datetime
from zoneinfo import ZoneInfo

from nonebot.log import logger

from ..compat import bootstrap
from .newssub import (
    CATEGORIES,
    CATEGORY_DAILY,
    CATEGORY_HOURLY,
    CATEGORY_LABELS,
    CATEGORY_TRADING,
    CATEGORY_REALTIME,
    CATEGORY_DESCRIPTIONS,
    NewsStorageError,
    NewsSubscription,
    NewsSubscriptionStore,
    get_subscription_store,
)
from ..platform import UnifiedEvent
from ..artifacts import create_html
from ..feature_data import NEWSWORK as FEATURES

bootstrap.install()

from SayuStock.utils.news import (  # noqa: E402
    NEWS_RETENTION_MS,
    NewsFeed,
    NewsItem,
    id_newer,
    source_label,
    get_news_port,
    is_news_error,
    should_rebase,
    split_watermark,
)
from SayuStock.utils.market import IpoEvent, IpoMarket, is_market_error  # noqa: E402
from SayuStock.utils.render_data import (  # noqa: E402
    IpoCalendarRenderData,
    build_ipo_calendar_render_data,
)
from SayuStock.utils.render_text import ipo_calendar_text  # noqa: E402
from SayuStock.stock_ipo.ipo_html import CSS_WIDTH, build_ipo_calendar_html  # noqa: E402

from .theme import themed_upstream_page  # noqa: E402
from .market import MarketServiceError  # noqa: E402

BJT = ZoneInfo("Asia/Shanghai")

REALTIME_INTERVAL_SECONDS = 300
DIGEST_BATCH = 50
SENT_HISTORY_MAX = 50

_MARKET_LABEL: dict[IpoMarket, str] = {
    IpoMarket.CN: "A股",
    IpoMarket.HK: "港股",
    IpoMarket.US: "美股",
}
_IPO_TOKEN_RE = re.compile(r"a股|[a-z]+|[\u4e00-\u9fff]+")
_IPO_TOKENS: dict[str, IpoMarket] = {
    "a股": IpoMarket.CN,
    "cn": IpoMarket.CN,
    "沪深": IpoMarket.CN,
    "港股": IpoMarket.HK,
    "港": IpoMarket.HK,
    "hk": IpoMarket.HK,
    "美股": IpoMarket.US,
    "美": IpoMarket.US,
    "us": IpoMarket.US,
    "纳斯达克": IpoMarket.US,
}

_CATEGORY_TOKENS: dict[str, str] = {
    "实时": CATEGORY_REALTIME,
    "逐条": CATEGORY_REALTIME,
    "立即": CATEGORY_REALTIME,
    "小时": CATEGORY_HOURLY,
    "每小时": CATEGORY_HOURLY,
    "整点": CATEGORY_HOURLY,
    "交易时段": CATEGORY_TRADING,
    "时段": CATEGORY_TRADING,
    "盘中": CATEGORY_TRADING,
    "每日": CATEGORY_DAILY,
    "每天": CATEGORY_DAILY,
    "日报": CATEGORY_DAILY,
}

_TRADING_SESSION_LABELS: dict[int, str] = {
    8: "隔夜汇总",
    12: "午间汇总",
    16: "收盘汇总",
    22: "晚间汇总",
}


class NewsFeedError(RuntimeError):
    """The upstream news source failed; callers must surface the real reason."""


# ---------------------------------------------------------------------------
# Feature catalogue (consumed by the controller's command registry)
# ---------------------------------------------------------------------------



def _command_result() -> Any:
    """Import ``CommandResult`` at call time so this module stays cycle-free."""
    from ..commands import CommandResult

    return CommandResult


def _now_ms() -> int:
    return int(time.time() * 1000)


def _fmt_time(published_ms: int, fmt: str = "%m-%d %H:%M") -> str:
    return datetime.fromtimestamp(published_ms / 1000, BJT).strftime(fmt)


def _require_group_admin(event: UnifiedEvent) -> str | None:
    """Return a refusal message, or ``None`` when the sender may manage pushes.

    A QWeb-originated event reaches this domain only after the host resolved a
    real adapter group context (``features.web_event``), so ``is_admin`` there is
    the verified QQ role of the requesting member, never a model-supplied flag.
    """
    if not event.is_group:
        return "新闻订阅只能在群聊中开启；请先在群里发送「订阅股票新闻」。"
    if not event.is_admin:
        return "仅群主或管理员可以管理本群的新闻订阅。"
    return None


def parse_category(argument: str) -> str | None:
    """Map the optional argument to a push category; ``None`` means unknown."""
    raw = argument.strip().lower()
    if not raw:
        return CATEGORY_REALTIME
    compact = raw.replace(" ", "")
    if compact in CATEGORIES:
        return compact
    for token in sorted(_CATEGORY_TOKENS, key=len, reverse=True):
        if token in compact:
            return _CATEGORY_TOKENS[token]
    return None


_CONFIG_CATEGORY_KEYS: tuple[tuple[str, str], ...] = (
    ("news_push_hourly_groups", CATEGORY_HOURLY),
    ("news_push_trading_session_groups", CATEGORY_TRADING),
    ("news_push_daily_groups", CATEGORY_DAILY),
)


def configured_category(group_id: str | None) -> str:
    """Migrate the legacy GsCore classification lists into the new store.

    The upstream control panel let admins list group ids under
    ``news_push_hourly_groups`` / ``news_push_trading_session_groups`` /
    ``news_push_daily_groups``.  Those lists still exist in the shared config
    file, so an unclassified subscribe inherits the same priority as before
    (hourly > trading > daily > realtime).  Unreadable config means realtime.
    """
    gid = str(group_id or "").strip()
    if not gid:
        return CATEGORY_REALTIME
    try:
        from SayuStock.stock_config.stock_config import STOCK_CONFIG
    except ImportError:
        return CATEGORY_REALTIME
    for key, category in _CONFIG_CATEGORY_KEYS:
        raw = STOCK_CONFIG.get_config(key).data
        if not isinstance(raw, list):
            continue
        if gid in {str(item).strip() for item in raw}:
            return category
    return CATEGORY_REALTIME


def parse_ipo_markets(argument: str) -> list[IpoMarket] | None:
    """Return the requested markets; ``None`` means every market."""
    raw = (argument or "").strip().lower()
    if not raw:
        return None
    picked: list[IpoMarket] = []
    for token in _IPO_TOKEN_RE.findall(raw):
        market = _IPO_TOKENS.get(token)
        if market is not None and market not in picked:
            picked.append(market)
    return picked or None


# ---------------------------------------------------------------------------
# Subscriptions (write path)
# ---------------------------------------------------------------------------


async def subscribe(
    event: UnifiedEvent,
    category: str,
    *,
    store: NewsSubscriptionStore | None = None,
) -> str:
    if category not in CATEGORIES:
        raise ValueError("未知的新闻推送类别")
    backend = store or get_subscription_store()
    now = time.time()
    existing = await backend.get(_owner_key(event))
    watermark = existing.watermark if existing is not None else ""
    seed_note = ""
    if existing is None:
        # Seed the watermark at the newest item, like the GsCore version, so a
        # freshly subscribed group is not flooded with the backlog.
        try:
            feed = await get_news_port().latest()
        except Exception as error:
            seed_note = f"\n⚠ 首轮水位线获取失败（{type(error).__name__}），将从下一条快讯开始推送"
        else:
            if is_news_error(feed):
                seed_note = f"\n⚠ 新闻源暂不可用（{feed.code}），将从下一条快讯开始推送"
            else:
                newest = _newest(feed)
                watermark = newest.watermark() if newest is not None else ""
    subscription = NewsSubscription(
        platform=event.platform,
        bot_id=event.bot_id or event.platform,
        group_id=event.group_id or "",
        category=category,
        watermark=watermark,
        created_at=existing.created_at if existing is not None else now,
        updated_at=now,
    )
    created = await backend.subscribe(subscription)
    if created:
        head = "✅ 已订阅股票新闻"
    elif existing is not None and existing.category != category:
        head = "✅ 已更新本群新闻推送类别"
    else:
        head = "ℹ️ 本群已订阅，推送类别保持不变"
    return (
        f"{head}\n"
        f"📰 推送方式：{CATEGORY_LABELS[category]}（{CATEGORY_DESCRIPTIONS[category]}）\n"
        "📚 新闻源：东财7x24 → 华尔街见闻 → 新浪 → 金十（前一个失败才换下一个）\n"
        "可在群里发送「退订新闻」关闭推送。" + seed_note
    )


async def unsubscribe(event: UnifiedEvent, *, store: NewsSubscriptionStore | None = None) -> str:
    backend = store or get_subscription_store()
    removed = await backend.remove(_owner_key(event))
    if removed:
        return "✅ 已取消本群的股票新闻推送。"
    return "ℹ️ 本群当前没有订阅股票新闻。"


async def subscription_status(event: UnifiedEvent, *, store: NewsSubscriptionStore | None = None) -> str:
    backend = store or get_subscription_store()
    current = await backend.get(_owner_key(event))
    if current is None:
        lines = ["📰 本群尚未订阅股票新闻。发送「订阅股票新闻 小时」即可开启。"]
    else:
        source, watermark = split_watermark(current.watermark)
        lines = [
            "📰 本群新闻订阅",
            f"推送方式：{CATEGORY_LABELS[current.category]}（{CATEGORY_DESCRIPTIONS[current.category]}）",
            f"已推送水位线：{current.watermark or '—'}" + (f"（来源：{source_label(source)}）" if source else ""),
        ]
        if not watermark:
            lines.append("尚未推送过内容；首轮会从当前最新快讯开始，不补发历史。")
    if event.is_admin:
        all_rows = await backend.list_all()
        own = [row for row in all_rows if row.group_id == (event.group_id or "")]
        lines.append(f"已知订阅群数量：{len(all_rows)}（本群记录 {len(own)} 条）")
    return "\n".join(lines)


def _owner_key(event: UnifiedEvent) -> str:
    return NewsSubscription(
        platform=event.platform,
        bot_id=event.bot_id or event.platform,
        group_id=event.group_id or "",
        category=CATEGORY_REALTIME,
        watermark="",
        created_at=0.0,
        updated_at=0.0,
    ).owner_key


# ---------------------------------------------------------------------------
# IPO calendar (read path)
# ---------------------------------------------------------------------------


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_date(value: object) -> Any:
    from datetime import date

    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _event_from_payload(payload: dict[str, Any]) -> IpoEvent:
    """Rebuild the typed event from ``MarketService.ipo_calendar``'s JSON view."""
    raw_market = payload.get("market")
    market = next((item for item in IpoMarket if item.value == raw_market), None)
    if market is None:
        raise MarketServiceError("IPO 日历返回了未知市场")
    code = _as_text(payload.get("code"))
    name = _as_text(payload.get("name"))
    if not code and not name:
        raise MarketServiceError("IPO 日历返回的事件缺少代码与名称")
    return IpoEvent(
        market=market,
        code=code,
        name=name,
        listing_date=_as_date(payload.get("listing_date")),
        apply_date=_as_date(payload.get("apply_date")),
        apply_end_date=_as_date(payload.get("apply_end_date")),
        ballot_date=_as_date(payload.get("ballot_date")),
        pay_date=_as_date(payload.get("pay_date")),
        grey_market_date=_as_date(payload.get("grey_market_date")),
        filed_date=_as_date(payload.get("filed_date")),
        issue_price=_as_float(payload.get("issue_price")),
        issue_price_text=_as_text(payload.get("issue_price_text")) or None,
        raise_yi=_as_float(payload.get("raise_yi")),
        currency=_as_text(payload.get("currency")) or "CNY",
        first_day_change=_as_float(payload.get("first_day_change")),
        oversubscription=_as_float(payload.get("oversubscription")),
        board=_as_text(payload.get("board")) or None,
    )


async def fetch_ipo_events(markets: list[IpoMarket]) -> tuple[list[IpoEvent], list[str], dict[str, Any]]:
    """Fetch each market through ``MarketService``; return events, failures, raw data."""
    from ..runtime import get_runtime

    service = get_runtime().market
    results = await asyncio.gather(
        *(service.ipo_calendar(market.value) for market in markets),
        return_exceptions=True,
    )
    events: list[IpoEvent] = []
    failed: list[str] = []
    raw: dict[str, Any] = {}
    for market, result in zip(markets, results, strict=True):
        label = _MARKET_LABEL[market]
        if isinstance(result, BaseException):
            failed.append(label)
            logger.warning("[SayuStock] IPO 日历 {} 获取失败: {}", label, result)
            continue
        if is_market_error(result):
            failed.append(label)
            logger.warning("[SayuStock] IPO 日历 {} 获取失败: {}", label, result.message)
            continue
        payloads = result.get("events") if isinstance(result, dict) else result
        if not isinstance(payloads, list):
            failed.append(label)
            logger.warning("[SayuStock] IPO 日历 {} 返回了无法识别的数据", label)
            continue
        raw[label] = payloads
        for payload in payloads:
            if isinstance(payload, dict):
                events.append(_event_from_payload(payload))
            elif isinstance(payload, IpoEvent):
                events.append(payload)
    return events, failed, raw


async def ipo_calendar(event: UnifiedEvent, argument: str) -> Any:
    markets = parse_ipo_markets(argument)
    wanted = markets or list(IpoMarket)
    events, failed, raw = await fetch_ipo_events(wanted)
    if len(failed) == len(wanted):
        return _command_result()(
            "IPO日历数据获取失败：" + "、".join(failed) + "市场数据源暂时不可用，请稍后再试。",
            structured={"markets": [m.value for m in wanted], "failed": failed},
        )

    data = build_ipo_calendar_render_data(events, markets=markets)
    visible = [group for group in data.groups if group.label not in failed]
    if data.total:
        text = ipo_calendar_text(
            IpoCalendarRenderData(
                anchor=data.anchor,
                window_start=data.window_start,
                window_end=data.window_end,
                span_days=data.span_days,
                days=data.days,
                groups=visible,
                total=sum(len(group.rows) for group in visible),
            )
        )
    else:
        labels = "、".join(group.label for group in visible)
        text = f"【IPO日历】{data.window_start:%m-%d} ~ {data.window_end:%m-%d}\n{labels}窗口内无新股发行或上市安排。"
    if failed:
        text += f"\n⚠ {'、'.join(failed)}市场数据源暂时不可用，未计入"

    html = themed_upstream_page(build_ipo_calendar_html(data, failed=failed), "IPO 日历")
    artifact = create_html(
        "IPO日历",
        html,
        kind="chart",
        owner=event.user_id,
        meta={"markets": [market.value for market in wanted], "failed": failed},
    )
    image = await _render(html, CSS_WIDTH) if event.platform != "qweb" and not event._extra.get("qweb") else None
    return _command_result()(
        text,
        image,
        [artifact],
        structured={
            "markets": [market.value for market in wanted],
            "failed": failed,
            "count": data.total,
            "window": [data.window_start.isoformat(), data.window_end.isoformat()],
            "events": raw,
        },
    )


async def _render(html: str, width: int) -> bytes | None:
    from .charts import ChartRenderError, rasterize
    from ..runtime import get_runtime

    runtime = get_runtime()
    if not runtime.render_enabled:
        return None
    try:
        async with runtime.render_lock:
            return await rasterize(html, width=width, scale=runtime.render_scale())
    except ChartRenderError as error:
        logger.info("[SayuStock] IPO 日历图片渲染不可用: {}", error)
        return None


# ---------------------------------------------------------------------------
# Scheduled pushes
# ---------------------------------------------------------------------------


def _pending_important(feed: NewsFeed) -> list[NewsItem]:
    return sorted(
        (item for item in feed.items if item.important),
        key=lambda item: (item.published_ms, item.id),
    )


def _advance(current: str, candidate: str) -> str:
    if not current:
        return candidate
    if id_newer(split_watermark(candidate)[1], split_watermark(current)[1]):
        return candidate
    return current


def _newest(feed: NewsFeed) -> NewsItem | None:
    if not feed.items:
        return None
    best = feed.items[0]
    for item in feed.items[1:]:
        if id_newer(item.id, best.id):
            best = item
    return best


async def _target_access(bot: Any, group_id: str) -> tuple[bool, str]:
    """Ask the Qiqi host whether this bot may still talk to the group.

    A standalone deployment has no host ACL module; there the subscription was
    already created by a verified group owner/admin, so delivery proceeds.
    """
    try:
        from src.utils.push_permissions import command_target_access
    except ImportError:
        return True, "standalone"
    try:
        numeric = int(group_id)
    except (TypeError, ValueError):
        return False, "目标群号不是数字"
    try:
        allowed, reason = await command_target_access(bot, numeric)
    except Exception as error:
        return False, f"群推送权限校验失败：{type(error).__name__}"
    if not isinstance(allowed, bool):
        return False, "群推送权限校验返回值无效"
    return allowed, str(reason or "")


async def _host_group_access(event: UnifiedEvent) -> tuple[bool, str] | None:
    """Check the host ACL when it exists; ``None`` means standalone/no ACL."""
    if not event.group_id or event.bot is None:
        return None
    try:
        from src.utils.push_permissions import command_target_access  # noqa: F401
    except ImportError:
        return None
    return await _target_access(event.bot, event.group_id)


async def _select_bot(bot_id: str) -> Any | None:
    from nonebot import get_bots

    bots = get_bots()
    for key, bot in bots.items():
        if str(key) == bot_id or str(getattr(bot, "self_id", "")) == bot_id:
            return bot
    return None


async def default_deliver(subscription: NewsSubscription, texts: list[str]) -> int:
    """Acknowledge only entries delivered to the subscribed OneBot group."""
    bot = await _select_bot(subscription.bot_id)
    if bot is None:
        logger.warning("[SayuStock] 新闻推送找不到 Bot {}，跳过群 {}", subscription.bot_id, subscription.group_id)
        return 0
    if subscription.platform not in {"onebot11", "onebot12"}:
        logger.warning(
            "[SayuStock] 新闻推送暂不支持 {} 适配器，跳过群 {}", subscription.platform, subscription.group_id
        )
        return 0
    allowed, reason = await _target_access(bot, subscription.group_id)
    if not allowed:
        logger.warning(
            "[SayuStock] 群 {} 不再具备推送权限（{}），跳过本轮新闻",
            subscription.group_id,
            reason,
        )
        return 0
    if not texts:
        return 0
    from nonebot.adapters.onebot.v11 import MessageSegment

    from .charts import news_card
    from ..platform.bridge import send_group_result

    # A digest header is descriptive, never an independently acknowledged news item.
    has_header = texts[0].startswith("📰 ")
    entries = texts[1:] if has_header else texts
    if not entries:
        return 0
    scope = UnifiedEvent(
        platform=subscription.platform,
        bot_id=subscription.bot_id,
        user_id=subscription.bot_id,
        group_id=subscription.group_id,
        text="",
        bot=bot,
    )
    delivered = 0
    for start in range(0, len(entries), 12):
        lines = entries[start : start + 12]
        source = texts[0] if has_header else CATEGORY_LABELS[subscription.category]
        html, _ = news_card({"source_label": source, "items": [{"title": line} for line in lines]}, len(lines))
        try:
            image = await _render(html, 960)
            if subscription.platform == "onebot12":
                from nonebot.adapters.onebot.v12 import MessageSegment as Segment12

                from ..platform.bridge import _image_segment

                segment = await _image_segment("onebot12", bot, image) if image else None
                await bot.call_api(
                    "send_message",
                    detail_type="group",
                    group_id=subscription.group_id,
                    message=[segment or Segment12.text("\n\n".join(lines))],
                )
            elif image:
                await bot.call_api(
                    "send_group_msg", group_id=int(subscription.group_id), message=MessageSegment.image(image)
                )
            else:
                await send_group_result(scope, "\n\n".join(lines), [])
        except Exception as error:
            logger.warning("[SayuStock] 新闻推送到群 {} 失败: {}", subscription.group_id, type(error).__name__)
            break
        delivered += len(lines)
    return delivered


class NewsPushScheduler:
    """One supervisor task driving realtime, hourly, trading and daily pushes."""

    def __init__(
        self,
        *,
        store: NewsSubscriptionStore | None = None,
        deliver: Callable[[NewsSubscription, list[str]], Awaitable[int]] | None = None,
        send_gap: tuple[float, float] = (2.0, 5.0),
    ) -> None:
        self._store = store
        self._deliver = deliver or default_deliver
        self._send_gap = send_gap
        self._last_realtime = 0.0
        self._digest_slots: dict[str, str] = {}
        self._sent: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()
        self._fetch_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    @property
    def store(self) -> NewsSubscriptionStore:
        return self._store or get_subscription_store()

    # -- lifecycle --------------------------------------------------------
    async def run(self) -> None:
        while not self._stopped:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("[SayuStock] 新闻推送循环异常: {}", error)
            try:
                await asyncio.sleep(20)
            except asyncio.CancelledError:
                raise

    def stop(self) -> None:
        self._stopped = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()

    async def aclose(self) -> None:
        """Cancel and await the loop so shutdown cannot leak a task."""
        task = self._task
        self.stop()
        if task is None or task.done():
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._stopped = False
        self._task = asyncio.get_running_loop().create_task(self.run())

    # -- slots ------------------------------------------------------------
    async def tick(self, now: datetime | None = None) -> list[str]:
        """Run every push that is due; returns slot names that were triggered."""
        stamp = now or datetime.now(BJT)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=BJT)
        local = stamp.astimezone(BJT)
        triggered: list[str] = []
        async with self._lock:
            if time.time() - self._last_realtime >= REALTIME_INTERVAL_SECONDS:
                self._last_realtime = time.time()
                if await self._push_realtime():
                    triggered.append("realtime")
            day = local.strftime("%Y-%m-%d")
            # The per-slot guard makes this a once-per-hour catch-up: a tick
            # that starts a few seconds late still delivers, but never twice.
            if await self._run_digest(CATEGORY_HOURLY, f"{day} {local.hour:02d}", "小时汇总"):
                triggered.append("hourly")
            if local.hour in _TRADING_SESSION_LABELS:
                label = _TRADING_SESSION_LABELS[local.hour]
                if await self._run_digest(CATEGORY_TRADING, f"{day} {local.hour:02d}", label):
                    triggered.append("trading")
            if local.hour == 8:
                if await self._run_digest(CATEGORY_DAILY, f"{day} 08", "每日汇总"):
                    triggered.append("daily")
        return triggered

    async def _run_digest(self, category: str, slot: str, label: str) -> bool:
        key = f"{category}:{slot}"
        if self._digest_slots.get(category) == key:
            return False
        subscriptions = [row for row in await self.store.list_all() if row.category == category]
        if not subscriptions:
            self._digest_slots[category] = key
            return False
        try:
            feed = await self._latest(cover_ms=NEWS_RETENTION_MS)
        except NewsFeedError as error:
            # Do not consume the slot: a source outage retries inside the
            # catch-up window instead of silently dropping this hour.
            logger.error("[SayuStock] {}取数失败，稍后重试：{}", label, error)
            return False
        self._digest_slots[category] = key
        items = _pending_important(feed)
        pushed = False
        for subscription in subscriptions:
            if await self._rebase_if_needed(subscription, feed):
                continue
            if await self._send_digest(subscription, items, label):
                pushed = True
            await self._gap()
        return pushed

    async def _push_realtime(self) -> bool:
        subscriptions = [row for row in await self.store.list_all() if row.category == CATEGORY_REALTIME]
        if not subscriptions:
            return False
        try:
            feed = await self._latest()
        except NewsFeedError as error:
            logger.error("[SayuStock] 实时快讯取数失败，本轮跳过：{}", error)
            return False
        pending = _pending_important(feed)
        for subscription in subscriptions:
            if await self._rebase_if_needed(subscription, feed):
                continue
            await self._send_realtime(subscription, pending)
            await self._gap()
        return True

    async def _latest(self, cover_ms: int = 0) -> NewsFeed:
        async with self._fetch_lock:
            if cover_ms:
                feed = await get_news_port().latest(cover_ms=cover_ms)
            else:
                feed = await get_news_port().latest()
        if is_news_error(feed):
            raise NewsFeedError(f"{feed.message}（错误码 {feed.code}）")
        return feed

    async def _rebase_if_needed(self, subscription: NewsSubscription, feed: NewsFeed) -> bool:
        if not should_rebase(subscription.watermark, feed.source):
            return False
        newest = _newest(feed)
        if newest is None:
            return True
        updated = await self._store_watermark(subscription, newest.watermark())
        logger.info(
            "[SayuStock] 群 {} 新闻水位线换源重置为 {}",
            subscription.group_id,
            updated.watermark if updated is not None else "（订阅已删除）",
        )
        return True

    async def _store_watermark(
        self,
        subscription: NewsSubscription,
        mark: str,
    ) -> NewsSubscription | None:
        backend = self.store
        return await backend.update(
            subscription.owner_key,
            lambda current: NewsSubscription(
                platform=current.platform,
                bot_id=current.bot_id,
                group_id=current.group_id,
                category=current.category,
                watermark=(mark if current.watermark == subscription.watermark else current.watermark),
                created_at=current.created_at,
                updated_at=time.time(),
            ),
        )

    def _already_sent(self, subscription: NewsSubscription, mark: str) -> bool:
        return mark in self._sent.get(subscription.owner_key, ())

    def _mark_sent(self, subscription: NewsSubscription, mark: str) -> None:
        history = self._sent.setdefault(subscription.owner_key, [])
        history.append(mark)
        if len(history) > SENT_HISTORY_MAX:
            del history[: len(history) - SENT_HISTORY_MAX]

    async def _send_realtime(self, subscription: NewsSubscription, pending: list[NewsItem]) -> None:
        _, watermark = split_watermark(subscription.watermark)
        pairs: list[tuple[str, str]] = []
        for item in pending:
            if not id_newer(item.id, watermark):
                continue
            mark = item.watermark()
            if self._already_sent(subscription, mark):
                continue
            stamp = _fmt_time(item.published_ms, "%m-%d %H:%M:%S")
            pairs.append((mark, f"【{stamp}】{source_label(item.source)}\n{item.text}"))
        if not pairs:
            return
        delivered = await self._deliver(subscription, [text for _, text in pairs])
        if delivered <= 0:
            logger.error(
                "[SayuStock] 快讯推送到群 {} 失败，水位线停在 {}，留待下轮重发",
                subscription.group_id,
                subscription.watermark or "—",
            )
            return
        sent_mark = subscription.watermark or ""
        for mark, _ in pairs[:delivered]:
            self._mark_sent(subscription, mark)
            sent_mark = _advance(sent_mark, mark)
        await self._store_watermark(subscription, sent_mark)
        if delivered < len(pairs):
            logger.error(
                "[SayuStock] 快讯推送到群 {} 只成功 {}/{} 条，剩余留待下轮",
                subscription.group_id,
                delivered,
                len(pairs),
            )

    async def _send_digest(
        self,
        subscription: NewsSubscription,
        items: list[NewsItem],
        label: str,
    ) -> bool:
        _, watermark = split_watermark(subscription.watermark)
        entries: list[tuple[str, str]] = []
        first_stamp = last_stamp = ""
        origin = ""
        for item in items:
            if not id_newer(item.id, watermark):
                continue
            mark = item.watermark()
            if self._already_sent(subscription, mark):
                continue
            stamp = _fmt_time(item.published_ms)
            if not entries:
                first_stamp = stamp
                origin = source_label(item.source)
            last_stamp = stamp
            entries.append((mark, f"【{stamp}】{item.text}"))
        if not entries:
            return False

        header = f"📰 {origin} · {label}（共{len(entries)}条 · {first_stamp}~{last_stamp}）"
        sent_mark = subscription.watermark or ""
        pushed = False
        for offset in range(0, len(entries), DIGEST_BATCH):
            batch = entries[offset : offset + DIGEST_BATCH]
            texts = [line for _, line in batch]
            if offset == 0:
                texts = [header, *texts]
            delivered = await self._deliver(subscription, texts)
            delivered = max(0, min(delivered, len(batch)))
            if delivered == 0:
                logger.error(
                    "[SayuStock] 快讯{}推送到群 {} 失败，水位线停在 {}",
                    label,
                    subscription.group_id,
                    subscription.watermark or "—",
                )
                break
            for mark, _ in batch[:delivered]:
                self._mark_sent(subscription, mark)
                sent_mark = _advance(sent_mark, mark)
            pushed = True
            if delivered < len(batch):
                break
        if pushed:
            await self._store_watermark(subscription, sent_mark)
        return pushed

    async def _gap(self) -> None:
        low, high = self._send_gap
        if high > 0:
            await asyncio.sleep(low + random.random() * max(0.0, high - low))


_scheduler: NewsPushScheduler | None = None


def get_scheduler() -> NewsPushScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = NewsPushScheduler()
    return _scheduler


def set_scheduler(scheduler: NewsPushScheduler | None) -> None:
    global _scheduler
    _scheduler = scheduler


async def start_pushes() -> None:
    """Lifecycle hook: start the supervised push loop once per process."""
    get_scheduler().start()


async def stop_pushes() -> None:
    """Lifecycle hook: cancel the push loop; safe when it never started."""
    scheduler = _scheduler
    if scheduler is not None:
        await scheduler.aclose()


# ---------------------------------------------------------------------------
# Dispatch (shared by group commands, QWeb and AI tooling)
# ---------------------------------------------------------------------------


async def dispatch(command: str, argument: str, event: UnifiedEvent) -> Any:
    """Run one newswork feature for the trusted event identity."""
    if not event.user_id:
        return _command_result()("无法确认当前账户身份，已拒绝执行。")
    if command in ("订阅股票新闻", "取消订阅股票新闻"):
        refusal = _require_group_admin(event)
        if refusal is not None:
            return _command_result()(refusal, structured={"ok": False, "reason": "permission"})
        access = await _host_group_access(event)
        if access is not None and not access[0]:
            return _command_result()(
                f"当前群未获得宿主授权（{access[1] or '无权限'}），已拒绝管理新闻订阅。",
                structured={"ok": False, "reason": "host_permission"},
            )
    try:
        if command == "订阅股票新闻":
            if event.platform not in {"onebot11", "onebot12"}:
                return _command_result()(
                    "当前协议未开放定时群推送，可使用「财经快讯」主动查询。", structured={"ok": False}
                )
            category = configured_category(event.group_id) if not argument.strip() else parse_category(argument)
            if category is None:
                options = "、".join(CATEGORY_LABELS.values())
                return _command_result()(
                    f"未识别推送类别。可选：{options}。例如：订阅股票新闻 小时",
                    structured={"ok": False, "reason": "category"},
                )
            text = await subscribe(event, category)
            return _command_result()(text, structured={"ok": True, "category": category})
        if command == "取消订阅股票新闻":
            text = await unsubscribe(event)
            return _command_result()(text, structured={"ok": True})
        if command == "新闻订阅状态":
            if not event.is_group:
                return _command_result()("新闻订阅按群管理；请在目标群聊中查看状态。")
            text = await subscription_status(event)
            return _command_result()(text, structured={"ok": True, "group_id": event.group_id})
        if command == "IPO日历":
            return await ipo_calendar(event, argument)
    except (NewsStorageError, MarketServiceError, NewsFeedError) as error:
        logger.error("[SayuStock] {} 执行失败: {}", command, error)
        return _command_result()(f"{command}失败：{error}", structured={"ok": False})
    except ValueError as error:
        return _command_result()(str(error), structured={"ok": False})
    return _command_result()(f"未实现的资讯指令：{command}")


__all__ = [
    "FEATURES",
    "NewsFeedError",
    "NewsPushScheduler",
    "configured_category",
    "default_deliver",
    "dispatch",
    "get_scheduler",
    "ipo_calendar",
    "parse_category",
    "parse_ipo_markets",
    "set_scheduler",
    "start_pushes",
    "stop_pushes",
    "subscribe",
    "subscription_status",
    "unsubscribe",
]
