"""FastAPI router for the SayuStock web console / QWeb base."""

from __future__ import annotations

import time
from typing import Any

from fastapi import Body, Query, Request, APIRouter, HTTPException
from fastapi.responses import Response, FileResponse, HTMLResponse, RedirectResponse

from . import STATIC_DIR, ASSET_TOKEN, TEMPLATES_DIR
from ..menu import MENU_DATA, PLUGIN_NAME, PLUGIN_VERSION, PLUGIN_HOMEPAGE, PLUGIN_DESCRIPTION
from ..ai.llm import LLMError, chat, configured
from ..config import get_value, load_config, update_config
from ..runtime import get_runtime, artifact_url
from ..ai.tools import TOOLS
from ..commands import execute
from ..platform import UnifiedEvent
from ..artifacts import get_artifact, list_artifacts
from ..ai.contracts import ToolContext
from ..services.charts import (
    ChartRenderError,
    rasterize,
    overview_html,
    build_kline_html,
    build_compare_html,
    build_intraday_html,
    build_multi_intraday_html,
)

ROUTER_PREFIX = "/sayustock"


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-sayustock-token", "").strip() or request.query_params.get("token", "").strip()


def _authorized(request: Request) -> bool:
    expected = str(get_value("web.token", "") or "").strip()
    if not expected:
        if bool(get_value("web.allow_localhost", True)) and request.client:
            return request.client.host in {"127.0.0.1", "::1", "localhost"}
        return True
    supplied = _bearer(request)
    if supplied == expected:
        return True
    if request.headers.get("x-qweb-admin") == "1" and str(get_value("web.trust_qweb", False)).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True
    return False


def _require(request: Request) -> None:
    if not _authorized(request):
        raise HTTPException(status_code=401, detail="无效的 SayuStock 访问令牌")


def _web_context(request: Request) -> ToolContext:
    user = (
        request.headers.get("x-qweb-user", "").strip()
        or request.headers.get("x-forwarded-user", "").strip()
        or "web-guest"
    )
    session = request.headers.get("x-qweb-session", "").strip() or f"web:{user}"
    return ToolContext(platform="web", user_id=user, session_id=session, bot_id="web")


def _watch_key(ctx: ToolContext) -> str:
    from ..watchlist import owner_key

    return owner_key(platform="web", bot_id="web", user_id=ctx.user_id)


def _artifact_payload(request: Request, artifact: Any) -> dict[str, Any]:
    base = str(get_value("web.public_base_url", "") or "")
    if isinstance(artifact, dict):
        artifact_id = artifact.get("id")
        title = artifact.get("title")
        kind = artifact.get("kind")
        mime = artifact.get("mime")
        prefix = f"{base}/sayustock" if base else "/sayustock"
        url = (
            f"{prefix}/artifact/{artifact_id}.png"
            if str(mime or "").startswith("image/")
            else f"{prefix}/artifact/{artifact_id}.html"
        )
        return {"id": artifact_id, "title": title, "kind": kind, "mime": mime, "url": url}
    return {
        "id": artifact.id,
        "title": artifact.title,
        "kind": artifact.kind,
        "mime": artifact.mime,
        "url": artifact_url(artifact, public_base_url=base),
    }


def _fit_chart_html(html: str) -> str:
    """Scale a fixed-width chart page to the iframe viewport."""
    script = """
<script>
(function () {
  function fit() {
    var page = document.querySelector('.chart-page');
    if (!page) return;
    var width = page.scrollWidth || 1200;
    var scale = Math.min(1, (window.innerWidth - 4) / width);
    page.style.transformOrigin = 'top left';
    page.style.transform = 'scale(' + scale + ')';
    page.style.position = 'absolute';
    page.style.left = '0';
    page.style.top = '0';
    document.body.style.width = (width * scale) + 'px';
    document.body.style.height = (page.scrollHeight * scale) + 'px';
    document.body.style.overflowX = 'hidden';
    document.documentElement.style.overflowX = 'hidden';
  }
  fit();
  window.addEventListener('resize', fit);
  window.addEventListener('load', fit);
})();
</script>
"""
    if "</body>" in html:
        return html.replace("</body>", script + "</body>")
    return html + script


def get_router() -> APIRouter:
    router = APIRouter()

    @router.get(f"{ROUTER_PREFIX}", include_in_schema=False)
    async def root() -> Any:
        return RedirectResponse(url=f"{ROUTER_PREFIX}/")

    @router.get(f"{ROUTER_PREFIX}/embed", include_in_schema=False)
    async def embed() -> Any:
        return RedirectResponse(url=f"{ROUTER_PREFIX}/?embed=1")

    @router.get(f"{ROUTER_PREFIX}/", include_in_schema=False)
    async def index() -> Any:
        path = TEMPLATES_DIR / "index.html"
        if not path.is_file():
            return HTMLResponse("<h1>SayuStock</h1><p>前端页面缺失。</p>", status_code=404)
        return FileResponse(path, media_type="text/html; charset=utf-8")

    @router.get(f"{ROUTER_PREFIX}/static/{{path:path}}", include_in_schema=False)
    async def static(path: str) -> Any:
        target = (STATIC_DIR / path).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return HTMLResponse("非法路径", status_code=400)
        if not target.is_file():
            return HTMLResponse("资源不存在", status_code=404)
        suffix = target.suffix.lower()
        media = {
            ".js": "application/javascript",
            ".css": "text/css",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".woff2": "font/woff2",
        }.get(suffix, "application/octet-stream")
        return FileResponse(target, media_type=media)

    @router.get(f"{ROUTER_PREFIX}/api/health")
    async def health() -> Any:
        return {
            "ok": True,
            "name": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "llm_configured": configured(),
            "render_enabled": get_runtime().render_enabled,
            "render_backend": "nonebot-plugin-htmlrender",
            "time": time.time(),
        }

    @router.get(f"{ROUTER_PREFIX}/api/meta")
    async def meta(request: Request) -> Any:
        _require(request)
        return {
            "name": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "description": PLUGIN_DESCRIPTION,
            "homepage": PLUGIN_HOMEPAGE,
            "llm_configured": configured(),
            "tools": [
                {"name": item.name, "description": item.description, "effect": item.effect} for item in TOOLS.values()
            ],
            "qweb": {
                "mount": ROUTER_PREFIX,
                "asset_token": ASSET_TOKEN,
                "embed": True,
            },
        }

    @router.get(f"{ROUTER_PREFIX}/api/config")
    async def read_config(request: Request) -> Any:
        _require(request)
        data = load_config()
        llm = dict(data.get("llm") or {})
        if llm.get("api_key"):
            llm["api_key"] = "***"
        data["llm"] = llm
        return data

    @router.post(f"{ROUTER_PREFIX}/api/config")
    async def write_config(request: Request, patch: dict[str, Any] = Body(...)) -> Any:
        _require(request)
        if request.client and request.client.host not in {"127.0.0.1", "::1"}:
            if str(get_value("web.token", "") or "").strip() == "":
                raise HTTPException(status_code=403, detail="写入配置需要先设置访问令牌")
        safe_patch = {
            key: value for key, value in patch.items() if key in {"market", "render", "web", "llm", "watchlist"}
        }
        updated = update_config(safe_patch)
        llm = dict(updated.get("llm") or {})
        if llm.get("api_key"):
            llm["api_key"] = "***"
        updated["llm"] = llm
        return {"ok": True, "config": updated}

    @router.get(f"{ROUTER_PREFIX}/api/overview")
    async def overview(request: Request, chart: bool = Query(False)) -> Any:
        _require(request)
        try:
            data = await get_runtime().market.overview()
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        payload: dict[str, Any] = {"ok": True, "data": data}
        if chart:
            html = overview_html(data)
            payload["html"] = html
            payload["chart_url"] = f"{ROUTER_PREFIX}/api/chart/html?type=overview"
        return payload

    @router.get(f"{ROUTER_PREFIX}/api/quote")
    async def quote(request: Request, q: str = Query(..., min_length=1, max_length=80)) -> Any:
        _require(request)
        try:
            data = await get_runtime().market.quote(q)
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"ok": True, "data": data}

    @router.get(f"{ROUTER_PREFIX}/api/kline")
    async def kline(
        request: Request,
        q: str = Query(..., min_length=1, max_length=80),
        period: str = Query("日k", max_length=10),
    ) -> Any:
        _require(request)
        try:
            data = await get_runtime().market.kline(q, period)
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"ok": True, "data": data}

    @router.get(f"{ROUTER_PREFIX}/api/sector")
    async def sector(request: Request, kind: str = Query("industry")) -> Any:
        _require(request)
        normalized = "concept" if kind in {"concept", "概念"} else "industry"
        try:
            data = await get_runtime().market.board(normalized, limit=30)
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"ok": True, "data": data}

    @router.get(f"{ROUTER_PREFIX}/api/chart/html")
    async def chart_html(
        request: Request,
        type: str = Query("kline"),
        q: str = Query("上证指数", max_length=120),
        period: str = Query("日k", max_length=10),
        ndays: int = Query(1, ge=1, le=5),
    ) -> Any:
        _require(request)
        runtime = get_runtime()
        try:
            kind = type.lower()
            if kind == "overview":
                data = await runtime.market.overview()
                html, width, title = overview_html(data), 1280, "A股大盘概览"
            elif kind == "intraday":
                queries = q.replace("，", " ").split()
                if len(queries) > 1:
                    html, width, title = await build_multi_intraday_html(runtime.market, queries, ndays)
                else:
                    html, width, title = await build_intraday_html(runtime.market, q, ndays)
            elif kind == "compare":
                queries = q.replace("，", " ").split()
                html, width, title = await build_compare_html(runtime.market, queries, period)
            else:
                html, width, title = await build_kline_html(runtime.market, q, period)
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        headers = {
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                "font-src data:; script-src 'unsafe-inline'; "
                "sandbox allow-same-origin allow-scripts"
            )
        }
        return HTMLResponse(_fit_chart_html(html), headers=headers)

    @router.get(f"{ROUTER_PREFIX}/api/chart/png")
    async def chart_png(
        request: Request,
        type: str = Query("kline"),
        q: str = Query("上证指数", max_length=120),
        period: str = Query("日k", max_length=10),
        ndays: int = Query(1, ge=1, le=5),
    ) -> Any:
        _require(request)
        runtime = get_runtime()
        try:
            kind = type.lower()
            if kind == "overview":
                data = await runtime.market.overview()
                html, width = overview_html(data), 1280
            elif kind == "intraday":
                queries = q.replace("，", " ").split()
                if len(queries) > 1:
                    html, width, _ = await build_multi_intraday_html(runtime.market, queries, ndays)
                else:
                    html, width, _ = await build_intraday_html(runtime.market, q, ndays)
            elif kind == "compare":
                queries = q.replace("，", " ").split()
                html, width, _ = await build_compare_html(runtime.market, queries, period)
            else:
                html, width, _ = await build_kline_html(runtime.market, q, period)
            async with runtime.render_lock:
                image = await rasterize(html, width=width, scale=runtime.render_scale())
        except ChartRenderError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return Response(content=image, media_type="image/png")

    @router.get(f"{ROUTER_PREFIX}/api/watchlist")
    async def watchlist(request: Request) -> Any:
        _require(request)
        ctx = _web_context(request)
        items = await get_runtime().watchlist.list(_watch_key(ctx))
        return {"ok": True, "items": items}

    @router.post(f"{ROUTER_PREFIX}/api/watchlist")
    async def watchlist_add(request: Request, body: dict[str, Any] = Body(...)) -> Any:
        _require(request)
        ctx = _web_context(request)
        query = str(body.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="缺少 query")
        runtime = get_runtime()
        symbol = await runtime.market.resolve(query)
        if symbol is None:
            raise HTTPException(status_code=404, detail=f"未找到标的：{query}")
        added = await runtime.watchlist.add(_watch_key(ctx), symbol.code, symbol.name, query)
        return {"ok": True, "added": added, "code": symbol.code, "name": symbol.name}

    @router.delete(f"{ROUTER_PREFIX}/api/watchlist/{{code}}")
    async def watchlist_remove(request: Request, code: str) -> Any:
        _require(request)
        ctx = _web_context(request)
        removed = await get_runtime().watchlist.remove(_watch_key(ctx), code)
        return {"ok": True, "removed": removed}

    @router.post(f"{ROUTER_PREFIX}/api/command")
    async def command(request: Request, body: dict[str, Any] = Body(...)) -> Any:
        _require(request)
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="缺少 text")
        ctx = _web_context(request)
        ue = UnifiedEvent(
            platform="web",
            bot_id="web",
            user_id=ctx.user_id,
            text=text,
            message_id="",
            sender_name=ctx.user_id,
        )
        try:
            result = await execute(text, ue)
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {
            "ok": True,
            "text": result.with_links(),
            "image": (
                "data:image/png;base64," + __import__("base64").b64encode(result.image).decode("ascii")
                if result.image
                else None
            ),
            "artifacts": [_artifact_payload(request, item) for item in result.artifacts],
        }

    @router.post(f"{ROUTER_PREFIX}/api/chat")
    async def web_chat(request: Request, body: dict[str, Any] = Body(...)) -> Any:
        _require(request)
        message = str(body.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="缺少 message")
        history = body.get("history")
        ctx = _web_context(request)
        if not configured():
            # Allow command-style use even before an LLM is configured.
            try:
                result = await execute(
                    message, UnifiedEvent(platform="web", bot_id="web", user_id=ctx.user_id, text=message)
                )
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            return {
                "ok": True,
                "mode": "command",
                "reply": result.with_links(),
                "tool_calls": [],
                "images": (
                    ["data:image/png;base64," + __import__("base64").b64encode(result.image).decode("ascii")]
                    if result.image
                    else []
                ),
                "artifacts": [_artifact_payload(request, item) for item in result.artifacts],
                "llm_configured": False,
            }
        try:
            result = await chat(message, context=ctx, history=history if isinstance(history, list) else [])
        except LLMError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        result["artifacts"] = [_artifact_payload(request, item) for item in result.get("artifacts", [])]
        return result

    @router.get(f"{ROUTER_PREFIX}/api/artifacts")
    async def artifacts(request: Request) -> Any:
        _require(request)
        return {"ok": True, "items": list_artifacts(50)}

    @router.get(f"{ROUTER_PREFIX}/artifact/{{artifact_id}}.html")
    async def artifact_html(artifact_id: str) -> Any:
        found = get_artifact(artifact_id)
        if found is None or found[0].mime not in {"text/html", "text/html; charset=utf-8"}:
            return HTMLResponse("制品不存在", status_code=404)
        _, path = found
        headers = {
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                "font-src data:; sandbox allow-same-origin"
            ),
            "X-Content-Type-Options": "nosniff",
        }
        return HTMLResponse(path.read_text(encoding="utf-8"), headers=headers)

    @router.get(f"{ROUTER_PREFIX}/artifact/{{artifact_id}}.png")
    async def artifact_png(artifact_id: str) -> Any:
        found = get_artifact(artifact_id)
        if found is None or not found[0].mime.startswith("image/"):
            return Response(status_code=404)
        _, path = found
        return Response(content=path.read_bytes(), media_type="image/png")

    @router.get(f"{ROUTER_PREFIX}/api/qweb/catalog")
    async def qweb_catalog(request: Request) -> Any:
        _require(request)
        features = []
        for index, item in enumerate(MENU_DATA):
            features.append(
                {
                    "id": f"sayustock.feature.{index + 1}",
                    "name": item.get("func"),
                    "summary": item.get("brief_des"),
                    "description": item.get("detail_des"),
                    "trigger_method": item.get("trigger_method"),
                    "triggers": [item.get("trigger_condition")],
                    "llm_intents": item.get("llm_intents") or [],
                    "hidden": False,
                }
            )
        return {
            "schema": "qweb.feature_catalog.v1",
            "plugin": "sayustock",
            "version": PLUGIN_VERSION,
            "features": features,
            "tools": [
                {"name": item.name, "description": item.description, "effect": item.effect} for item in TOOLS.values()
            ],
        }

    @router.get(f"{ROUTER_PREFIX}/api/qweb/manifest")
    async def qweb_manifest(request: Request) -> Any:
        _require(request)
        return {
            "schema": "qweb.plugin.v1",
            "id": "sayustock",
            "name": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "description": PLUGIN_DESCRIPTION,
            "homepage": PLUGIN_HOMEPAGE,
            "mount": ROUTER_PREFIX,
            "entry": f"{ROUTER_PREFIX}/",
            "embed": f"{ROUTER_PREFIX}/?embed=1",
            "admin": "/qweb/admin/sayustock",
            "catalog": f"{ROUTER_PREFIX}/api/qweb/catalog",
            "api": f"{ROUTER_PREFIX}/api",
            "bridge": f"{ROUTER_PREFIX}/static/qweb-bridge.js",
            "postMessage": {
                "source": "sayustock",
                "ready": "sayustock.ready",
                "reply": "sayustock.reply",
                "error": "sayustock.error",
            },
            "capabilities": [
                "market.overview",
                "market.quote",
                "market.kline",
                "market.sector",
                "chart.html",
                "chart.png",
                "artifact.html",
                "llm.chat",
            ],
            "auth": {
                "type": "bearer",
                "env": "SAYUSTOCK_WEB_TOKEN",
                "qweb_admin_header": "X-QWeb-Admin",
            },
        }

    return router


def mount_webui(app: Any) -> bool:
    """Mount the router on the NoneBot FastAPI app (idempotent)."""
    if getattr(app, "__sayustock_mounted__", False):
        return True
    try:
        app.include_router(get_router())
        setattr(app, "__sayustock_mounted__", True)
        return True
    except Exception as error:
        from nonebot.log import logger

        logger.error("[SayuStock] 挂载 WebUI 失败: {}", error)
        return False


__all__ = ["ROUTER_PREFIX", "get_router", "mount_webui"]
