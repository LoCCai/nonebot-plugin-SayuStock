"""Layered configuration: defaults -> ``config.json`` -> environment overrides."""

from __future__ import annotations

import os
import copy
import json
import threading
from typing import Any
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .paths import CONFIG_PATH

DEFAULTS: dict[str, Any] = {
    "market": {
        "provider_chain": [],
        "quote_ttl_seconds": 8,
        "board_ttl_seconds": 30,
        "request_timeout_seconds": 20,
    },
    "render": {
        "enabled": True,
        "scale": 1.25,
        "timeout_seconds": 45,
        "cache_ttl_seconds": 120,
    },
    # The page is only mounted when the QWeb host provides its auth dependencies.
    "web": {
        "enabled": True,
        "public_base_url": "",
    },
    "watchlist": {"max_items": 30},
}


class PluginConfig(BaseModel):
    """NoneBot ``PluginMetadata.config`` and environment-facing options."""

    model_config = ConfigDict(extra="ignore")

    sayustock_data_dir: str | None = None
    sayustock_storage: str = "file"
    sayustock_web_enabled: bool = True
    sayustock_public_base_url: str = ""
    sayustock_render_enabled: bool = True


_ENV_OVERRIDES: dict[str, tuple[str, str, type]] = {
    "market.provider_chain": ("SAYUSTOCK_MARKET_CHAIN", "market", "list"),
    "web.enabled": ("SAYUSTOCK_WEB_ENABLED", "web", "bool"),
    "web.public_base_url": ("SAYUSTOCK_PUBLIC_BASE_URL", "web", "str"),
    "render.enabled": ("SAYUSTOCK_RENDER_ENABLED", "render", "bool"),
}

_lock = threading.RLock()
_cache: dict[str, Any] | None = None
_cache_mtime: float | None = None


def _coerce(raw: str, kind: str) -> Any:
    if kind == "bool":
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if kind == "list":
        return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]
    return raw.strip()


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_config(*, force: bool = False) -> dict[str, Any]:
    """Load the merged configuration, re-reading the file when it changes."""
    global _cache, _cache_mtime
    with _lock:
        try:
            mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            mtime = None
        if _cache is not None and not force and _cache_mtime == mtime:
            return copy.deepcopy(_cache)

        config = deep_merge(DEFAULTS, _read_json(CONFIG_PATH))
        for dotted, (env_name, section, kind) in _ENV_OVERRIDES.items():
            raw = os.environ.get(env_name)
            if raw is None or not raw.strip():
                continue
            key = dotted.split(".", 1)[1]
            config.setdefault(section, {})[key] = _coerce(raw, kind)
        _cache = config
        _cache_mtime = mtime
        return copy.deepcopy(config)


def get_value(dotted: str, default: Any = None) -> Any:
    node: Any = load_config()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def save_config(config: dict[str, Any]) -> None:
    """Atomically persist a full configuration document."""
    global _cache, _cache_mtime
    with _lock:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG_PATH.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(CONFIG_PATH)
        _cache = deep_merge(DEFAULTS, config)
        try:
            _cache_mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            _cache_mtime = None


def update_config(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a patch into the persisted config and return the new document."""
    with _lock:
        current = load_config(force=True)
        merged = deep_merge(current, patch)
        save_config(merged)
        return merged


def ensure_config_file() -> Path:
    """Create a documented default config on first run."""
    if not CONFIG_PATH.exists():
        save_config(load_config())
    return CONFIG_PATH


__all__ = [
    "DEFAULTS",
    "PluginConfig",
    "deep_merge",
    "ensure_config_file",
    "get_value",
    "load_config",
    "save_config",
    "update_config",
]
