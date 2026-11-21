from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
import nonebot
from pydantic import SecretStr

from nonebot_plugin_sayustock import storage, artifacts
from nonebot_plugin_sayustock.watchlist import WatchlistStore, owner_key


def _ignore_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "_driver_storage_value", lambda: "")


def _install_qi_web(monkeypatch: pytest.MonkeyPatch, database_url: object) -> None:
    src = types.ModuleType("src")
    src.__path__ = []  # type: ignore[attr-defined]
    qi_web = types.ModuleType("src.qi_web")
    qi_web.__path__ = []  # type: ignore[attr-defined]
    config = types.ModuleType("src.qi_web.config")

    class Settings:
        pass

    settings = Settings()
    settings.database_url = database_url  # type: ignore[attr-defined]
    config.get_settings = lambda: settings  # type: ignore[attr-defined]
    qi_web.config = config  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src", src)
    monkeypatch.setitem(sys.modules, "src.qi_web", qi_web)
    monkeypatch.setitem(sys.modules, "src.qi_web.config", config)


def test_storage_mode_defaults_to_file_and_reads_nonebot_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SAYUSTOCK_STORAGE", raising=False)
    original_driver_value = storage._driver_storage_value
    _ignore_driver(monkeypatch)
    assert storage.storage_mode() == "file"
    monkeypatch.setattr(storage, "_driver_storage_value", original_driver_value)

    class DriverConfig:
        sayustock_storage = "postgres"

    class Driver:
        config = DriverConfig()

    monkeypatch.setattr(nonebot, "get_driver", lambda: Driver())
    assert storage.storage_mode() == "postgres"

    monkeypatch.setenv("SAYUSTOCK_STORAGE", "file")
    assert storage.storage_mode() == "file"


def test_unknown_storage_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAYUSTOCK_STORAGE", "sqlite")
    with pytest.raises(storage.StorageConfigurationError):
        storage.storage_mode()


def test_database_url_accepts_env_override_and_secret_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    dsn = "postgresql://sayu:secret@db.example/qiqi"
    monkeypatch.setenv("SAYUSTOCK_DATABASE_URL", dsn)
    assert storage.resolve_database_url() == dsn

    monkeypatch.delenv("SAYUSTOCK_DATABASE_URL")
    _install_qi_web(monkeypatch, SecretStr(dsn))
    assert storage.resolve_database_url() == dsn
    _install_qi_web(monkeypatch, dsn)
    assert storage.resolve_database_url() == dsn


def test_sqlalchemy_uri_is_translated_for_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SAYUSTOCK_DATABASE_URL",
        "postgresql+asyncpg://sayu:secret@db.example/qiqi?ssl=require",
    )
    assert storage.resolve_database_url() == "postgresql://sayu:secret@db.example/qiqi?sslmode=require"


def test_invalid_connection_scheme_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SAYUSTOCK_DATABASE_URL",
        "mysql://sayu:secret@db.example/qiqi",
    )
    with pytest.raises(storage.StorageConfigurationError) as caught:
        storage.resolve_database_url()
    assert "secret" not in str(caught.value)


def test_database_url_failures_do_not_echo_connection_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SAYUSTOCK_DATABASE_URL", raising=False)
    _install_qi_web(monkeypatch, "postgresql://sayu:secret@db.example/qiqi")

    class BrokenSettings:
        @property
        def database_url(self) -> str:
            raise RuntimeError("postgresql://sayu:secret@db.example/qiqi")

    config = sys.modules["src.qi_web.config"]
    config.get_settings = lambda: BrokenSettings()  # type: ignore[attr-defined]
    with pytest.raises(storage.StorageConfigurationError) as caught:
        storage.resolve_database_url()
    assert "secret" not in str(caught.value)

    _install_qi_web(monkeypatch, None)
    with pytest.raises(storage.StorageConfigurationError):
        storage.resolve_database_url()


def test_pg_watchlist_construction_fails_closed_without_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAYUSTOCK_STORAGE", "postgres")
    monkeypatch.delenv("SAYUSTOCK_DATABASE_URL", raising=False)
    from nonebot_plugin_sayustock import postgres_watchlist

    monkeypatch.setattr(
        postgres_watchlist,
        "resolve_database_url",
        lambda: (_ for _ in ()).throw(storage.StorageConfigurationError("missing host database_url")),
    )
    # This must not silently fall back to WATCHLIST_PATH.
    with pytest.raises(storage.StorageConfigurationError):
        WatchlistStore()


def test_pg_artifact_fails_closed_and_removes_new_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SAYUSTOCK_STORAGE", "postgres")
    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(
        storage,
        "resolve_database_url",
        lambda: (_ for _ in ()).throw(storage.StorageConfigurationError("missing host database_url")),
    )
    with pytest.raises(storage.StorageConfigurationError):
        artifacts.create_html("fail closed", "<html></html>", owner="alice")
    assert list(tmp_path.iterdir()) == []


def test_pg_artifact_queries_the_index_not_the_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SAYUSTOCK_STORAGE", "postgres")
    monkeypatch.setenv("SAYUSTOCK_DATABASE_URL", "postgresql://unused.example/qiqi")
    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", tmp_path)
    from nonebot_plugin_sayustock import postgres_artifacts

    calls: list[dict[str, object]] = []

    def fake_store(record: dict[str, object], *, cutoff: float, max_artifacts: int) -> list[str]:
        calls.append(record)
        return []

    monkeypatch.setattr(postgres_artifacts, "store_record", fake_store)
    created = artifacts.create_html("pg index", "<html></html>", owner="alice")
    assert created.owner == "alice"
    assert calls and calls[0]["suffix"] == ".html"
    assert not (tmp_path / "index.json").exists()
    assert (tmp_path / f"{created.id}.html").is_file()


def test_file_mode_keeps_index_and_owner_isolation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SAYUSTOCK_STORAGE", "file")
    monkeypatch.setattr(artifacts, "ARTIFACT_DIR", tmp_path)
    created = artifacts.create_html(
        "file index",
        "<html></html>",
        owner="alice",
        meta={"owner": "ignored-when-owner-is-set"},
    )
    assert (tmp_path / "index.json").is_file()
    assert artifacts.get_artifact(created.id, owner="alice") is not None
    assert artifacts.get_artifact(created.id, owner="bob") is None
    assert [item["id"] for item in artifacts.list_artifacts(owner="alice")] == [created.id]
    assert artifacts.list_artifacts(owner="bob") == []


def test_owner_falls_back_to_meta_owner() -> None:
    record = {"owner": "", "meta": {"owner": "bob"}}
    assert artifacts._record_owner(record) == "bob"
    assert artifacts._record_owner({"owner": "alice", "meta": {"owner": "bob"}}) == "alice"


@pytest.mark.asyncio
async def test_file_watchlist_still_works(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SAYUSTOCK_STORAGE", "file")
    store = WatchlistStore(tmp_path / "watchlist.json")
    key = owner_key(platform="test", bot_id="b", user_id="u")
    assert store.snapshot() == {"version": 1, "owners": {}}
    assert await store.list(key) == []
    assert await store.add(key, "600519", "贵州茅台", "贵州茅台") is True
    assert await store.list(key) == [{"code": "600519", "name": "贵州茅台", "query": "贵州茅台"}]
    assert (tmp_path / "watchlist.json").is_file()
