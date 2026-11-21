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
  模型参数里的 `user_id`。写工具（自选增删、订阅及模拟盘管理）必须等宿主完成确认；上游
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
股票数据、本人自选与授权群业务，由宿主管理聊天模型。

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

股票研究台可从市场、选股与资讯、个股研究、自选四处直接操作。图表与报告在页内预览。
实时 HTML 行情图跟随当前 QWeb 的配色；保存的报告和图片沿用生成时的七七报告主题。


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

七七 OneBot v11 群中，财经快讯等单张卡直接以图片投递。图文、多图和纯文字结果各使用一条合并转发。股票卡片复用七七发布的主题和日夜配色，网页从结构化制品字段打开 HTML。

股票页直接链接到 `/qweb/chat`。七七加载插件后，登录用户在该对话选择模型。业务工具复用当前回答的身份、授权、工具额度及计费，图表写入当前会话附件。股票页通过 `/qweb/api/v1/auth/session` 读取写操作所需的 CSRF 令牌。

## 6. 配置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `web.enabled` | `true` | 是否尝试挂载；无 QWeb 宿主依赖时仍不挂载 |
| `web.public_base_url` | 空 | 私聊制品链接的站点前缀 |
| `render.enabled` | `true` | 是否尝试 HTML→PNG（nonebot-plugin-htmlrender） |
| `render.scale` | `1.25` | 图片像素倍率 |
| `market.provider_chain` | 空 | 行情源链；空则用原插件默认 |

不存在 `web.token`、`llm.*`、`SAYUSTOCK_LLM_*` 等插件自有模型/令牌配置。

### PostgreSQL 存储（七七部署）

独立部署默认 `SAYUSTOCK_STORAGE=file`，自选与制品索引都留在现有
`{SAYUSTOCK_DATA_DIR}/watchlist.json`、`{SAYUSTOCK_DATA_DIR}/artifacts/index.json`
路径，行为不变。

七七显式设置 `SAYUSTOCK_STORAGE=postgres`（可通过 NoneBot `.env.prod` 或进程
环境变量）。连接串不在插件里配置，而是复用宿主
`src.qi_web.config.get_settings().database_url`（`str` 或 `SecretStr`）；
测试环境才允许用 `SAYUSTOCK_DATABASE_URL` 覆盖。插件不会打印连接串。

PG 模式是 **fail closed**：宿主配置缺失、psycopg 缺失、表缺失或 SQL 失败都会
报错，绝不回退到文件。插件运行时不执行 DDL、不建表；运维预先创建：

- `qiqi_sayustock.watchlist(owner_key text primary key, items jsonb not null)`
- `qiqi_sayustock.artifact(id text primary key, record jsonb not null)`

制品正文（`.html` / `.png`）仍在原 `artifacts/` 目录，只有索引写 PG。自选用
`SELECT ... FOR UPDATE` 事务锁保留顺序、字段、空列表和 owner 隔离；制品索引
创建/清理在同一事务内完成，文件在索引提交后再删除。启动时会校验两张表可用。

## 7. 测试

```bash
python -m pytest tests_nb2 -q
```

覆盖：配置合并、菜单、协议事件、行情工具、QWeb 成员/管理员/CSRF、制品归属、
MoEllm ToolSpec 与上游 `tool_manager` 注册。网络型行情测试默认跳过。

真实 PG 并发/fail-closed 测试只在提供测试库时运行，且只操作临时 owner 行，不建表：

```bash
SAYUSTOCK_DATABASE_URL=postgresql://... pytest tests_nb2/test_storage_postgres.py -q
```


## 七七分支的专题与部署

群成员可用「股票帮助」，网页在股票页的「研究与模拟盘」选择业务。登录后可通过
`GET /qweb/sayustock/api/features` 读取与群菜单同源的专题清单。查询包括 ETF 申赎篮子、
市场云图、持仓与行业集中度、估值对比、IPO、账本、交易记录和宏观事件。
`POST /qweb/sayustock/api/features/{名称}` 接收 `argument`，群业务可传 `group_id`。
宿主重新核验群成员身份与指令权限。写操作需要 CSRF 和 `confirmed: true`。
QWeb 对话和 MoEllmChats 分别将 `sayustock_manage_feature` 交给宿主确认；
`sayustock_research_feature` 提供查询、结构化数据和属于本人的图表。

七七以 `SAYUSTOCK_STORAGE=postgres` 启用专用 schema。已有 001 的部署，依次执行插件
`migrations/002_news_subscription.sql` 和 `003_papertrade.sql`，然后加载插件。新闻调度器
记录群与机器人各自的水位。模拟盘用 PostgreSQL 事务记账。交易日决策及宏观复核由宿主 AI 客户端执行，
实时推送支持 OneBot v11/v12。QQ 官方适配器按平台能力响应主动查询。

「模型预测」使用独立的 Kronos 金融时序模型。安装包的 `forecast` extra、适合机器的 CPU 或 CUDA
版本的 PyTorch，并准备配对的权重。CPU 推荐 Kronos-mini 配 Kronos-Tokenizer-2k。
离线部署可在 `SAYUSTOCK_KRONOS_MODEL`、`SAYUSTOCK_KRONOS_TOKENIZER` 分别指定包含
`config.json` 与 `model.safetensors` 的目录。输入采用 30 分钟 K 线。预测中的重复请求共用并发限制。
