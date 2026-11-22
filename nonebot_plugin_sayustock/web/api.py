"""FastAPI router for the SayuStock page embedded by the QWeb host.

There is deliberately no plugin-owned login, token, admin header or LLM route.
Membership/admin/CSRF all come from the host dependencies; the host account's
``qq`` is the only identity the router will use.  Private artifacts are served
only to their owner.
"""

from __future__ import annotations

import asyncio
import re
import time
import base64
from typing import Any
from pathlib import Path

from fastapi import Body, Query, Depends, Request, APIRouter, HTTPException
from fastapi.responses import Response, FileResponse, HTMLResponse, RedirectResponse

from .host import (
    CSRF_HEADER,
    HOST_DEPENDENCIES_MODULE,
    WebAccount,
    HostDependencies,
    WebIdentityError,
    load_host_dependencies,
)
from ..menu import MENU_DATA, PLUGIN_NAME, PLUGIN_VERSION, PLUGIN_HOMEPAGE, PLUGIN_DESCRIPTION
from ..paths import STATIC_DIR, TEMPLATES_DIR
from ..runtime import get_runtime
from ..storage import storage_mode
from ..ai.tools import TOOLS, run_tool
from ..commands import execute
from ..features import invoke, lookup, catalog, web_event
from ..platform import UnifiedEvent
from ..artifacts import Artifact, get_artifact, list_artifacts
from ..ai.contracts import ToolContext
from ..services.theme import ThemeContext, resolve_theme
from ..services.charts import (
    ChartRenderError,
    rasterize,
    overview_html,
    build_kline_html,
    build_compare_html,
    build_intraday_html,
    build_multi_intraday_html,
)
from ..services.overview import breadth_svg, market_mood, sources_text

ROUTER_PREFIX = "/qweb/sayustock"


def _mount_path(request: Request, prefix: str) -> str:
    root = str(request.scope.get("root_path") or "").rstrip("/")
    return f"{root}{prefix}"


def _artifact_payload(request: Request, artifact: Artifact, prefix: str) -> dict[str, Any]:
    suffix = "png" if artifact.mime.startswith("image/") else "html"
    return {
        "id": artifact.id,
        "title": artifact.title,
        "kind": artifact.kind,
        "mime": artifact.mime,
        "owner": artifact.owner,
        "url": f"{_mount_path(request, prefix)}/artifact/{artifact.id}.{suffix}",
    }


def _watch_key(account: WebAccount) -> str:
    from ..watchlist import owner_key

    return owner_key(platform="qweb", bot_id="qweb", user_id=account.qq)


def _unavailable_router() -> APIRouter:
    router = APIRouter()

    async def unavailable(path: str = "") -> Any:
        raise HTTPException(
            status_code=503,
            detail=f"SayuStock 网页未启用：未找到 QWeb 宿主依赖 {HOST_DEPENDENCIES_MODULE}",
        )

    router.add_api_route(ROUTER_PREFIX, unavailable, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    router.add_api_route(
        f"{ROUTER_PREFIX}/{{path:path}}",
        unavailable,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    return router


def _build_router(host: HostDependencies, prefix: str = ROUTER_PREFIX) -> APIRouter:
    router = APIRouter()

    async def member(account: Any = Depends(host.require_membership)) -> WebAccount:
        try:
            return WebAccount.from_host(account)
        except WebIdentityError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error

    async def admin(account: Any = Depends(host.admin_account)) -> WebAccount:
        try:
            return WebAccount.from_host(account)
        except WebIdentityError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

    async def csrf_guard(request: Request, _: Any = Depends(host.csrf_protected)) -> None:
        if not request.headers.get(CSRF_HEADER, "").strip():
            raise HTTPException(status_code=403, detail=f"缺少 {CSRF_HEADER}")

    @router.get(f"{prefix}", include_in_schema=False)
    async def root(_account: WebAccount = Depends(member)) -> Any:
        return RedirectResponse(url=f"{prefix}/")

    @router.get(f"{prefix}/embed", include_in_schema=False)
    async def embed(_account: WebAccount = Depends(member)) -> Any:
        return RedirectResponse(url=f"{prefix}/?embed=1")

    @router.get(f"{prefix}/", include_in_schema=False)
    async def index(_account: WebAccount = Depends(member)) -> Any:
        path = TEMPLATES_DIR / "index.html"
        if not path.is_file():
            return HTMLResponse("<h1>SayuStock</h1><p>前端页面缺失。</p>", status_code=404)
        headers = {
            "X-Frame-Options": "SAMEORIGIN",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": (
                "default-src 'self'; base-uri 'self'; object-src 'none'; form-action 'self'; "
                "script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                "frame-ancestors 'self'"
            ),
        }
        return FileResponse(path, media_type="text/html; charset=utf-8", headers=headers)

    @router.get(f"{prefix}/static/{{path:path}}", include_in_schema=False)
    async def static(path: str, _account: WebAccount = Depends(member)) -> Any:
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

    @router.get(f"{prefix}/api/health")
    async def health(_account: WebAccount = Depends(member)) -> Any:
        return {
            "ok": True,
            "name": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "render_enabled": get_runtime().render_enabled,
            "render_backend": "nonebot-plugin-htmlrender",
            "storage": storage_mode(),
            "time": time.time(),
        }

    @router.get(f"{prefix}/api/meta")
    async def meta(request: Request, _account: WebAccount = Depends(member)) -> Any:
        return {
            "name": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "description": PLUGIN_DESCRIPTION,
            "homepage": PLUGIN_HOMEPAGE,
            "mount": _mount_path(request, prefix),
            "tools": [
                {
                    "name": item.name,
                    "description": item.description,
                    "effect": item.effect,
                    "permission": item.permission,
                }
                for item in TOOLS.values()
            ],
            "qweb": {
                "host": HOST_DEPENDENCIES_MODULE,
                "chat": "/qweb/chat",
                "csrf_header": CSRF_HEADER,
                "embed": True,
            },
        }

    @router.get(f"{prefix}/api/mood/{{number}}")
    async def mood_image(number: int, _account: WebAccount = Depends(member)) -> Any:
        if not 1 <= number <= 11:
            raise HTTPException(404, "未知情绪图")
        from SayuStock.render import card_widgets

        path = Path(card_widgets.__file__).resolve().parents[1] / "stock_info" / "texture2d" / f"title{number}.png"
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})

    @router.get(f"{prefix}/api/overview")
    async def overview(request: Request, chart: bool = Query(False), _account: WebAccount = Depends(member)) -> Any:
        try:
            data = await get_runtime().market.overview(include_flow=True)
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        mood_label, number = market_mood(data)
        payload: dict[str, Any] = {
            "ok": True,
            "data": data,
            "breadth_svg": breadth_svg(data["breadth"]),
            "mood": mood_label,
            "mood_image": f"{_mount_path(request, prefix)}/api/mood/{number}" if number else None,
            "sources": sources_text(data),
        }
        if chart:
            html = overview_html(data)
            payload["html"] = html
            payload["chart_url"] = f"{_mount_path(request, prefix)}/api/chart/html?type=overview"
        return payload

    @router.get(f"{prefix}/api/quote")
    async def quote(
        request: Request,
        q: str = Query(..., min_length=1, max_length=80),
        _account: WebAccount = Depends(member),
    ) -> Any:
        try:
            data = await get_runtime().market.quote(q)
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"ok": True, "data": data}

    @router.get(f"{prefix}/api/kline")
    async def kline(
        request: Request,
        q: str = Query(..., min_length=1, max_length=80),
        period: str = Query("日k", max_length=10),
        _account: WebAccount = Depends(member),
    ) -> Any:
        try:
            data = await get_runtime().market.kline(q, period)
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"ok": True, "data": data}

    @router.get(f"{prefix}/api/sector")
    async def sector(
        request: Request,
        kind: str = Query("industry"),
        falling: bool = False,
        _account: WebAccount = Depends(member),
    ) -> Any:
        normalized = "concept" if kind in {"concept", "概念"} else "industry"
        try:
            data = await get_runtime().market.board(normalized, limit=30, falling=falling)
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"ok": True, "data": data}

    @router.get(f"{prefix}/api/chart/html")
    async def chart_html(
        request: Request,
        type: str = Query("kline"),
        q: str = Query("上证指数", max_length=120),
        period: str = Query("日k", max_length=10),
        ndays: int = Query(1, ge=1, le=5),
        colors: str = Query("", max_length=55),
        mode: str = Query("dark", pattern="^(dark|light)$"),
        _account: WebAccount = Depends(member),
    ) -> Any:
        runtime = get_runtime()
        context = None
        if colors:
            if not re.fullmatch(r"[a-fA-F0-9]{6}(?:,[a-fA-F0-9]{6}){7}", colors):
                raise HTTPException(status_code=422, detail="图表配色格式错误")
            keys = ("background", "surface", "text", "muted", "accent", "secondary", "pink", "border")
            palette = {key: f"#{value}" for key, value in zip(keys, colors.split(","), strict=True)}
            context = ThemeContext(resolve_theme(), palette=palette, post_mode=mode, mode=mode)
        try:
            kind = type.lower()
            if kind == "overview":
                data = await runtime.market.overview(include_flow=True)
                html, width, title = overview_html(data, context=context), 1280, "A股大盘概览"
            elif kind == "intraday":
                queries = q.replace("，", " ").split()
                if len(queries) > 1:
                    html, width, title = await build_multi_intraday_html(
                        runtime.market, queries, ndays, context=context
                    )
                else:
                    html, width, title = await build_intraday_html(runtime.market, q, ndays, context=context)
            elif kind == "compare":
                queries = q.replace("，", " ").split()
                html, width, title = await build_compare_html(runtime.market, queries, period, context=context)
            else:
                html, width, title = await build_kline_html(runtime.market, q, period, context=context)
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        headers = {
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                "font-src data:; "
                "frame-ancestors 'self'; sandbox"
            ),
            "X-Frame-Options": "SAMEORIGIN",
        }
        return HTMLResponse(html, headers=headers)

    @router.get(f"{prefix}/api/chart/png")
    async def chart_png(
        request: Request,
        type: str = Query("kline"),
        q: str = Query("上证指数", max_length=120),
        period: str = Query("日k", max_length=10),
        ndays: int = Query(1, ge=1, le=5),
        _account: WebAccount = Depends(member),
    ) -> Any:
        runtime = get_runtime()
        try:
            kind = type.lower()
            if kind == "overview":
                data = await runtime.market.overview(include_flow=True)
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

    @router.get(f"{prefix}/api/watchlist")
    async def watchlist(account: WebAccount = Depends(member)) -> Any:
        items = await get_runtime().watchlist.list(_watch_key(account))
        return {"ok": True, "items": items}

    @router.post(f"{prefix}/api/watchlist")
    async def watchlist_add(
        body: dict[str, Any] = Body(...),
        account: WebAccount = Depends(member),
        _csrf: None = Depends(csrf_guard),
    ) -> Any:
        query = str(body.get("query") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="缺少 query")
        runtime = get_runtime()
        symbol = await runtime.market.resolve(query)
        if symbol is None:
            raise HTTPException(status_code=404, detail=f"未找到标的：{query}")
        added = await runtime.watchlist.add(_watch_key(account), symbol.code, symbol.name, query)
        return {"ok": True, "added": added, "code": symbol.code, "name": symbol.name}

    @router.delete(f"{prefix}/api/watchlist/{{code}}")
    async def watchlist_remove(
        code: str,
        account: WebAccount = Depends(member),
        _csrf: None = Depends(csrf_guard),
    ) -> Any:
        removed = await get_runtime().watchlist.remove(_watch_key(account), code)
        return {"ok": True, "removed": removed}

    def feature_result(request, result):
        return {
            "ok": True,
            "text": result.text,
            "structured": result.structured,
            "image": (
                "data:image/png;base64," + base64.b64encode(result.image).decode("ascii") if result.image else None
            ),
            "artifacts": [_artifact_payload(request, item, prefix) for item in result.artifacts],
        }

    @router.get(f"{prefix}/api/features")
    async def features(_account: WebAccount = Depends(member)) -> Any:
        return {
            "items": [
                {
                    "command": f.command,
                    "category": f.category,
                    "usage": f.usage,
                    "writes": f.writes,
                    "aliases": f.aliases,
                }
                for f in catalog().values()
            ]
        }

    @router.get(f"{prefix}/api/features/groups")
    async def feature_groups(account: WebAccount = Depends(member)) -> Any:
        from types import SimpleNamespace

        from src.qi_web.services.chat_actions import account_groups

        return {"items": await account_groups(SimpleNamespace(qq=int(account.qq)))}

    async def perform_feature(request, feature, argument, body, account):
        if not isinstance(argument, str) or len(argument) > 2000:
            raise HTTPException(422, "功能参数无效")
        if feature.writes and body.get("confirmed") is not True:
            raise HTTPException(409, "请确认要执行的操作")
        try:
            event = await web_event(
                account.qq, f"{feature.command} {argument}".rstrip(), str(body.get("group_id") or "")
            )
            result = await invoke(feature, argument, event)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return feature_result(request, result)

    @router.post(f"{prefix}/api/features/{{name}}")
    async def feature_action(
        request: Request,
        name: str,
        body: dict[str, Any] = Body(...),
        account: WebAccount = Depends(member),
        _csrf: None = Depends(csrf_guard),
    ) -> Any:
        feature = catalog().get(name)
        if not feature:
            raise HTTPException(404, "未知股票功能")
        return await perform_feature(request, feature, body.get("argument", ""), body, account)

    @router.post(f"{prefix}/api/command")
    async def command(
        request: Request,
        body: dict[str, Any] = Body(...),
        account: WebAccount = Depends(member),
        _csrf: None = Depends(csrf_guard),
    ) -> Any:
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="缺少 text")
        words = text.lstrip("/").split(maxsplit=1)
        feature = lookup(words[0]) if words else None
        if feature:
            return await perform_feature(request, feature, words[1] if len(words) > 1 else "", body, account)
        ue = UnifiedEvent(
            platform="qweb",
            bot_id="qweb",
            user_id=account.qq,
            text=text,
            message_id="",
            sender_name=account.qq,
        )
        try:
            result = await execute(text, ue)
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return feature_result(request, result)

    @router.get(f"{prefix}/api/artifacts")
    async def artifacts(account: WebAccount = Depends(member)) -> Any:
        return {"ok": True, "items": await asyncio.to_thread(list_artifacts, 50, owner=account.qq)}

    @router.get(f"{prefix}/artifact/{{artifact_id}}.html")
    async def artifact_html(artifact_id: str, account: WebAccount = Depends(member)) -> Any:
        found = await asyncio.to_thread(get_artifact, artifact_id, owner=account.qq)
        if found is None or found[0].mime not in {"text/html", "text/html; charset=utf-8"}:
            return HTMLResponse("制品不存在", status_code=404)
        _, path = found
        headers = {
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                "font-src data:; sandbox allow-same-origin"
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        }
        return HTMLResponse(await asyncio.to_thread(path.read_text, encoding="utf-8"), headers=headers)

    @router.get(f"{prefix}/artifact/{{artifact_id}}.png")
    async def artifact_png(artifact_id: str, account: WebAccount = Depends(member)) -> Any:
        found = await asyncio.to_thread(get_artifact, artifact_id, owner=account.qq)
        if found is None or not found[0].mime.startswith("image/"):
            return Response(status_code=404)
        _, path = found
        return Response(content=await asyncio.to_thread(path.read_bytes), media_type="image/png")

    @router.get(f"{prefix}/api/qweb/tools")
    async def qweb_tools(_account: WebAccount = Depends(member)) -> Any:
        return {
            "schema": "qweb.plugin.tools.v1",
            "tools": [
                {
                    "name": item.name,
                    "description": item.description,
                    "parameters": item.parameters,
                    "effect": item.effect,
                    "permission": item.permission,
                }
                for item in TOOLS.values()
            ],
            "execute": f"{prefix}/api/qweb/tools/{{name}}",
            "csrf_header": CSRF_HEADER,
        }

    @router.post(f"{prefix}/api/qweb/tools/{{name}}")
    async def qweb_execute_tool(
        request: Request,
        name: str,
        body: dict[str, Any] = Body(...),
        account: WebAccount = Depends(member),
        _csrf: None = Depends(csrf_guard),
    ) -> Any:
        definition = TOOLS.get(name)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"未知工具：{name}")
        arguments = body.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        confirmed = body.get("confirmed") is True
        if definition.effect == "mutating" and not confirmed:
            raise HTTPException(
                status_code=409,
                detail="该工具会修改用户数据，需要宿主先取得用户确认并传入 confirmed=true",
            )
        context = ToolContext(
            platform="qweb",
            user_id=account.qq,
            session_id=f"qweb:{account.qq}",
            bot_id="qweb",
            confirmed=confirmed,
        )
        try:
            outcome = await run_tool(name, arguments, context)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=f"未知工具：{name}") from error
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        payload = outcome.public()
        payload["ok"] = True
        payload["artifacts"] = [_artifact_payload(request, item, prefix) for item in outcome.artifacts]
        return payload

    @router.get(f"{prefix}/api/qweb/catalog")
    async def qweb_catalog(_account: WebAccount = Depends(admin)) -> Any:
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

    @router.get(f"{prefix}/api/qweb/manifest")
    async def qweb_manifest(request: Request, _account: WebAccount = Depends(admin)) -> Any:
        mount = _mount_path(request, prefix)
        return {
            "schema": "qweb.plugin.v1",
            "id": "sayustock",
            "name": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "description": PLUGIN_DESCRIPTION,
            "homepage": PLUGIN_HOMEPAGE,
            "mount": mount,
            "entry": f"{mount}/",
            "embed": f"{mount}/?embed=1",
            "admin": f"{mount}/api/qweb/manifest",
            "catalog": f"{mount}/api/qweb/catalog",
            "tools": f"{mount}/api/qweb/tools",
            "api": f"{mount}/api",
            "chat": {
                "provider": "qweb",
                "endpoint": "/qweb/chat",
                "billing": "host_account",
            },
            "capabilities": [
                "market.overview",
                "market.quote",
                "market.kline",
                "market.sector",
                "chart.html",
                "chart.png",
                "artifact.html",
                "tools.execute",
            ],
            "auth": {
                "membership": f"{HOST_DEPENDENCIES_MODULE}.require_membership",
                "admin": f"{HOST_DEPENDENCIES_MODULE}.admin_account",
                "csrf": f"{HOST_DEPENDENCIES_MODULE}.csrf_protected",
                "csrf_header": CSRF_HEADER,
                "identity": "account.qq",
            },
        }

    return router


def get_router(host: HostDependencies | None = None) -> APIRouter:
    """Build the router; without QWeb host dependencies every route is 503."""
    resolved = host if host is not None else load_host_dependencies()
    if resolved is None:
        return _unavailable_router()
    return _build_router(resolved)


def mount_webui(app: Any, host: HostDependencies | None = None) -> bool:
    """Mount the router on the host FastAPI app (idempotent)."""
    if getattr(app, "__sayustock_mounted__", False):
        return True
    resolved = host if host is not None else load_host_dependencies()
    if resolved is None:
        from nonebot.log import logger

        logger.warning(
            "[SayuStock] 未找到 QWeb 宿主依赖 {}，网页按默认策略不挂载",
            HOST_DEPENDENCIES_MODULE,
        )
        return False
    try:
        try:
            from src.qi_web.plugin_pages import include_plugin_router
        except ImportError:
            include_plugin_router = None
        for prefix in (ROUTER_PREFIX, "/qweb/api/v1/plugins/sayustock"):
            router = _build_router(resolved, prefix)
            if include_plugin_router is None:
                app.include_router(router)
            else:
                include_plugin_router(app, router)
        setattr(app, "__sayustock_mounted__", True)
        return True
    except Exception as error:
        from nonebot.log import logger

        logger.error("[SayuStock] 挂载 QWeb 页面失败: {}", error)
        return False


__all__ = ["ROUTER_PREFIX", "get_router", "mount_webui"]
