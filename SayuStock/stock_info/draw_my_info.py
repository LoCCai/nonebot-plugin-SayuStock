from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..render import RenderError, render_html
from .watch_view import load_watch_view, watch_index_quotes as _watch_index_quotes
from ..utils.utils import convert_list
from ..utils.market import get_market
from ..utils.database.models import SsBind


async def draw_my_stock_img(ev: Event) -> str | bytes:
    saved = await SsBind.get_uid_list_by_game(ev.at if ev.at else ev.user_id, ev.bot_id)
    if not saved:
        return "您还未添加自选呢~请输入 添加自选 查看帮助!"
    market = get_market()
    view = await load_watch_view(convert_list(saved), market, await _watch_index_quotes(market))
    ai_return(view.text)
    try:
        return await render_html(view.html, width=view.width, label="我的自选")
    except RenderError as error:
        logger.warning(f"[SayuStock] 我的自选出图失败: {error}")
        return "自选出图失败"
