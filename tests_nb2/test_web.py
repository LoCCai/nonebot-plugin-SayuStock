from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nonebot_plugin_sayustock import config
from nonebot_plugin_sayustock.web import get_router
from nonebot_plugin_sayustock.runtime import Runtime, set_runtime
from nonebot_plugin_sayustock.services.market import MarketService


@pytest.fixture
def client(fake_market, monkeypatch):
    monkeypatch.setenv("SAYUSTOCK_WEB_TOKEN", "test-token")
    config.load_config(force=True)
    set_runtime(Runtime(market=MarketService()))
    app = FastAPI()
    app.include_router(get_router())
    yield TestClient(app)
    set_runtime(None)


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def test_health_is_public(client):
    response = client.get("/sayustock/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_requires_token_for_business_api(client):
    response = client.get("/sayustock/api/overview")
    assert response.status_code == 401


def test_overview_and_quote(client):
    overview = client.get("/sayustock/api/overview", headers=headers())
    assert overview.status_code == 200
    assert overview.json()["data"]["indices"]
    quote = client.get("/sayustock/api/quote?q=贵州茅台", headers=headers())
    assert quote.status_code == 200
    assert quote.json()["data"]["name"] == "贵州茅台"


def test_chart_html_and_command(client):
    chart = client.get(
        "/sayustock/api/chart/html?type=kline&q=贵州茅台&period=日k",
        headers=headers(),
    )
    assert chart.status_code == 200
    assert "<html" in chart.text
    command = client.post("/sayustock/api/command", headers=headers(), json={"text": "大盘概览"})
    assert command.status_code == 200
    assert "大盘概览" in command.json()["text"]
    assert command.json()["artifacts"]


def test_chat_without_llm_uses_command_mode(client):
    response = client.post("/sayustock/api/chat", headers=headers(), json={"message": "大盘概览"})
    assert response.status_code == 200
    assert response.json()["mode"] == "command"


def test_artifact_list(client):
    response = client.get("/sayustock/api/artifacts", headers=headers())
    assert response.status_code == 200
    assert isinstance(response.json()["items"], list)


def test_qweb_manifest_and_bridge(client):
    manifest = client.get("/sayustock/api/qweb/manifest", headers=headers())
    assert manifest.status_code == 200
    assert manifest.json()["schema"] == "qweb.plugin.v1"
    assert manifest.json()["embed"].endswith("embed=1")
    catalog = client.get("/sayustock/api/qweb/catalog", headers=headers())
    assert catalog.status_code == 200
    assert catalog.json()["schema"] == "qweb.feature_catalog.v1"
    assert catalog.json()["features"]
    embed = client.get("/sayustock/embed", follow_redirects=False)
    assert embed.status_code in {302, 307}
    bridge = client.get("/sayustock/static/qweb-bridge.js")
    assert bridge.status_code == 200
    assert "SayuStockQWeb" in bridge.text
