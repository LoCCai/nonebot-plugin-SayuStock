"""Protocol-neutral event and delivery helpers.

Supported adapters:

* OneBot v11 (``nonebot.adapters.onebot.v11``)
* OneBot v12 (``nonebot.adapters.onebot.v12``)
* QQ official bot (``nonebot.adapters.qq``)

All business code receives :class:`UnifiedEvent`, never an adapter class.
"""

from __future__ import annotations

from typing import Any
from dataclasses import field, dataclass


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _first_at(event: Any) -> str:
    """Return the first ``at`` target, ignoring the bot itself when possible."""
    try:
        message = event.get_message()
    except Exception:
        return ""
    for segment in message:
        if getattr(segment, "type", "") != "at":
            continue
        data = getattr(segment, "data", {}) or {}
        target = _text(data.get("qq") or data.get("user_id") or data.get("id"))
        if target and target != "all":
            return target
    return ""


def _sender_name(event: Any) -> str:
    sender = getattr(event, "sender", None)
    for attr in ("card", "nickname", "name"):
        value = getattr(sender, attr, None)
        if value:
            return _text(value)
    author = getattr(event, "author", None)
    for attr in ("username", "nickname", "name"):
        value = getattr(author, attr, None)
        if value:
            return _text(value)
    return ""


@dataclass(slots=True)
class UnifiedEvent:
    """A stable view over the three supported NoneBot adapters."""

    platform: str
    bot_id: str
    user_id: str
    text: str
    group_id: str | None = None
    channel_id: str | None = None
    guild_id: str | None = None
    message_id: str = ""
    at: str = ""
    sender_name: str = ""
    is_admin: bool = False
    raw_event: Any = None
    bot: Any = None
    _extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_group(self) -> bool:
        return bool(self.group_id or self.channel_id or self.guild_id)

    @property
    def session_id(self) -> str:
        peer = self.group_id or self.channel_id or self.guild_id or self.user_id
        return f"{self.platform}:{self.bot_id}:{peer}"

    @property
    def actor_id(self) -> str:
        return self.at or self.user_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "bot_id": self.bot_id,
            "user_id": self.user_id,
            "group_id": self.group_id,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "text": self.text,
            "sender_name": self.sender_name,
            "is_group": self.is_group,
        }


__all__ = ["UnifiedEvent"]
