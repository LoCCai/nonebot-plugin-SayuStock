"""Logger facade backed by NoneBot's logger when available."""

from __future__ import annotations

import logging
from typing import Any

try:  # pragma: no cover - exercised in a real NoneBot process
    from nonebot.log import logger as _nb_logger

    logger = _nb_logger
except Exception:  # pragma: no cover - unit tests without a driver
    logger = logging.getLogger("sayustock")


def log_success(message: str, *args: Any, **kwargs: Any) -> None:
    """NoneBot's logger has no ``success`` level; keep the call site familiar."""
    logger.info(message, *args, **kwargs)


if not hasattr(logger, "success"):  # pragma: no cover - depends on host version
    setattr(logger, "success", log_success)


__all__ = ["logger", "log_success"]
