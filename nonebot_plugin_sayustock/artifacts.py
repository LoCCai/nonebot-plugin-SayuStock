"""Small, on-disk artifact store for HTML reports and rendered images."""

from __future__ import annotations

import re
import json
import time
import secrets
from typing import Any
from pathlib import Path
from dataclasses import asdict, dataclass

from .paths import ARTIFACT_DIR

_ID_RE = re.compile(r"^[a-f0-9]{12}$")
_MAX_AGE_SECONDS = 14 * 24 * 3600
_MAX_ARTIFACTS = 500


@dataclass(slots=True)
class Artifact:
    id: str
    title: str
    kind: str
    mime: str
    created_at: float
    size: int
    meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _index_path() -> Path:
    return ARTIFACT_DIR / "index.json"


def _load_index() -> dict[str, dict[str, Any]]:
    path = _index_path()
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _save_index(index: dict[str, dict[str, Any]]) -> None:
    path = _index_path()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _cleanup(index: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cutoff = time.time() - _MAX_AGE_SECONDS
    kept = {key: value for key, value in index.items() if float(value.get("created_at", 0)) >= cutoff}
    if len(kept) > _MAX_ARTIFACTS:
        ordered = sorted(kept.items(), key=lambda item: item[1].get("created_at", 0))
        for key, _ in ordered[: len(kept) - _MAX_ARTIFACTS]:
            kept.pop(key, None)
    for key in set(index) - set(kept):
        for suffix in (".html", ".png", ".txt"):
            (ARTIFACT_DIR / f"{key}{suffix}").unlink(missing_ok=True)
    return kept


def create_artifact(
    *,
    title: str,
    kind: str,
    mime: str,
    payload: bytes,
    suffix: str,
    meta: dict[str, Any] | None = None,
) -> Artifact:
    """Persist a new artifact and return its public descriptor."""
    artifact_id = secrets.token_hex(6)
    path = ARTIFACT_DIR / f"{artifact_id}{suffix}"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    record = {
        "id": artifact_id,
        "title": title,
        "kind": kind,
        "mime": mime,
        "created_at": time.time(),
        "size": len(payload),
        "meta": meta or {},
        "suffix": suffix,
    }
    index = _cleanup(_load_index())
    index[artifact_id] = record
    _save_index(index)
    return Artifact(
        id=artifact_id,
        title=title,
        kind=kind,
        mime=mime,
        created_at=record["created_at"],
        size=len(payload),
        meta=meta or {},
    )


def create_html(title: str, html: str, *, kind: str = "report", meta: dict[str, Any] | None = None) -> Artifact:
    return create_artifact(
        title=title,
        kind=kind,
        mime="text/html; charset=utf-8",
        payload=html.encode("utf-8"),
        suffix=".html",
        meta=meta,
    )


def create_png(title: str, image: bytes, *, meta: dict[str, Any] | None = None) -> Artifact:
    return create_artifact(
        title=title,
        kind="chart",
        mime="image/png",
        payload=image,
        suffix=".png",
        meta=meta,
    )


def get_artifact(artifact_id: str) -> tuple[Artifact, Path] | None:
    if not _ID_RE.fullmatch(artifact_id or ""):
        return None
    record = _load_index().get(artifact_id)
    if not record:
        return None
    suffix = str(record.get("suffix") or ".html")
    path = ARTIFACT_DIR / f"{artifact_id}{suffix}"
    if not path.is_file():
        return None
    return (
        Artifact(
            id=artifact_id,
            title=str(record.get("title") or ""),
            kind=str(record.get("kind") or "report"),
            mime=str(record.get("mime") or "application/octet-stream"),
            created_at=float(record.get("created_at") or 0),
            size=int(record.get("size") or 0),
            meta=record.get("meta") if isinstance(record.get("meta"), dict) else {},
        ),
        path,
    )


def list_artifacts(limit: int = 30) -> list[dict[str, Any]]:
    index = _cleanup(_load_index())
    ordered = sorted(index.values(), key=lambda value: value.get("created_at", 0), reverse=True)
    return [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "kind": item.get("kind"),
            "mime": item.get("mime"),
            "size": item.get("size"),
            "created_at": item.get("created_at"),
            "meta": item.get("meta") or {},
        }
        for item in ordered[: max(1, min(limit, 200))]
    ]


__all__ = [
    "Artifact",
    "create_artifact",
    "create_html",
    "create_png",
    "get_artifact",
    "list_artifacts",
]
