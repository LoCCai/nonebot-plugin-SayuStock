"""QWeb host integration boundary.

The plugin never authenticates a web request itself.  It consumes the host
FastAPI dependencies from ``src.qi_web.dependencies`` and trusts only their
returned account; client-supplied identity, admin and group headers are
ignored.  Without that host module the web console is unavailable.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable
from dataclasses import dataclass

HOST_DEPENDENCIES_MODULE = "src.qi_web.dependencies"
CSRF_HEADER = "X-CSRF-Token"


class HostUnavailableError(RuntimeError):
    """The QWeb host FastAPI dependencies are not importable."""


class WebIdentityError(RuntimeError):
    """The host dependency did not return an account with a usable QQ id."""


@dataclass(frozen=True, slots=True)
class HostDependencies:
    require_membership: Callable[..., Any]
    admin_account: Callable[..., Any]
    csrf_protected: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class WebAccount:
    qq: str

    @classmethod
    def from_host(cls, account: Any) -> "WebAccount":
        qq = str(getattr(account, "qq", "") or "").strip()
        if not qq:
            raise WebIdentityError("宿主账户缺少 account.qq")
        return cls(qq=qq)


def load_host_dependencies() -> HostDependencies | None:
    """Return the host auth callables, or ``None`` when QWeb is absent."""
    try:
        module = importlib.import_module(HOST_DEPENDENCIES_MODULE)
    except Exception:
        return None
    require_membership = getattr(module, "require_membership", None)
    admin_account = getattr(module, "admin_account", None)
    csrf_protected = getattr(module, "csrf_protected", None)
    if not callable(require_membership) or not callable(admin_account) or not callable(csrf_protected):
        return None
    return HostDependencies(
        require_membership=require_membership,
        admin_account=admin_account,
        csrf_protected=csrf_protected,
    )


__all__ = [
    "CSRF_HEADER",
    "HOST_DEPENDENCIES_MODULE",
    "HostDependencies",
    "HostUnavailableError",
    "WebAccount",
    "WebIdentityError",
    "load_host_dependencies",
]
