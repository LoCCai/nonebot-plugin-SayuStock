"""Optional MoEllmChats integration: expose SayuStock tools to its LLM runtime."""

from __future__ import annotations

import json
from typing import Any

from nonebot.log import logger

from .tools import TOOLS, run_tool
from ..platform import from_event
from .contracts import ToolContext, ToolOutcome

_REGISTERED = False
_NAMES: list[str] = []


def _context_from_tool(tool_context: Any) -> ToolContext:
    event = getattr(tool_context, "event", None)
    bot = getattr(tool_context, "bot", None)
    if event is not None and bot is not None:
        unified = from_event(bot, event)
        return ToolContext(
            platform=unified.platform,
            user_id=unified.user_id,
            session_id=unified.session_id,
            bot_id=unified.bot_id,
            event=event,
            bot=bot,
            request_id=getattr(tool_context, "request_id", None),
            confirmed=bool(getattr(tool_context, "confirmed", False)),
        )
    return ToolContext(
        platform="unknown",
        user_id="unknown",
        session_id="unknown",
        request_id=getattr(tool_context, "request_id", None),
        confirmed=bool(getattr(tool_context, "confirmed", False)),
    )


def _compact_structured(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "…"
    if isinstance(value, dict):
        return {str(key): _compact_structured(item, depth=depth + 1) for key, item in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_compact_structured(item, depth=depth + 1) for item in value[:30]]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


def _structured_for_tool(outcome: ToolOutcome) -> dict[str, Any]:
    compact = _compact_structured(outcome.structured or {})
    try:
        encoded = json.dumps(compact, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"note": "结构化结果无法序列化"}
    if len(encoded.encode("utf-8")) > 24000:
        return {"note": "结构化结果过大，已省略", "preview": encoded[:12000]}
    return compact


def _handler_for(name: str):
    async def handler(*, _tool_context: Any, **kwargs: Any) -> Any:
        from nonebot_plugin_moellmchats import ToolResult

        context = _context_from_tool(_tool_context)
        try:
            outcome = await run_tool(name, kwargs, context)
        except Exception as error:
            logger.warning("[SayuStock] MoEllm 工具 {} 失败: {}", name, error)
            return ToolResult(text=f"{name} 执行失败：{error}")
        images = outcome.public().get("images") or []
        return ToolResult(
            text=outcome.text[:12000],
            images=images[:4],
            structured=_structured_for_tool(outcome),
            metadata={
                "artifacts": [{"id": item.id, "title": item.title, "kind": item.kind} for item in outcome.artifacts],
                **outcome.metadata,
            },
        )

    return handler


def register() -> bool:
    """Register every SayuStock tool with MoEllmChats (idempotent)."""
    global _REGISTERED
    if _REGISTERED:
        return True
    try:
        from nonebot_plugin_moellmchats import (
            ToolSpec,
            ToolEffect,
            register_tool,
        )
        from nonebot_plugin_moellmchats.tool_contracts import tool_registry
    except Exception:
        logger.debug("[SayuStock] 未安装 MoEllmChats，跳过 ToolSpec 注册")
        return False

    try:
        for definition in TOOLS.values():
            if tool_registry.get(definition.name) is not None:
                logger.warning("[SayuStock] 工具名已被占用，跳过: {}", definition.name)
                continue
            register_tool(
                ToolSpec(
                    name=definition.name,
                    description=definition.description,
                    parameters=definition.parameters,
                    handler=_handler_for(definition.name),
                    effect=(ToolEffect.MUTATING if definition.effect == "mutating" else ToolEffect.READ_ONLY),
                    permission=definition.permission,
                    timeout_seconds=definition.timeout_seconds,
                    result_limit=16000,
                )
            )
            _NAMES.append(definition.name)
    except Exception as error:  # pragma: no cover - upstream version drift
        logger.warning("[SayuStock] 注册 MoEllm 工具失败: {}", error)
        return False
    _REGISTERED = True
    logger.info("[SayuStock] 已向 MoEllmChats 注册 {} 个股票工具", len(_NAMES))
    return True


def unregister() -> None:
    global _REGISTERED
    if not _REGISTERED:
        return
    try:
        from nonebot_plugin_moellmchats.tool_contracts import tool_registry

        for name in _NAMES:
            tool_registry.unregister(name)
    except Exception:
        pass
    _NAMES.clear()
    _REGISTERED = False


__all__ = ["register", "unregister"]
