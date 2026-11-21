"""Optional MoEllmChats integration for SayuStock tools.

Two host generations are supported:

* a ToolSpec registry (``ToolSpec`` / ``register_tool``), where mutating
  tools use the host's confirmation flag; and
* the upstream 0.22-style ``tool_manager.custom_tools`` discovery, where only
  read-only tools are injected and the host supplies the real event/bot so
  identity cannot be forged by model arguments.

If neither API is present the plugin still works through MoEllmChats' normal
NoneBot plugin dispatch (metadata usage + real command matchers).
"""

from __future__ import annotations

import json
from typing import Any

from nonebot.log import logger

from .tools import TOOLS, run_tool
from ..platform import from_event
from .contracts import ToolContext, ToolOutcome

_REGISTERED = False
_TOOLSPEC_NAMES: list[str] = []
_NATIVE_NAMES: list[str] = []


def _context_from_event(event: Any, bot: Any, *, confirmed: bool = False) -> ToolContext:
    if event is not None and bot is not None:
        unified = from_event(bot, event)
        return ToolContext(
            platform=unified.platform,
            user_id=unified.user_id,
            session_id=unified.session_id,
            bot_id=unified.bot_id,
            event=event,
            bot=bot,
            confirmed=confirmed,
        )
    return ToolContext(
        platform="unknown",
        user_id="unknown",
        session_id="unknown",
        confirmed=confirmed,
    )


def _context_from_tool(tool_context: Any) -> ToolContext:
    event = getattr(tool_context, "event", None)
    bot = getattr(tool_context, "bot", None)
    context = _context_from_event(event, bot, confirmed=bool(getattr(tool_context, "confirmed", False)))
    context.request_id = getattr(tool_context, "request_id", None)
    return context


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


def _tool_text(outcome: ToolOutcome) -> str:
    structured = _structured_for_tool(outcome)
    if not structured:
        return outcome.text[:12000]
    return (
        outcome.text[:12000]
        + "\n\n<structured>\n"
        + json.dumps(structured, ensure_ascii=False, default=str)[:12000]
        + "\n</structured>"
    )


def _artifact_metadata(outcome: ToolOutcome) -> list[dict[str, Any]]:
    return [{"id": item.id, "title": item.title, "kind": item.kind, "mime": item.mime} for item in outcome.artifacts]


def _handler_for(name: str):
    async def handler(*, _tool_context: Any, **kwargs: Any) -> Any:
        from nonebot_plugin_moellmchats import ToolResult

        definition = TOOLS[name]
        context = _context_from_tool(_tool_context)
        if context.platform == "unknown" or not context.user_id:
            return ToolResult(text="无法取得本次会话的用户身份。")
        if definition.effect == "mutating" and not context.confirmed:
            return ToolResult(
                text=f"{name} 会修改用户数据，需要宿主完成二阶段确认后重试。",
                structured={
                    "requires_confirmation": True,
                    "tool": name,
                    "arguments": kwargs,
                },
                metadata={"effect": "mutating", "requires_confirmation": True},
            )
        try:
            outcome = await run_tool(name, kwargs, context)
        except Exception as error:
            logger.warning("[SayuStock] MoEllm 工具 {} 失败: {}", name, error)
            return ToolResult(text=f"{name} 执行失败：{error}")
        return ToolResult(
            text=_tool_text(outcome),
            images=(outcome.public().get("images") or [])[:4],
            structured=_structured_for_tool(outcome),
            metadata={"artifacts": _artifact_metadata(outcome), **outcome.metadata},
        )

    return handler


def _native_handler(name: str):
    async def handler(_event: Any = None, _bot: Any = None, **kwargs: Any) -> dict[str, Any]:
        context = _context_from_event(_event, _bot)
        if context.platform == "unknown" or not context.user_id:
            return {"text": "无法取得本次会话的用户身份。"}
        try:
            outcome = await run_tool(name, kwargs, context)
        except Exception as error:
            logger.warning("[SayuStock] MoEllm 原生工具 {} 失败: {}", name, error)
            return {"text": f"{name} 执行失败：{error}"}
        return {
            "text": _tool_text(outcome),
            "images": (outcome.public().get("images") or [])[:4],
            "metadata": {"artifacts": _artifact_metadata(outcome), **outcome.metadata},
        }

    return handler


def _register_toolspec() -> bool:
    try:
        from nonebot_plugin_moellmchats import (
            ToolSpec,
            ToolEffect,
            register_tool,
        )
        from nonebot_plugin_moellmchats.tool_contracts import tool_registry
    except Exception:
        return False

    names: list[str] = []
    try:
        for definition in TOOLS.values():
            if tool_registry.get(definition.name) is not None:
                logger.warning("[SayuStock] MoEllm 工具名已被占用，跳过: {}", definition.name)
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
            names.append(definition.name)
    except Exception as error:
        logger.warning("[SayuStock] ToolSpec 注册失败，尝试上游 tool_manager: {}", error)
        for name in names:
            try:
                tool_registry.unregister(name)
            except Exception:
                pass
        return False
    _TOOLSPEC_NAMES.extend(names)
    if names:
        logger.info("[SayuStock] 已向 MoEllmChats ToolSpec 注册 {} 个股票工具", len(names))
    return bool(names)


def _register_native() -> bool:
    try:
        from nonebot_plugin_moellmchats.tool_manager import tool_manager
    except Exception:
        return False
    registry = getattr(tool_manager, "custom_tools", None)
    if not isinstance(registry, dict):
        return False
    names: list[str] = []
    for definition in TOOLS.values():
        if definition.effect != "read_only":
            continue
        if definition.name in registry:
            continue
        registry[definition.name] = {
            "name": definition.name,
            "description": definition.description,
            "parameters": definition.parameters,
            "func": _native_handler(definition.name),
        }
        names.append(definition.name)
    _NATIVE_NAMES.extend(names)
    if names:
        logger.info("[SayuStock] 已向 MoEllmChats tool_manager 注册 {} 个只读股票工具", len(names))
    return bool(names)


def register() -> bool:
    """Register SayuStock tools with MoEllmChats (idempotent)."""
    global _REGISTERED
    if _REGISTERED:
        return True
    if _register_toolspec():
        _REGISTERED = True
        return True
    if _register_native():
        _REGISTERED = True
        return True
    logger.debug("[SayuStock] 未发现 MoEllmChats 工具注册接口，使用插件指令分发兜底")
    return False


def unregister() -> None:
    global _REGISTERED
    if not _REGISTERED:
        return
    if _TOOLSPEC_NAMES:
        try:
            from nonebot_plugin_moellmchats.tool_contracts import tool_registry

            for name in _TOOLSPEC_NAMES:
                tool_registry.unregister(name)
        except Exception:
            pass
    if _NATIVE_NAMES:
        try:
            from nonebot_plugin_moellmchats.tool_manager import tool_manager

            for name in _NATIVE_NAMES:
                tool_manager.custom_tools.pop(name, None)
        except Exception:
            pass
    _TOOLSPEC_NAMES.clear()
    _NATIVE_NAMES.clear()
    _REGISTERED = False


def registered_names() -> tuple[str, ...]:
    return tuple(_TOOLSPEC_NAMES + _NATIVE_NAMES)


__all__ = ["register", "registered_names", "unregister"]
