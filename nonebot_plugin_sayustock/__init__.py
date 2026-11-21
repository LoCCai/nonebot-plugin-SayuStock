"""SayuStock · QWeb/NoneBot2 branch entry point.

OneBot v11 / OneBot v12 / QQ official share one set of commands.  The plugin
exposes stock data/analysis tools to the host MoEllmChats runtime and a
host-authenticated QWeb page under ``/qweb/sayustock``.  It does not own any model,
API key, provider or chat route.
"""

from __future__ import annotations

from typing import Any

from nonebot import require, get_driver, on_command
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Bot, Event

from .menu import USAGE, MENU_DATA, PLUGIN_VERSION, PLUGIN_HOMEPAGE, PLUGIN_DESCRIPTION, menu_extra
from .config import PluginConfig, get_value
from .runtime import get_runtime
from .commands import (
    CommandResult,
    deliver,
    execute,
    my_watch,
    add_watch,
    clear_watch,
    help_result,
    news_result,
    rank_result,
    remove_watch,
    stock_result,
    report_result,
    screen_result,
    sector_result,
    compare_result,
    overview_result,
    financial_result,
    technical_result,
    valuation_result,
)
from .platform import from_event

# Host QWeb chat wiring is owned by the main controller; this flag advertises
# that the stock tool surface is available for that wiring.
QIQI_CHAT_TOOLS = True

MOELLM_AVAILABLE = False
try:
    import importlib.util

    if importlib.util.find_spec("nonebot_plugin_moellmchats") is not None:
        require("nonebot_plugin_moellmchats")
        MOELLM_AVAILABLE = True
    else:
        logger.debug("[SayuStock] 未安装 MoEllmChats，跳过工具桥接")
except Exception:
    logger.debug("[SayuStock] MoEllmChats 未加载，工具桥接将在启动时重试")

HTMLRENDER_AVAILABLE = False
try:
    import importlib.util

    if importlib.util.find_spec("nonebot_plugin_htmlrender") is not None:
        require("nonebot_plugin_htmlrender")
        HTMLRENDER_AVAILABLE = True
    else:
        logger.warning("[SayuStock] 未安装 nonebot-plugin-htmlrender，图片将降级为 HTML 制品")
except Exception:
    logger.warning("[SayuStock] nonebot-plugin-htmlrender 加载失败，图片将降级为 HTML 制品")

__plugin_meta__ = PluginMetadata(
    name="SayuStock 股票",
    description=PLUGIN_DESCRIPTION,
    usage=USAGE,
    type="application",
    homepage=PLUGIN_HOMEPAGE,
    config=PluginConfig,
    supported_adapters={"~onebot.v11", "~onebot.v12", "~qq"},
    extra=menu_extra(),
)

__version__ = PLUGIN_VERSION

if MOELLM_AVAILABLE:
    try:
        from .ai import moellm_bridge as _moellm_bridge

        _moellm_bridge.register()
    except Exception as _error:
        logger.debug("[SayuStock] 加载期 MoEllmChats 工具注册失败: {}", _error)


def _arg_text(args: Any) -> str:
    return args.extract_plain_text().strip() if hasattr(args, "extract_plain_text") else str(args).strip()


async def _deliver(bot: Bot, event: Event, result: CommandResult) -> None:
    await deliver(from_event(bot, event), result)


async def _guard(bot: Bot, event: Event, awaitable: Any) -> None:
    try:
        result = await awaitable
    except Exception as error:
        logger.exception("[SayuStock] 命令执行失败: {}", error)
        ue = from_event(bot, event)
        await deliver(ue, CommandResult(f"股票查询失败：{error}"))
        return
    await _deliver(bot, event, result)


# ---------------------------------------------------------------------------
# Prefix commands (COMMAND_START aware)
# ---------------------------------------------------------------------------
help_matcher = on_command(
    "股票帮助",
    aliases={"股票菜单"},
    priority=5,
    block=True,
)


@help_matcher.handle()
async def _help(bot: Bot, event: Event) -> None:
    await _guard(bot, event, help_result())


overview_matcher = on_command("大盘概览", aliases={"大盘", "市场概览", "股票大盘"}, priority=5, block=True)


@overview_matcher.handle()
async def _overview(bot: Bot, event: Event) -> None:
    await _guard(bot, event, overview_result(owner=from_event(bot, event).user_id))


stock_matcher = on_command("个股", aliases={"行情", "查行情"}, priority=5, block=True)


@stock_matcher.handle()
async def _stock(bot: Bot, event: Event, args: Any = CommandArg()) -> None:
    await _guard(bot, event, stock_result(_arg_text(args), owner=from_event(bot, event).user_id))


compare_matcher = on_command("对比个股", aliases={"个股对比", "走势对比"}, priority=5, block=True)


@compare_matcher.handle()
async def _compare(bot: Bot, event: Event, args: Any = CommandArg()) -> None:
    await _guard(bot, event, compare_result(_arg_text(args), owner=from_event(bot, event).user_id))


sector_matcher = on_command("行业板块", aliases={"板块", "概念板块", "板块排行"}, priority=5, block=True)


@sector_matcher.handle()
async def _sector(bot: Bot, event: Event) -> None:
    kind = "concept" if "概念" in event.get_plaintext() else "industry"
    await _guard(bot, event, sector_result(kind))


technical_matcher = on_command("技术分析", aliases={"股票分析"}, priority=5, block=True)


@technical_matcher.handle()
async def _technical(bot: Bot, event: Event, args: Any = CommandArg()) -> None:
    await _guard(bot, event, technical_result(_arg_text(args)))


screen_matcher = on_command("自动选股", aliases={"选股", "条件选股"}, priority=5, block=True)


@screen_matcher.handle()
async def _screen(bot: Bot, event: Event, args: Any = CommandArg()) -> None:
    await _guard(bot, event, screen_result(_arg_text(args)))


add_matcher = on_command("添加自选", aliases={"添加个股", "加入自选", "添加股票"}, priority=5, block=True)


@add_matcher.handle()
async def _add(bot: Bot, event: Event, args: Any = CommandArg()) -> None:
    await _guard(bot, event, add_watch(from_event(bot, event), _arg_text(args)))


remove_matcher = on_command("删除自选", aliases={"删除个股", "移除自选", "删除股票"}, priority=5, block=True)


@remove_matcher.handle()
async def _remove(bot: Bot, event: Event, args: Any = CommandArg()) -> None:
    await _guard(bot, event, remove_watch(from_event(bot, event), _arg_text(args)))


mine_matcher = on_command("我的自选", aliases={"我的个股"}, priority=5, block=True)


@mine_matcher.handle()
async def _mine(bot: Bot, event: Event) -> None:
    await _guard(bot, event, my_watch(from_event(bot, event)))


clear_matcher = on_command("清空自选", priority=5, block=True)


@clear_matcher.handle()
async def _clear(bot: Bot, event: Event) -> None:
    await _guard(bot, event, clear_watch(from_event(bot, event)))


report_matcher = on_command("股票报告", aliases={"研报", "股票研报"}, priority=5, block=True)


@report_matcher.handle()
async def _report(bot: Bot, event: Event, args: Any = CommandArg()) -> None:
    await _guard(bot, event, report_result(from_event(bot, event), _arg_text(args)))


financial_matcher = on_command("财务", aliases={"财务分析", "基本面"}, priority=5, block=True)


@financial_matcher.handle()
async def _financial(bot: Bot, event: Event, args: Any = CommandArg()) -> None:
    await _guard(bot, event, financial_result(_arg_text(args)))


valuation_matcher = on_command("估值", aliases={"估值分析"}, priority=5, block=True)


@valuation_matcher.handle()
async def _valuation(bot: Bot, event: Event, args: Any = CommandArg()) -> None:
    await _guard(bot, event, valuation_result(_arg_text(args)))


news_matcher = on_command("股票资讯", aliases={"财经快讯", "快讯"}, priority=5, block=True)


@news_matcher.handle()
async def _news(bot: Bot, event: Event) -> None:
    await _guard(bot, event, news_result(owner=from_event(bot, event).user_id))


rank_matcher = on_command("资金排行", aliases={"排行榜", "榜单"}, priority=5, block=True)


@rank_matcher.handle()
async def _rank(bot: Bot, event: Event) -> None:
    await _guard(bot, event, rank_result("main_inflow"))


dispatcher = on_command("股票", priority=5, block=True)


@dispatcher.handle()
async def _dispatch(bot: Bot, event: Event, args: Any = CommandArg()) -> None:
    sub = _arg_text(args)
    if not sub:
        await _guard(bot, event, help_result())
        return
    await _guard(bot, event, execute(sub, from_event(bot, event)))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
driver = get_driver()


@driver.on_startup
async def _sayustock_startup() -> None:
    runtime = get_runtime()
    await runtime.startup()
    logger.info("[SayuStock] NoneBot2 runtime ready")

    if bool(get_value("web.enabled", True)):
        from .web import mount_webui

        app = getattr(driver, "server_app", None)
        if app is not None:
            if mount_webui(app):
                logger.info("[SayuStock] QWeb 页面已挂载到 /qweb/sayustock/")
            else:
                logger.warning("[SayuStock] 未检测到 QWeb 宿主，网页默认不启用")
        else:
            logger.warning("[SayuStock] 当前 driver 无 server_app，跳过 QWeb 页面挂载")

    try:
        from .ai import moellm_bridge

        if moellm_bridge.register():
            logger.info("[SayuStock] MoEllmChats 工具已注册")
    except Exception as error:
        logger.debug("[SayuStock] MoEllmChats 桥接未启用: {}", error)


@driver.on_shutdown
async def _sayustock_shutdown() -> None:
    try:
        from .ai import moellm_bridge

        moellm_bridge.unregister()
    except Exception:
        pass
    try:
        from .storage import close_postgres_pools

        await close_postgres_pools()
    except Exception as error:
        logger.debug("[SayuStock] 关闭 PostgreSQL 连接池失败: {}", type(error).__name__)


__all__ = ["QIQI_CHAT_TOOLS", "__plugin_meta__", "__version__", "MENU_DATA"]
