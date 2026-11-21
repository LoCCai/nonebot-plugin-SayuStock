"""PostgreSQL watchlist storage with row-level locking.

Each owner is one row.  Mutations take ``SELECT ... FOR UPDATE`` inside a
transaction, so two bot processes appending to the same owner cannot lose an
item.  The JSONB document is the ordered list itself; empty lists are kept.
"""

from __future__ import annotations

import copy
from typing import Any

from .config import get_value
from .storage import (
    WATCHLIST_TABLE,
    PostgresStorageError,
    log_postgres_failure,
    resolve_database_url,
    async_connection_pool,
)

_SELECT_SQL = f"SELECT items FROM {WATCHLIST_TABLE} WHERE owner_key = %s"
_SELECT_FOR_UPDATE_SQL = f"{_SELECT_SQL} FOR UPDATE"
_INSERT_EMPTY_SQL = (
    f"INSERT INTO {WATCHLIST_TABLE} (owner_key, items) VALUES (%s, %s) ON CONFLICT (owner_key) DO NOTHING"
)
_UPDATE_SQL = f"UPDATE {WATCHLIST_TABLE} SET items = %s WHERE owner_key = %s"


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError:
        raise PostgresStorageError("PostgreSQL 存储需要安装 psycopg[binary,pool]") from None
    return Jsonb(value)


def _decode_items(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise PostgresStorageError("自选数据格式无效：items 不是 JSON 数组")
    items: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict) or not all(isinstance(key, str) for key in entry):
            raise PostgresStorageError("自选数据格式无效：条目必须是字符串键对象")
        items.append(copy.deepcopy(entry))
    return items


class PostgresWatchlist:
    """Async watchlist backend; one instance can serve the whole process."""

    def __init__(self) -> None:
        # Resolve eagerly so a PG deployment with missing host settings fails
        # closed at runtime construction instead of silently using files.
        resolve_database_url()

    async def list(self, owner_key: str) -> list[dict[str, Any]]:
        try:
            pool = await async_connection_pool()
            async with pool.connection() as connection:
                cursor = await connection.execute(_SELECT_SQL, (owner_key,))
                row = await cursor.fetchone()
        except PostgresStorageError:
            raise
        except Exception as error:
            log_postgres_failure("读取自选", error)
            raise PostgresStorageError("PostgreSQL 自选存储不可用（读取失败）") from None
        if row is None:
            return []
        return _decode_items(row[0])

    async def add(self, owner_key: str, code: str, name: str, query: str) -> bool:
        max_items = _max_items()
        try:
            pool = await async_connection_pool()
            async with pool.connection() as connection:
                async with connection.transaction():
                    await connection.execute(_INSERT_EMPTY_SQL, (owner_key, _jsonb([])))
                    cursor = await connection.execute(_SELECT_FOR_UPDATE_SQL, (owner_key,))
                    row = await cursor.fetchone()
                    if row is None:
                        raise PostgresStorageError("PostgreSQL 自选行不存在")
                    items = _decode_items(row[0])
                    if any(item.get("code") == code for item in items):
                        return False
                    if len(items) >= max_items:
                        raise ValueError(f"自选最多 {max_items} 只")
                    items.append({"code": code, "name": name, "query": query})
                    await connection.execute(_UPDATE_SQL, (_jsonb(items), owner_key))
            return True
        except (ValueError, PostgresStorageError):
            raise
        except Exception as error:
            log_postgres_failure("写入自选", error)
            raise PostgresStorageError("PostgreSQL 自选存储不可用（写入失败）") from None

    async def remove(self, owner_key: str, code: str) -> bool:
        try:
            pool = await async_connection_pool()
            async with pool.connection() as connection:
                async with connection.transaction():
                    cursor = await connection.execute(_SELECT_FOR_UPDATE_SQL, (owner_key,))
                    row = await cursor.fetchone()
                    if row is None:
                        return False
                    items = _decode_items(row[0])
                    remaining = [item for item in items if item.get("code") != code]
                    if len(remaining) == len(items):
                        return False
                    await connection.execute(_UPDATE_SQL, (_jsonb(remaining), owner_key))
            return True
        except PostgresStorageError:
            raise
        except Exception as error:
            log_postgres_failure("删除自选", error)
            raise PostgresStorageError("PostgreSQL 自选存储不可用（删除失败）") from None

    async def clear(self, owner_key: str) -> int:
        try:
            pool = await async_connection_pool()
            async with pool.connection() as connection:
                async with connection.transaction():
                    cursor = await connection.execute(_SELECT_FOR_UPDATE_SQL, (owner_key,))
                    row = await cursor.fetchone()
                    if row is None:
                        return 0
                    items = _decode_items(row[0])
                    await connection.execute(_UPDATE_SQL, (_jsonb([]), owner_key))
            return len(items)
        except PostgresStorageError:
            raise
        except Exception as error:
            log_postgres_failure("清空自选", error)
            raise PostgresStorageError("PostgreSQL 自选存储不可用（清空失败）") from None


def _max_items() -> int:
    raw = get_value("watchlist.max_items", 30)
    try:
        return max(1, min(int(raw), 100))
    except (TypeError, ValueError):
        return 30


def snapshot_owners() -> dict[str, list[dict[str, Any]]]:
    """Read every owner for exports; mutations still use the async backend."""
    from .storage import sync_connection_pool

    with sync_connection_pool().connection() as connection:
        rows = connection.execute(f"SELECT owner_key, items FROM {WATCHLIST_TABLE}").fetchall()
    return {owner: _decode_items(items) for owner, items in rows}


__all__ = ["PostgresWatchlist", "snapshot_owners"]
