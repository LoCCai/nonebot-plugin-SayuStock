"""Minimal GsCore compatibility surface used by SayuStock's data/render layers.

The NoneBot2 branch deliberately keeps the original ``SayuStock`` package as the
single source of market, indicator and rendering truth.  Those modules import a
few framework helpers at module import time (most notably ``logger``); this
shim supplies only the pieces required to run them outside GsCore.

It intentionally does **not** provide ``gsuid_core.sv`` so that
``SayuStock/__init__.py`` does not boot the GsCore plugin registration path when
this branch is active.  ``gsuid_core.bot`` is a type-only placeholder used by the
reused chart modules.
"""

from __future__ import annotations

__version__ = "0.0.0-sayustock-nb2"
