"""模拟盘与宏观事件域（原 stock_papertrade / stock_macro 的 NoneBot2 端口）。

设计边界：

* 账本仍然只有一套 —— 直接复用 ``SayuStock.stock_papertrade.db`` 的 SQLModel 仓储，
  不建第二套账本、不把结果写 JSON；
* 权限在 :func:`dispatch` 内部按可信 ``UnifiedEvent`` 判定，模型/网页参数里的身份
  一律不采信；写操作的二次确认由主控（QWeb / AI 工具）在调用前完成；
* 真实行情与真实宿主 AI 才产生结果：行情不可达、宿主 AI 未配置、数据库不可用时
  返回真实错误，绝不编造价格、成交或事件；
* 群播报只登记 ``SayuPaperBroadcastTarget``，本域不在命令里直接群发（避免冒充群）。

命令范围与原插件一致（另加两个只读/低频运维入口：``模拟盘快照``、``模拟盘决策``），
策略硬闸、T+1、费率与涨跌停全部走原 ``strategy`` / ``trade_executor`` 实现。
"""

from __future__ import annotations

import json
from typing import Any

from nonebot.log import logger

from ..compat import legacy, bootstrap
from ..feature_data import PAPERWORK as FEATURES

bootstrap.install()
legacy.install()

from ..platform import UnifiedEvent  # noqa: E402

DEFAULT_PERIOD_LABELS: dict[str, str] = {
    "日": "今日",
    "今日": "今日",
    "今天": "今日",
    "today": "今日",
    "周": "本周",
    "本周": "本周",
    "this_week": "本周",
    "月": "本月",
    "本月": "本月",
    "this_month": "本月",
    "季": "本季",
    "本季": "本季",
    "年": "本年",
    "本年": "本年",
    "今年": "本年",
    "总": "总",
    "全部": "总",
    "all": "总",
}

# 主命令名 -> (别名, 参数说明, 是否写账本/管理)

_MANAGER_ONLY = frozenset(
    {
        "模拟盘初始化",
        "模拟盘创建",
        "模拟盘改名",
        "模拟盘删除",
        "模拟盘停用",
        "模拟盘启用",
        "模拟盘策略切换",
        "模拟盘推送添加",
        "模拟盘推送删除",
        "模拟盘查询",
        "模拟盘快照",
        "模拟盘决策",
        "模拟盘对账",
        "模拟盘假期还原",
        "模拟盘清盘",
        "宏观事件刷新",
    }
)

_GROUP_ONLY = frozenset({"模拟盘推送添加", "模拟盘推送删除", "模拟盘推送列表"})

_MASTER_ONLY = frozenset({"模拟盘模拟测试", "模拟盘对账", "模拟盘假期还原", "模拟盘假期诊断", "模拟盘清盘"})


def _result(text: str, structured: dict[str, Any] | None = None) -> Any:
    from ..commands import CommandResult

    return CommandResult(text, structured=structured or {})


def _is_manager(event: UnifiedEvent) -> bool:
    """群管理员，或 OneBot 私聊里的宿主 SUPERUSER（原插件的“私聊视为最高权限”）。

    QWeb 事件没有群角色、也没有 bot 适配器，天然走拒绝分支：网页未提供授权群
    就不能管理群盘，只能读。
    """
    if event.platform == "qweb":
        return False
    return event.is_admin or _is_master(event)


def _is_master(event: UnifiedEvent) -> bool:
    from nonebot import get_driver

    if event.platform not in {"onebot11", "onebot12", "qq"}:
        return False
    if not event.is_group or event.raw_event is None or event.bot is None:
        return False
    from nonebot.adapters import Bot, Event

    if not isinstance(event.raw_event, Event) or not isinstance(event.bot, Bot):
        return False
    users = get_driver().config.superusers
    adapter = event.bot.adapter.get_name().split(maxsplit=1)[0].lower()
    return event.user_id in users or f"{adapter}:{event.user_id}" in users


def _admin_denied(event: UnifiedEvent) -> str:
    if event.platform == "qweb" and not event.is_group:
        return (
            "⚠️ 网页未提供授权群，不能在此管理模拟盘。\n"
            "请在已授权群内发送该指令，或直接使用只读的「模拟盘列表 / 模拟盘查看 / 模拟盘自选」。"
        )
    return "⚠️ 仅群主/管理员可执行该操作。"


def _parse_cash(raw: str) -> float | None:
    text = raw.replace(",", "").replace("，", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if 10_000 <= value <= 1_000_000_000 else None


def _split_args(raw: str) -> list[str]:
    return [token for token in raw.replace("，", " ").split() if token]


async def _create_account(event: UnifiedEvent, name: str, strategy_id: str, initial_cash: float) -> str:
    from SayuStock.stock_papertrade import db, strategies, account_scope

    error = account_scope.validate_account_name(name)
    if error:
        return error
    normalized = account_scope.normalize_account_name(name)
    if strategies.get_strategy(strategy_id) is None:
        return f"⚠️ 没有名为 {strategy_id!r} 的策略。\n{strategies.describe_all()}"
    existing = await db.PaperAccountRepo.get_by_name(normalized)
    if existing is not None:
        return f"ℹ️ 已存在名为「{normalized}」的模拟盘（id={existing.id}）。换个名字，或用「模拟盘查看 {normalized}」。"
    enabled = await db.PaperAccountRepo.list_enabled()
    if len(enabled) >= account_scope.MAX_ENABLED_ACCOUNTS:
        return (
            f"⚠️ 启用中的模拟盘已达上限 {account_scope.MAX_ENABLED_ACCOUNTS} 个。"
            "请先用「模拟盘停用 <盘名>」停掉不用的盘。"
        )
    account = await db.PaperAccountRepo.create(
        normalized,
        event.group_id or "",
        event.bot_id or event.platform,
        strategy_id=strategy_id,
        initial_cash=initial_cash,
        mode="balanced",
        initialized_by=event.user_id,
    )
    if event.group_id:
        try:
            await db.PaperBroadcastRepo.add(
                account.id or 0,
                bot_id=event.bot_id or event.platform,
                group_id=str(event.group_id),
                bot_self_id=event.bot_id or "",
                ws_bot_id="",
                created_by=event.user_id,
            )
        except Exception as error:  # 订阅失败不影响建盘，但必须告知
            logger.warning("[SayuStock][PaperTrade] 建盘订阅播报失败: {}", error)
    from .paperwork_lifecycle import start_lifecycle

    start_lifecycle()
    return (
        f"✅ 模拟盘「{account.name}」已创建\n"
        f"策略: {account.strategy_id}\n"
        f"初始资金: {account.initial_cash:,.0f}\n"
        "交易由宿主 AI 每 30 分钟（交易日 9:30-11:30 / 13:00-15:00）决策，"
        "收盘 15:05 写净值快照；成交只落原账本，本群如需播报请用「模拟盘推送添加」。"
    )


async def _resolve(account_name: str, *, fallback_default: bool) -> Any:
    from SayuStock.stock_papertrade import account_scope

    return await account_scope.resolve_account(name=account_name, fallback_default=fallback_default)


async def _account_text(name: str, argument: str) -> tuple[Any, str]:
    from SayuStock.stock_papertrade import account_scope

    target = account_scope.normalize_account_name(argument)
    if not target:
        return None, "⚠️ 用法需要盘名。发送「模拟盘列表」查看现有的盘。"
    account = await _resolve(target, fallback_default=False)
    if account is None:
        return None, account_scope.not_opened_message(name=target)
    return account, ""


# ---------------------------------------------------------------------------
# HTML 视图
# ---------------------------------------------------------------------------
async def _finalize(title: str, html: str, width: int, event: UnifiedEvent, *, kind: str = "report") -> Any:
    from .theme import themed_upstream_page
    from ..runtime import get_runtime
    from ..artifacts import create_html
    from ..services.charts import ChartRenderError, rasterize

    themed = themed_upstream_page(html, title)
    artifact = create_html(title, themed, kind=kind, owner=event.user_id, meta={"command": title})
    image: bytes | None = None
    runtime = get_runtime()
    if event.platform != "qweb" and not event._extra.get("qweb") and runtime.render_enabled:
        try:
            async with runtime.render_lock:
                image = await rasterize(themed, width=width, scale=runtime.render_scale())
        except ChartRenderError as error:
            logger.info("[SayuStock][PaperTrade] 图片渲染不可用: {}", error)
        except Exception as error:
            logger.warning("[SayuStock][PaperTrade] 图片渲染异常: {}", error)
    return _result(f"【{title}】", {"artifact": artifact.to_dict(), "image": image is not None}), image, artifact


async def _account_view(account: Any, event: UnifiedEvent) -> Any:
    from SayuStock.render.extras_ui import Metric, direction
    from SayuStock.render.templates import render_template
    from SayuStock.stock_papertrade import db

    positions = await db.PaperPositionRepo.list_by_account(account.id or 0)
    trades = await db.PaperTradeRepo.list_by_account(account.id or 0, limit=5)
    value = sum(item.qty * item.avg_cost for item in positions)
    equity = float(account.cash) + value
    pnl = equity - float(account.initial_cash)
    pnl_pct = pnl / float(account.initial_cash) if account.initial_cash else 0.0
    share = value / equity if equity else 0.0
    html = render_template(
        "paper_account.html",
        width=900,
        account_name=account.name,
        subtitle=f"策略 {account.strategy_id} · 风控 {account.mode}",
        enabled=bool(account.enabled),
        equity=f"{equity:,.0f}",
        pnl=f"{pnl:+,.0f}",
        pnl_pct=f"{pnl_pct:+.2%}",
        pnl_tone=direction(pnl),
        metrics_values=(
            Metric("期初资金", f"{account.initial_cash:,.0f}"),
            Metric("当前现金", f"{account.cash:,.0f}"),
            Metric("持仓成本", f"{value:,.0f}", note=f"仓位 {share:.1%}"),
        ),
        positions=tuple(
            {
                "name": item.stock_name or item.stock_code,
                "code": item.stock_code,
                "quantity": f"{item.qty:,}",
                "price": f"{item.avg_cost:.2f}",
                "value": f"{item.qty * item.avg_cost:,.0f}",
            }
            for item in positions
        ),
        trades=tuple(
            {
                "name": item.stock_name or item.stock_code,
                "side": "买入" if item.side == "buy" else "卖出",
                "tone": "up" if item.side == "buy" else "down",
                "quantity": f"{item.qty:,}",
                "price": f"{item.price:.2f}",
                "fee": f"{item.fee:.2f}",
            }
            for item in trades
        ),
        footer="SayuStock · 模拟盘账户账本",
    )
    text = (
        f"【模拟盘「{account.name}」】\n"
        f"策略 {account.strategy_id} · 状态 {'🟢运行中' if account.enabled else '🔴已停用'}\n"
        f"总资产（持仓按成本）{equity:,.0f} · 累计盈亏 {pnl:+,.0f}（{pnl_pct:+.2%}）\n"
        f"现金 {account.cash:,.0f} · 持仓 {len(positions)} 只 · 最近交易 {len(trades)} 笔"
    )
    structured = {
        "account": account.name,
        "account_id": account.id,
        "cash": float(account.cash),
        "total_equity": round(equity, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct * 100.0, 4),
        "positions": [
            {"code": item.stock_code, "name": item.stock_name, "qty": item.qty, "avg_cost": item.avg_cost}
            for item in positions
        ],
        "trades": [
            {
                "side": item.side,
                "code": item.stock_code,
                "name": item.stock_name,
                "qty": item.qty,
                "price": item.price,
                "fee": item.fee,
                "realized_pnl": item.realized_pnl,
            }
            for item in trades
        ],
    }
    status, image, artifact = await _finalize(f"模拟盘「{account.name}」", html, 900, event)
    status.text = text
    status.structured = {**structured, "artifact": artifact.to_dict(), "image": image is not None}
    if image is not None:
        status.image = image
        status.artifacts = [artifact]
    else:
        status.artifacts = [artifact]
    return status


async def _holdings_view(account: Any, event: UnifiedEvent) -> Any:
    from SayuStock.utils.market import get_market, is_market_error
    from SayuStock.utils.warn_box import Failure
    from SayuStock.utils.sparkline import sparkline_from_series
    from SayuStock.stock_papertrade import db
    from SayuStock.utils.index_quotes import quote_index_items
    from SayuStock.utils.paper_holdings_html import (
        SPARK_H,
        SPARK_W,
        PAPER_HOLDINGS_WIDTH,
        HoldingBarRow,
        build_paper_holdings_html,
    )

    positions = await db.PaperPositionRepo.list_by_account(account.id or 0)
    market = get_market()
    rows: list[HoldingBarRow] = []
    failures: list[Failure] = []
    sparks: dict[str, str] = {}
    for position in positions:
        quote = None
        error = ""
        try:
            result = await market.quote(position.secid or position.stock_code)
            if is_market_error(result) or result.price <= 0:
                error = result.message if is_market_error(result) else "行情价格无效"
            else:
                quote = result
        except Exception as exc:
            error = f"行情请求失败：{type(exc).__name__}"
        price = float(quote.price) if quote is not None else float(position.last_quote_price or position.avg_cost)
        basis = (
            "最新行情" if quote is not None else ("上次报价" if position.last_quote_price is not None else "成本估值")
        )
        day_change = float(quote.change_pct) if quote is not None and quote.change_pct is not None else None
        if error:
            failures.append(
                Failure(label=f"{position.stock_name or position.stock_code}({position.stock_code})", reason=error)
            )
        if quote is not None:
            try:
                series = await market.intraday(position.secid or position.stock_code)
                if not is_market_error(series):
                    spark = sparkline_from_series(series, width=SPARK_W, height=SPARK_H)
                    if spark:
                        sparks[position.stock_code] = spark
            except Exception as exc:
                logger.debug("[SayuStock][PaperTrade] 分时图跳过 {}: {}", position.stock_code, exc)
        cost = float(position.avg_cost or 0.0)
        qty = int(position.qty)
        market_value = price * qty
        unreal = (price - cost) * qty if cost else 0.0
        rows.append(
            HoldingBarRow(
                code=position.stock_code,
                name=position.stock_name or position.stock_code,
                qty=qty,
                avg_cost=cost,
                current_price=round(price, 4),
                day_change_pct=day_change,
                unrealized_pnl=round(unreal, 2),
                unrealized_pnl_pct=round(unreal / (cost * qty) * 100.0, 4) if cost and qty else 0.0,
                market_value=round(market_value, 2),
                provider=quote.provider if quote is not None else None,
                quote_error=error,
                price_basis=basis,
            )
        )
    try:
        index_items = await quote_index_items(["上证指数", "深证成指", "中证A500", "中证2000"])
    except Exception as exc:
        logger.warning("[SayuStock][PaperTrade] 指数条获取失败: {}", exc)
        index_items = []
    html = build_paper_holdings_html(
        account_name=account.name,
        strategy_id=account.strategy_id,
        enabled=int(account.enabled or 0) == 1,
        cash=float(account.cash),
        initial_cash=float(account.initial_cash),
        holdings=rows,
        index_items=index_items,
        sparklines=sparks,
        failures=failures,
        warn_title=f"{len(failures)}/{len(rows)} 只持仓今日行情获取失败",
    )
    position_value = sum(item.market_value for item in rows)
    equity = float(account.cash) + position_value
    pnl = equity - float(account.initial_cash)
    text = (
        f"【模拟盘自选】{account.name} 总资产 {equity:,.0f} 现金 {float(account.cash):,.0f} 持仓 {len(rows)} 只\n"
        f"累计 {pnl:+,.0f}（{(pnl / float(account.initial_cash) * 100.0) if account.initial_cash else 0.0:+.2f}%）"
    )
    if failures:
        text += "\n⚠️ " + "；".join(f"{item.label} {item.reason}" for item in failures[:5])
    status, image, artifact = await _finalize("模拟盘自选", html, PAPER_HOLDINGS_WIDTH, event, kind="chart")
    status.text = text
    status.image = image
    status.artifacts = [artifact]
    status.image_only = image is not None
    status.structured = {
        "account": account.name,
        "account_id": account.id,
        "total_equity": round(equity, 2),
        "holdings": [
            {
                "code": item.code,
                "name": item.name,
                "qty": item.qty,
                "avg_cost": item.avg_cost,
                "price": item.current_price,
                "market_value": item.market_value,
                "unrealized_pnl": item.unrealized_pnl,
                "day_change_pct": item.day_change_pct,
                "price_basis": item.price_basis,
                "quote_error": item.quote_error,
            }
            for item in rows
        ],
        "artifact": artifact.to_dict(),
        "image": image is not None,
    }
    return status


async def _leaderboard(event: UnifiedEvent) -> Any:
    from SayuStock.render.extras_ui import direction
    from SayuStock.render.templates import render_template
    from SayuStock.stock_papertrade import db

    snapshots = await db.PaperSnapshotRepo.list_latest_all_accounts(limit=20)
    accounts = {account.id: account.name for account in await db.PaperAccountRepo.list_all() if account.id is not None}
    rows = [
        {
            "name": accounts.get(item.account_id, f"#{item.account_id}"),
            "trade_date": item.trade_date.isoformat(),
            "equity": f"{item.total_equity:,.0f}",
            "pnl": f"{item.total_pnl:+,.0f}",
            "pnl_tone": direction(item.total_pnl),
            "return_pct": f"{item.total_pnl_pct:+.2f}%",
            "return_tone": direction(item.total_pnl_pct),
        }
        for item in snapshots[:20]
    ]
    html = render_template("paper_leaderboard.html", width=900, rows=rows, footer="SayuStock · 模拟盘每日净值快照")
    text = "【模拟盘排行】\n" + (
        "\n".join(
            f"{index}. {row['name']}（{row['trade_date']}） 总资产 {row['equity']} 收益 {row['return_pct']}"
            for index, row in enumerate(rows, 1)
        )
        if rows
        else "暂无净值快照；收盘 15:05 自动写入，或发「模拟盘快照」立即写。"
    )
    status, image, artifact = await _finalize("模拟盘排行", html, 900, event)
    status.text = text
    status.image = image
    status.artifacts = [artifact]
    status.structured = {"rows": rows, "artifact": artifact.to_dict(), "image": image is not None}
    return status


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
async def _cmd_init(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.stock_papertrade import db, strategies, account_scope

    from .paperwork_lifecycle import start_lifecycle

    cash = 1_000_000.0
    if argument.strip():
        parsed = _parse_cash(argument)
        if parsed is None:
            return _result(f"⚠️ 初始资金格式错误：{argument!r}（须为 1 万~10 亿的数字）")
        cash = parsed
    existing = await db.PaperAccountRepo.get_by_name(account_scope.DEFAULT_ACCOUNT_NAME)
    if existing is not None:
        start_lifecycle()
        return _result(
            f"✅ 默认模拟盘已就绪（策略={existing.strategy_id}，现金 {existing.cash:,.0f}）。"
            "发送「模拟盘决策」可立即跑一轮。"
        )
    return _result(
        await _create_account(event, account_scope.DEFAULT_ACCOUNT_NAME, strategies.DEFAULT_STRATEGY_ID, cash)
    )


async def _cmd_create(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.stock_papertrade import strategies

    tokens = _split_args(argument)
    if not tokens:
        return _result("用法：模拟盘创建 <盘名> <策略id> [初始资金]\n\n" + strategies.describe_all())
    name = tokens[0]
    strategy_id = strategies.DEFAULT_STRATEGY_ID
    cash = 1_000_000.0
    for token in tokens[1:]:
        parsed = _parse_cash(token)
        if parsed is not None:
            cash = parsed
        else:
            strategy_id = token
    return _result(await _create_account(event, name, strategy_id, cash))


async def _cmd_list() -> Any:
    from SayuStock.stock_papertrade import db
    from SayuStock.stock_papertrade.account_scope import not_opened_message

    accounts = await db.PaperAccountRepo.list_all()
    if not accounts:
        return _result(not_opened_message())
    lines = [f"【模拟盘列表 · 共 {len(accounts)} 个】"]
    structured: list[dict[str, Any]] = []
    for item in accounts:
        snapshot = await db.PaperSnapshotRepo.latest(item.id or 0)
        lines.append(
            f"{'🟢' if item.enabled else '🔴'} {item.name}  [{item.strategy_id}]\n"
            f"    现金 {item.cash:,.0f}  总资产 "
            f"{f'{snapshot.total_equity:,.0f}' if snapshot else '—'}  "
            f"收益 {f'{snapshot.total_pnl_pct:+.2f}%' if snapshot else '—'}"
        )
        structured.append(
            {
                "account_id": item.id,
                "name": item.name,
                "strategy_id": item.strategy_id,
                "enabled": bool(item.enabled),
                "cash": item.cash,
                "total_equity": snapshot.total_equity if snapshot else None,
                "total_pnl_pct": snapshot.total_pnl_pct if snapshot else None,
            }
        )
    lines.append("\n查询：模拟盘查看 <盘名> / 模拟盘自选 <盘名> / 模拟盘收益 <盘名> <周期>")
    return _result("\n".join(lines), {"accounts": structured})


async def _cmd_rename(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.stock_papertrade import db, account_scope

    tokens = _split_args(argument)
    if len(tokens) != 2:
        return _result("⚠️ 用法：模拟盘改名 <旧名> <新名>")
    account = await _resolve(tokens[0], fallback_default=False)
    if account is None:
        return _result(account_scope.not_opened_message(name=tokens[0]))
    error = account_scope.validate_account_name(tokens[1])
    if error:
        return _result(error)
    normalized = account_scope.normalize_account_name(tokens[1])
    if await db.PaperAccountRepo.get_by_name(normalized) is not None:
        return _result(f"⚠️ 已存在名为「{normalized}」的模拟盘。")
    updated = await db.PaperAccountRepo.rename(account.id or 0, normalized)
    if updated is None:
        return _result("⚠️ 改名失败（账户可能刚被删除）")
    return _result(f"✅ 「{account.name}」已改名为「{updated.name}」（account_id 未变，账本沿用）。")


async def _cmd_delete(argument: str) -> Any:
    from SayuStock.stock_papertrade import db

    account, error = await _account_text("删除", argument)
    if account is None:
        return _result(error)
    deleted = await db.PaperAccountRepo.reset_account(account.id or 0)
    return _result(
        f"✅ 模拟盘「{account.name}」已删除\n"
        f"清理：持仓 {deleted.get('position', 0)} · 流水 {deleted.get('trade', 0)} · "
        f"决策 {deleted.get('decision', 0)} · 快照 {deleted.get('snapshot', 0)} · "
        f"候选 {deleted.get('agent_pool', 0)} · 播报订阅 {deleted.get('broadcast', 0)}",
        {"deleted": deleted, "account": account.name},
    )


async def _cmd_toggle(argument: str, *, enable: bool) -> Any:
    from SayuStock.stock_papertrade import db, account_scope

    from .paperwork_lifecycle import start_lifecycle

    account, error = await _account_text("启停", argument)
    if account is None:
        return _result(error)
    if enable and account.enabled:
        return _result(f"ℹ️ 模拟盘「{account.name}」本来就是启用状态。")
    if not enable and not account.enabled:
        return _result(f"ℹ️ 模拟盘「{account.name}」本来就是停用状态。")
    if enable:
        enabled = await db.PaperAccountRepo.list_enabled()
        if len(enabled) >= account_scope.MAX_ENABLED_ACCOUNTS:
            return _result(f"⚠️ 启用中的模拟盘已达上限 {account_scope.MAX_ENABLED_ACCOUNTS} 个，请先停用别的盘。")
    updated = await db.PaperAccountRepo.update(account.id or 0, enabled=1 if enable else 0)
    if updated is None:
        return _result("⚠️ 操作失败（账户可能刚被删除）")
    if enable:
        start_lifecycle()
    return _result(
        f"{'🟢 已启用' if enable else '🔴 已停用'}模拟盘「{updated.name}」"
        + ("，后续决策轮次将包含该盘。" if enable else "，不再参与决策轮次。")
    )


async def _cmd_strategy_list() -> Any:
    from SayuStock.stock_papertrade import strategies

    return _result(
        strategies.describe_all() + "\n切换：模拟盘策略切换 <盘名> <策略id>",
        {"strategies": [{"id": item.id, "name": item.name} for item in strategies.list_strategies()]},
    )


async def _cmd_strategy_switch(argument: str) -> Any:
    from SayuStock.stock_papertrade import db, strategies, account_scope

    tokens = _split_args(argument)
    if len(tokens) != 2:
        return _result("用法：模拟盘策略切换 <盘名> <策略id>\n\n" + strategies.describe_all())
    account = await _resolve(tokens[0], fallback_default=False)
    if account is None:
        return _result(account_scope.not_opened_message(name=tokens[0]))
    strategy_id = tokens[1]
    if strategies.get_strategy(strategy_id) is None:
        return _result(f"⚠️ 没有名为 {strategy_id!r} 的策略。\n{strategies.describe_all()}")
    updated = await db.PaperAccountRepo.update(account.id or 0, strategy_id=strategy_id, strategy_params="{}")
    if updated is None:
        return _result("⚠️ 切换失败（账户可能刚被删除）")
    return _result(
        f"✅ 模拟盘「{updated.name}」策略已切换：{account.strategy_id} → {strategy_id}\n"
        "下一轮决策即按新策略硬闸执行；策略参数已重置为默认值。"
    )


async def _subscribe(event: UnifiedEvent, argument: str, *, add: bool) -> Any:
    from SayuStock.stock_papertrade import db

    if not event.group_id:
        return _result("⚠️ 播报订阅只能在群里设置（私聊没有群号）。")
    account, error = await _account_text("订阅", argument)
    if account is None:
        return _result(error)
    if add:
        await db.PaperBroadcastRepo.add(
            account.id or 0,
            bot_id=event.bot_id or event.platform,
            group_id=str(event.group_id),
            bot_self_id=event.bot_id or "",
            ws_bot_id="",
            created_by=event.user_id,
        )
        return _result(f"✅ 本群已订阅模拟盘「{account.name}」的成交播报。")
    removed = await db.PaperBroadcastRepo.remove(account.id or 0, str(event.group_id))
    if removed <= 0:
        return _result(f"ℹ️ 本群本来就没有订阅「{account.name}」的播报。")
    return _result(f"✅ 本群已退订模拟盘「{account.name}」的成交播报。")


async def _broadcast_list(event: UnifiedEvent) -> Any:
    from SayuStock.stock_papertrade import cross_group

    if not event.group_id:
        return _result("⚠️ 该命令只能在群里使用。")
    rows = await cross_group.query_accounts_by_group(str(event.group_id))
    if not rows:
        return _result("ℹ️ 本群未订阅任何模拟盘的播报。用「模拟盘推送添加 <盘名>」订阅。")
    lines = [f"【本群订阅的模拟盘 · {len(rows)} 个】"]
    lines.extend(f"· {row['account_name']}  [{row['strategy_id']}]" for row in rows)
    return _result("\n".join(lines), {"accounts": rows})


async def _cmd_view(event: UnifiedEvent, argument: str) -> Any:
    account, error = await _account_text("查看", argument)
    if account is None:
        return _result(error)
    return await _account_view(account, event)


async def _cmd_holdings(event: UnifiedEvent, argument: str) -> Any:
    account = await _resolve(argument or "", fallback_default=True)
    if account is None:
        from SayuStock.stock_papertrade.account_scope import not_opened_message

        return _result(not_opened_message(name=argument))
    return await _holdings_view(account, event)


async def _cmd_pnl(argument: str) -> Any:
    import datetime as dt

    from SayuStock.stock_papertrade import db, account_scope

    name, period = account_scope.split_name_and_period(argument)
    if not name:
        return _result("⚠️ 用法：模拟盘收益 <盘名> [周期]\n周期 = 日/周/月/季/年/总")
    period = period or "总"
    year_to_date = period in {"ytd", "今年", "本年"}
    if year_to_date:
        period = "年"
    if period not in DEFAULT_PERIOD_LABELS:
        return _result(f"⚠️ 周期须为 日/周/月/季/年/总（收到 {period!r}）")
    account = await _resolve(name, fallback_default=False)
    if account is None:
        return _result(account_scope.not_opened_message(name=name))
    days = {"今日": 1, "本周": 7, "本月": 30, "本季": 90, "本年": 365, "总": None}[DEFAULT_PERIOD_LABELS[period]]
    since = None if days is None else dt.datetime.now() - dt.timedelta(days=days)
    if year_to_date:
        since = dt.datetime(dt.date.today().year, 1, 1)
    aggregate = await db.PaperTradeRepo.aggregate_pnl(account.id or 0, since=since)
    return _result(
        f"📊 模拟盘「{account.name}」· {DEFAULT_PERIOD_LABELS[period]}盈亏\n"
        f"已实现盈亏: {aggregate['total_pnl']:+,.2f}\n"
        f"总成交额: {aggregate['total_amount']:,.0f}\n"
        f"总手续费: {aggregate['total_fee']:,.2f}\n"
        f"交易笔数: {aggregate['trade_count']}",
        {"period": DEFAULT_PERIOD_LABELS[period], **aggregate},
    )


async def _cmd_records(argument: str) -> Any:
    from SayuStock.stock_papertrade import db

    account, error = await _account_text("记录", argument)
    if account is None:
        return _result(error)
    rows = await db.PaperTradeRepo.list_by_account(account.id or 0, limit=20)
    if not rows:
        return _result(f"ℹ️ 模拟盘「{account.name}」暂无交易记录")
    lines = [f"【模拟盘「{account.name}」· 最近 20 笔交易】"]
    for item in rows:
        at = item.executed_at.strftime("%m-%d %H:%M") if item.executed_at else "?"
        pnl = f" 盈{item.realized_pnl:+.0f}" if item.side == "sell" and item.realized_pnl else ""
        lines.append(
            f"[{at}] {'买' if item.side == 'buy' else '卖'} "
            f"{item.stock_name or item.stock_code} {item.qty}@{item.price:.2f} 费{item.fee:.1f}{pnl}"
        )
    structured = [
        {
            "side": item.side,
            "code": item.stock_code,
            "name": item.stock_name,
            "qty": item.qty,
            "price": item.price,
            "fee": item.fee,
            "realized_pnl": item.realized_pnl,
            "executed_at": item.executed_at.isoformat() if item.executed_at else None,
        }
        for item in rows
    ]
    return _result("\n".join(lines), {"trades": structured})


async def _cmd_query(argument: str) -> Any:
    from SayuStock.stock_papertrade import db, cross_group, account_scope

    raw = account_scope.normalize_account_name(argument)
    if not raw:
        return _result("⚠️ 用法：模拟盘查询 <盘名>")
    account = await _resolve(raw, fallback_default=False)
    if account is None and raw.isdigit():
        account = await db.PaperAccountRepo.get_by_id(int(raw))
        if account is None:
            matches = await db.PaperAccountRepo.list_by_origin_group(raw)
            if len(matches) > 1:
                return _result(
                    f"ℹ️ 原群 {raw} 下有多个盘：" + "、".join(item.name for item in matches) + "。请用盘名再查。"
                )
            if matches:
                account = matches[0]
    if account is None:
        return _result(account_scope.not_opened_message(name=raw))
    positions = await cross_group.query_positions(account.id or 0)
    snapshot = await cross_group.query_latest_snapshot(account.id or 0)
    lines = [
        f"【模拟盘「{account.name}」】",
        f"策略: {account.strategy_id}  模式: {account.mode}  状态: {'🟢' if account.enabled else '🔴'}",
        f"现金: {account.cash:,.0f}  初始: {account.initial_cash:,.0f}",
    ]
    if snapshot:
        lines.append(
            f"总资产: {snapshot['total_equity']:,.0f}  盈亏: "
            f"{snapshot['total_pnl']:+,.0f} ({snapshot['total_pnl_pct']:+.2f}%)"
        )
    if positions:
        lines.append(f"持仓: {len(positions)} 只")
        lines.extend(
            f"  - {item['stock_name'] or item['stock_code']} ×{item['qty']}@{item['avg_cost']:.2f}"
            for item in positions[:5]
        )
    return _result(
        "\n".join(lines), {"account": cross_group._acc_to_dict(account), "snapshot": snapshot, "positions": positions}
    )


async def _cmd_snapshot(argument: str) -> Any:
    from SayuStock.stock_papertrade import account_scope

    from .paperwork_lifecycle import run_snapshot

    if argument.strip():
        account = await _resolve(account_scope.normalize_account_name(argument), fallback_default=False)
        if account is None:
            return _result(account_scope.not_opened_message(name=argument))
        result = await run_snapshot(account.id or 0)
    else:
        result = await run_snapshot()
    if not result.get("written"):
        return _result("ℹ️ 没有启用中的模拟盘可写快照。", result)
    lines = [f"✅ 已写 {result['written']} 个盘的 {result['date']} 净值快照"]
    for item in result.get("accounts") or []:
        lines.append(
            f"· {item['name']}: 总资产 {item['total_equity']:,.0f} 现金 "
            f"{item['total_equity'] - item['position_value']:,.0f} 持仓市值 {item['position_value']:,.0f} "
            f"收益率 {item['total_pnl_pct']:+.2f}%"
        )
    if result.get("warnings"):
        lines.append("⚠️ " + "；".join(result["warnings"][:8]))
    return _result("\n".join(lines), result)


async def _cmd_decision(argument: str) -> Any:
    from SayuStock.stock_papertrade import account_scope

    from .paperwork_lifecycle import run_decision

    if argument.strip():
        account = await _resolve(account_scope.normalize_account_name(argument), fallback_default=False)
        if account is None:
            return _result(account_scope.not_opened_message(name=argument))
        result = await run_decision(account.id or 0)
    else:
        result = await run_decision()
    if result.get("status") != "ok":
        return _result(f"⚠️ {result.get('message') or result.get('error') or '本轮决策失败'}", result)
    accounts = result.get("accounts") or []
    if not accounts:
        return _result("ℹ️ 没有启用中的模拟盘可决策。", result)
    lines = ["【模拟盘决策 · 宿主 AI】"]
    for item in accounts:
        if item.get("status") != "ok":
            lines.append(f"· {item.get('account')}: ⚠️ {item.get('error')}")
            continue
        actions = item.get("actions") or []
        lines.append(f"· {item.get('account')}: {'无动作' if not actions else ''}")
        for action in actions:
            lines.append(
                f"  - {action.get('code')} {action.get('side')} {action.get('status')} "
                f"{action.get('reason') or action.get('qty') or ''}"
            )
        if item.get("quote_failures"):
            lines.append("  ⚠️ 行情失败: " + "、".join(item["quote_failures"]))
    return _result("\n".join(lines), result)


async def _cmd_heal_ledger() -> Any:
    from SayuStock.utils.database.papertrade_migration import heal_orphan_ledger

    summary = await heal_orphan_ledger()
    lines = ["🧾 模拟盘对账修复"]
    if not any(summary.get(key) for key in ("orphans", "cash_restated", "broadcast_filled", "positions_plan_healed")):
        lines.append("账本已自洽：没有无流水持仓，现金与流水一致，播报路由无需回填。")
        return _result("\n".join(lines), summary)
    if summary.get("orphans"):
        lines.append(f"删除幽灵仓 {len(summary['orphans'])} 只")
    if summary.get("cash_restated"):
        lines.append(f"更正卖出现金 {len(summary['cash_restated'])} 个盘")
    if summary.get("positions_plan_healed"):
        lines.append(f"补齐持仓计划 {summary['positions_plan_healed']} 只")
    lines.append(f"播报路由补全 {summary.get('broadcast_filled', 0)} 条")
    return _result("\n".join(lines), summary)


async def _cmd_heal_holiday(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.stock_papertrade.holiday_heal import heal_holiday_trades

    confirmed = argument.strip() == "执行"
    if confirmed and event.platform == "qweb":
        return _result("⚠️ 网页不允许执行假期还原（会改真实账本）；请由七七超级用户在已授权群内操作。")
    summary = await heal_holiday_trades(dry_run=not confirmed)
    tag = "已执行" if confirmed else "预演"
    lines = [f"🧹 模拟盘 · 假期成交还原（{tag}）", f"休市表来源：{summary['source']}"]
    lines.append(f"扫描流水 {summary['scanned_trades']} 笔，时间跨度 {summary['traded_span'] or '(无)'}")
    if summary.get("skipped"):
        lines.append(f"⚠️ 未执行：{summary['skipped']}")
        return _result("\n".join(lines), summary)
    if summary.get("unparsed_trades"):
        lines.append(f"⚠️ {summary['unparsed_trades']} 笔流水时间戳无法解析，本次不动账。")
        return _result("\n".join(lines), summary)
    if summary.get("dirty_days"):
        lines.append(f"脏数据日期 {summary['dirty_days'][0]} ~ {summary['dirty_days'][-1]}")
        lines.append(f"{'将删除' if not confirmed else '已删除'}流水 {summary['deleted_trades']} 笔")
    else:
        if summary.get("calendar_available"):
            lines.append("没有落在已知休市日期的成交。")
        else:
            lines.append("休市表不可用，未判断流水日期；本次仅核对持仓。")
    lines.append(
        f"持仓修复 {len(summary.get('positions') or [])} 处 / 现金修复 {len(summary.get('cash_fixed') or [])} 个盘"
    )
    if not confirmed:
        lines.append("以上为预演。确认后发「模拟盘假期还原 执行」。")
    return _result("\n".join(lines), summary)


async def _cmd_holiday_diagnose() -> Any:
    from SayuStock.stock_papertrade.holiday_heal import diagnose_holiday_heal

    return _result(await diagnose_holiday_heal())


async def _cmd_clear(event: UnifiedEvent, argument: str) -> Any:
    from SayuStock.stock_papertrade import db, account_scope

    raw = account_scope.normalize_account_name(argument)
    if not raw:
        return _result("⚠️ 用法：模拟盘清盘 <盘名>（禁止无参落到默认盘）")
    account = await _resolve(raw, fallback_default=False)
    if account is None:
        return _result(account_scope.not_opened_message(name=raw))
    if event.platform == "qweb":
        return _result("⚠️ 网页不允许清盘（会删除真实账本）；请由七七超级用户在已授权群内操作。")
    deleted = await db.PaperAccountRepo.reset_account(account.id or 0)
    return _result(
        f"✅ 已清盘「{account.name}」\nDB 合计删除 {sum(deleted.values())} 行\n"
        "账户已不存在；下次「模拟盘初始化」会新建干净账户。",
        {"account": account.name, "deleted": deleted},
    )


async def _cmd_macro_list() -> Any:
    from SayuStock.stock_macro.repo import MacroEventRepo
    from SayuStock.stock_macro.presentation import format_macro_events_text
    from SayuStock.utils.database.macro_models import MACRO_STATUS_OPEN, MACRO_STATUS_SETTLED

    open_rows = await MacroEventRepo.list_by_status(MACRO_STATUS_OPEN, limit=10)
    settled = await MacroEventRepo.list_by_status(MACRO_STATUS_SETTLED, limit=3)
    text = format_macro_events_text(open_rows)
    if settled:
        text += "\n\n最近已定论：" + "、".join(item.title for item in settled)
    text += "\n\n管理员可发「宏观事件刷新」用宿主 AI 立即复核。"
    structured = [
        {
            "slug": item.slug,
            "title": item.title,
            "status": item.status,
            "severity": item.severity,
            "direction": item.direction,
            "result": item.result,
            "stance": item.stance,
        }
        for item in open_rows
    ]
    return _result(text, {"events": structured, "settled": [item.title for item in settled]})


async def _cmd_macro_refresh() -> Any:
    from .paperwork_lifecycle import host_ai_available, run_macro_refresh

    if not host_ai_available():
        return _result("⚠️ 宿主未提供七七 AI 服务，无法复核宏观事件；事件表保持原样。")
    result = await run_macro_refresh(force=True)
    if result.get("status") != "ok":
        return _result(f"⚠️ 宏观复核失败：{result.get('error') or result.get('message')}", result)
    return _result(
        f"✅ 宏观复核完成：写入 {len(result.get('written') or [])} 条"
        f"{'，跳过 ' + '、'.join(result['skipped']) if result.get('skipped') else ''}",
        result,
    )


async def _exercise_ledger(event: UnifiedEvent):
    """Exercise the native ledger in an uncommitted account scoped to this request."""
    from uuid import uuid4

    from SayuStock.stock_papertrade import db
    from gsuid_core.utils.database.base_models import rollback_session

    from .paperwork_lifecycle import run_decision, run_snapshot

    name = "验收-" + uuid4().hex[:12]
    async with rollback_session():
        account = await db.PaperAccountRepo.create(name, "", event.bot_id, initialized_by=event.user_id)
        decision = await run_decision(account.id)
        # Replay two ledger legs at fixed sample prices. This exercises fees and
        # cash/position writes even outside trading hours, inside this transaction only.
        from SayuStock.stock_papertrade.matcher import match_order

        sample_code = "000001"
        replay = []
        for side, qty, price in (("buy", 200, 10.0), ("sell", 100, 11.0)):
            current = await db.PaperAccountRepo.get_by_id(account.id)
            position = await db.PaperPositionRepo.get(account.id, sample_code)
            held = int(position.qty) if position else 0
            match = match_order(side, sample_code, qty, price, current.cash, held)
            if not match.ok:
                raise ValueError(match.reason)
            trade, balance = await db.PaperTradeRepo.append_with_cash_update(
                account.id,
                stock_code=sample_code,
                stock_name="演练样本",
                secid="0." + sample_code,
                side=side,
                qty=match.actual_qty,
                price=match.price,
                amount=match.amount,
                fee=match.fee_total,
                realized_pnl=(price - position.avg_cost) * match.actual_qty - match.fee_total if side == "sell" else 0,
                reason="回滚演练固定行情",
            )
            replay.append({"side": side, "id": trade.id, "fee": match.fee_total, "position_qty": balance})
        snapshot = await run_snapshot(account.id)
        trades = await db.PaperTradeRepo.list_by_account(account.id, limit=10)
        positions = await db.PaperPositionRepo.list_by_account(account.id)
    passed = await db.PaperAccountRepo.get_by_name(name) is None
    return _result(
        f"模拟盘演练：决策 {decision['status']}，成交流水 {len(trades)} 笔，持仓 {len(positions)} 只，"
        f"其中固定样本账本回放 2 笔，快照 {snapshot['written']} 条，事务回滚{'成功' if passed else '失败'}。",
        {"decision": decision, "snapshot": snapshot, "ledger_replay": replay, "rolled_back": passed},
    )


async def _dispatch_impl(command: str, argument: str, event: UnifiedEvent) -> Any:
    if command not in FEATURES:
        return _result("未识别的模拟盘/宏观指令。")
    if command in _MASTER_ONLY and not _is_master(event):
        if event.platform == "qweb":
            return _result("⚠️ 网页不允许修改或删除真实账本，请由七七超级用户在已授权群内操作。")
        return _result("此操作仅限七七超级用户在授权群中执行。")
    if command in _MANAGER_ONLY and not _is_manager(event):
        return _result(_admin_denied(event))
    if command in _GROUP_ONLY and not event.is_group:
        return _result("⚠️ 播报订阅只能在群里操作（私聊没有群号）。")

    if command in {
        "模拟盘决策记录",
        "模拟盘关注列表",
        "模拟盘候选列表",
        "量能扫描",
        "交易日判断",
        "股票财报",
        "股票指标",
    }:
        from SayuStock.stock_papertrade import ai_tools

        if command == "股票财报":
            code, _, report = argument.partition(" ")
            text = await ai_tools.stock_financials(None, code, report.strip() or "main")
        elif command == "交易日判断":
            from SayuStock.stock_papertrade.trading_calendar import ensure_intraday_probe

            await ensure_intraday_probe()
            text = await ai_tools.stock_is_trading_day(None)
        else:
            readers = {
                "模拟盘决策记录": ai_tools.papertrade_decision_list,
                "模拟盘关注列表": ai_tools.papertrade_watchlist_list,
                "模拟盘候选列表": ai_tools.papertrade_agent_pool_list,
                "量能扫描": ai_tools.papertrade_volume_scan,
                "股票指标": ai_tools.stock_indicators,
            }
            text = await readers[command](None, argument.strip())
        try:
            rows = json.loads(text)
        except ValueError:
            return _result(text)
        return _result(text, {"data": rows})
    if command == "模拟盘模拟测试":
        return await _exercise_ledger(event)
    if command == "模拟盘初始化":
        return await _cmd_init(event, argument)
    if command == "模拟盘创建":
        return await _cmd_create(event, argument)
    if command == "模拟盘列表":
        return await _cmd_list()
    if command == "模拟盘改名":
        return await _cmd_rename(event, argument)
    if command == "模拟盘删除":
        return await _cmd_delete(argument)
    if command == "模拟盘停用":
        return await _cmd_toggle(argument, enable=False)
    if command == "模拟盘启用":
        return await _cmd_toggle(argument, enable=True)
    if command == "模拟盘策略列表":
        return await _cmd_strategy_list()
    if command == "模拟盘策略切换":
        return await _cmd_strategy_switch(argument)
    if command == "模拟盘推送添加":
        return await _subscribe(event, argument, add=True)
    if command == "模拟盘推送删除":
        return await _subscribe(event, argument, add=False)
    if command == "模拟盘推送列表":
        return await _broadcast_list(event)
    if command == "模拟盘查看":
        return await _cmd_view(event, argument)
    if command == "模拟盘自选":
        return await _cmd_holdings(event, argument)
    if command == "模拟盘收益":
        return await _cmd_pnl(argument)
    if command == "模拟盘记录":
        return await _cmd_records(argument)
    if command == "模拟盘排行":
        return await _leaderboard(event)
    if command == "模拟盘查询":
        return await _cmd_query(argument)
    if command == "模拟盘快照":
        return await _cmd_snapshot(argument)
    if command == "模拟盘决策":
        return await _cmd_decision(argument)
    if command == "模拟盘对账":
        return await _cmd_heal_ledger()
    if command == "模拟盘假期还原":
        return await _cmd_heal_holiday(event, argument)
    if command == "模拟盘假期诊断":
        return await _cmd_holiday_diagnose()
    if command == "模拟盘清盘":
        return await _cmd_clear(event, argument)
    if command == "宏观待复核":
        from SayuStock.stock_macro.ai_tools import macro_event_refresh_due

        data = json.loads(await macro_event_refresh_due(None))
        text = "\n".join(item["title"] + " · " + "、".join(item["suggested_queries"]) for item in data["due"])
        return _result(text or "暂无到期宏观事件。", data)
    if command == "宏观事件":
        return await _cmd_macro_list()
    if command == "宏观事件刷新":
        return await _cmd_macro_refresh()
    return _result("未实现的指令。")


async def on_startup() -> None:
    """Host hook: start the lifecycle loop beside the other plugin services.

    Called by the plugin start-up hook (owned by the main controller).  Starting
    it here is idempotent, and the loop only acts on trading days/slots.
    """
    from .paperwork_lifecycle import start_lifecycle, host_ai_available

    if not host_ai_available():
        logger.warning("[SayuStock][PaperTrade] 未找到七七 AI 服务，决策/宏观复核将不可用（账本仍可查）")
    start_lifecycle()


async def on_shutdown() -> None:
    """Host hook: stop the lifecycle loop before the process exits."""
    from .paperwork_lifecycle import stop_lifecycle

    await stop_lifecycle()


async def dispatch(command: str, argument: str, event: UnifiedEvent) -> Any:
    """Execute one paperwork/macro feature for a trusted event.

    Dependency/DB failures surface as an explicit error instead of a fabricated
    result: the plugin never invents balances, trades or events.
    """
    if not event.user_id:
        return _result("未取得可信身份，无法执行模拟盘操作。")
    if len(argument) > 2000:
        return _result("参数过长，请缩小输入。")
    try:
        return await _dispatch_impl(command, argument, event)
    except ImportError as error:
        logger.warning("[SayuStock][PaperTrade] 依赖缺失: {}", error)
        return _result(f"⚠️ 模拟盘数据层不可用：缺少依赖 {error.name or error}，账本未改动。")
    except RuntimeError as error:
        logger.warning("[SayuStock][PaperTrade] 存储不可用: {}", error)
        return _result(f"⚠️ 模拟盘数据层不可用：{error}，账本未改动。")
    except Exception as error:
        # 边界层：DB/行情等不可用必须是真实错误，不得伪造余额、成交或事件。
        logger.warning("[SayuStock][PaperTrade] {} 执行失败 ({})", command, type(error).__name__)
        return _result(
            f"⚠️ {command}执行失败（{type(error).__name__}）。",
            {"error": type(error).__name__},
        )


__all__ = ["FEATURES", "dispatch", "on_shutdown", "on_startup"]
