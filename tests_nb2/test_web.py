from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import FastAPI, Request, HTTPException
from fastapi.testclient import TestClient

from nonebot_plugin_sayustock import config
from nonebot_plugin_sayustock.web import host as host_module, get_router
from nonebot_plugin_sayustock.runtime import Runtime, set_runtime
from nonebot_plugin_sayustock.web.host import HostDependencies
from nonebot_plugin_sayustock.services.market import MarketService


@dataclass
class HostAccount:
    qq: str


async def require_membership(request: Request) -> HostAccount:
    qq = request.headers.get("x-test-account", "").strip()
    if not qq:
        raise HTTPException(status_code=401, detail="QWeb membership required")
    return HostAccount(qq=qq)


async def admin_account(request: Request) -> HostAccount:
    account = await require_membership(request)
    if request.headers.get("x-test-admin") != "1":
        raise HTTPException(status_code=403, detail="QWeb admin required")
    return account


async def csrf_protected(request: Request) -> None:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.headers.get("x-csrf-token") != "csrf-ok":
            raise HTTPException(status_code=403, detail="bad csrf")


FAKE_HOST = HostDependencies(
    require_membership=require_membership,
    admin_account=admin_account,
    csrf_protected=csrf_protected,
)


@pytest.fixture
def client(fake_market, monkeypatch):
    monkeypatch.setenv("SAYUSTOCK_RENDER_ENABLED", "false")
    config.load_config(force=True)
    set_runtime(Runtime(market=MarketService()))
    app = FastAPI()
    app.include_router(get_router(FAKE_HOST))
    yield TestClient(app)
    set_runtime(None)


def headers(user: str = "alice", *, csrf: bool = True, admin: bool = False) -> dict[str, str]:
    values = {"X-Test-Account": user}
    if csrf:
        values["X-CSRF-Token"] = "csrf-ok"
    if admin:
        values["X-Test-Admin"] = "1"
    return values


def test_no_host_is_unavailable(client, monkeypatch):
    monkeypatch.setattr(host_module, "load_host_dependencies", lambda: None)
    from nonebot_plugin_sayustock.web import api

    monkeypatch.setattr(api, "load_host_dependencies", lambda: None)
    app = FastAPI()
    app.include_router(get_router())
    response = TestClient(app).get(f"{ROUTER_PREFIX}/api/overview")
    assert response.status_code == 503
    assert "QWeb" in response.json()["detail"]


def test_membership_is_required(client):
    assert client.get(f"{ROUTER_PREFIX}/api/health").status_code == 401
    response = client.get(f"{ROUTER_PREFIX}/api/health", headers=headers())
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_overview_quote_and_page(client):
    overview = client.get(f"{ROUTER_PREFIX}/api/overview", headers=headers())
    assert overview.status_code == 200
    assert overview.json()["data"]["indices"]
    quote = client.get(f"{ROUTER_PREFIX}/api/quote?q=贵州茅台", headers=headers())
    assert quote.status_code == 200
    assert quote.json()["data"]["name"] == "贵州茅台"
    page = client.get(f"{ROUTER_PREFIX}/", headers=headers())
    assert page.status_code == 200
    assert "SAYUSTOCK_WEB_TOKEN" not in page.text
    assert "llm-base" not in page.text


def test_chart_html_and_command_write_need_csrf(client):
    chart = client.get(
        f"{ROUTER_PREFIX}/api/chart/html?type=kline&q=贵州茅台&period=日k",
        headers=headers(),
    )
    assert chart.status_code == 200
    assert "<html" in chart.text
    blocked = client.post(f"{ROUTER_PREFIX}/api/command", headers=headers(csrf=False), json={"text": "大盘概览"})
    assert blocked.status_code == 403
    command = client.post(
        f"{ROUTER_PREFIX}/api/command",
        headers=headers(),
        json={"text": "大盘概览"},
    )
    assert command.status_code == 200
    assert "大盘概览" in command.json()["text"]
    assert command.json()["artifacts"]


def test_private_artifacts_are_owner_only(client):
    forged = headers("alice")
    forged.update({"X-QWeb-User": "bob", "X-QWeb-Admin": "1", "X-QWeb-Session": "forged"})
    command = client.post(f"{ROUTER_PREFIX}/api/command", headers=forged, json={"text": "大盘概览"})
    assert command.status_code == 200
    artifact = command.json()["artifacts"][0]
    artifact_url = artifact["url"]

    owner_view = client.get(artifact_url, headers=headers("alice"))
    assert owner_view.status_code == 200
    assert "sandbox" in owner_view.headers["content-security-policy"]
    assert owner_view.headers["content-disposition"] == "inline"

    assert client.get(artifact_url, headers=headers("bob")).status_code == 404
    bob_list = client.get(f"{ROUTER_PREFIX}/api/artifacts", headers=headers("bob"))
    assert all(item["owner"] == "bob" for item in bob_list.json()["items"])
    alice_list = client.get(f"{ROUTER_PREFIX}/api/artifacts", headers=headers("alice"))
    assert any(item["id"] == artifact["id"] for item in alice_list.json()["items"])


def test_watchlist_post_and_delete_require_csrf(client):
    blocked = client.post(f"{ROUTER_PREFIX}/api/watchlist", headers=headers(csrf=False), json={"query": "600519"})
    assert blocked.status_code == 403
    added = client.post(f"{ROUTER_PREFIX}/api/watchlist", headers=headers(), json={"query": "600519"})
    assert added.status_code == 200
    removed = client.delete(f"{ROUTER_PREFIX}/api/watchlist/600519", headers=headers(csrf=False))
    assert removed.status_code == 403
    removed = client.delete(f"{ROUTER_PREFIX}/api/watchlist/600519", headers=headers())
    assert removed.status_code == 200


def test_qweb_manifest_requires_host_admin_not_forged_header(client):
    no_admin = client.get(
        f"{ROUTER_PREFIX}/api/qweb/manifest",
        headers={**headers(), "X-QWeb-Admin": "1"},
    )
    assert no_admin.status_code == 403
    manifest = client.get(f"{ROUTER_PREFIX}/api/qweb/manifest", headers=headers(admin=True))
    assert manifest.status_code == 200
    payload = manifest.json()
    assert payload["schema"] == "qweb.plugin.v1"
    assert payload["auth"]["identity"] == "account.qq"
    assert payload["auth"]["csrf_header"] == "X-CSRF-Token"
    assert payload["chat"]["endpoint"] == "/qweb/chat"
    assert "llm.chat" not in payload["capabilities"]


def test_qweb_tool_execution_uses_account_and_confirmation(client):
    tools = client.get(f"{ROUTER_PREFIX}/api/qweb/tools", headers=headers())
    assert tools.status_code == 200
    names = {item["name"] for item in tools.json()["tools"]}
    assert "sayustock_quote" in names

    quote = client.post(
        f"{ROUTER_PREFIX}/api/qweb/tools/sayustock_quote",
        headers={**headers(), "X-QWeb-User": "bob"},
        json={"arguments": {"query": "贵州茅台"}},
    )
    assert quote.status_code == 200
    assert "贵州茅台" in quote.json()["text"]

    unconfirmed = client.post(
        f"{ROUTER_PREFIX}/api/qweb/tools/sayustock_watchlist_add",
        headers=headers("alice"),
        json={"arguments": {"query": "600519"}},
    )
    assert unconfirmed.status_code == 409
    confirmed = client.post(
        f"{ROUTER_PREFIX}/api/qweb/tools/sayustock_watchlist_add",
        headers=headers("alice"),
        json={"arguments": {"query": "600519"}, "confirmed": True},
    )
    assert confirmed.status_code == 200


def test_plugin_owned_chat_and_config_routes_are_gone(client):
    assert client.post(f"{ROUTER_PREFIX}/api/chat", headers=headers(), json={"message": "大盘"}).status_code == 404
    assert client.get(f"{ROUTER_PREFIX}/api/config", headers=headers()).status_code == 404
    assert client.post(f"{ROUTER_PREFIX}/api/config", headers=headers(), json={}).status_code == 404
