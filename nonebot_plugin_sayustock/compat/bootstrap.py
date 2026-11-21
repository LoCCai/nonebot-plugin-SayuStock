"""Install the lightweight GsCore shim before importing legacy SayuStock code."""

from __future__ import annotations

import sys
from pathlib import Path

_COMPAT_DIR = Path(__file__).resolve().parent
_INSTALLED = False


def install() -> None:
    """Put the shim ahead of any installed GsCore copy (idempotent)."""
    global _INSTALLED
    if _INSTALLED:
        return
    path = str(_COMPAT_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    _INSTALLED = True


__all__ = ["install"]
