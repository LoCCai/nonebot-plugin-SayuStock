"""Durable, per-bot group news subscriptions. Qiqi stores them in its stock PostgreSQL schema."""

from __future__ import annotations

import json
import asyncio
from typing import Callable
from pathlib import Path
from dataclasses import replace, dataclass

from ..paths import DATA_DIR
from ..storage import (
    PostgresStorageError,
    postgres_enabled,
    log_postgres_failure,
    async_connection_pool,
)

NEWS_OWNER_PREFIX = "news:"

CATEGORY_REALTIME = "realtime"
CATEGORY_HOURLY = "hourly"
CATEGORY_TRADING = "trading"
CATEGORY_DAILY = "daily"

CATEGORIES = (CATEGORY_REALTIME, CATEGORY_HOURLY, CATEGORY_TRADING, CATEGORY_DAILY)

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_REALTIME: "逐条实时推送",
    CATEGORY_HOURLY: "小时汇总",
    CATEGORY_TRADING: "交易时段汇总",
    CATEGORY_DAILY: "每日汇总",
}

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    CATEGORY_REALTIME: "每 5 分钟检查一次，重要快讯逐条推送",
    CATEGORY_HOURLY: "每小时整点合并推送上一小时的重要快讯",
    CATEGORY_TRADING: "每天 08:00 / 12:00 / 16:00 / 22:00 合并推送",
    CATEGORY_DAILY: "每天 08:00 合并推送一次",
}


class NewsStorageError(RuntimeError):
    """The subscription backend is unavailable or holds an invalid record."""


@dataclass(frozen=True, slots=True)
class NewsSubscription:
    platform: str
    bot_id: str
    group_id: str
    category: str
    watermark: str
    created_at: float
    updated_at: float

    @property
    def owner_key(self) -> str:
        peer = self.group_id or self.platform
        return f"{NEWS_OWNER_PREFIX}{self.platform}:{self.bot_id or self.platform}:{peer}"

    def record(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "bot_id": self.bot_id,
            "group_id": self.group_id,
            "category": self.category,
            "watermark": self.watermark,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _text_field(record: dict[str, object], name: str) -> str:
    try:
        value = record[name]
    except KeyError:
        raise NewsStorageError(f"新闻订阅记录缺少字段：{name}") from None
    if not isinstance(value, str):
        raise NewsStorageError(f"新闻订阅记录字段类型无效：{name}")
    return value


def _time_field(record: dict[str, object], name: str) -> float:
    try:
        value = record[name]
    except KeyError:
        raise NewsStorageError(f"新闻订阅记录缺少字段：{name}") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NewsStorageError(f"新闻订阅记录字段类型无效：{name}")
    return float(value)


def decode_record(raw: object) -> NewsSubscription:
    if not isinstance(raw, dict):
        raise NewsStorageError("新闻订阅记录必须是 JSON 对象")
    category = _text_field(raw, "category")
    if category not in CATEGORIES:
        raise NewsStorageError(f"新闻订阅推送类别无效：{category}")
    subscription = NewsSubscription(
        platform=_text_field(raw, "platform"),
        bot_id=_text_field(raw, "bot_id"),
        group_id=_text_field(raw, "group_id"),
        category=category,
        watermark=_text_field(raw, "watermark"),
        created_at=_time_field(raw, "created_at"),
        updated_at=_time_field(raw, "updated_at"),
    )
    if not subscription.group_id:
        raise NewsStorageError("新闻订阅记录缺少群号")
    return subscription


def _jsonb(value: object) -> object:
    try:
        from psycopg.types.json import Jsonb
    except ImportError:
        raise NewsStorageError("PostgreSQL 存储需要安装 psycopg[binary,pool]") from None
    return Jsonb(value)


SUBSCRIPTION_TABLE = "qiqi_sayustock.news_subscription"
_SELECT_SQL = f"SELECT record FROM {SUBSCRIPTION_TABLE} WHERE owner_key = %s"
_SELECT_FOR_UPDATE_SQL = f"{_SELECT_SQL} FOR UPDATE"
_UPSERT_SQL = (
    f"INSERT INTO {SUBSCRIPTION_TABLE} (owner_key, record) VALUES (%s, %s) "
    "ON CONFLICT (owner_key) DO UPDATE SET record = EXCLUDED.record"
)
_DELETE_SQL = f"DELETE FROM {SUBSCRIPTION_TABLE} WHERE owner_key = %s"
_LIST_SQL = f"SELECT record FROM {SUBSCRIPTION_TABLE} ORDER BY owner_key"


class NewsSubscriptionStore:
    """File-backed store by default; PostgreSQL when Qiqi enables it."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (DATA_DIR / "news_subscriptions.json")
        self._lock = asyncio.Lock()
        self._data: dict[str, dict[str, object]] | None = None

    def _load(self) -> dict[str, dict[str, object]]:
        if self._data is not None:
            return self._data
        if not self._path.is_file():
            self._data = {}
            return self._data
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise NewsStorageError(f"新闻订阅文件读取失败：{error}") from None
        rows = parsed.get("subscriptions") if isinstance(parsed, dict) else None
        if not isinstance(rows, dict):
            raise NewsStorageError("新闻订阅文件格式无效")
        decoded: dict[str, dict[str, object]] = {}
        for key, value in rows.items():
            if isinstance(key, str) and isinstance(value, dict):
                decoded[key] = value
        self._data = decoded
        return self._data

    def _save(self) -> None:
        rows = self._load()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"version": 1, "subscriptions": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._path)

    async def get(self, owner_key: str) -> NewsSubscription | None:
        if postgres_enabled():
            return await self._pg_get(owner_key)
        async with self._lock:
            raw = self._load().get(owner_key)
            return None if raw is None else decode_record(raw)

    async def list_all(self) -> list[NewsSubscription]:
        if postgres_enabled():
            return await self._pg_list()
        async with self._lock:
            return [decode_record(record) for _, record in sorted(self._load().items())]

    async def put(self, subscription: NewsSubscription) -> None:
        if postgres_enabled():
            await self._pg_put(subscription)
            return
        async with self._lock:
            self._load()[subscription.owner_key] = subscription.record()
            self._save()

    async def subscribe(self, subscription: NewsSubscription) -> bool:
        """Create or update-in-place; returns ``True`` only on first creation."""
        key = subscription.owner_key

        def changed(previous: NewsSubscription) -> NewsSubscription:
            return replace(previous, category=subscription.category, updated_at=subscription.updated_at)

        if postgres_enabled():
            pool = await async_connection_pool()
            async with pool.connection() as connection, connection.transaction():
                # Serialise simultaneous first subscriptions, including the absent-row case.
                await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
                cursor = await connection.execute(_SELECT_FOR_UPDATE_SQL, (key,))
                existing = await cursor.fetchone()
                record = changed(decode_record(existing[0])) if existing else subscription
                await connection.execute(_UPSERT_SQL, (key, _jsonb(record.record())))
                return existing is None
        async with self._lock:
            rows = self._load()
            existing = rows.get(key)
            record = changed(decode_record(existing)) if existing else subscription
            rows[key] = record.record()
            self._save()
            return existing is None

    async def update(
        self,
        owner_key: str,
        mutator: Callable[[NewsSubscription], NewsSubscription],
    ) -> NewsSubscription | None:
        if postgres_enabled():
            return await self._pg_update(owner_key, mutator)
        async with self._lock:
            raw = self._load().get(owner_key)
            if raw is None:
                return None
            current = decode_record(raw)
            updated = mutator(current)
            self._load()[owner_key] = updated.record()
            self._save()
            return updated

    async def remove(self, owner_key: str) -> bool:
        if postgres_enabled():
            return await self._pg_remove(owner_key)
        async with self._lock:
            data = self._load()
            if owner_key not in data:
                return False
            data.pop(owner_key)
            self._save()
            return True

    # -- PostgreSQL -------------------------------------------------------
    async def _pg_get(self, owner_key: str) -> NewsSubscription | None:
        pool = await async_connection_pool()
        try:
            async with pool.connection() as connection:
                cursor = await connection.execute(_SELECT_SQL, (owner_key,))
                row = await cursor.fetchone()
        except NewsStorageError:
            raise
        except Exception as error:
            log_postgres_failure("读取新闻订阅", error)
            raise NewsStorageError("PostgreSQL 新闻订阅存储不可用（读取失败）") from None
        return None if row is None else decode_record(row[0])

    async def _pg_list(self) -> list[NewsSubscription]:
        pool = await async_connection_pool()
        try:
            async with pool.connection() as connection:
                cursor = await connection.execute(_LIST_SQL)
                rows = await cursor.fetchall()
        except NewsStorageError:
            raise
        except Exception as error:
            log_postgres_failure("读取新闻订阅列表", error)
            raise NewsStorageError("PostgreSQL 新闻订阅存储不可用（读取失败）") from None
        return [decode_record(row[0]) for row in rows]

    async def _pg_put(self, subscription: NewsSubscription) -> None:
        pool = await async_connection_pool()
        try:
            async with pool.connection() as connection:
                await connection.execute(
                    _UPSERT_SQL,
                    (subscription.owner_key, _jsonb(subscription.record())),
                )
        except NewsStorageError:
            raise
        except Exception as error:
            log_postgres_failure("写入新闻订阅", error)
            raise NewsStorageError("PostgreSQL 新闻订阅存储不可用（写入失败）") from None

    async def _pg_update(
        self,
        owner_key: str,
        mutator: Callable[[NewsSubscription], NewsSubscription],
    ) -> NewsSubscription | None:
        pool = await async_connection_pool()
        try:
            async with pool.connection() as connection:
                async with connection.transaction():
                    cursor = await connection.execute(_SELECT_FOR_UPDATE_SQL, (owner_key,))
                    row = await cursor.fetchone()
                    if row is None:
                        return None
                    updated = mutator(decode_record(row[0]))
                    await connection.execute(
                        _UPSERT_SQL,
                        (owner_key, _jsonb(updated.record())),
                    )
                    return updated
        except NewsStorageError:
            raise
        except Exception as error:
            log_postgres_failure("更新新闻订阅", error)
            raise NewsStorageError("PostgreSQL 新闻订阅存储不可用（更新失败）") from None

    async def _pg_remove(self, owner_key: str) -> bool:
        pool = await async_connection_pool()
        try:
            async with pool.connection() as connection:
                cursor = await connection.execute(_DELETE_SQL, (owner_key,))
                return cursor.rowcount > 0
        except PostgresStorageError:
            raise
        except Exception as error:
            log_postgres_failure("删除新闻订阅", error)
            raise NewsStorageError("PostgreSQL 新闻订阅存储不可用（删除失败）") from None


_store: NewsSubscriptionStore | None = None


def get_subscription_store() -> NewsSubscriptionStore:
    global _store
    if _store is None:
        _store = NewsSubscriptionStore()
    return _store


def set_subscription_store(store: NewsSubscriptionStore | None) -> None:
    """Inject a temporary store (tests or an alternate deployment backend)."""
    global _store
    _store = store


__all__ = [
    "CATEGORIES",
    "CATEGORY_DAILY",
    "CATEGORY_DESCRIPTIONS",
    "CATEGORY_HOURLY",
    "CATEGORY_LABELS",
    "CATEGORY_REALTIME",
    "CATEGORY_TRADING",
    "NEWS_OWNER_PREFIX",
    "SUBSCRIPTION_TABLE",
    "NewsStorageError",
    "NewsSubscription",
    "NewsSubscriptionStore",
    "decode_record",
    "get_subscription_store",
    "set_subscription_store",
]
