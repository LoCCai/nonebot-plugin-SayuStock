"""Resource-root shim for legacy modules that expect GsCore's data store."""

from __future__ import annotations

import os
from pathlib import Path


def get_res_path(*parts: str) -> Path:
    root = os.environ.get("SAYUSTOCK_RES_PATH", "").strip()
    base = Path(root).expanduser() if root else Path.cwd() / "data"
    path = base.joinpath(*parts) if parts else base
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = ["get_res_path"]
