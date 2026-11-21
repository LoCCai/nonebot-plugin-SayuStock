"""Artifact payload files plus either a file or PostgreSQL index."""

from __future__ import annotations

import re
import json
import time
import secrets
from typing import Any
from pathlib import Path
from threading import Lock
from dataclasses import field, asdict, dataclass

from nonebot.log import logger

from .paths import ARTIFACT_DIR
from .storage import StorageConfigurationError, postgres_enabled

_ID_RE = re.compile(r"^[a-f0-9]{12}$")
_MAX_AGE_SECONDS = 14 * 24 * 3600
_MAX_ARTIFACTS = 500
_index_lock = Lock()


@dataclass(slots=True)
class Artifact:
    id: str
    title: str
    kind: str
    mime: str
    created_at: float
    size: int
    meta: dict[str, Any] = field(default_factory=dict)
    owner: str = ""

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


def _cleanup(index: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    cutoff = time.time() - _MAX_AGE_SECONDS
    kept = {key: value for key, value in index.items() if float(value.get("created_at", 0)) >= cutoff}
    if len(kept) > _MAX_ARTIFACTS:
        ordered = sorted(kept.items(), key=lambda item: item[1].get("created_at", 0))
        for key, _ in ordered[: len(kept) - _MAX_ARTIFACTS]:
            kept.pop(key, None)
    removed = [key for key in index if key not in kept]
    return kept, removed


def _remove_files(artifact_ids: list[str]) -> None:
    """Delete stale payloads only after the index transaction/commit succeeds."""
    for key in artifact_ids:
        for suffix in (".html", ".png", ".txt"):
            path = ARTIFACT_DIR / f"{key}{suffix}"
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                logger.warning("[SayuStock] 清理制品文件失败 {}: {}", path, error)


def _record_owner(record: dict[str, Any]) -> str:
    owner = record.get("owner")
    if isinstance(owner, str) and owner:
        return owner
    meta = record.get("meta")
    if isinstance(meta, dict):
        value = meta.get("owner")
        if isinstance(value, str):
            return value
    return ""


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "title": record.get("title"),
        "kind": record.get("kind"),
        "mime": record.get("mime"),
        "size": record.get("size"),
        "created_at": record.get("created_at"),
        "owner": _record_owner(record),
        "meta": record.get("meta") or {},
    }


def create_artifact(
    *,
    title: str,
    kind: str,
    mime: str,
    payload: bytes,
    suffix: str,
    owner: str = "",
    meta: dict[str, Any] | None = None,
) -> Artifact:
    """Persist a new artifact and return its public descriptor."""
    use_postgres = postgres_enabled()
    artifact_id = secrets.token_hex(6)
    path = ARTIFACT_DIR / f"{artifact_id}{suffix}"
    # The index publishes the completed payload. Never overwrite another artifact.
    with path.open("xb") as stream:
        try:
            stream.write(payload)
        except OSError:
            path.unlink(missing_ok=True)
            raise
    created_at = time.time()
    record = {
        "id": artifact_id,
        "title": title,
        "kind": kind,
        "mime": mime,
        "created_at": created_at,
        "size": len(payload),
        "owner": owner,
        "meta": meta or {},
        "suffix": suffix,
    }
    if use_postgres:
        from .postgres_artifacts import store_record

        try:
            removed = store_record(
                record,
                cutoff=created_at - _MAX_AGE_SECONDS,
                max_artifacts=_MAX_ARTIFACTS,
            )
        except StorageConfigurationError:
            path.unlink(missing_ok=True)
            raise
        # A lost commit acknowledgement leaves the outcome unknown. Retain the
        # file so a successfully committed index entry always resolves.
        _remove_files(removed)
    else:
        with _index_lock:
            index = _load_index()
            index[artifact_id] = record
            kept, removed = _cleanup(index)
            try:
                _save_index(kept)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        _remove_files(removed)
    return Artifact(
        id=artifact_id,
        title=title,
        kind=kind,
        mime=mime,
        created_at=created_at,
        size=len(payload),
        meta=meta or {},
        owner=owner,
    )


def create_html(
    title: str,
    html: str,
    *,
    kind: str = "report",
    owner: str = "",
    meta: dict[str, Any] | None = None,
) -> Artifact:
    return create_artifact(
        title=title,
        kind=kind,
        mime="text/html; charset=utf-8",
        payload=html.encode("utf-8"),
        suffix=".html",
        owner=owner,
        meta=meta,
    )


def create_png(
    title: str,
    image: bytes,
    *,
    owner: str = "",
    meta: dict[str, Any] | None = None,
) -> Artifact:
    return create_artifact(
        title=title,
        kind="chart",
        mime="image/png",
        payload=image,
        suffix=".png",
        owner=owner,
        meta=meta,
    )


def get_artifact(artifact_id: str, *, owner: str | None = None) -> tuple[Artifact, Path] | None:
    if not _ID_RE.fullmatch(artifact_id or ""):
        return None
    if postgres_enabled():
        from .postgres_artifacts import get_record

        record = get_record(artifact_id, owner)
    else:
        record = _load_index().get(artifact_id)
    if not record:
        return None
    record_owner = _record_owner(record)
    if owner is not None and record_owner != owner:
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
            owner=record_owner,
        ),
        path,
    )


def list_artifacts(limit: int = 30, *, owner: str | None = None) -> list[dict[str, Any]]:
    maximum = max(1, min(limit, 200))
    cutoff = time.time() - _MAX_AGE_SECONDS
    if postgres_enabled():
        from .postgres_artifacts import list_records

        records = list_records(maximum, owner=owner, cutoff=cutoff)
        items: list[dict[str, Any]] = []
        for record in records:
            if owner is not None and _record_owner(record) != owner:
                continue
            items.append(_summary(record))
        return items[:maximum]

    index = _load_index()
    index = {key: row for key, row in index.items() if float(row.get("created_at", 0)) >= cutoff}
    ordered = sorted(index.values(), key=lambda value: value.get("created_at", 0), reverse=True)
    items = []
    for item in ordered:
        record_owner = _record_owner(item)
        if owner is not None and record_owner != owner:
            continue
        items.append(_summary(item))
        if len(items) >= maximum:
            break
    return items


__all__ = [
    "Artifact",
    "create_artifact",
    "create_html",
    "create_png",
    "get_artifact",
    "list_artifacts",
]
