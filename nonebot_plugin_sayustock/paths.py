"""Filesystem layout for the NoneBot2 branch."""

from __future__ import annotations

import os
from pathlib import Path

PLUGIN_NAME = "sayustock"
PLUGIN_DIR = Path(__file__).resolve().parent
REPO_DIR = PLUGIN_DIR.parent

_ENV_DATA_DIR = "SAYUSTOCK_DATA_DIR"


def _configured_data_dir() -> str | None:
    value = os.environ.get(_ENV_DATA_DIR, "").strip()
    if value:
        return value
    try:
        from nonebot import get_driver

        raw = getattr(get_driver().config, "sayustock_data_dir", None)
        if raw:
            return str(raw).strip()
    except Exception:
        pass
    return None


def resolve_data_dir() -> Path:
    """Return the writable plugin data directory, creating it on demand."""
    configured = _configured_data_dir()
    base = Path(configured).expanduser() if configured else Path.cwd() / "data" / PLUGIN_NAME
    base = base.resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


DATA_DIR = resolve_data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
CACHE_DIR = DATA_DIR / "cache"
ARTIFACT_DIR = DATA_DIR / "artifacts"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"
WEB_DIR = PLUGIN_DIR / "web"
STATIC_DIR = WEB_DIR / "static"
TEMPLATES_DIR = WEB_DIR / "templates"

for _directory in (CACHE_DIR, ARTIFACT_DIR):
    _directory.mkdir(parents=True, exist_ok=True)


__all__ = [
    "ARTIFACT_DIR",
    "CACHE_DIR",
    "CONFIG_PATH",
    "DATA_DIR",
    "PLUGIN_DIR",
    "PLUGIN_NAME",
    "REPO_DIR",
    "STATIC_DIR",
    "TEMPLATES_DIR",
    "WATCHLIST_PATH",
    "WEB_DIR",
    "resolve_data_dir",
]
