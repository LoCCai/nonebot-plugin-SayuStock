"""Adapter detection and message delivery."""

from __future__ import annotations

import base64
from typing import Any
from pathlib import Path

from nonebot.log import logger

from .event import UnifiedEvent, _text, _first_at, _sender_name


def adapter_name(bot: Any) -> str:
    adapter = getattr(bot, "adapter", None)
    name = getattr(adapter, "get_name", None)
    if callable(name):
        try:
            return _text(name())
        except Exception:
            pass
    return _text(getattr(adapter, "__class__", type(adapter)).__name__)


def platform_id(bot: Any) -> str:
    name = adapter_name(bot).lower()
    if "onebot" in name and "v12" in name:
        return "onebot12"
    if "onebot" in name:
        return "onebot11"
    if "qq" in name:
        return "qq"
    if "telegram" in name:
        return "telegram"
    if "discord" in name:
        return "discord"
    return "unknown"


def bot_id(bot: Any) -> str:
    for attr in ("self_id", "bot_id", "app_id"):
        value = getattr(bot, attr, None)
        if value:
            return _text(value)
    return ""


def is_admin(event: Any) -> bool:
    sender = getattr(event, "sender", None)
    role = _text(getattr(sender, "role", "")).lower()
    if role in {"owner", "admin", "administrator"}:
        return True
    author = getattr(event, "author", None)
    member = getattr(author, "member", None)
    roles = getattr(member, "roles", None)
    if roles:
        return any(_text(role).lower() in {"owner", "admin", "administrator"} for role in roles)
    return False


def from_event(bot: Any, event: Any, *, text: str | None = None) -> UnifiedEvent:
    """Convert an adapter event into :class:`UnifiedEvent`."""
    platform = platform_id(bot)
    plain = text
    if plain is None:
        getter = getattr(event, "get_plaintext", None)
        plain = _text(getter()) if callable(getter) else _text(getattr(event, "raw_message", ""))
    user_id = _text(
        getattr(event, "user_id", None)
        or getattr(event, "user_openid", None)
        or (getattr(getattr(event, "author", None), "id", None))
        or (getattr(getattr(event, "author", None), "user_openid", None))
    )
    group_id = getattr(event, "group_id", None) or getattr(event, "group_openid", None)
    channel_id = getattr(event, "channel_id", None)
    guild_id = getattr(event, "guild_id", None)
    message_id = _text(
        getattr(event, "message_id", None) or getattr(event, "id", None) or getattr(event, "event_id", None)
    )
    return UnifiedEvent(
        platform=platform,
        bot_id=bot_id(bot),
        user_id=user_id,
        text=plain.strip(),
        group_id=_text(group_id) or None,
        channel_id=_text(channel_id) or None,
        guild_id=_text(guild_id) or None,
        message_id=message_id,
        at=_first_at(event),
        sender_name=_sender_name(event),
        is_admin=is_admin(event),
        raw_event=event,
        bot=bot,
    )


async def _send(bot: Any, event: Any, message: Any) -> Any:
    return await bot.send(event, message)


async def _image_segment(platform: str, bot: Any, image: bytes | Path, *, caption: str = "") -> Any:
    """Build an adapter-appropriate image segment.

    OneBot v11 accepts ``base64://``.  OneBot v12 needs an uploaded ``file_id``
    (``upload_file`` action); QQ official uses a local file attachment.
    Returns ``None`` when the adapter has no safe builder, letting the caller
    fall back to text.
    """
    if isinstance(image, Path):
        payload = image.read_bytes()
    else:
        payload = image
    encoded = base64.b64encode(payload).decode("ascii")

    if platform == "onebot11":
        from nonebot.adapters.onebot.v11 import MessageSegment

        built = MessageSegment.image("base64://" + encoded)
        if caption:
            return MessageSegment.text(caption) + built
        return built

    if platform == "onebot12":
        from nonebot.adapters.onebot.v12 import MessageSegment

        file_id: str | None = None
        try:
            uploaded = await bot.call_api("upload_file", type="base64", name="sayustock.png", data=encoded)
        except Exception as error:
            logger.debug("[SayuStock] OneBot12 upload_file 失败: {}", error)
            uploaded = None
        if isinstance(uploaded, dict):
            file_id = _text(uploaded.get("file_id") or uploaded.get("id"))
        elif uploaded:
            file_id = _text(uploaded)
        if not file_id:
            # Some implementations accept a base64 URI directly; try it as a
            # non-standard fallback rather than dropping the chart silently.
            file_id = "base64://" + encoded
        built = MessageSegment.image(file_id)
        if caption:
            return MessageSegment.text(caption) + built
        return built

    if platform == "qq":
        try:
            from nonebot.adapters.qq import MessageSegment

            built = MessageSegment.file_image(payload, file_name="sayustock.png")
            if caption:
                return MessageSegment.text(caption) + built
            return built
        except Exception as error:
            logger.debug("[SayuStock] QQ 图片构造失败: {}", error)
            return None
    return None


async def send_text(ue: UnifiedEvent, text: str) -> None:
    if not text:
        return
    await _send(ue.bot, ue.raw_event, text)


async def send_image(ue: UnifiedEvent, image: bytes, *, caption: str = "") -> bool:
    """Send an image, returning ``False`` when the adapter cannot deliver it."""
    segment = await _image_segment(ue.platform, ue.bot, image, caption=caption)
    if segment is None:
        logger.warning("[SayuStock] 当前适配器不支持图片构造：{}", ue.platform)
        return False
    try:
        await _send(ue.bot, ue.raw_event, segment)
        return True
    except Exception as error:
        logger.warning("[SayuStock] 图片发送失败：{}", error)
        return False


async def send_group_result(ue: UnifiedEvent, text: str, images: list[bytes]) -> None:
    """Deliver one response. OneBot v11 groups support native forward nodes."""
    segments = [await _image_segment(ue.platform, ue.bot, image) for image in images]
    segments = [segment for segment in segments if segment is not None]
    if len(segments) == 1 and not text:
        await _send(ue.bot, ue.raw_event, segments[0])
        return
    if ue.platform == "onebot11" and ue.group_id:
        from nonebot.adapters.onebot.v11 import Message, MessageSegment

        nodes = [Message(segment) for segment in segments]
        if text:
            nodes.append(Message(MessageSegment.text(text)))
        if not nodes:
            return
        # The gateway interprets segment arrays literally (including user text).
        await ue.bot.call_api(
            "send_group_forward_msg", group_id=int(ue.group_id),
            messages=[{"type": "node", "data": {
                "uin": ue.bot_id, "name": "七七", "content": list(node),
            }} for node in nodes],
        )
    elif segments:
        message = segments[0].get_message_class()(segments)
        if text:
            message += text
        await _send(ue.bot, ue.raw_event, message)
    elif text:
        await send_text(ue, text)


async def send_result(ue: UnifiedEvent, text: str, image: bytes | None = None) -> None:
    """Send an optional image plus concise text, degrading safely."""
    if ue.group_id or ue.channel_id or ue.guild_id:
        await send_group_result(ue, text, [image] if image else [])
        return
    delivered = False
    if image:
        delivered = await send_image(ue, image)
    if text:
        await send_text(ue, text)
    elif not delivered:
        await send_text(ue, "图片生成失败，请稍后再试。")


__all__ = [
    "adapter_name",
    "bot_id",
    "from_event",
    "is_admin",
    "platform_id",
    "send_image",
    "send_result",
    "send_text",
]
