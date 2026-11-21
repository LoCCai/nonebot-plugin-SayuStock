# SayuStock · QWeb 分支

> 本分支把 SayuStock 作为 **QWeb / NoneBot2 数据与图表插件** 使用：
> **OneBot v11 / OneBot v12 / QQ 官方机器人**共用一套命令；群聊研究由宿主
> **MoEllmChats** 驱动；网页对话使用宿主 **`/qweb/chat`** 的个人模型与计费。
>
> 插件不再自带模型、密钥、供应商、直连聊天或网页配置路由。原 GsCore 业务源码
> 保留在 `SayuStock/`，行情、指标与图表仍复用同一份实现。

## 1. 安装

在 NoneBot2 工程中：

```bash
pip install "nonebot-plugin-sayustock @ git+https://github.com/LoCCai/nonebot-plugin-SayuStock.git@qiqi"
playwright install chromium
```

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_sayustock"]
adapters = [
  { name = "OneBot V11", module_name = "nonebot.adapters.onebot.v11" },
  { name = "OneBot V12", module_name = "nonebot.adapters.onebot.v12" },
  { name = "QQ", module_name = "nonebot.adapters.qq" },
]
driver = "~fastapi"
```

群聊图片仍通过 **nonebot-plugin-htmlrender** 的公开 `html_to_pic` 输出 PNG；
网页图表与 HTML 报告以可嵌入的 HTML 制品展示，不需要启动第二条渲染链。

## 2. 宿主边界

### 群聊

- 宿主 MoEllmChats 可使用 ToolSpec / `tool_manager` 接口拿到 `sayustock_*` 工具；
  没有这些接口时，宿主仍可通过 NoneBot 插件元数据与真实群命令调用。
- 工具身份只取宿主传来的真实事件（`event.user_id` / `event.group_id`），不会读取
  模型参数里的 `user_id`。写工具（自选增删）必须等宿主完成确认；上游
  `tool_manager` 只注入只读工具，写操作请让用户直接发命令。

### 网页

网页默认只在 QWeb 宿主内挂载。鉴权全部使用宿主 FastAPI 依赖
`src.qi_web.dependencies`：

| 依赖 | 用途 |
| --- | --- |
| `require_membership` | 成员身份；用户身份只取返回的 `account.qq` |
| `admin_account` | 管理接口（`/api/qweb/manifest`、`/api/qweb/catalog`） |
| `csrf_protected` | 所有写请求；同时要求 `X-CSRF-Token` |

插件**不接受** `X-QWeb-User`、`X-QWeb-Admin`、`X-QWeb-Group` 等客户端自报身份。
宿主模块不存在时 `get_router()` 返回 503，启动钩子也不会挂载页面。

网页写请求必须带 `X-CSRF-Token`。对话由宿主 `/qweb/chat` 承担；本插件只暴露
股票工具和只读数据，不保存任何模型配置。

### 私有制品

图表/报告制品写入时记录当前账户 `qq`。`/api/artifacts`、`/artifact/{id}.html`、
`/artifact/{id}.png` 只返回当前 `account.qq` 名下的制品；他人 ID 即使猜中也返回 404。
HTML 制品带 `Content-Security-Policy: sandbox`，可由宿主页面 iframe 内嵌或新窗口打开。

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

命令统一走 `on_command`，由 `COMMAND_START` 决定前缀；不要再注册
`on_fullmatch` / `on_regex` 的平行入口，否则真实群聊会重复响应。

## 4. PicMenu Next

`PluginMetadata.extra.menu_data` 内置 12 个三级菜单项；菜单命令与真实 matcher
同源。PicMenu 未安装时不影响插件运行。

## 5. QWeb API

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/qweb/sayustock/` | 成员 | 页面 |
| GET | `/qweb/sayustock/api/health` | 成员 | 健康检查 |
| GET | `/qweb/sayustock/api/meta` | 成员 | 版本、工具、宿主信息 |
| GET | `/qweb/sayustock/api/overview` | 成员 | 大盘数据 |
| GET | `/qweb/sayustock/api/quote?q=` | 成员 | 单标的行情 |
| GET | `/qweb/sayustock/api/kline?q=&period=` | 成员 | K线 + 指标 |
| GET | `/qweb/sayustock/api/chart/html?type=` | 成员 | 可 iframe 的 HTML 图表 |
| GET | `/qweb/sayustock/api/chart/png?type=` | 成员 | PNG 图表 |
| GET/POST/DELETE | `/qweb/sayustock/api/watchlist` | 成员 + CSRF | 自选 |
| POST | `/qweb/sayustock/api/command` | 成员 + CSRF | 命令模式 |
| GET | `/qweb/sayustock/api/artifacts` | 成员 | 当前账户制品 |
| GET | `/qweb/sayustock/artifact/{id}.html` | 成员 + 所有者 | 沙箱 HTML 制品 |
| GET | `/qweb/sayustock/artifact/{id}.png` | 成员 + 所有者 | PNG 制品 |
| GET | `/qweb/sayustock/api/qweb/tools` | 成员 | 工具 schema |
| POST | `/qweb/sayustock/api/qweb/tools/{name}` | 成员 + CSRF | 宿主执行工具；写工具需 `confirmed=true` |
| GET | `/qweb/sayustock/api/qweb/manifest` | 管理员 | 宿主清单 |
| GET | `/qweb/sayustock/api/qweb/catalog` | 管理员 | 功能目录 |

清单 schema 为 `qweb.plugin.v1`，其中声明：

```json
{
  "chat": {"provider": "qweb", "endpoint": "/qweb/chat", "billing": "host_account"},
  "auth": {
    "membership": "src.qi_web.dependencies.require_membership",
    "admin": "src.qi_web.dependencies.admin_account",
    "csrf": "src.qi_web.dependencies.csrf_protected",
    "csrf_header": "X-CSRF-Token",
    "identity": "account.qq"
  }
}
```

### 网页对话

股票页直接链接到 `/qweb/chat`。七七加载插件后，登录用户在该对话选择模型。业务工具复用当前回答的身份、授权、工具额度及计费，图表写入当前会话附件。股票页通过 `/qweb/api/v1/auth/session` 读取写操作所需的 CSRF 令牌。

## 6. 配置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `web.enabled` | `true` | 是否尝试挂载；无 QWeb 宿主依赖时仍不挂载 |
| `web.public_base_url` | 空 | Bot 消息中 HTML 链接的站点前缀 |
| `render.enabled` | `true` | 是否尝试 HTML→PNG（nonebot-plugin-htmlrender） |
| `render.scale` | `1.25` | 图片像素倍率 |
| `market.provider_chain` | 空 | 行情源链；空则用原插件默认 |

不存在 `web.token`、`llm.*`、`SAYUSTOCK_LLM_*` 等插件自有模型/令牌配置。

## 7. 测试

```bash
python -m pytest tests_nb2 -q
```

覆盖：配置合并、菜单、协议事件、行情工具、QWeb 成员/管理员/CSRF、制品归属、
MoEllm ToolSpec 与上游 `tool_manager` 注册。网络型行情测试默认跳过。
