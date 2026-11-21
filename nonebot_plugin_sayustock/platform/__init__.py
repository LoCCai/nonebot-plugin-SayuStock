"""Protocol-neutral platform layer."""

from .event import UnifiedEvent
from .bridge import (
    send_text,
    from_event,
    send_image,
    platform_id,
    send_result,
    adapter_name,
)

__all__ = [
    "UnifiedEvent",
    "adapter_name",
    "from_event",
    "platform_id",
    "send_image",
    "send_result",
    "send_text",
]
