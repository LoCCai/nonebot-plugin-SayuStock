"""Session plumbing for the original SayuStock SQLModel repositories.

The NoneBot2 branch reuses the upstream models and repositories unchanged.  This
module supplies the small part of ``gsuid_core.utils.database.base_models`` they
need: a lazily created async engine plus the ``with_session`` decorator with the
same nested-write semantics (one session per asyncio task, savepoint for nested
calls).

Storage choice is deliberately fail-closed:

* ``SAYUSTOCK_PAPERTRADE_DATABASE_URL`` -- explicit override, used by tests and
  the isolated PostgreSQL deployment;
* ``SAYUSTOCK_STORAGE=postgres`` -- the Qiqi deployment, reuses the host
  ``src.qi_web.config.get_settings().database_url`` and the dedicated
  ``qiqi_sayustock`` schema (never ``public``);
* otherwise a local SQLite file under ``SAYUSTOCK_DATA_DIR``.

Production credentials are only read when PostgreSQL is explicitly selected.
"""

from __future__ import annotations

import os
import asyncio
import contextvars
from typing import Any
from contextlib import asynccontextmanager

from sqlmodel import Field, SQLModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SCHEMA = "qiqi_sayustock"
_DATABASE_URL_ENV = "SAYUSTOCK_PAPERTRADE_DATABASE_URL"
_STORAGE_ENV = "SAYUSTOCK_STORAGE"
_HOST_DATABASE_URL_ENV = "SAYUSTOCK_DATABASE_URL"

_engine: Any | None = None
_maker: async_sessionmaker[AsyncSession] | None = None
_init_lock = asyncio.Lock()
_write_session: contextvars.ContextVar[tuple[AsyncSession, asyncio.Task[Any]] | None] = contextvars.ContextVar(
    "sayustock_papertrade_write_session",
    default=None,
)


class BaseIDModel(SQLModel):
    """Primary-key model matching the upstream ``BaseIDModel`` contract."""

    id: int = Field(default=None, primary_key=True, title="序号")


def _database_target() -> tuple[str, dict[str, Any]]:
    from nonebot_plugin_sayustock.paths import DATA_DIR
    from nonebot_plugin_sayustock.storage import postgres_enabled, resolve_database_url

    explicit = os.environ.get(_DATABASE_URL_ENV, "").strip()
    if explicit or postgres_enabled():
        from sqlalchemy.engine import make_url

        url = make_url(explicit or resolve_database_url())
        if url.get_backend_name() not in {"postgresql", "postgres"}:
            raise ValueError("模拟盘需要 PostgreSQL 连接")
        return url.set(drivername="postgresql+psycopg"), {
            "connect_args": {"options": "-c search_path=qiqi_sayustock"},
            "pool_pre_ping": True,
        }
    return f"sqlite+aiosqlite:///{DATA_DIR / 'papertrade.db'}", {"connect_args": {"timeout": 10}}


async def _get_maker() -> async_sessionmaker[AsyncSession]:
    global _engine, _maker
    if _maker is not None:
        return _maker
    async with _init_lock:
        if _maker is not None:
            return _maker
        import importlib

        importlib.import_module("SayuStock.utils.database.papertrade_models")
        importlib.import_module("SayuStock.utils.database.macro_models")
        url, options = _database_target()
        _engine = create_async_engine(url, **options)
        if _engine.dialect.name == "sqlite":
            tables = [t for t in SQLModel.metadata.sorted_tables if t.name.startswith(("sayupaper", "sayumacro"))]
            async with _engine.begin() as connection:
                await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=tables))
        _maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
        return _maker


class _LazySessionContext:
    """``async with async_maker() as session`` backed by lazy initialisation."""

    def __init__(self) -> None:
        self._manager: Any | None = None
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        maker = await _get_maker()
        self._manager = maker()
        self.session = await self._manager.__aenter__()
        return self.session

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        if self._manager is None:
            return None
        return await self._manager.__aexit__(exc_type, exc, traceback)


class _LazySessionMaker:
    def __call__(self) -> _LazySessionContext:
        return _LazySessionContext()


async_maker: _LazySessionMaker = _LazySessionMaker()


def _owned_session() -> AsyncSession | None:
    active = _write_session.get()
    if active is None:
        return None
    session, owner = active
    if asyncio.current_task() is not owner:
        return None
    return session


@asynccontextmanager
async def rollback_session():
    async with _LazySessionContext() as session:
        async with session.begin() as transaction:
            if session.bind.dialect.name == "sqlite":
                from sqlalchemy import text
                await session.execute(text("BEGIN"))
            token = _write_session.set((session, asyncio.current_task()))
            try:
                yield
            finally:
                _write_session.reset(token)
                await transaction.rollback()


def with_session(func: Any) -> Any:
    """Run a repository coroutine in one session; nested calls reuse a savepoint."""

    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        active = _owned_session()
        if active is not None:
            async with active.begin_nested():
                return await func(self, active, *args, **kwargs)
        async with _LazySessionContext() as session:
            token = _write_session.set((session, asyncio.current_task()))  # type: ignore[arg-type]
            try:
                result = await func(self, session, *args, **kwargs)
                await session.commit()
                return result
            finally:
                _write_session.reset(token)

    return wrapper


async def reset_engine_for_tests() -> None:
    """Dispose cached engine state so a test can switch databases."""
    global _engine, _maker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _maker = None


__all__ = ["BaseIDModel", "async_maker", "reset_engine_for_tests", "with_session"]
