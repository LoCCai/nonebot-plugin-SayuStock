"""Import the reused SayuStock domain packages without their GsCore entry points.

``SayuStock/stock_papertrade/__init__.py`` (and its macro sibling) register SV
triggers, AI tools and knowledge bases at import time.  Those need the GsCore
framework and, for the tool layer, ``pydantic_ai``.  The NoneBot2 branch only
wants the repositories, strategies and rendering helpers, so it installs a
package shell for the two subpackages and imports their submodules directly.

The shell approach is the same one the test suite uses (``test/conftest.py``):
the outer ``SayuStock`` package stays real so ``sayustock`` keeps working.
"""

from __future__ import annotations

import sys
import types
import importlib
from typing import Any
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_SAYU_ROOT = _PLUGIN_ROOT / "SayuStock"
_SHELLS: tuple[tuple[str, str], ...] = (
    ("SayuStock.stock_papertrade", "stock_papertrade"),
    ("SayuStock.stock_macro", "stock_macro"),
)


def _core_available() -> bool:
    """A real GsCore provides ``gsuid_core.sv``; then its own ``__init__`` runs."""
    try:
        import gsuid_core.sv  # noqa: F401
    except ImportError:
        return False
    return True


def install() -> None:
    """Replace the two domain packages with path-only shells (idempotent)."""
    if _core_available():
        # Real GsCore deployment: keep the package __init__ (SV/AI registration).
        return
    if "SayuStock" not in sys.modules:
        import SayuStock  # noqa: F401  # real package, no plugin registration without Core

    for name, directory in _SHELLS:
        existing = sys.modules.get(name)
        if existing is not None and getattr(existing, "__path__", None) is not None:
            continue
        path = _SAYU_ROOT / directory
        module = types.ModuleType(name)
        module.__path__ = [str(path)]  # type: ignore[attr-defined]
        module.__package__ = name
        module.__file__ = str(path / "__init__.py")
        sys.modules[name] = module


def module(name: str) -> Any:
    """Import ``SayuStock.stock_papertrade.<name>`` / ``stock_macro.<name>``."""
    install()
    for prefix in ("SayuStock.stock_papertrade.", "SayuStock.stock_macro."):
        if name.startswith(prefix):
            return importlib.import_module(name)
    raise ValueError(f"只允许加载模拟盘/宏观子模块：{name}")


def papertrade(name: str) -> Any:
    return module(f"SayuStock.stock_papertrade.{name}")


def macro(name: str) -> Any:
    return module(f"SayuStock.stock_macro.{name}")


__all__ = ["install", "macro", "module", "papertrade"]
