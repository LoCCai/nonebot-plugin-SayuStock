"""Synchronous PostgreSQL index for artifact files.

Payload bytes remain in ``ARTIFACT_DIR`` using the existing names.  Only the
index record lives in ``qiqi_sayustock.artifact``.  Creation and retention
cleanup share one transaction guarded by an advisory lock, so multiple bot
processes cannot publish a record whose cleanup removed a newer entry.
"""

from __future__ import annotations

from typing import Any

from .storage import (
    ARTIFACT_TABLE,
    PostgresStorageError,
    StorageConfigurationError,
    log_postgres_failure,
    sync_connection_pool,
)

_LOCK_NAME = f"{ARTIFACT_TABLE}.cleanup"
_OWNER_SQL = (
    "(CASE"
    " WHEN jsonb_typeof(record->'owner') = 'string' AND record->>'owner' <> ''"
    " THEN record->>'owner'"
    " WHEN jsonb_typeof(record->'meta'->'owner') = 'string'"
    " THEN record->'meta'->>'owner'"
    " ELSE '' END)"
)


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError:
        raise PostgresStorageError("PostgreSQL 存储需要安装 psycopg[binary,pool]") from None
    return Jsonb(value)


def _record(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PostgresStorageError("制品索引数据格式无效：record 不是 JSON 对象")
    return raw


def store_record(
    record: dict[str, Any],
    *,
    cutoff: float,
    max_artifacts: int,
) -> list[str]:
    """Insert a full index record atomically and return keys to delete files for."""
    artifact_id = str(record.get("id") or "")
    if not artifact_id:
        raise PostgresStorageError("制品索引缺少 id")
    try:
        pool = sync_connection_pool()
        with pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (_LOCK_NAME,))
                    cursor.execute(
                        f"INSERT INTO {ARTIFACT_TABLE} (id, record) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                        (artifact_id, _jsonb(record)),
                    )
                    if cursor.rowcount != 1:
                        raise PostgresStorageError("制品索引主键冲突，请重试")
                    cursor.execute(
                        f"SELECT id, COALESCE((record->>'created_at')::double precision, 0) "
                        f"FROM {ARTIFACT_TABLE} ORDER BY 2 ASC"
                    )
                    rows = cursor.fetchall()
                    kept: list[str] = [str(row[0]) for row in rows if float(row[1]) >= cutoff]
                    if len(kept) > max_artifacts:
                        kept = kept[len(kept) - max_artifacts :]
                    kept_set = set(kept)
                    removed = [str(row[0]) for row in rows if str(row[0]) not in kept_set]
                    if removed:
                        cursor.execute(
                            f"DELETE FROM {ARTIFACT_TABLE} WHERE id = ANY(%s::text[])",
                            (removed,),
                        )
        return removed
    except (StorageConfigurationError, PostgresStorageError):
        raise
    except Exception as error:
        log_postgres_failure("写入制品索引", error)
        raise PostgresStorageError("PostgreSQL 制品索引不可用（写入失败）") from None


def get_record(artifact_id: str, owner: str | None) -> dict[str, Any] | None:
    sql = f"SELECT record FROM {ARTIFACT_TABLE} WHERE id = %s"
    parameters: list[object] = [artifact_id]
    if owner is not None:
        sql += f" AND {_OWNER_SQL} = %s"
        parameters.append(owner)
    try:
        pool = sync_connection_pool()
        with pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                row = cursor.fetchone()
    except (StorageConfigurationError, PostgresStorageError):
        raise
    except Exception as error:
        log_postgres_failure("读取制品索引", error)
        raise PostgresStorageError("PostgreSQL 制品索引不可用（读取失败）") from None
    return None if row is None else _record(row[0])


def list_records(limit: int, *, owner: str | None, cutoff: float) -> list[dict[str, Any]]:
    maximum = max(1, min(limit, 200))
    sql = f"SELECT record FROM {ARTIFACT_TABLE} WHERE COALESCE((record->>'created_at')::double precision, 0) >= %s"
    parameters: list[object] = [cutoff]
    if owner is not None:
        sql += f" AND {_OWNER_SQL} = %s"
        parameters.append(owner)
    sql += " ORDER BY COALESCE((record->>'created_at')::double precision, 0) DESC LIMIT %s"
    parameters.append(maximum)
    try:
        pool = sync_connection_pool()
        with pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                rows = cursor.fetchall()
    except (StorageConfigurationError, PostgresStorageError):
        raise
    except Exception as error:
        log_postgres_failure("列出制品索引", error)
        raise PostgresStorageError("PostgreSQL 制品索引不可用（列举失败）") from None
    return [_record(row[0]) for row in rows]


__all__ = ["get_record", "list_records", "store_record"]
