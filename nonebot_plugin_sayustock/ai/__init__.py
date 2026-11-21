"""AI tool layer for SayuStock.

The plugin does not own an LLM endpoint or API key.  Group chat is driven by
the host MoEllmChats runtime and the web page uses the QWeb host's personal
model through ``/qweb/chat``.  This package only exposes stock data/analysis
tools and their schemas.
"""

from .tools import TOOLS, run_tool, tool_schemas
from .contracts import ToolContext, ToolOutcome, ToolDefinition
from .moellm_bridge import register, unregister

__all__ = [
    "TOOLS",
    "ToolContext",
    "ToolDefinition",
    "ToolOutcome",
    "register",
    "run_tool",
    "tool_schemas",
    "unregister",
]
