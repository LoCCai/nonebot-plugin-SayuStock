from typing import Any

from . import db, strategies
from .strategy import MACRO_SEVERE_FLAG_KEY, MACRO_SEVERE_TITLES_KEY, normalize_plan_indicators
from ..utils.database.papertrade_models import SayuPaperAccount


def _strategy_of(account: SayuPaperAccount) -> tuple[strategies.Strategy, dict[str, Any]]:
    """拿账户的策略对象 + 归一化参数。"""
    return strategies.resolve_with_params(
        account.strategy_id,
        db.parse_strategy_params(account.strategy_params),
    )


async def _gate_entry(
    account: SayuPaperAccount,
    *,
    stock_code: str,
    indicators: dict[str, Any],
    score: float,
    source: str,
    side: str,
) -> str:
    """落库硬闸。量能盘的顶底/放量在这里用日 K 函数算，覆盖 Agent 传入值。

    buy 侧再把宏观事件表里「进行中 + risk_off + 高危」的事实注入 indicators
    （``_macro_severe_*`` 内部键），让 ``macro_gate_reason`` 能拒掉与表矛盾的「进攻」。
    """
    from .strategies.base import _is_stop_triggered
    from .strategies.volume_extremum import VolumeExtremumStrategy, load_structure

    strategy, params = _strategy_of(account)
    merged = dict(normalize_plan_indicators(indicators))
    # 止损卖必须先放行：load_structure 失败会短路，把 stop_triggered 旁路锁死
    if str(side).strip().lower() == "sell" and _is_stop_triggered(merged):
        return ""
    if str(side).strip().lower() == "buy":
        from ..stock_macro.repo import MacroEventRepo
        from ..stock_macro.presentation import severe_risk_off_titles

        severe = await MacroEventRepo.list_open_severe()
        merged[MACRO_SEVERE_FLAG_KEY] = bool(severe)
        merged[MACRO_SEVERE_TITLES_KEY] = severe_risk_off_titles(severe)
    if strategy.id == VolumeExtremumStrategy.id:
        measured = await load_structure(stock_code, params, intent=side)
        if isinstance(measured, str):
            return measured
        merged.update(measured.as_indicators())
    return strategy.validate_entry(
        params,
        strategies.GateInput(
            stock_code=stock_code,
            indicators=merged,
            score=score,
            source=source,
            side=side,
        ),
    )
