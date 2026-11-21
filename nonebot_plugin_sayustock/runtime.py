"""Process-wide runtime container."""

from __future__ import annotations

import asyncio
from dataclasses import field, dataclass

from nonebot.log import logger

from .compat import bootstrap
from .config import get_value, ensure_config_file
from .storage import postgres_enabled, verify_postgres_storage
from .artifacts import Artifact
from .watchlist import WatchlistStore

bootstrap.install()

from .services.market import MarketService  # noqa: E402


@dataclass
class Runtime:
    market: MarketService = field(default_factory=MarketService)
    watchlist: WatchlistStore = field(default_factory=WatchlistStore)
    render_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    started: bool = False

    async def startup(self) -> None:
        if self.started:
            return
        ensure_config_file()
        if postgres_enabled():
            await verify_postgres_storage()
            logger.info("[SayuStock] PostgreSQL 存储已就绪（schema=qiqi_sayustock）")
        self.started = True

    @property
    def render_enabled(self) -> bool:
        return bool(get_value("render.enabled", True))

    def render_scale(self) -> float:
        try:
            return max(1.0, min(float(get_value("render.scale", 1.5)), 4.0))
        except (TypeError, ValueError):
            return 1.5


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = Runtime()
    return _runtime


def set_runtime(runtime: Runtime | None) -> None:
    global _runtime
    _runtime = runtime


def artifact_url(artifact: Artifact, *, public_base_url: str = "") -> str:
    base = (public_base_url or "").rstrip("/")
    prefix = f"{base}/qweb/sayustock"
    if artifact.mime.startswith("image/"):
        return f"{prefix}/artifact/{artifact.id}.png"
    return f"{prefix}/artifact/{artifact.id}.html"


__all__ = ["Runtime", "artifact_url", "get_runtime", "set_runtime"]
