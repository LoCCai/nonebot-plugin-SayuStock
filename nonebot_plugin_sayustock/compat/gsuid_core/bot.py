"""Type-only ``Bot`` placeholder for the reused SayuStock chart modules.

The original ``stock_ai`` / ``stock_stockinfo`` renderers import ``Bot`` for
annotations but never construct one; the NoneBot2 layer delivers
``CommandResult`` itself.  This shim is intentionally inert so importing those
modules does not boot GsCore's bot runtime.
"""

from __future__ import annotations

from typing import Any


class Bot:
    """Placeholder matching the annotation surface the legacy code touches."""

    async def send(self, message: Any, **kwargs: Any) -> Any:
        raise RuntimeError("兼容层 Bot 不发送消息；请使用 CommandResult 交付")


__all__ = ["Bot"]
