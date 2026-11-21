"""Storage backend selection and fail-closed PostgreSQL plumbing.

File storage remains the standalone default.  The Qiqi deployment sets
``SAYUSTOCK_STORAGE=postgres`` and keeps its connection string in the QWeb
host settings; this module never logs or echoes a database URL.
"""

from __future__ import annotations

import os
import asyncio
import threading
from typing import Any

from pydantic import SecretStr
from nonebot.log import logger

SCHEMA = "qiqi_sayustock"
WATCHLIST_TABLE = f"{SCHEMA}.watchlist"
ARTIFACT_TABLE = f"{SCHEMA}.artifact"

_STORAGE_ENV = "SAYUSTOCK_STORAGE"
_DATABASE_URL_ENV = "SAYUSTOCK_DATABASE_URL"
_FILE_MODES = {"", "file", "filesystem", "local"}
_POSTGRES_MODES = {"postgres", "postgresql"}
_POOL_TIMEOUT_SECONDS = 10.0


class StorageConfigurationError(RuntimeError):
    """The selected storage backend is not configured correctly."""


class PostgresStorageError(RuntimeError):
    """A PostgreSQL operation failed without exposing connection details."""


_sync_lock = threading.Lock()
_sync_pool: Any | None = None
_async_pools: dict[asyncio.AbstractEventLoop, Any] = {}
_async_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _driver_storage_value() -> str:
    try:
        from nonebot import get_driver

        value = get_driver().config.sayustock_storage
    except Exception:
        return ""
    return value.strip() if isinstance(value, str) else ""


def storage_mode() -> str:
    """Return ``file`` or ``postgres``; reject unknown modes (fail closed)."""
    raw = os.environ.get(_STORAGE_ENV, "")
    if not raw.strip():
        raw = _driver_storage_value()
    mode = raw.strip().lower()
    if mode in _FILE_MODES:
        return "file"
    if mode in _POSTGRES_MODES:
        return "postgres"
    raise StorageConfigurationError("SAYUSTOCK_STORAGE 仅支持 file 或 postgres，未知值已拒绝")


def postgres_enabled() -> bool:
    return storage_mode() == "postgres"


def _coerce_database_url(raw: object) -> str:
    if isinstance(raw, SecretStr):
        value = raw.get_secret_value().strip()
    elif isinstance(raw, str):
        value = raw.strip()
    else:
        raise StorageConfigurationError("PostgreSQL 的 database_url 必须是 str 或 SecretStr")
    if not value:
        raise StorageConfigurationError("PostgreSQL 存储已启用，但 database_url 为空")
    return value


def _libpq_url(value: str, ssl_mode: str = "auto") -> str:
    """Translate a QWeb SQLAlchemy URI while preserving its encoded credentials."""
    from urllib.parse import urlsplit, parse_qsl, urlencode

    if "://" not in value:
        return value
    parts = urlsplit(value)
    if parts.scheme.split("+", 1)[0] not in {"postgres", "postgresql"}:
        raise ValueError("Unsupported PostgreSQL URL scheme")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "ssl" in query:
        query.setdefault("sslmode", query.pop("ssl"))
    if ssl_mode and ssl_mode != "auto":
        query.setdefault("sslmode", ssl_mode)
    suffix = "?" + urlencode(query) if query else ""
    return f"postgresql://{parts.netloc}{parts.path}{suffix}"


def resolve_database_url() -> str:
    """Resolve the DSN; only the QWeb host settings and test env are trusted.

    The returned value is internal.  Callers must not print it, log it, return
    it from an API, or attach it to an exception message.
    """
    override = os.environ.get(_DATABASE_URL_ENV, "").strip()
    if override:
        try:
            return _libpq_url(override)
        except ValueError:
            raise StorageConfigurationError("PostgreSQL 的 database_url 格式无效") from None
    try:
        from src.qi_web.config import get_settings
    except Exception:
        raise StorageConfigurationError(
            "PostgreSQL 存储已启用，但无法加载宿主 src.qi_web.config.get_settings()"
        ) from None
    try:
        settings = get_settings()
        raw = settings.database_url
    except Exception:
        raise StorageConfigurationError("PostgreSQL 存储已启用，但无法从宿主配置读取 database_url") from None
    ssl_mode = "auto"
    try:
        configured_ssl_mode = settings.database_ssl_mode
    except AttributeError:
        pass
    else:
        if isinstance(configured_ssl_mode, str) and configured_ssl_mode.strip():
            ssl_mode = configured_ssl_mode.strip()
    try:
        return _libpq_url(_coerce_database_url(raw), ssl_mode)
    except ValueError:
        raise StorageConfigurationError("PostgreSQL 的 database_url 格式无效") from None


def log_postgres_failure(operation: str, error: BaseException) -> None:
    """Log only the exception class so a DSN cannot leak through messages."""
    logger.error("[SayuStock] PostgreSQL {}失败（{}）", operation, type(error).__name__)


def _open_sync_pool(dsn: str) -> Any:
    pool = None
    try:
        from psycopg_pool import ConnectionPool
    except ImportError:
        raise StorageConfigurationError("PostgreSQL 存储需要安装 psycopg[binary,pool]>=3.2,<4") from None
    try:
        pool = ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=4,
            timeout=_POOL_TIMEOUT_SECONDS,
            open=False,
        )
        pool.open(wait=True, timeout=_POOL_TIMEOUT_SECONDS)
        return pool
    except Exception as error:
        if pool is not None:
            try:
                pool.close()
            except Exception:
                pass
        log_postgres_failure("连接数据库", error)
        raise PostgresStorageError("PostgreSQL 存储连接不可用") from None


def sync_connection_pool() -> Any:
    """Return the process-wide synchronous pool used by artifact indexes."""
    global _sync_pool
    if _sync_pool is not None:
        return _sync_pool
    with _sync_lock:
        if _sync_pool is None:
            _sync_pool = _open_sync_pool(resolve_database_url())
        return _sync_pool


async def async_connection_pool() -> Any:
    """Return the pool for the running event loop used by watchlists."""
    loop = asyncio.get_running_loop()
    pool = _async_pools.get(loop)
    if pool is not None:
        return pool
    lock = _async_locks.setdefault(loop, asyncio.Lock())
    async with lock:
        pool = _async_pools.get(loop)
        if pool is not None:
            return pool
        pool = None
        dsn = resolve_database_url()
        try:
            from psycopg_pool import AsyncConnectionPool
        except ImportError:
            raise StorageConfigurationError("PostgreSQL 存储需要安装 psycopg[binary,pool]>=3.2,<4") from None
        try:
            pool = AsyncConnectionPool(
                conninfo=dsn,
                min_size=1,
                max_size=4,
                timeout=_POOL_TIMEOUT_SECONDS,
                open=False,
            )
            await pool.open()
            await pool.wait(timeout=_POOL_TIMEOUT_SECONDS)
        except Exception as error:
            if pool is not None:
                try:
                    await pool.close()
                except Exception:
                    pass
            log_postgres_failure("连接数据库", error)
            raise PostgresStorageError("PostgreSQL 存储连接不可用") from None
        _async_pools[loop] = pool
        return pool


async def verify_postgres_storage() -> None:
    """Validate connectivity and the two externally managed tables at startup."""
    pool = await async_connection_pool()
    try:
        async with pool.connection() as connection:
            await connection.execute(f"SELECT 1 FROM {WATCHLIST_TABLE} LIMIT 1")
            await connection.execute(f"SELECT 1 FROM {ARTIFACT_TABLE} LIMIT 1")
    except Exception as error:
        log_postgres_failure("校验存储表", error)
        raise PostgresStorageError(
            "PostgreSQL 存储表不可用；请确认 qiqi_sayustock.watchlist/artifact 已由运维创建"
        ) from None


async def close_postgres_pools() -> None:
    """Close the pools owned by the current process/event loop."""
    global _sync_pool
    sync_pool = _sync_pool
    _sync_pool = None
    if sync_pool is not None:
        try:
            await asyncio.to_thread(sync_pool.close)
        except Exception as error:
            log_postgres_failure("关闭同步连接池", error)

    loop = asyncio.get_running_loop()
    async_pool = _async_pools.pop(loop, None)
    _async_locks.pop(loop, None)
    if async_pool is not None:
        try:
            await async_pool.close()
        except Exception as error:
            log_postgres_failure("关闭异步连接池", error)


__all__ = [
    "ARTIFACT_TABLE",
    "SCHEMA",
    "WATCHLIST_TABLE",
    "PostgresStorageError",
    "StorageConfigurationError",
    "async_connection_pool",
    "close_postgres_pools",
    "log_postgres_failure",
    "postgres_enabled",
    "resolve_database_url",
    "storage_mode",
    "sync_connection_pool",
    "verify_postgres_storage",
]
