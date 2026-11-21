"""PicMenu Next menu projection and shared command metadata."""

from __future__ import annotations

from typing import Any

PLUGIN_NAME = "SayuStock"
PLUGIN_VERSION = "0.9.0-qiqi.1"
PLUGIN_HOMEPAGE = "https://github.com/LoCCai/nonebot-plugin-SayuStock"
PLUGIN_DESCRIPTION = (
    "A股/港股/美股/ETF 行情、K线、分时、板块云图、技术分析、条件选股、自选管理、HTML 图表/研究报告与宿主 QWeb 股票页。"
)

USAGE = """股票帮助 / 股票菜单：查看命令
大盘概览 / 大盘：指数、涨跌家数、成交额、北向与板块强弱
个股 贵州茅台 / 个股 日k 600519 / 个股 五日 510300：分时或K线
对比个股 贵州茅台 五粮液 沪深300：区间走势对比
板块 / 行业板块 / 概念板块：板块涨幅榜
技术分析 600519：多周期技术指标评分
自动选股 市值50-200 PE<30 涨跌幅>2：条件选股
添加自选 600519 / 删除自选 600519 / 我的自选 / 清空自选
股票报告 贵州茅台：生成 HTML 研究报告
网页入口：由 QWeb 宿主挂载 /qweb/sayustock/，登录、模型与计费均使用宿主账户"""

MENU_DATA: list[dict[str, Any]] = [
    {
        "func": "大盘概览",
        "trigger_method": "消息触发",
        "trigger_condition": "大盘概览 / 大盘",
        "brief_des": "核心指数、涨跌家数、成交额、北向资金与板块强弱一屏查看",
        "detail_des": (
            "汇总上证指数、深证成指、创业板指、沪深300 等核心指数，附涨跌分布、"
            "两市成交额、北向净流入和行业/概念涨跌榜。可生成 PNG 或 HTML 图表。"
        ),
        "llm_intents": ["今天大盘怎么样", "市场情绪如何", "涨跌家数", "大盘复盘"],
    },
    {
        "func": "个股行情",
        "trigger_method": "消息触发",
        "trigger_condition": "个股 [周期] [代码/名称]",
        "brief_des": "查询股票/ETF/指数的实时行情、分时图与K线图",
        "detail_des": (
            "不带周期时出当日分时图；支持 五日/5日、5k、15k、30k、60k、日k、周k、"
            "月k、季k、年k。支持 A股、港股、美股、ETF、场外基金、加密货币与 VIX。"
        ),
        "llm_intents": ["看看这只股票", "查一下行情", "分时图", "K线", "走势"],
    },
    {
        "func": "对比个股",
        "trigger_method": "消息触发",
        "trigger_condition": "对比个股 [周期] [标的1] [标的2] ...",
        "brief_des": "2~6 只股票/ETF/指数的区间涨跌幅对比",
        "detail_des": "按同一交易日历对齐收盘价并归一化，输出对比图与区间涨跌幅。",
        "llm_intents": ["对比这两只股票", "谁涨得多", "走势比较"],
    },
    {
        "func": "板块排行",
        "trigger_method": "消息触发",
        "trigger_condition": "板块 / 行业板块 / 概念板块",
        "brief_des": "行业与概念板块涨跌幅、成交额、领涨股排行",
        "detail_des": "查看当日行业/概念板块强弱，定位领涨与领跌方向。",
        "llm_intents": ["今天哪些板块强", "行业涨幅榜", "概念热度"],
    },
    {
        "func": "技术分析",
        "trigger_method": "消息触发",
        "trigger_condition": "技术分析 [周期] [代码/名称]",
        "brief_des": "MA/MACD/RSI/KDJ/BOLL/ATR/CMF 多指标评分与风险信号",
        "detail_des": "输出 -1~+1 技术评分、多空信号、支撑压力与风险提示。",
        "llm_intents": ["技术面怎么样", "能不能买", "指标分析", "支撑压力"],
    },
    {
        "func": "自动选股",
        "trigger_method": "消息触发",
        "trigger_condition": "自动选股 市值50-200 PE<30 涨跌幅>2",
        "brief_des": "按涨跌幅、PE、市值、行业筛选沪深A股",
        "detail_des": "股票池为按市值排序的前 3000 只沪深A股；结果仅供研究。",
        "llm_intents": ["帮我选股", "筛选股票", "低估值的票", "涨幅榜选股"],
    },
    {
        "func": "自选管理",
        "trigger_method": "消息触发",
        "trigger_condition": "添加自选 / 删除自选 / 我的自选 / 清空自选",
        "brief_des": "按用户隔离的自选股增删查",
        "detail_des": "自选按平台、Bot 与用户隔离存储；网页端按宿主账户隔离。",
        "llm_intents": ["加入自选", "删掉自选", "我的自选股", "关注这只"],
    },
    {
        "func": "股票报告",
        "trigger_method": "消息触发",
        "trigger_condition": "股票报告 [代码/名称]",
        "brief_des": "生成可独立打开的 HTML 研究报告",
        "detail_des": "把行情、技术指标与数据表渲染为 HTML 报告，并提供网页链接。",
        "llm_intents": ["写一份研报", "生成分析报告", "复盘报告", "出一页纸"],
    },
    {
        "func": "财务与估值",
        "trigger_method": "消息触发",
        "trigger_condition": "财务 600519 / 估值 pb 600519",
        "brief_des": "财务快照与 PE/PB/股息率历史估值",
        "detail_des": "ROE、营收/净利同比、毛利率、负债率、EPS/BPS 与估值序列描述统计。",
        "llm_intents": ["基本面怎么样", "财报如何", "估值贵不贵", "PE 历史分位"],
    },
    {
        "func": "快讯与榜单",
        "trigger_method": "消息触发",
        "trigger_condition": "股票资讯 / 资金排行",
        "brief_des": "财经快讯与资金流/换手/ROE 榜单",
        "detail_des": "快讯自动换源；榜单只作扫描线索，不构成买卖依据。",
        "llm_intents": ["最新财经新闻", "资金流向", "换手率排行", "ROE 排行"],
    },
    {
        "func": "AI 自定义图表",
        "trigger_method": "AI 工具",
        "trigger_condition": "sayustock_plot_series / sayustock_pie_chart / sayustock_data_table",
        "brief_des": "把任意分析序列、占比或表格渲染为 HTML/PNG",
        "detail_des": (
            "面向宿主网页与 MoEllmChats 的数据可视化工具：折线/柱状/饼图、"
            "描述统计、相关系数矩阵与表格报告，不限于行情数据。"
        ),
        "llm_intents": ["画个图", "做个表格", "分析这组数据", "相关性", "占比图"],
    },
    {
        "func": "QWeb 股票页",
        "trigger_method": "网页触发",
        "trigger_condition": "QWeb 宿主挂载的 /qweb/sayustock/ 页面",
        "brief_des": "在宿主网页查看行情、图表与报告；对话交给宿主 /qweb/chat",
        "detail_des": (
            "页面只负责行情、图表、报告和股票工具，不保存模型密钥；"
            "成员身份与管理权限来自 QWeb 宿主，对话模型和计费由宿主账户承担。"
        ),
        "llm_intents": ["网页里问股票", "网页看行情", "在网页看图表"],
    },
]

MENU_BY_FUNC = {item["func"]: item for item in MENU_DATA}


def menu_extra() -> dict[str, Any]:
    """Payload for ``PluginMetadata.extra`` understood by PicMenu Next."""
    return {
        "author": "LoCCai",
        "version": PLUGIN_VERSION,
        "homepage": PLUGIN_HOMEPAGE,
        "menu_data": MENU_DATA,
        "pmn": {"markdown": False, "inherit_func_template": True},
    }


__all__ = [
    "MENU_BY_FUNC",
    "MENU_DATA",
    "PLUGIN_DESCRIPTION",
    "PLUGIN_HOMEPAGE",
    "PLUGIN_NAME",
    "PLUGIN_VERSION",
    "USAGE",
    "menu_extra",
]
