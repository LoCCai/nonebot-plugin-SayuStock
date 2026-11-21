"""Web console package."""

from __future__ import annotations

import secrets

from ..paths import STATIC_DIR, TEMPLATES_DIR

ASSET_TOKEN = secrets.token_urlsafe(18)

from .api import ROUTER_PREFIX, get_router, mount_webui  # noqa: E402

__all__ = [
    "ASSET_TOKEN",
    "ROUTER_PREFIX",
    "STATIC_DIR",
    "TEMPLATES_DIR",
    "get_router",
    "mount_webui",
]
