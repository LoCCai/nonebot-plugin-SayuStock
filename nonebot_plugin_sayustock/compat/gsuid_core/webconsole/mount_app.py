"""``PageSchema`` / ``GsAdminModel`` / ``site`` shim.

The upstream SQLModel modules decorate their admin classes at import time.  The
NoneBot2 branch has no GsCore WebConsole, so the decorator records the model and
returns the class unchanged instead of raising.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class PageSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    label: str = ""
    icon: str = ""


class GsAdminModel:
    pk_name: str = "id"
    page_schema: PageSchema | None = None
    model: Any = None


class _Site:
    def __init__(self) -> None:
        self.registered: list[type[GsAdminModel]] = []

    def register_admin(self, model: type[GsAdminModel]) -> type[GsAdminModel]:
        self.registered.append(model)
        return model


site = _Site()

__all__ = ["GsAdminModel", "PageSchema", "site"]
