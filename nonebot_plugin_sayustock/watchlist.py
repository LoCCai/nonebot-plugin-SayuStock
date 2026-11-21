"""Per-user watchlists shared by bot commands, tools and the web console."""

from __future__ import annotations

import json
import asyncio
from typing import Any
from pathlib import Path

from .paths import WATCHLIST_PATH
from .config import get_value


def owner_key(*, platform: str, bot_id: str, user_id: str) -> str:
    return f"{platform}:{bot_id}:{user_id}"


class WatchlistStore:
    def __init__(self, path: Path = WATCHLIST_PATH) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._data: dict[str, list[dict[str, str]]] | None = None

    def _load(self) -> dict[str, list[dict[str, str]]]:
        if self._data is not None:
            return self._data
        if not self._path.is_file():
            self._data = {}
            return self._data
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            parsed = {}
        owners = parsed.get("owners") if isinstance(parsed, dict) else None
        self._data = owners if isinstance(owners, dict) else {}
        return self._data

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"version": 1, "owners": self._load()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._path)

    def _max_items(self) -> int:
        raw = get_value("watchlist.max_items", 30)
        try:
            return max(1, min(int(raw), 100))
        except (TypeError, ValueError):
            return 30

    async def list(self, key: str) -> list[dict[str, str]]:
        async with self._lock:
            return [dict(item) for item in self._load().get(key, [])]

    async def add(self, key: str, code: str, name: str, query: str) -> bool:
        async with self._lock:
            items = self._load().setdefault(key, [])
            if any(item.get("code") == code for item in items):
                return False
            if len(items) >= self._max_items():
                raise ValueError(f"自选最多 {self._max_items()} 只")
            items.append({"code": code, "name": name, "query": query})
            self._save()
            return True

    async def remove(self, key: str, code: str) -> bool:
        async with self._lock:
            items = self._load().get(key, [])
            remaining = [item for item in items if item.get("code") != code]
            if len(remaining) == len(items):
                return False
            self._load()[key] = remaining
            self._save()
            return True

    async def clear(self, key: str) -> int:
        async with self._lock:
            items = self._load().pop(key, [])
            self._save()
            return len(items)

    def snapshot(self) -> dict[str, Any]:
        return {"version": 1, "owners": self._load()}


__all__ = ["WatchlistStore", "owner_key"]
