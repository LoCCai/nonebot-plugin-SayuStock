"""Optional real-PostgreSQL checks; never creates or alters tables.

Run with a disposable database whose schema was provisioned by the operator:

    SAYUSTOCK_DATABASE_URL=... pytest tests_nb2/test_storage_postgres.py -q

The DSN is never printed by the plugin or these tests.
"""

from __future__ import annotations

import os
import time
import uuid
import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")
from psycopg.types.json import Jsonb  # noqa: E402

from nonebot_plugin_sayustock import artifacts  # noqa: E402
from nonebot_plugin_sayustock.storage import (  # noqa: E402
    ARTIFACT_TABLE,
    WATCHLIST_TABLE,
    close_postgres_pools,
    verify_postgres_storage,
)
from nonebot_plugin_sayustock.artifacts import Artifact  # noqa: E402
from nonebot_plugin_sayustock.watchlist import WatchlistStore  # noqa: E402
from nonebot_plugin_sayustock.postgres_watchlist import PostgresWatchlist  # noqa: E402

DATABASE_URL = os.environ.get("SAYUSTOCK_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="SAYUSTOCK_DATABASE_URL 未设置")


@pytest.fixture
def postgres_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not DATABASE_URL:
        pytest.skip("SAYUSTOCK_DATABASE_URL 未设置")
    monkeypatch.setenv("SAYUSTOCK_STORAGE", "postgres")
    monkeypatch.setenv("SAYUSTOCK_DATABASE_URL", DATABASE_URL)


def _execute(sql: str, parameters: tuple[object, ...]) -> None:
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(sql, parameters)
        connection.commit()


def _fetchone(sql: str, parameters: tuple[object, ...]) -> tuple[object, ...] | None:
    with psycopg.connect(DATABASE_URL) as connection:
        cursor = connection.execute(sql, parameters)
        return cursor.fetchone()


def _delete_watchlist(keys: list[str]) -> None:
    if keys:
        _execute(f"DELETE FROM {WATCHLIST_TABLE} WHERE owner_key = ANY(%s::text[])", (keys,))


def _delete_artifacts(ids: list[str]) -> None:
    if ids:
        _execute(f"DELETE FROM {ARTIFACT_TABLE} WHERE id = ANY(%s::text[])", (ids,))


def _key(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4().hex}"


@pytest.mark.asyncio
async def test_startup_verifier_accepts_operator_provisioned_tables(postgres_env: None) -> None:
    try:
        await verify_postgres_storage()
    finally:
        await close_postgres_pools()


@pytest.mark.asyncio
async def test_watchlist_concurrent_adds_preserve_order_fields_and_empty_list(postgres_env: None) -> None:
    backend = PostgresWatchlist()
    key = _key("test-concurrent")
    codes = [f"T{i:05d}" for i in range(12)]
    try:
        assert await backend.add(key, "600519", "贵州茅台", "贵州茅台") is True
        assert await backend.add(key, "000001", "平安银行", "000001") is True
        added = await asyncio.gather(*(backend.add(key, code, f"名称{code}", code) for code in codes))
        assert all(added)
        items = await backend.list(key)
        assert [item["code"] for item in items[:2]] == ["600519", "000001"]
        assert len(items) == 14
        assert {item["code"] for item in items} == {"600519", "000001", *codes}
        assert items[0] == {"code": "600519", "name": "贵州茅台", "query": "贵州茅台"}
        snapshot = WatchlistStore().snapshot()
        assert snapshot["owners"][key] == items

        assert await backend.clear(key) == 14
        assert await backend.list(key) == []
        row = _fetchone(f"SELECT items FROM {WATCHLIST_TABLE} WHERE owner_key = %s", (key,))
        assert row is not None
        assert row[0] == []
        assert WatchlistStore().snapshot()["owners"][key] == []
        assert await backend.remove(key, "600519") is False
    finally:
        _delete_watchlist([key])
        await close_postgres_pools()


@pytest.mark.asyncio
async def test_watchlist_owner_isolation_and_extra_fields(postgres_env: None) -> None:
    backend = PostgresWatchlist()
    owner_a = _key("test-owner-a")
    owner_b = _key("test-owner-b")
    try:
        _execute(
            f"INSERT INTO {WATCHLIST_TABLE} (owner_key, items) VALUES (%s, %s)",
            (
                owner_a,
                Jsonb([{"code": "600519", "name": "贵州茅台", "query": "贵州茅台", "tag": "core"}]),
            ),
        )
        _execute(
            f"INSERT INTO {WATCHLIST_TABLE} (owner_key, items) VALUES (%s, %s)",
            (owner_b, Jsonb([{"code": "000001", "name": "平安银行", "query": "000001"}])),
        )
        assert await backend.list(owner_a) == [
            {"code": "600519", "name": "贵州茅台", "query": "贵州茅台", "tag": "core"}
        ]
        assert await backend.list(owner_b) == [{"code": "000001", "name": "平安银行", "query": "000001"}]
        assert await backend.remove(owner_a, "600519") is True
        assert await backend.list(owner_a) == []
        assert await backend.list(owner_b) == [{"code": "000001", "name": "平安银行", "query": "000001"}]
    finally:
        _delete_watchlist([owner_a, owner_b])
        await close_postgres_pools()


def test_artifact_owner_isolation_fallback_and_full_record(
    postgres_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", tmp_path)
    alice = _key("test-alice")
    bob = _key("test-bob")
    created: list[str] = []
    try:
        first = artifacts.create_html("alice report", "<html>alice</html>", owner=alice, meta={"session": "s1"})
        second = artifacts.create_html("bob report", "<html>bob</html>", owner=bob)
        created.extend([first.id, second.id])
        found = artifacts.get_artifact(first.id, owner=alice)
        assert found is not None
        assert artifacts.get_artifact(first.id, owner=bob) is None
        assert [item["id"] for item in artifacts.list_artifacts(200, owner=alice)] == [first.id]
        assert [item["id"] for item in artifacts.list_artifacts(200, owner=bob)] == [second.id]

        row = _fetchone(f"SELECT record FROM {ARTIFACT_TABLE} WHERE id = %s", (first.id,))
        assert row is not None
        record = row[0]
        assert isinstance(record, dict)
        assert set(record) == {
            "id",
            "title",
            "kind",
            "mime",
            "created_at",
            "size",
            "owner",
            "meta",
            "suffix",
        }
        assert record["suffix"] == ".html"
        assert record["owner"] == alice

        fallback_record = dict(record)
        fallback_record.pop("owner", None)
        fallback_record["meta"] = {"owner": bob}
        _execute(
            f"UPDATE {ARTIFACT_TABLE} SET record = %s WHERE id = %s",
            (Jsonb(fallback_record), first.id),
        )
        assert artifacts.get_artifact(first.id, owner=bob) is not None
        assert artifacts.get_artifact(first.id, owner=alice) is None
        assert {item["id"] for item in artifacts.list_artifacts(200, owner=bob)} == {first.id, second.id}
        assert artifacts.list_artifacts(200, owner=alice) == []
    finally:
        _delete_artifacts(created)
        asyncio.run(close_postgres_pools())


def test_artifact_cleanup_removes_row_and_payload_together(
    postgres_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", tmp_path)
    old_artifact: Artifact | None = None
    new_artifact: Artifact | None = None
    try:
        old_artifact = artifacts.create_html("old", "<html>old</html>", owner=_key("test-old"))
        row = _fetchone(f"SELECT record FROM {ARTIFACT_TABLE} WHERE id = %s", (old_artifact.id,))
        assert row is not None and isinstance(row[0], dict)
        old_record = dict(row[0])
        old_record["created_at"] = time.time() - 15 * 24 * 3600
        _execute(
            f"UPDATE {ARTIFACT_TABLE} SET record = %s WHERE id = %s",
            (Jsonb(old_record), old_artifact.id),
        )
        old_path = tmp_path / f"{old_artifact.id}.html"
        assert old_path.is_file()

        new_artifact = artifacts.create_html("new", "<html>new</html>", owner=_key("test-new"))
        assert not old_path.exists()
        assert _fetchone(f"SELECT id FROM {ARTIFACT_TABLE} WHERE id = %s", (old_artifact.id,)) is None
        assert new_artifact is not None
        assert (tmp_path / f"{new_artifact.id}.html").is_file()
    finally:
        _delete_artifacts([item.id for item in (old_artifact, new_artifact) if item is not None])
        asyncio.run(close_postgres_pools())


def test_concurrent_artifact_creation_keeps_every_record(
    postgres_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", tmp_path)
    owner = _key("test-threads")
    created: list[Artifact] = []

    def create(index: int) -> Artifact:
        return artifacts.create_html(f"thread-{index}", "<html></html>", owner=owner)

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            created = list(executor.map(create, range(12)))
        ids = [item.id for item in created]
        assert len(set(ids)) == 12
        assert all((tmp_path / f"{artifact_id}.html").is_file() for artifact_id in ids)
        row = _fetchone(
            f"SELECT count(*) FROM {ARTIFACT_TABLE} WHERE record->>'owner' = %s",
            (owner,),
        )
        assert row is not None and row[0] == 12
        listed = artifacts.list_artifacts(200, owner=owner)
        assert {item["id"] for item in listed} == set(ids)
    finally:
        _delete_artifacts([item.id for item in created])
        asyncio.run(close_postgres_pools())
