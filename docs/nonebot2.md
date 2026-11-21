# SayuStock · NoneBot2 分支

> 本分支把 SayuStock 从 GsCore 插件改造成可独立安装的 NoneBot2 插件：
> **OneBot v11 / OneBot v12 / QQ 官方机器人**共用一套命令、PicMenu Next 菜单、
> MoEllmChats ToolSpec 与 `/sayustock` 网页 AI 基座。
>
> 原 GsCore 业务源码保留在 `SayuStock/`，行情、指标与图表仍复用同一份实现，
> 避免形成第二套数据口径。

## 1. 安装

在 NoneBot2 工程中：

```bash
# 推荐：完整安装（含 HTML→PNG 渲染）
pip install "nonebot-plugin-sayustock[render] @ git+https://github.com/LoCCai/nonebot-plugin-SayuStock.git@nonebot2"

# 或把仓库放入 src/plugins/ 后，在 pyproject.toml 中启用
```

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_sayustock"]
adapters = [
  { name = "OneBot V11", module_name = "nonebot.adapters.onebot.v11" },
  { name = "OneBot V12", module_name = "nonebot.adapters.onebot.v12" },
  # QQ 官方机器人（可选）
  { name = "QQ", module_name = "nonebot.adapters.qq" },
]
driver = "~fastapi"
```

首次启动后渲染图片需要：

```bash
playwright install chromium
```

不安装 Playwright 时，插件仍可运行：图表以 HTML 制品保存，在网页中直接查看；
Bot 侧返回 HTML 链接而不是 PNG。

## 2. 配置

配置分三层，优先级从高到低：环境变量 → `data/sayustock/config.json` → 内置默认值。

```env
DRIVER=~fastapi
HOST=0.0.0.0
PORT=8080
COMMAND_START=["/", ""]
SUPERUSERS=["你的QQ号"]

SAYUSTOCK_WEB_ENABLED=true
SAYUSTOCK_WEB_TOKEN=请换成一个随机长令牌
SAYUSTOCK_PUBLIC_BASE_URL=https://bot.example.com

# 网页 AI（OpenAI Chat Completions 兼容）
SAYUSTOCK_LLM_BASE_URL=https://api.openai.com/v1
SAYUSTOCK_LLM_API_KEY=sk-...
SAYUSTOCK_LLM_MODEL=gpt-4o-mini
```

数据目录默认 `./data/sayustock`，可用 `SAYUSTOCK_DATA_DIR` 覆盖。
配置文件首次启动自动生成；网页「接入设置」也可以修改 LLM 配置。

### 关键配置项

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `web.enabled` | `true` | 是否挂载 `/sayustock` 网页 |
| `web.token` | 空 | Bearer 令牌；公网部署必须设置 |
| `web.public_base_url` | 空 | Bot 消息里 HTML 链接的站点前缀 |
| `web.trust_qweb` | `false` | 是否信任 `X-QWeb-Admin: 1` |
| `llm.base_url` / `llm.model` | 空 | 网页 AI 的 OpenAI 兼容端点 |
| `render.enabled` | `true` | 是否尝试 HTML→PNG |
| `render.scale` | `1.25` | 图片像素倍率 |
| `market.provider_chain` | 空 | 行情源链；空则用原插件默认（东财→腾讯→新浪） |

## 3. 命令

| 命令 | 说明 |
| --- | --- |
| `股票帮助` / `大盘概览` / `大盘` | 帮助、市场总览 |
| `个股 600519` / `个股 日k 贵州茅台` / `个股 五日 510300` | 分时 / K线 |
| `对比个股 贵州茅台 五粮液 沪深300` | 区间走势对比 |
| `行业板块` / `概念板块` / `板块` | 板块涨幅榜 |
| `技术分析 600519` / `技术分析 周k 贵州茅台` | 多指标评分 |
| `自动选股 市值50-200 PE<30 涨跌幅>2` | 条件选股 |
| `财务 600519` / `估值 pb 600519` / `资金排行` / `股票资讯` | 财务、估值、榜单与快讯 |
| `添加自选 600519` / `删除自选 600519` / `我的自选` / `清空自选` | 自选管理 |
| `股票报告 600519` | 生成 HTML 研究报告 |

默认 `COMMAND_START=["/", ""]` 时，带前缀与不带前缀都可用。也支持
`/股票 大盘概览` 这种二级分发。

## 4. PicMenu Next

插件 `PluginMetadata.extra.menu_data` 已内置 12 个三级菜单项（大盘、个股、对比、
板块、技术分析、选股、自选、报告、财务估值、快讯榜单、AI 自定义图表、网页 AI），并带 `llm_intents` 精确别名。
PicMenu Next 加载后会自动收录；`PMN_ONLY_SUPERUSER_SEE_HIDDEN` 等配置照常生效。

无需在本插件里 `require("nonebot_plugin_picmenu_next")`，菜单是元数据投影，
PicMenu 未安装时不会影响插件运行。

## 5. MoEllmChats

检测到 `nonebot_plugin_moellmchats` 时，插件加载阶段即自动注册以下强类型 ToolSpec
（启动钩子再做一次幂等重试，避免依赖加载顺序造成目录缺失）：

- 只读：`sayustock_market_overview`、`sayustock_quote`、`sayustock_kline`、
  `sayustock_technical_analysis`、`sayustock_sector_ranking`、
  `sayustock_compare`、`sayustock_screen`、`sayustock_generate_chart`、
  `sayustock_generate_report`、`sayustock_plot_series`、`sayustock_pie_chart`、
  `sayustock_data_table`、`sayustock_analyze_series`、`sayustock_market_ranking`、
  `sayustock_financials`、`sayustock_valuation`、`sayustock_ipo_calendar`、
  `sayustock_news`、`sayustock_watchlist_list`、`sayustock_artifact_list`
- 变更（走 MoEllm 二阶段确认）：`sayustock_watchlist_add`、`sayustock_watchlist_remove`

工具结果包含文本摘要、结构化 JSON、PNG data URL 与 HTML 制品元数据。
模型可以先用行情工具取数，再调 `sayustock_generate_report` 输出 HTML 研报。

## 6. 网页 AI / QWeb 基座

启动后访问：

```
http://<host>:<port>/sayustock/
```

页面包含：市场总览、个股行情（K线/分时 iframe）、网页 AI、图表与报告、接入设置。

API 基座（全部以 `/sayustock` 为前缀）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/sayustock/api/health` | 健康检查、LLM/渲染状态 |
| GET | `/sayustock/api/overview` | 大盘结构化数据 |
| GET | `/sayustock/api/quote?q=` | 单标的行情 |
| GET | `/sayustock/api/kline?q=&period=` | K线 + 指标 |
| GET | `/sayustock/api/chart/html?type=` | 完整 HTML 图表（可直接 iframe） |
| GET | `/sayustock/api/chart/png?type=` | PNG 图表 |
| POST | `/sayustock/api/chat` | LLM 对话 + 工具循环 |
| POST | `/sayustock/api/command` | 无 LLM 的命令模式 |
| GET | `/sayustock/api/artifacts` | 制品列表 |
| GET | `/sayustock/artifact/{id}.html` | 沙箱化 HTML 制品 |
| GET | `/sayustock/api/qweb/manifest` | QWeb 插件清单 |

### QWeb 接入约定

- 挂载点：`/sayustock`
- 清单：`GET /sayustock/api/qweb/manifest`，schema `qweb.plugin.v1`；
  功能目录：`GET /sayustock/api/qweb/catalog`，schema `qweb.feature_catalog.v1`
- 管理台入口约定：`/qweb/admin/sayustock`（由宿主路由到本插件页面）
- 鉴权：`Authorization: Bearer <SAYUSTOCK_WEB_TOKEN>`；
  宿主管理台可传 `X-QWeb-Admin: 1`（需 `web.trust_qweb=true`）
- 用户透传：`X-QWeb-User` / `X-QWeb-Session`，用于隔离自选与对话
- 制品：`/sayustock/artifact/{id}.html` 返回带 CSP `sandbox` 的只读页面，
  适合由 QWeb 管理台 iframe 嵌入

### iframe / postMessage

嵌入地址：

```html
<iframe src="https://bot.example.com/sayustock/?embed=1&token=..."></iframe>
```

`embed=1` 会隐藏插件自身的侧栏，只保留内容区。桥接脚本
`/sayustock/static/qweb-bridge.js` 暴露 `window.SayuStockQWeb`：

```js
window.SayuStockQWeb.chat("今天大盘怎么样")
window.SayuStockQWeb.command("个股 600519")
window.SayuStockQWeb.openArtifact(id)
```

同时支持 postMessage：

```js
iframe.contentWindow.postMessage(
  { source: "qweb", type: "sayustock.chat", message: "分析一下贵州茅台" },
  "*",
)
```

插件向宿主发送 `{ source: "sayustock", type: "sayustock.ready" | "sayustock.reply" | "sayustock.error" }`。

## 7. 架构

```text
nonebot_plugin_sayustock/
├── __init__.py          # PluginMetadata、matcher、启动挂载
├── config.py            # 默认值 + JSON + 环境变量合并
├── paths.py             # data/sayustock 目录
├── platform/            # OneBot11 / OneBot12 / QQ 统一事件与发送
├── services/
│   ├── market.py        # 复用 SayuStock.utils.market + 缓存/结构化
│   └── charts.py        # 复用原 render/ 模板；HTML + PNG
├── ai/
│   ├── tools.py         # 共享 ToolDefinition / ToolSpec 工具
│   ├── llm.py           # OpenAI 兼容对话 + 有界工具循环
│   └── moellm_bridge.py # 注册到 MoEllmChats
├── web/                 # FastAPI + 原生前端 + QWeb 基座
├── commands.py          # Bot / Web 共用命令执行
├── menu.py              # PicMenu menu_data
└── compat/gsuid_core/   # 仅供应 logger/config/render 的最小 shim
```

### 为什么保留 `SayuStock/`

原仓库 4 万行行情、指标、模板与模拟盘代码是唯一事实源。本分支只新增
NoneBot2 运行时，不复制行情解析、指标公式和 Jinja 模板；`SayuStock/__init__.py`
在检测不到 GsCore 时跳过 GsCore 注册，使 `SayuStock.utils.market` 可以被
NoneBot2 直接复用。

### 扩展新工具

1. 在 `ai/tools.py` 实现 async handler，返回 `ToolOutcome`。
2. 用 `ToolDefinition` 注册（JSON Schema 顶层必须 `type=object`）。
3. Web AI 自动获得该工具；MoEllmChats 启动时自动注册。
4. 在 `menu.py` 增加菜单项与 `llm_intents`。
5. 在 `docs/nonebot2.md` 记录参数与权限边界。

### 扩展网页

- 页面：`web/templates/index.html`
- 样式/脚本：`web/static/`
- API：`web/api.py`
- 制品：`artifacts.py`（HTML 通过 CSP sandbox 渲染，禁止脚本）

## 8. 测试

```bash
python -m pytest tests_nb2 -q
```

覆盖：配置合并、菜单元数据、协议事件归一化、行情工具、Web API、LLM 工具循环、
MoEllm 注册桥接。网络型行情测试默认跳过，避免 CI 依赖外部接口。