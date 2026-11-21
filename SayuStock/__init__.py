"""SayuStock business source tree.

The original distribution is a GsCore plugin.  The ``nonebot2`` branch keeps
this tree as the single source of truth for market data, indicators and
rendering, while ``nonebot_plugin_sayustock`` supplies the NoneBot2 runtime.
When the GsCore framework is absent (the NoneBot2 case) the plugin-level
registration is skipped so that importing ``SayuStock.utils.market`` does not
drag in GsCore's AI/DB stack.
"""

from __future__ import annotations

try:
    from gsuid_core.sv import Plugins as _Plugins
except ImportError:  # NoneBot2 branch: no GsCore registration
    _Plugins = None

if _Plugins is not None:
    from . import (
        stock_agent,  # noqa: F401
        stock_macro,  # noqa: F401
        stock_analysis,  # noqa: F401
        stock_papertrade,  # noqa: F401
        stock_holdings_analysis,  # noqa: F401
    )

    _Plugins(
        name="SayuStock",
        force_prefix=["a", "股票"],
        allow_empty_prefix=True,
    )

__all__ = ["__version__"]
__version__ = "0.9.0-nb2.1"
