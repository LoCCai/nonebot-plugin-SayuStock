"""No-op ``ai_return`` used when the plugin runs without GsCore's AI runtime.

The NoneBot2 branch hands the same semantic text to ``CommandResult`` and the
host chat, so legacy renderers still call ``ai_return`` but must not fail when
the Core trigger bus is absent.
"""

from __future__ import annotations

from typing import Any


def ai_return(text: Any, *args: Any, **kwargs: Any) -> None:
    del text, args, kwargs


__all__ = ["ai_return"]
