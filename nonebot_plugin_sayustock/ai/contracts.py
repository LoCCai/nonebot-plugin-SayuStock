"""Tool contracts shared by the web agent and the MoEllmChats bridge."""

from __future__ import annotations

import base64
from typing import Any, Callable, Awaitable
from dataclasses import field, dataclass

from ..artifacts import Artifact

ToolHandler = Callable[..., Awaitable["ToolOutcome"]]


@dataclass(slots=True)
class ToolContext:
    """Identity available to a tool invocation.

    ``event``/``bot`` are present for bot-side calls; web calls only carry the
    stable ids.  Handlers must never trust model-provided identity fields.
    """

    platform: str
    user_id: str
    session_id: str
    bot_id: str = ""
    event: Any = None
    bot: Any = None
    request_id: str | None = None
    confirmed: bool = False


@dataclass(slots=True)
class ToolOutcome:
    text: str
    structured: dict[str, Any] = field(default_factory=dict)
    images: list[bytes] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "structured": self.structured,
            "images": ["data:image/png;base64," + base64.b64encode(image).decode("ascii") for image in self.images],
            "artifacts": [
                {
                    "id": artifact.id,
                    "title": artifact.title,
                    "kind": artifact.kind,
                    "mime": artifact.mime,
                }
                for artifact in self.artifacts
            ],
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    effect: str = "read_only"
    permission: str = "user"
    timeout_seconds: float = 60.0

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


__all__ = ["ToolContext", "ToolDefinition", "ToolHandler", "ToolOutcome"]
