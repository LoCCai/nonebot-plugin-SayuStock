"""Multi-symbol PE / PB / dividend-yield history comparison.

The data and chart builders import standalone; GsCore SV commands live in
``_gscore_commands``.
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
