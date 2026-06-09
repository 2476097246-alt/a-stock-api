# a-stock-api

A 股全栈数据 **REST API 服务** — 24 端点 · 7 层架构 · OpenAPI · 生产级容错

将 [a-stock-data](https://github.com/simonlin1212/a-stock-data) 的数据抓取能力封装为可部署的 HTTP API，专为大模型 **Function Calling / Tool Use**（Dify、微信助手等）设计。已在阿里云 ECS 生产环境验证。

> **v3.3.0（2026-06）** 同步上游 v3.2.2 修复：东财 slist 概念板块（#18）、巨潮动态 orgId（#19）。

---

## 项目定位

| | 上游 [a-stock-data](https://github.com/simonlin1212/a-stock-data) | 本项目 **a-stock-api** |
|---|---|---|
| 形态 | 自包含 Skill 文件（Markdown + 内嵌 Python） | Flask REST API 服务 |
| 使用方式 | AI 编程助手读取 SKILL.md 抄代码 | HTTP 调用 `/api/v1/stock/*` |
| 部署 | 无需服务器 | ECS / Serverless，gunicorn + systemd |
| 场景 | 个人开发者、Claude Code | Dify Agent、微信助手、商业化产品 |

```
┌─────────────────────────────────────────────────────────┐
│  Dify / 微信助手 / 其他 LLM 客户端                        │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP + OpenAPI
┌────────────────────────▼────────────────────────────────┐
│  api_wrapper/  ← 本项目原创                               │
│  ├── app.py          Flask 路由 + 容错降级 + 防封追踪     │
│  ├── stock_core.py   数据抓取核心（抽取自 SKILL.md）       │
│  ├── openapi.yaml    OpenAPI 3.0 规范                    │
│  └── DEPLOY.md       ECS 部署指南                        │
└────────────────────────┬────────────────────────────────┘
                         │ 直连 HTTP / TCP
┌────────────────────────▼────────────────────────────────┐
│  13 个数据源：腾讯 / 东财 / 同花顺 / 百度 / 新浪 / 巨潮 …  │
│  数据逻辑源自 a-stock-data（Apache 2.0）                  │
└─────────────────────────────────────────────────────────┘
```

---

## 核心特性

- **24 条 REST 路由**，覆盖行情、信号、资金面、研报、新闻、基础数据、公告七大层级
- **OpenAPI 3.0** 规范，Dify 可直接「从 OpenAPI 导入」创建工具
- **全路由容错降级**：异常时返回 HTTP 200 + 空结构 + `note`，避免 LLM 调用链因 500 中断
- **东财防封**：`em_get()` 串行节流（≥1s）+ 响应头 `X-RateLimit-EM-Calls` 统计
- **生产部署**：ECS + gunicorn + systemd 保活，详见 [api_wrapper/DEPLOY.md](./api_wrapper/DEPLOY.md)

---

## API 端点一览

| 层级 | 路由 | 说明 |
|------|------|------|
| 系统 | `GET /api/v1/health` | 健康检查 + 东财调用统计 |
| 行情 | `GET /api/v1/stock/quote` | 腾讯实时行情（PE/PB/市值/涨跌停） |
| 行情 | `GET /api/v1/stock/kline` | 百度日K线 + MA5/10/20 |
| 信号 | `GET /api/v1/stock/hot` | 同花顺当日强势股 + 题材归因 |
| 信号 | `GET /api/v1/stock/northbound` | 北向资金实时分钟流向 |
| 信号 | `GET /api/v1/stock/concept` | 概念板块归属（东财 slist） |
| 信号 | `GET /api/v1/stock/fund_flow` | 个股资金流向（分钟/120日） |
| 信号 | `GET /api/v1/stock/dragon_tiger` | 龙虎榜（个股/全市场） |
| 信号 | `GET /api/v1/stock/industry` | 行业板块涨跌排名 |
| 资金面 | `GET /api/v1/stock/margin` | 融资融券明细 |
| 资金面 | `GET /api/v1/stock/block_trade` | 大宗交易 |
| 资金面 | `GET /api/v1/stock/lockup` | 限售解禁日历 |
| 资金面 | `GET /api/v1/stock/holder` | 股东户数变化 |
| 资金面 | `GET /api/v1/stock/dividend` | 分红送转历史 |
| 研报 | `GET /api/v1/stock/reports` | 东财研报列表 |
| 研报 | `GET /api/v1/stock/eps_forecast` | 同花顺 EPS 一致预期 |
| 研报 | `GET /api/v1/stock/iwencai/search` | iwencai 语义搜索 |
| 研报 | `GET /api/v1/stock/iwencai/query` | iwencai 查询 |
| 新闻 | `GET /api/v1/stock/news` | 个股新闻 |
| 新闻 | `GET /api/v1/stock/global_news` | 全球 7×24 资讯 |
| 基础 | `GET /api/v1/stock/financial` | 新浪财报三表 |
| 基础 | `GET /api/v1/stock/announcements` | 巨潮公告 |
| 估值 | `GET /api/v1/stock/valuation` | 综合估值（PE/PEG/消化年数） |
| 估值 | `GET /api/v1/stock/peg` | PEG 计算 |

完整参数与响应格式见 [api_wrapper/openapi.yaml](./api_wrapper/openapi.yaml)。

---

## 快速开始

### 本地调试

```bash
cd api_wrapper
pip install -r requirements.txt
python app.py
# 访问 http://localhost:9000/api/v1/health
```

### 验证接口

```bash
curl http://localhost:9000/api/v1/health
curl "http://localhost:9000/api/v1/stock/quote?codes=600519,688017"
curl "http://localhost:9000/api/v1/stock/concept?code=600519"
curl "http://localhost:9000/api/v1/stock/announcements?code=601318"
```

### 生产部署

详见 [api_wrapper/DEPLOY.md](./api_wrapper/DEPLOY.md)（阿里云 ECS + gunicorn + systemd）。

---

## Dify 接入

1. **工具 → 从 OpenAPI 导入**，填入 `api_wrapper/openapi.yaml`
2. 或将 `servers.url` 改为你的 ECS 地址后导入
3. 在 Agent 中绑定工具，即可通过自然语言查询 A 股数据

> **注意**：`/api/v1/stock/concept` 响应格式为 `{total, boards, concept_tags}`（v3.2.2 起），不再使用 `industry/concept/region` 三分结构。`concept_tags` 字段保持兼容。

---

## 项目结构

```
a-stock-api/
├── api_wrapper/           # ← 本项目核心（原创）
│   ├── app.py             #   Flask 路由层
│   ├── stock_core.py      #   数据抓取核心
│   ├── openapi.yaml       #   OpenAPI 3.0 规范
│   ├── DEPLOY.md          #   部署指南
│   ├── requirements.txt
│   ├── bootstrap          #   阿里云 FC 启动脚本
│   └── s.yaml             #   Serverless Devs 配置
├── SKILL.md               # 上游 a-stock-data 原始 Skill 文件
├── CHANGELOG.md
└── LICENSE                # Apache 2.0
```

---

## 致谢与许可

本项目的数据抓取逻辑基于 Simon 林开源的 [a-stock-data](https://github.com/simonlin1212/a-stock-data)（[Apache 2.0](./LICENSE)）。

- **上游提供**：SKILL.md 内嵌的数据源封装、7 层架构设计、东财防封 `em_get()` 策略
- **本项目原创**：REST API 层、OpenAPI 规范、容错降级、防封追踪、部署方案、估值路由

遵循 Apache 2.0 许可，使用本项目请注明出处。

---

## Disclaimer

本项目仅提供数据获取工具，不构成任何投资建议。股市有风险，投资需谨慎。
