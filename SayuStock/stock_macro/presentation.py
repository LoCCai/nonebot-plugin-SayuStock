from datetime import datetime

from .repo import is_due_for_check
from ..utils.database.macro_models import MACRO_STATUS_OPEN, SayuMacroEvent


def format_macro_events_text(rows: list[SayuMacroEvent], now: datetime | None = None) -> str:
    """用户命令 / 播报用的纯文本视图。"""
    moment = now or datetime.now()
    if not rows:
        return "宏观事件表为空。AI 在心跳里发现关税 / 地缘 / 央行 / 油价类大事后会自动登记。"
    lines: list[str] = ["【宏观重大事件】"]
    for r in rows:
        flag = "🟠进行中" if r.status == MACRO_STATUS_OPEN else "⚪已定论"
        stale = "（待复核）" if is_due_for_check(r, moment) else ""
        checked = r.last_checked_at.isoformat(timespec="minutes") if r.last_checked_at else "未复核"
        lines.append(f"{flag} [{r.direction}/sev{r.severity}] {r.title}{stale}")
        if r.result:
            lines.append(f"  进展：{r.result[:160]}")
        if r.stance:
            lines.append(f"  含义：{r.stance[:120]}")
        lines.append(f"  最近复核：{checked}")
    return "\n".join(lines)


def severe_risk_off_titles(rows: list[SayuMacroEvent]) -> str:
    """硬闸提示用：高危 risk_off 事件标题拼成一行。"""
    return "、".join(f"{r.title}(sev{r.severity})" for r in rows)
