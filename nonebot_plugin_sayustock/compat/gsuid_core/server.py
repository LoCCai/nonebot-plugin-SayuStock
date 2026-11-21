"""Startup hook shim.

The legacy module ``utils/database/papertrade_migration.py`` decorates its
migration entry with ``@on_core_start_before``.  In the NoneBot2 branch there is
no Core startup bus, so the decorator only records the hook and returns the
function unchanged; the migration itself is a no-op because SQLite/PostgreSQL
tables are created from the SQLModel metadata on first use.
"""

from __future__ import annotations

from typing import Any, Callable


def _register(priority: int) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    del priority

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        hooks.append(func)
        return func

    return decorator


hooks: list[Callable[..., Any]] = []


def on_core_start_before(priority: int = 0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    return _register(priority)


def on_core_start(priority: int = 0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    return _register(priority)


__all__ = ["hooks", "on_core_start", "on_core_start_before"]
