"""Minimal OpenAI-compatible chat client with a bounded tool loop."""

from __future__ import annotations

import json
import time
import asyncio
from typing import Any

import httpx
from nonebot.log import logger

from .tools import run_tool, tool_schemas
from ..config import get_value
from .contracts import ToolContext


class LLMError(RuntimeError):
    """Configuration, transport or provider failure."""


def _endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("未配置 LLM base_url，请在 data/sayustock/config.json 的 llm 段填写")
    if base.endswith("/chat/completions"):
        return base
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"


def configured() -> bool:
    return bool(str(get_value("llm.base_url", "")).strip() and str(get_value("llm.model", "")).strip())


async def _complete(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    base = str(get_value("llm.base_url", "")).strip()
    key = str(get_value("llm.api_key", "")).strip()
    model = str(get_value("llm.model", "")).strip()
    if not model:
        raise LLMError("未配置 llm.model")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": float(get_value("llm.temperature", 0.4) or 0.4),
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    timeout = float(get_value("llm.timeout_seconds", 90) or 90)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(_endpoint(base), headers=headers, json=payload)
    except httpx.HTTPError as error:
        raise LLMError(f"LLM 请求失败：{error}") from error

    if response.status_code >= 400:
        detail = response.text[:500]
        raise LLMError(f"LLM 返回 HTTP {response.status_code}：{detail}")
    try:
        data = response.json()
    except ValueError as error:
        raise LLMError("LLM 返回了非 JSON 响应") from error
    if not isinstance(data, dict) or not data.get("choices"):
        raise LLMError("LLM 响应缺少 choices")
    return data


def _assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content") or "",
    }
    if message.get("tool_calls"):
        payload["tool_calls"] = message["tool_calls"]
    return payload


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def chat(
    message: str,
    *,
    context: ToolContext,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one user turn through the model and its tools."""
    if not configured():
        raise LLMError(
            "网页 AI 尚未配置。请在 data/sayustock/config.json 的 llm 段填写 base_url、"
            "api_key 与 model，或设置 SAYUSTOCK_LLM_BASE_URL / SAYUSTOCK_LLM_MODEL。"
        )

    max_history = int(get_value("web.max_chat_history", 24) or 24)
    messages: list[dict[str, Any]] = [{"role": "system", "content": str(get_value("llm.system_prompt", ""))}]
    for item in (history or [])[-max_history:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content[:8000]})
    messages.append({"role": "user", "content": message[:8000]})

    tools = tool_schemas()
    tool_events: list[dict[str, Any]] = []
    images: list[str] = []
    artifacts: list[dict[str, Any]] = []
    steps = max(1, min(int(get_value("llm.max_steps", 6) or 6), 10))
    usage: dict[str, Any] = {}

    for step in range(steps):
        data = await _complete(messages, tools=tools)
        if isinstance(data.get("usage"), dict):
            usage = data["usage"]
        choice = data["choices"][0]
        assistant = choice.get("message") or {}
        calls = assistant.get("tool_calls") or []
        if not calls:
            reply = str(assistant.get("content") or "").strip()
            return {
                "ok": True,
                "reply": reply or "模型没有返回文本。",
                "steps": step + 1,
                "tool_calls": tool_events,
                "images": images,
                "artifacts": artifacts,
                "usage": usage,
                "model": str(get_value("llm.model", "")),
            }

        messages.append(_assistant_message(assistant))
        for call in calls[:4]:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            arguments = _parse_arguments(function.get("arguments"))
            started = time.monotonic()
            try:
                outcome = await asyncio.wait_for(run_tool(name, arguments, context), timeout=100)
                content = outcome.text
                if outcome.structured:
                    content += (
                        "\n\n<structured>\n"
                        + json.dumps(outcome.structured, ensure_ascii=False, default=str)[:12000]
                        + "\n</structured>"
                    )
                for image in outcome.public()["images"]:
                    if image not in images:
                        images.append(image)
                for artifact in outcome.artifacts:
                    artifacts.append(
                        {
                            "id": artifact.id,
                            "title": artifact.title,
                            "kind": artifact.kind,
                            "mime": artifact.mime,
                        }
                    )
                status = "ok"
            except asyncio.TimeoutError:
                content = f"工具 {name} 执行超时。"
                status = "timeout"
            except Exception as error:
                content = f"工具 {name} 执行失败：{error}"
                status = "error"
                logger.warning("[SayuStock] LLM 工具 {} 失败: {}", name, error)
            tool_events.append(
                {
                    "name": name,
                    "arguments": arguments,
                    "status": status,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "name": name,
                    "content": content[:20000],
                }
            )

    return {
        "ok": True,
        "reply": "工具调用步数已达上限。以下是已获取的结果，请缩小问题范围后重试。",
        "steps": steps,
        "tool_calls": tool_events,
        "images": images,
        "artifacts": artifacts,
        "usage": usage,
        "model": str(get_value("llm.model", "")),
    }


__all__ = ["LLMError", "chat", "configured"]
