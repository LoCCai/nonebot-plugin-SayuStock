"""Minimal GsCore config model objects for legacy configuration modules."""

from __future__ import annotations

from typing import Any


class GSC:
    def __init__(self, *args: Any, default: Any = None, **kwargs: Any) -> None:
        self.title = str(args[0]) if args else str(kwargs.get("title", ""))
        self.desc = str(args[1]) if len(args) > 1 else str(kwargs.get("desc", ""))
        if default is not None:
            self.data = default
        elif len(args) >= 3:
            self.data = args[2]
        else:
            self.data = kwargs.get("default", None)


class GsStrConfig(GSC):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.data is None:
            self.data = ""


class GsIntConfig(GSC):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.data is None:
            self.data = 0


class GsBoolConfig(GSC):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.data is None:
            self.data = False


class GsListStrConfig(GSC):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.data is None:
            self.data = []


class GsDivider(GSC):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.data = None


__all__ = [
    "GSC",
    "GsBoolConfig",
    "GsDivider",
    "GsIntConfig",
    "GsListStrConfig",
    "GsStrConfig",
]
