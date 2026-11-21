"""QWeb page package."""

from __future__ import annotations

from .api import ROUTER_PREFIX, get_router, mount_webui
from .host import WebAccount, HostDependencies, load_host_dependencies
from ..paths import STATIC_DIR, TEMPLATES_DIR

__all__ = [
    "HostDependencies",
    "ROUTER_PREFIX",
    "STATIC_DIR",
    "TEMPLATES_DIR",
    "WebAccount",
    "get_router",
    "load_host_dependencies",
    "mount_webui",
]
