"""Deterministic papertrade lifecycle: snapshots, decisions and macro refresh.

The upstream plugin drives this through GsCore Kanban agents.  This branch has
no Kanban; the same jobs are scheduled in-process and the *only* model call goes
through the Qiqi host AI service (``src.utils.ai_tools_fx``) that the main
controller already configures and bills.  Nothing here reads model settings,
connection strings or user identity from the model output.

Safety rules encoded here:

* every write is scoped to an ``account_id`` loaded from the database, and runs
  inside ``account_scope.grant_write(account_id)``; the model never names the
  account;
* a missing host AI, a malformed model answer or a missing quote is reported as
  a real error and never replaced by fabricated prices or trades;
* decisions pass the same ``strategy.validate_entry`` gates and the same
  ``TradeExecutor`` matching/fee/T+1 path as the agent tools.
"""

from __future__ import annotations

import json
import asyncio
from typing import Any, Callable, Awaitable
from datetime import date, datetime
from zoneinfo import ZoneInfo
from dataclasses import field, dataclass

from nonebot.log import logger

from ..compat import legacy, bootstrap

bootstrap.install()
legacy.install()

CST = ZoneInfo("Asia/Shanghai")
DECISION_MINUTES = (0, 30)
SNAPSHOT_TIME = (15, 5)
MACRO_TIMES = ((8, 40), (12, 40), (20, 40))
TICK_SECONDS = 20.0
MAX_DECISION_CANDIDATES = 6

HostAI = Callable[[str, str], Awaitable[str]]
_host_ai: HostAI | None = None


def set_host_ai(caller: HostAI | None) -> None:
    """Install the host model caller (main controller owns model selection)."""
    global _host_ai
    _host_ai = caller


async def _default_host_ai(prompt: str, system_prompt: str) -> str:
    """Use Qiqi's existing AI client; never configure a second provider here."""
    try:
        from src.utils.ai_tools_fx import get_ai_client
    except ImportError:
        raise RuntimeError("宿主未提供七七 AI 服务（src.utils.ai_tools_fx），无法调用模型") from None
    client = get_ai_client()
    return await client.generate_text(prompt, system_prompt=system_prompt)


async def host_ai(prompt: str, system_prompt: str) -> str:
    caller = _host_ai or _default_host_ai
    return await caller(prompt, system_prompt)


def host_ai_available() -> bool:
    if _host_ai is not None:
        return True
    try:
        from src.utils.ai_tools_fx import get_ai_client  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass(slots=True)
class LifecycleState:
    started: bool = False
    last_tick_at: datetime | None = None
    last_snapshot_date: str = ""
    last_decision_slot: str = ""
    last_macro_slot: str = ""
    last_maintenance_slot: str = ""
    last_error: str = ""
    last_snapshot_result: dict[str, Any] = field(default_factory=dict)
    last_decision_result: dict[str, Any] = field(default_factory=dict)
    last_macro_result: dict[str, Any] = field(default_factory=dict)
    runs: int = 0


_state = LifecycleState()
_task: asyncio.Task[None] | None = None
_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(CST)


def lifecycle_status() -> dict[str, Any]:
    return {
        "started": _state.started,
        "host_ai": host_ai_available(),
        "last_tick_at": _state.last_tick_at.isoformat(timespec="seconds") if _state.last_tick_at else None,
        "last_snapshot_date": _state.last_snapshot_date,
        "last_decision_slot": _state.last_decision_slot,
        "last_macro_slot": _state.last_macro_slot,
        "last_error": _state.last_error,
        "last_snapshot_result": _state.last_snapshot_result,
        "last_decision_result": _state.last_decision_result,
        "last_macro_result": _state.last_macro_result,
        "runs": _state.runs,
    }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _code(codes: list[str] | tuple[str, ...], limit: int) -> list[str]:
    seen: list[str] = []
    for item in codes:
        key = item.strip()
        if key and key not in seen:
            seen.append(key)
        if len(seen) >= limit:
            break
    return seen


async def _quote_map(codes: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Batch quotes through the market port; failures are reported, not faked."""
    from SayuStock.utils.market import get_market, is_market_error

    secids = [f"1.{code}" if code.startswith("6") else f"0.{code}" for code in codes]
    results = await get_market().quotes(secids)
    prices: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for code, result in zip(codes, results, strict=True):
        if is_market_error(result) or result.price <= 0:
            failures.append(f"{code} 行情不可用")
            continue
        prices[code] = {
            "price": float(result.price),
            "change_pct": result.change_pct,
            "name": result.symbol.name,
            "provider": result.provider,
        }
    return prices, failures


def _position_value(positions: list[Any], prices: dict[str, dict[str, Any]]) -> tuple[float, list[str]]:
    value = 0.0
    warnings: list[str] = []
    for position in positions:
        code = str(position.stock_code)
        quote = prices.get(code)
        if quote is not None:
            value += quote["price"] * int(position.qty)
            continue
        fallback = position.last_quote_price if position.last_quote_price is not None else position.avg_cost
        value += float(fallback or 0.0) * int(position.qty)
        warnings.append(f"{code} 使用{'上次报价' if position.last_quote_price is not None else '成本价'}")
    return round(value, 2), warnings


# ---------------------------------------------------------------------------
# Snapshot (deterministic, no model)
# ---------------------------------------------------------------------------
async def run_snapshot(account_id: int = 0) -> dict[str, Any]:
    """Write today's equity snapshot for one or all enabled accounts."""
    from SayuStock.stock_papertrade import db

    accounts = (
        await db.PaperAccountRepo.list_enabled()
        if account_id <= 0
        else [await db.PaperAccountRepo.get_by_id(account_id)]
    )
    today = _now().date()
    result: dict[str, Any] = {"date": today.isoformat(), "written": 0, "accounts": [], "warnings": []}
    for account in accounts:
        if account is None or account.id is None:
            continue
        positions = await db.PaperPositionRepo.list_by_account(account.id)
        prices, failures = await _quote_map([str(item.stock_code) for item in positions])
        position_value, warnings = _position_value(positions, prices)
        equity = round(float(account.cash) + position_value, 2)
        initial = float(account.initial_cash or 0.0)
        total_pnl = round(equity - initial, 2)
        total_pct = round(total_pnl / initial * 100.0, 4) if initial else 0.0
        previous = await db.PaperSnapshotRepo.prev_before(account.id, today)
        day_pnl = round(equity - previous.total_equity, 2) if previous is not None else total_pnl
        day_pct = (
            round(day_pnl / previous.total_equity * 100.0, 4)
            if previous is not None and previous.total_equity
            else total_pct
        )
        await db.PaperSnapshotRepo.upsert_for_date(
            account.id,
            today,
            cash=float(account.cash),
            position_value=position_value,
            total_equity=equity,
            day_pnl=day_pnl,
            day_pnl_pct=day_pct,
            total_pnl=total_pnl,
            total_pnl_pct=total_pct,
            group_id=account.group_id,
            bot_id=account.bot_id,
        )
        result["written"] += 1
        result["accounts"].append(
            {
                "name": account.name,
                "account_id": account.id,
                "total_equity": equity,
                "position_value": position_value,
                "day_pnl": day_pnl,
                "total_pnl_pct": total_pct,
                "quotes_failed": failures,
                "price_fallbacks": warnings,
            }
        )
        result["warnings"].extend(f"{account.name}: {item}" for item in failures + warnings)
    _state.last_snapshot_result = result
    return result


# ---------------------------------------------------------------------------
# Decisions (host AI proposes, deterministic gates dispose)
# ---------------------------------------------------------------------------
_DECISION_SYSTEM = (
    "你是模拟盘交易决策器，只能输出 JSON。不要编造行情：只能用给定的行情与指标。"
    "账户、身份由系统提供，禁止在 JSON 中写账户名、用户 ID 或群号。"
)


def _decision_prompt(
    account: Any, positions: list[Any], pool: list[str], quotes: dict[str, dict[str, Any]], macro: list[Any]
) -> str:
    payload = {
        "account": {
            "name": account.name,
            "mode": account.mode,
            "cash": float(account.cash),
            "initial_cash": float(account.initial_cash),
            "strategy_id": account.strategy_id,
        },
        "positions": [
            {
                "code": item.stock_code,
                "name": item.stock_name,
                "qty": int(item.qty),
                "avg_cost": item.avg_cost,
                "quote": quotes.get(item.stock_code),
                "stop": item.plan_stop_price,
                "take": item.plan_take_price,
                "thesis": item.plan_thesis,
            }
            for item in positions
        ],
        "macro": [
            {"title": m.title, "severity": m.severity, "direction": m.direction, "stance": m.stance} for m in macro
        ],
        "candidates": [
            {"code": code, **quotes.get(code, {})}
            for code in pool
            if code not in {str(p.stock_code) for p in positions}
        ],
        "rules": {
            "side": "buy 或 sell 或 hold",
            "buy_plan": "plan_stop_pct(<0) 或 plan_stop_price(>0)，plan_take_pct(>0) 或 plan_take_price(>0)",
            "macro": "macro_regime 取 进攻/中性/防御；macro_note ≥8 字",
            "cash": "买入金额不得超过可用现金，卖出不得超过持仓",
            "revision_type": "hold 且已有持仓时才可提出 backfill/trail_stop/macro_tighten/take_adjust，附理由与 plan",
        },
    }
    return (
        "请根据下列真实持仓、候选与行情，给出本轮最多 3 个动作。"
        "没有把握就返回空 actions。计划字段用于风控硬闸，缺字段会被拒绝。\n"
        '输出格式：{"actions":[{"code":"600519","side":"buy","qty":100,"reason":"...",'
        '"plan":{"plan_stop_pct":-0.05,"plan_take_pct":0.1,"plan_thesis":"..."},'
        '"macro_regime":"中性","macro_note":"..."}],"summary":"..."}\n'
        + json.dumps(payload, ensure_ascii=False, default=str)
    )


def parse_model_object(raw: str) -> dict[str, Any]:
    """Extract one JSON object from a model answer; malformed answers yield ``{}``."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        parsed = json.loads(text)
    except ValueError:
        start = text.find("{")
        if start < 0:
            return {}
        try:
            parsed, _index = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_actions(raw: str) -> list[dict[str, Any]]:
    actions = parse_model_object(raw).get("actions")
    if not isinstance(actions, list):
        return []
    return [item for item in actions if isinstance(item, dict)]


def _valid_action(action: dict[str, Any]) -> bool:
    """Reject model output that names an account/identity or an unknown side."""
    forbidden = {"account", "account_id", "account_name", "user_id", "group_id", "bot_id", "qq"}
    if forbidden & set(action):
        return False
    code = str(action.get("code") or "").strip()
    side = str(action.get("side") or "hold").strip().lower()
    if side not in {"buy", "sell", "hold"}:
        return False
    return side == "hold" or (len(code) == 6 and code.isdigit())


async def _execute_action(account: Any, action: dict[str, Any]) -> dict[str, Any]:
    from SayuStock.stock_papertrade import db
    from SayuStock.stock_papertrade.strategy import normalize_plan_indicators
    from SayuStock.stock_papertrade.trade_executor import get_executor

    code = str(action.get("code") or "").strip()
    side = str(action.get("side") or "hold").strip().lower()
    if side == "hold" and code and action.get("revision_type"):
        from SayuStock.stock_papertrade.ai_tools import papertrade_position_plan_update

        plan = action.get("plan") or {}
        if not isinstance(plan, dict):
            return {"code": code, "side": side, "status": "rejected", "reason": "计划格式无效"}
        fields = {
            k: v for k, v in plan.items() if k in {"plan_stop_price", "plan_stop_pct", "plan_take_price", "plan_thesis"}
        }
        if any(not isinstance(v, (int, float)) for k, v in fields.items() if k != "plan_thesis"):
            return {"code": code, "side": side, "status": "rejected", "reason": "计划点位格式无效"}
        note = await papertrade_position_plan_update(
            None, code, str(action["revision_type"]), str(action.get("reason") or ""), **fields
        )
        return {
            "code": code,
            "side": side,
            "status": "updated" if note.startswith("ok ") else "blocked",
            "reason": note,
        }
    if side == "hold" or not code:
        await db.PaperDecisionRepo.append(
            account.id or 0,
            action="hold",
            stock_code=code or None,
            stock_name=str(action.get("name") or "") or None,
            reason=str(action.get("reason") or ""),
            group_id=account.group_id,
            bot_id=account.bot_id,
        )
        return {"code": code, "side": "hold", "status": "skipped", "reason": str(action.get("reason") or "")}
    fresh = await db.PaperAccountRepo.get_by_id(account.id or 0)
    if fresh is not None:
        account = fresh
    try:
        qty = int(action.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        return {"code": code, "side": side, "status": "rejected", "reason": "数量无效"}
    plan_raw = action.get("plan")
    plan: dict[str, Any] = normalize_plan_indicators(plan_raw if isinstance(plan_raw, dict) else {})
    for key in ("macro_regime", "macro_note"):
        if action.get(key) is not None:
            plan[key] = action[key]
    from SayuStock.stock_papertrade.entry_gate import _gate_entry

    reason = await _gate_entry(account, stock_code=code, indicators=plan, score=0.0, source="trade", side=side)
    if reason:
        await db.PaperDecisionRepo.append(
            account.id or 0,
            action=side,
            stock_code=code,
            stock_name=str(action.get("name") or ""),
            reason=str(action.get("reason") or ""),
            indicators=json.dumps(plan, ensure_ascii=False),
            blocked_by=reason,
            group_id=account.group_id,
            bot_id=account.bot_id,
        )
        return {"code": code, "side": side, "status": "blocked", "reason": reason}

    positions = await db.PaperPositionRepo.list_by_account(account.id or 0)
    held = next((item for item in positions if str(item.stock_code) == code), None)
    executor = get_executor()
    matched = await executor.match(
        side=side,
        stock_code=code,
        qty=qty,
        cash_available=float(account.cash),
        position_qty=int(held.qty) if held is not None else 0,
    )
    if not matched.ok:
        await db.PaperDecisionRepo.append(
            account.id or 0,
            action=side,
            stock_code=code,
            stock_name=str(action.get("name") or ""),
            reason=str(action.get("reason") or ""),
            indicators=json.dumps(plan, ensure_ascii=False),
            blocked_by=matched.reason,
            group_id=account.group_id,
            bot_id=account.bot_id,
        )
        return {"code": code, "side": side, "status": "rejected", "reason": matched.reason}

    recorded = await executor.record_trade(
        account_id=account.id or 0,
        stock_code=code,
        stock_name=str(action.get("name") or code),
        secid=f"1.{code}" if code.startswith("6") else f"0.{code}",
        side=side,
        price=matched.price,
        qty=matched.actual_qty,
        amount=matched.amount,
        fee=matched.fee_total,
        realized_pnl=round((matched.price - held.avg_cost) * matched.actual_qty - matched.fee_total, 2)
        if side == "sell" and held is not None
        else 0.0,
        reason=str(action.get("reason") or ""),
        snapshot=json.dumps(plan, ensure_ascii=False),
        mode=account.mode,
    )
    if not recorded.ok:
        return {"code": code, "side": side, "status": "rejected", "reason": recorded.message}
    await db.PaperDecisionRepo.append(
        account.id or 0,
        action=side,
        stock_code=code,
        stock_name=str(action.get("name") or code),
        reason=str(action.get("reason") or ""),
        indicators=json.dumps(plan, ensure_ascii=False),
        trade_id=recorded.trade_id,
        group_id=account.group_id,
        bot_id=account.bot_id,
    )
    return {
        "code": code,
        "side": side,
        "status": "executed",
        "qty": matched.actual_qty,
        "price": matched.price,
        "fee": matched.fee_total,
        "trade_id": recorded.trade_id,
    }


async def notify_fill(account, fill: dict) -> None:
    from nonebot import get_bots

    from SayuStock.stock_papertrade.db import PaperBroadcastRepo

    from .newswork import _target_access
    from ..platform import platform_id

    side = "买入" if fill["side"] == "buy" else "卖出"
    text = (
        f"【{account.name}】{side} {fill['code']} {fill['qty']} 股，"
        f"成交价 {fill['price']:.2f} 元，费用 {fill['fee']:.2f} 元"
    )
    for target in await PaperBroadcastRepo.list_by_account(account.id):
        bot = get_bots().get(target.bot_self_id or target.bot_id)
        if bot is None:
            continue
        try:
            allowed, _ = await _target_access(bot, target.group_id)
            if not allowed:
                continue
            if platform_id(bot) == "onebot11":
                from nonebot.adapters.onebot.v11 import MessageSegment

                await bot.call_api(
                    "send_group_forward_msg",
                    group_id=int(target.group_id),
                    messages=[MessageSegment.node_custom(bot.self_id, "七七", text)],
                )
            elif platform_id(bot) == "onebot12":
                await bot.send_message(
                    detail_type="group", group_id=target.group_id, message=[{"type": "text", "data": {"text": text}}]
                )
        except Exception as error:
            logger.warning("[SayuStock] 成交 {} 播报失败 ({})", fill["trade_id"], type(error).__name__)


async def run_decision(account_id: int = 0) -> dict[str, Any]:
    """One decision round for an enabled account using the host AI service."""
    if not host_ai_available():
        result = {"status": "unavailable", "message": "宿主未提供七七 AI 服务，本轮不决策、不写账本"}
        _state.last_decision_result = result
        return result
    from SayuStock.stock_papertrade import db, account_scope, candidate_pool

    accounts = (
        await db.PaperAccountRepo.list_enabled()
        if account_id <= 0
        else [await db.PaperAccountRepo.get_by_id(account_id)]
    )
    outcomes: list[dict[str, Any]] = []
    for account in accounts:
        if account is None or account.id is None or int(account.enabled or 0) == 0:
            continue
        positions = await db.PaperPositionRepo.list_by_account(account.id)
        from SayuStock.stock_papertrade.ai_tools import papertrade_candidate_refresh

        with account_scope.grant_write(account.id):
            await papertrade_candidate_refresh(None)
        codes = await candidate_pool.build_candidate_pool(account.id)
        pool = _code(
            [str(item) for item in codes if str(item) not in {p.stock_code for p in positions}], MAX_DECISION_CANDIDATES
        )
        from SayuStock.stock_macro.repo import MacroEventRepo
        from SayuStock.utils.database.macro_models import MACRO_STATUS_OPEN

        quotes, failures = await _quote_map(list(dict.fromkeys([*pool, *(p.stock_code for p in positions)])))
        macro = await MacroEventRepo.list_by_status(MACRO_STATUS_OPEN, limit=8)
        from SayuStock.stock_papertrade import strategies

        strategy, params = strategies.resolve_with_params(
            account.strategy_id, db.parse_strategy_params(account.strategy_params)
        )
        prompt = (
            strategy.prompt_block(params)
            + "\n"
            + _decision_prompt(account, positions, [code for code in pool if code in quotes], quotes, macro)
        )
        try:
            answer = await host_ai(prompt, _DECISION_SYSTEM)
        except Exception as error:
            outcomes.append({"account": account.name, "status": "error", "error": f"{type(error).__name__}: {error}"})
            continue
        parsed = parse_model_object(answer)
        if not isinstance(parsed.get("actions"), list) or not all(isinstance(a, dict) for a in parsed["actions"]):
            outcomes.append({"account": account.name, "status": "error", "error": "宿主模型未返回有效 actions 列表"})
            continue
        actions = parsed["actions"]
        acted: list[dict[str, Any]] = []
        with account_scope.grant_write(account.id):
            for action in actions[:3]:
                if not _valid_action(action):
                    acted.append(
                        {
                            "code": action.get("code"),
                            "side": action.get("side"),
                            "status": "rejected",
                            "reason": "动作字段不合法（禁止模型指定账户/身份）",
                        }
                    )
                    continue
                if action.get("side") == "buy" and (
                    str(action.get("code")) not in pool or str(action.get("code")) not in quotes
                ):
                    acted.append({"status": "rejected", "reason": "未取得候选标的报价"})
                    continue
                result = await _execute_action(account, action)
                acted.append(result)
                if result.get("status") == "executed":
                    await notify_fill(account, result)
        outcomes.append(
            {
                "account": account.name,
                "account_id": account.id,
                "status": "ok",
                "actions": acted,
                "quote_failures": failures,
            }
        )
    failed = [row for row in outcomes if row.get("status") == "error"]
    status = ("error" if len(failed) == len(outcomes) else "partial") if failed else "ok"
    result = {"status": status, "accounts": outcomes}
    if failed:
        result["message"] = "；".join(f"{row['account']}：{row['error']}" for row in failed)
    _state.last_decision_result = result
    return result


# ---------------------------------------------------------------------------
# Macro refresh (host AI extracts events; repo validates)
# ---------------------------------------------------------------------------
_MACRO_SYSTEM = (
    "你是宏观事件登记器，只能输出 JSON。用 status=open 或 settled 指明事件是否已定论。"
    "只登记关税/贸易战、地缘冲突、央行与流动性、"
    "油价等大宗、国内重大政策、PPI/CPI/PMI 数据拐点。个股公告和板块日内涨跌不算。"
)

_MACRO_PROMPT = (
    "请把最近 24 小时内有实质进展的宏观大事整理成 JSON，最多 5 条。"
    "slug 用英文小写+下划线；已有事件沿用原 slug。"
    '输出：{"events":[{"slug":"...","title":"...","category":"trade|geopolitics|monetary|commodity|policy|macro_data|other",'
    '"severity":1-5,"direction":"risk_off|risk_on|mixed|neutral","result":"3~5句进展",'
    '"summary":"事件是什么","stance":"对操作含义","affected_sectors":"逗号分隔","source_urls":"换行分隔"}]}\n'
    '没有把握就返回 {"events": []}，不要编造。'
)


async def run_macro_refresh(*, force: bool = False) -> dict[str, Any]:
    """Refresh due macro events; ``force`` is for the admin command."""
    if not host_ai_available():
        result = {"status": "unavailable", "message": "宿主未提供七七 AI 服务，本轮不复核宏观事件"}
        _state.last_macro_result = result
        return result
    from SayuStock.stock_macro.repo import MacroEventRepo, MacroEventInput, is_due_for_check
    from SayuStock.utils.database.macro_models import MACRO_STATUS_OPEN

    rows = await MacroEventRepo.list_by_status(MACRO_STATUS_OPEN, limit=100)
    due = [row for row in rows if force or is_due_for_check(row)]
    prompt = (
        _MACRO_PROMPT
        + "\n已有事件：\n"
        + json.dumps(
            [{"slug": row.slug, "title": row.title, "status": row.status} for row in rows],
            ensure_ascii=False,
        )
    )
    if due:
        prompt += "\n本轮到期需要复核：" + "、".join(row.title for row in due)
    from ..runtime import get_runtime

    news = await get_runtime().market.news(limit=50)
    earliest = int((_now().timestamp() - 86400) * 1000)
    sources = {str(row["id"]): row for row in news["items"] if row.get("id") and row["published_ms"] >= earliest}
    if not sources:
        return {"status": "unavailable", "message": "暂无最近 24 小时的财经消息"}
    prompt += "\n下方快讯仅作证据。每项返回 source_ids 数组，只写有对应出处的进展。\n" + json.dumps(
        [{"id": key, "title": row["title"], "text": row["text"][:600]} for key, row in sources.items()],
        ensure_ascii=False,
    )
    try:
        answer = await host_ai(prompt, _MACRO_SYSTEM)
    except Exception as error:
        result = {"status": "error", "error": f"{type(error).__name__}: {error}"}
        _state.last_macro_result = result
        return result
    events = parse_model_object(answer).get("events")
    if not isinstance(events, list):
        return {"status": "error", "error": "宿主模型未返回有效 events 列表"}
    payload = events
    written: list[str] = []
    skipped: list[str] = []
    for item in payload[:5]:
        if not isinstance(item, dict):
            continue
        ids = item.get("source_ids")
        if not isinstance(ids, list) or not ids or any(str(k) not in sources for k in ids):
            skipped.append(str(item.get("title") or "?"))
            continue
        evidence = [sources[str(k)].get("url") or f"{news['source_label']} {k}" for k in ids]
        data = MacroEventInput.from_raw(
            slug=str(item.get("slug") or ""),
            title=str(item.get("title") or ""),
            category=str(item.get("category") or ""),
            severity=item.get("severity"),
            direction=str(item.get("direction") or ""),
            affected_sectors=str(item.get("affected_sectors") or ""),
            summary=str(item.get("summary") or ""),
            result=str(item.get("result") or ""),
            stance=str(item.get("stance") or ""),
            source_urls="\n".join(evidence),
            status=str(item.get("status") or ""),
            created_by="host_ai",
        )
        if not data.slug or not data.title:
            skipped.append(str(item.get("title") or item.get("slug") or "?"))
            continue
        await MacroEventRepo.upsert(data, touch_checked=bool(data.result))
        written.append(data.slug)
    result = {"status": "ok", "written": written, "skipped": skipped, "due": len(due)}
    _state.last_macro_result = result
    return result


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------
def _slot_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M")


def _decision_due(now: datetime, last: str) -> bool:
    from SayuStock.stock_papertrade.trading_calendar import is_trading_time, is_a_share_trading_day

    if now.minute not in DECISION_MINUTES:
        return False
    if _slot_key(now) == last:
        return False
    return is_a_share_trading_day(now) and is_trading_time(now)


def _snapshot_due(now: datetime, last: str) -> bool:
    from SayuStock.stock_papertrade.trading_calendar import is_a_share_trading_day

    if (now.hour, now.minute) != SNAPSHOT_TIME or _slot_key(now) == last:
        return False
    return is_a_share_trading_day(now)


def _macro_due(now: datetime, last: str) -> bool:
    return (now.hour, now.minute) in MACRO_TIMES and _slot_key(now) != last


async def _tick() -> None:
    now = _now()
    _state.last_tick_at = now
    slot = _slot_key(now)
    if (now.hour, now.minute) in {(0, 20), (23, 0)} and slot != _state.last_maintenance_slot:
        _state.last_maintenance_slot = slot
        if now.hour == 0:
            from SayuStock.stock_cloudmap.cache_files import prune_market_cache

            await asyncio.to_thread(prune_market_cache)
        else:
            from SayuStock.stock_info.draw_info import _save_preview

            from ..paths import DATA_DIR
            from .charts import rasterize, overview_html
            from ..runtime import get_runtime

            runtime = get_runtime()
            if runtime.render_enabled:
                document = overview_html(await runtime.market.overview(include_flow=True))
                async with runtime.render_lock:
                    image = await rasterize(document, width=1280, scale=runtime.render_scale())
                await asyncio.to_thread(_save_preview, DATA_DIR / "preview_output" / "market_overview.png", image)
    if _macro_due(now, _state.last_macro_slot):
        _state.last_macro_slot = _slot_key(now)
        if host_ai_available():
            await run_macro_refresh()
    if _snapshot_due(now, _state.last_snapshot_date):
        result = await run_snapshot()
        _state.last_snapshot_date = _slot_key(now)
        logger.info("[SayuStock][PaperTrade] 收盘快照: {}", result.get("written"))
    if _decision_due(now, _state.last_decision_slot):
        _state.last_decision_slot = _slot_key(now)
        result = await run_decision()
        logger.info("[SayuStock][PaperTrade] 决策轮次: {}", len(result.get("accounts") or []))


async def _loop() -> None:
    while True:
        async with _lock:
            _state.runs += 1
            try:
                await _tick()
                _state.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as error:
                _state.last_error = f"{type(error).__name__}: {error}"
                logger.warning("[SayuStock][PaperTrade] 生命周期 tick 失败: {}", error)
        await asyncio.sleep(TICK_SECONDS)


def start_lifecycle() -> bool:
    """Start the in-process loop; idempotent. Returns ``True`` when newly started."""
    global _task
    if _task is not None and not _task.done():
        return False
    _task = asyncio.get_running_loop().create_task(_loop(), name="sayustock-papertrade-lifecycle")
    _state.started = True
    logger.info("[SayuStock][PaperTrade] 生命周期已启动（宿主 AI: {}）", host_ai_available())
    return True


async def stop_lifecycle() -> None:
    global _task
    task, _task = _task, None
    _state.started = False
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def snapshot_today() -> date:
    return _now().date()


__all__ = [
    "DECISION_MINUTES",
    "SNAPSHOT_TIME",
    "host_ai_available",
    "lifecycle_status",
    "parse_model_object",
    "run_decision",
    "run_macro_refresh",
    "run_snapshot",
    "set_host_ai",
    "snapshot_today",
    "start_lifecycle",
    "stop_lifecycle",
]
