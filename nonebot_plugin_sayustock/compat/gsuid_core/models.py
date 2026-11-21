"""Minimal ``gsuid_core.models`` shim for the reused papertrade core.

Only the fields read by ``stock_papertrade`` helpers are provided.  Business code
must never construct this type from untrusted input: it exists solely so the
legacy scope/permission helpers can be reused unchanged.
"""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass


@dataclass(slots=True)
class Event:
    user_id: str = ""
    group_id: str | None = None
    bot_id: str = ""
    user_pm: int | str | bool | None = 6
    user_type: str = "group"
    bot_self_id: str = ""
    WS_BOT_ID: str = ""
    real_bot_id: str = ""
    text: str = ""
    raw_event: Any = None
    bot: Any = None


__all__ = ["Event"]
