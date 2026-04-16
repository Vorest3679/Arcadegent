# Arcadegent

Arcadegent 是一个面向音游机厅检索、Agent 问答和路线建议的全栈应用。项目当前已经形成从数据抓取、ETL、FastAPI 后端、Agent 工具运行时，到 React 地图化前端的本地闭环。

## 功能概览

- Agent 对话：支持机厅搜索、附近推荐、导航路线三类意图，前端默认使用异步会话派发和 SSE 实时事件流。
- 实时过程展示：会话会推送 `session.started`、`subagent.changed`、`tool.*`、`navigation.route_ready`、`assistant.token`、`assistant.completed` 等事件。
- 地图化结果：聊天路线和机厅浏览都能渲染高德地图点位、路线卡片，并提供 Web 高德查看和唤起高德导航链接。
- 机厅浏览器：支持关键词、地区级联、省市区筛选、只看有机台、更新时间/机台数/指定机种数量排序、分页和门店详情。
- 会话管理：历史会话列表、详情加载、运行中重连、删除会话，数据保存在本地 JSON 文件。
- 地理能力：支持浏览器定位缓存、高德逆地理编码、机厅坐标缓存、无坐标机厅的区域级地图回退。
- Agent 工具系统：内置 DB 查询、地理解析、路线规划、总结工具，同时支持启动时发现 MCP 工具并投影为 `mcp__*`。
- 数据链路：提供 Bemanicn 抓取脚本、ETL 规范化脚本、QA 产物和 Supabase migration 草案。

## 技术栈

- Backend: Python 3.11+, FastAPI, Pydantic, httpx, FastMCP
- Agent: OpenAI-compatible LLM provider, ReAct runtime, YAML subagent definitions, JSON Schema tool registry
- Frontend: React 18, TypeScript, Vite, Zustand, marked, DOMPurify
- Map: 高德 Web JS API, 高德 REST API, 高德 MCP endpoint
- Data: 本地 JSONL 读模型、本地 JSON 会话存储、ETL 输出 JSONL/SQLite/QA report
- Tests: pytest, Playwright

## 目录结构

```text
backend/                         FastAPI 后端、Agent 运行时、工具系统和测试
backend/app/agent/context/       Agent prompts 与技能片段
backend/app/agent/nodes/         main agent / worker YAML 定义和 provider profile
backend/app/agent/tools/         builtin tools、MCP gateway、工具 schema 和 manifest
apps/web/                        React + Vite 前端
apps/web/src/components/map/     高德地图、路线卡片和地图操作组件
apps/web/tests/e2e/              Playwright 端到端测试
scripts/                         Bemanicn 抓取与辅助脚本
scripts/etl/                     ETL 规范化脚本与测试
data/raw/                        原始抓取数据
data/processed/                  ETL 产物
data/runtime/                    本地会话、地理缓存等运行时数据
supabase/migrations/             数据库迁移草案
docs/                            方案、交接、开发细节和问题记录
```

## 文档入口

- [内建工具动态注册表写法](docs/builtin-tool-manifest-guide.md)
- [Agent 地图结果渲染设计](docs/dev-details/agent-map-artifacts-rendering.md)
- [Agent context payload 设计](docs/dev-details/agent-context-payload-design.md)
- [动态工具注册实现说明](docs/dev-details/dynamic-tool-registry-implementation.md)
- [浏览器定位与逆地理编码](docs/browser-location-reverse-geocoding.md)
- [开发计划索引](docs/plans/index.md)
- [Issue 索引](docs/issues/index.md)

## 环境要求

- Python `>=3.11`
- Node.js `>=18`
- npm `>=9`

仓库根目录没有统一的前端 workspace `package.json`，前端命令需要在 `apps/web/` 下执行。

## 快速开始

以下命令默认从仓库根目录 `Arcadegent/` 开始。

### 1. 安装后端依赖

macOS / Linux:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cd ..
```

Windows PowerShell:

```powershell
cd backend
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cd ..
```

### 2. 安装前端依赖

```bash
cd apps/web
npm install
cd ../..
```

### 3. 配置后端环境变量

后端启动时会自动读取仓库根目录下的 `.env`。建议从示例文件复制：

macOS / Linux:

```bash
cp backend/.env.example .env
```

Windows PowerShell:

```powershell
Copy-Item backend/.env.example .env
```

常用配置如下：

```dotenv
APP_ENV=dev
LOG_LEVEL=INFO
HOST=0.0.0.0
PORT=8000
CORS_ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

ARCADE_DATA_JSONL=data/raw/bemanicn/shops_detail.jsonl
CHAT_SESSION_STORE_PATH=data/runtime/chat_sessions.json

LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT_SECONDS=20
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=500

AGENT_MAX_STEPS=20
AGENT_CONTEXT_WINDOW=24
AGENT_PROVIDER_PROFILE=default

MCP_SERVERS_DIR=backend/app/agent/tools/mcp/servers
MCP_DEFAULT_TIMEOUT_SECONDS=10

AMAP_API_KEY=
AMAP_BASE_URL=https://restapi.amap.com
AMAP_TIMEOUT_SECONDS=8
ARCADE_GEO_CACHE_PATH=data/runtime/arcade_geo_cache.json
ARCADE_GEO_SYNC_LIMIT=8
ARCADE_GEO_MAX_WORKERS=4
ARCADE_GEO_REQUEST_TIMEOUT_SECONDS=1.2
```

说明：

- `ARCADE_DATA_JSONL` 是后端实际读取的机厅数据源，默认指向 `data/raw/bemanicn/shops_detail.jsonl`。
- `LLM_API_KEY` 为空时服务可以启动，机厅列表接口也可使用，但 Agent 对话不会产生有意义的模型编排结果。
- `CHAT_SESSION_STORE_PATH`、`ARCADE_GEO_CACHE_PATH` 会写入 `data/runtime/`，目录不存在时会自动创建。
- `AMAP_API_KEY` 用于高德 REST 路线、逆地理编码和后端地理缓存；高德 Web JS API 的浏览器 key 需要单独配在前端。

### 4. 配置 MCP

默认会扫描 `backend/app/agent/tools/mcp/servers/*.json`。当前内置高德配置如下：

```json
{
  "transport": "streamable-http",
  "url": "https://mcp.amap.com/mcp?key=${AMAP_API_KEY}"
}
```

文件名会成为 server name，例如 `amap.json` 会注册为 `amap`，远端工具会投影成类似 `mcp__amap__maps_direction_walking` 的本地工具名。

如果第三方 MCP 工具命名发生变化，可以在同一个 JSON 里显式指定路线工具：

```json
{
  "transport": "streamable-http",
  "url": "https://mcp.amap.com/mcp?key=${AMAP_API_KEY}",
  "route_tool_name": "maps_direction_walking"
}
```

也支持标准 MCP config fragment：

```json
{
  "mcpServers": {
    "fetch": {
      "transport": "sse",
      "url": "https://example.com/mcp"
    }
  }
}
```

### 5. 启动后端

macOS / Linux:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --access-log
```

Windows PowerShell:

```powershell
cd backend
.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --access-log
```

启动后可访问：

- Health: `http://localhost:8000/health`
- Swagger: `http://localhost:8000/docs`

`/health` 会返回数据加载状态、tool provider 状态和 MCP discovery 状态。高德 MCP 问题优先看 `mcp.servers.amap.available_tools`、`selected_route_tool` 和 `last_error`。

### 6. 配置并启动前端

复制前端环境变量：

macOS / Linux:

```bash
cp apps/web/.env.example apps/web/.env.local
```

Windows PowerShell:

```powershell
Copy-Item apps/web/.env.example apps/web/.env.local
```

`apps/web/.env.local`：

```dotenv
VITE_API_BASE=http://127.0.0.1:8000
VITE_AMAP_WEB_KEY=
VITE_AMAP_SECURITY_JS_CODE=
VITE_AMAP_URI_SRC=arcadegent_web
```

注意：`VITE_AMAP_WEB_KEY` 必须是高德 Web JS API 可用的浏览器 key，不能直接复用只给 REST / MCP 用的 key，否则页面会报 `USERKEY_PLAT_NOMATCH` 或类似鉴权错误。

启动：

```bash
cd apps/web
npm run dev
```

打开：

- Chat: `http://localhost:5173`
- Arcade Explorer: `http://localhost:5173/?view=arcades`

## API 概览

- `GET /health`：健康检查、数据加载状态、tool provider 和 MCP discovery 状态
- `GET /api/v1/arcades`：机厅列表、筛选、排序和分页
- `GET /api/v1/arcades/{source_id}`：机厅详情
- `GET /api/v1/regions/provinces`：省份列表
- `GET /api/v1/regions/cities`：城市列表，参数 `province_code`
- `GET /api/v1/regions/counties`：区县列表，参数 `city_code`
- `POST /api/v1/location/reverse-geocode`：浏览器坐标逆地理编码
- `POST /api/chat`：同步 Agent 对话入口
- `POST /api/chat/sessions`：异步派发 Agent 会话，前端默认使用
- `GET /api/stream/{session_id}`：SSE 实时事件流，支持 `last_event_id` 和 `Last-Event-ID`
- `GET /api/v1/chat/sessions`：历史会话列表
- `GET /api/v1/chat/sessions/{session_id}`：会话详情、历史 turns、地图 artifacts
- `DELETE /api/v1/chat/sessions/{session_id}`：删除会话

## Agent 运行时

Agent 配置分为几层：

- Subagent 定义：`backend/app/agent/nodes/definitions/*.yaml`
- Provider profile：`backend/app/agent/nodes/profiles/provider_profiles.yaml`
- Tool policy：`backend/app/agent/nodes/profiles/tool_policies.yaml`
- Prompt：`backend/app/agent/context/prompts/*.md`
- Skill：`backend/app/agent/context/skills/*.md`
- Builtin tool manifest：`backend/app/agent/tools/builtin/tools_manifest.json`
- Builtin tool schema：`backend/app/agent/tools/builtin/schemas/*.json`
- MCP server 配置：`backend/app/agent/tools/mcp/servers/*.json`

当前主流程是 `main_agent` 识别意图并调度 worker。`search_worker` 负责机厅查询，`navigation_worker` 负责目标解析和路线规划，最终再由 summary 流程生成用户可见回复。

路线规划优先尝试可用的高德 MCP 路线工具；不可用时使用内置 `route_plan_tool`，该工具会先请求高德 REST 路线 API，失败后退化为离线直线距离和估算时间。

## 数据处理

抓取 Bemanicn 数据：

```bash
python scripts/scrape_bemanicn.py --max-shops 30
```

默认输出到 `data/raw/bemanicn/`，主要包括：

- `province_index.json`
- `shops_seed.jsonl`
- `shops_detail_raw.jsonl`
- `shops_detail_props.jsonl`
- `shops_detail.jsonl`
- `run_summary.json`

规范化并生成 QA 产物：

```bash
python scripts/etl/ingest_arcades.py \
  --input data/raw/bemanicn/shops_detail.jsonl \
  --run-summary data/raw/bemanicn/run_summary.json \
  --output-dir data/processed/bemanicn \
  --sqlite-path data/processed/arcadegent.db
```

主要产物：

- `data/processed/bemanicn/arcade_shops.jsonl`
- `data/processed/bemanicn/arcade_titles.jsonl`
- `data/processed/bemanicn/bad_rows.jsonl`
- `data/processed/bemanicn/qa_report.json`
- `data/processed/bemanicn/ingest_run.json`
- `data/processed/arcadegent.db`

当前 API 默认直接读取 `ARCADE_DATA_JSONL` 指向的 JSONL。ETL 的主要价值是规范化、质量校验、SQLite 中间产物和后续数据库迁移准备。

## 常用开发命令

后端测试：

```bash
cd backend
python -m pytest -q
```

ETL 测试：

```bash
python -m pytest -q scripts/etl/tests
```

前端开发：

```bash
cd apps/web
npm run dev
```

前端打包：

```bash
cd apps/web
npm run build
```

前端 E2E：

```bash
cd apps/web
npm run test:e2e
```

## 故障排查

- 后端启动但无数据：检查 `ARCADE_DATA_JSONL` 是否存在，或访问 `/health` 看 `store` 状态。
- Agent 回复 provider 错误：检查 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 和 `AGENT_PROVIDER_PROFILE`。
- 高德 MCP 没有路线：访问 `/health`，查看 `mcp.servers.amap.available_tools`、`selected_route_tool`、`last_error`，必要时配置 `route_tool_name`。
- 前端地图不可用：检查 `VITE_AMAP_WEB_KEY` 是否是 Web JS API key，必要时配置 `VITE_AMAP_SECURITY_JS_CODE`。
- 路线只有直线估算：通常是高德 MCP 和 REST 都不可用，检查 `AMAP_API_KEY`、额度、网络和 `/health`。
- 前端跨域错误：确认 `.env` 里的 `CORS_ALLOW_ORIGINS` 包含当前 Vite 地址。

## 当前限制

- 线上级认证、多用户隔离和权限管理尚未接入。
- 机厅查询当前以本地 JSONL 为读模型，Supabase migration 仍是数据库落地草案。
- MCP discovery 依赖第三方服务返回的 tool schema，远端命名变化时可能需要手动指定 `route_tool_name`。
- 没有高德 Web JS key 时，前端仍可列表检索并生成高德跳转 URI，但内嵌地图不可用。
