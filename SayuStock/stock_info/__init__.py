"""Stock overview / fund pages.

The standalone NoneBot2 runtime imports this package for its data and render
helpers only.  Legacy GsCore command registration lives in
``_gscore_commands`` and is imported only when a real ``gsuid_core.sv`` is
available, so the compat shim never boots SV matchers.
"""

from __future__ import annotations


def _gscore_available() -> bool:
    try:
        from gsuid_core.sv import SV  # noqa: F401
    except ImportError:
        return False
    return True


if _gscore_available():
    from . import _gscore_commands  # noqa: F401
