# Arcadegent

Arcadegent 是一个面向音游机厅检索、Agent 问答和路线建议的全栈应用。当前公开仓库保留应用运行、Agent 编排、地图渲染和部署相关代码；数据采集、私有数据集、运行缓存和生产密钥不随仓库公开。

QQ群：1091316877

## 功能概览

- Agent 对话：支持机厅搜索、附近推荐、导航路线三类意图，前端默认使用异步会话派发和 SSE 实时事件流。
- 实时过程展示：会话会推送 `session.started`、`subagent.changed`、`tool.*`、`navigation.route_ready`、`assistant.token`、`assistant.completed` 等事件。
- 地图化结果：聊天路线和机厅浏览都能渲染高德地图点位、路线卡片，并提供 Web 高德查看和唤起高德导航链接。
- 机厅浏览器：支持关键词、地区级联、省市区筛选、只看有机台、更新时间/机台数/指定机种数量排序、分页和门店详情。
- 会话管理：支持历史会话列表、详情加载、运行中重连和删除会话。
- 地理能力：支持浏览器定位缓存、高德逆地理编码、机厅坐标缓存、无坐标机厅的区域级地图回退。
- Agent 工具系统：内置 DB 查询、地理解析、路线规划、总结工具，同时支持启动时发现 MCP 工具并投影为 `mcp__*`。

## 技术栈

- Backend: Python 3.11+, FastAPI, Pydantic, httpx, FastMCP
- Agent: OpenAI-compatible LLM provider, ReAct runtime, YAML subagent definitions, JSON Schema tool registry
- Frontend: React 18, TypeScript, Vite, Zustand, marked, DOMPurify
- Map: 高德 Web JS API, 高德 REST API, 高德 MCP endpoint
- Data: JSONL 读模型、可选 Supabase 读模型、本地 JSON 会话存储
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
deploy/nginx/                    生产 Nginx 示例配置
docs/guidings/                   对外指南文档
docs/dev-details/                对外开发细节文档
docker-compose.yml               本地或服务器 compose 编排
```

`data/`、运行缓存、私有脚本、计划/issue 归档、数据库迁移草案和真实环境变量按敏感资料处理，不随公开仓库发布。需要本地运行完整数据链路时，请自行准备兼容的 JSONL 数据源或配置后端可访问的数据库。

## 文档入口

- [Docs 总览](docs/README.md)
- [内建工具动态注册表写法](docs/guidings/builtin-tool-manifest-guide.md)
- [Agent 地图结果渲染设计](docs/dev-details/agent-map-artifacts-rendering.md)
- [Agent context payload 设计](docs/dev-details/agent-context-payload-design.md)
- [动态工具注册实现说明](docs/dev-details/dynamic-tool-registry-implementation.md)
- [浏览器定位与逆地理编码](docs/dev-details/browser-location-reverse-geocoding.md)

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

ARCADE_DATA_SOURCE=jsonl
ARCADE_DATA_JSONL=data/local/arcades.jsonl
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_TIMEOUT_SECONDS=8
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

- `ARCADE_DATA_SOURCE` 可选 `jsonl` 或 `supabase`，默认 `jsonl`。
- `ARCADE_DATA_JSONL` 是 JSONL 模式读取的机厅数据源；公开仓库不包含真实数据，请指向你本地准备的兼容文件。
- `ARCADE_DATA_SOURCE=supabase` 时必须配置 `SUPABASE_URL`，以及 `SUPABASE_ANON_KEY` 或 `SUPABASE_SERVICE_ROLE_KEY`。缺少配置会启动失败，不会静默回退 JSONL。
- `SUPABASE_SERVICE_ROLE_KEY` 仅用于后端或导入脚本，不要暴露给浏览器端。
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

## Docker 启动

Docker 方式会启动两个容器：

- `backend`：FastAPI 服务，容器内端口 `8000`
- `web`：Nginx 托管前端静态产物，并把 `/api/*`、`/api/stream/*`、`/health` 反代到后端

### 1. 安装并启动 Docker

macOS / Windows 本地开发推荐安装 Docker Desktop。安装后需要先启动 Docker Desktop，再运行 compose 命令；只安装 Docker CLI 但 daemon 没启动时，会看到类似错误：

```text
Cannot connect to the Docker daemon at unix:///Users/xxx/.docker/run/docker.sock. Is the docker daemon running?
```

macOS 可用：

```bash
open -a Docker
docker version
```

Windows PowerShell 可用：

```powershell
Start-Process "Docker Desktop"
docker version
```

`docker version` 同时显示 `Client` 和 `Server` 信息，才说明 Docker daemon 已经可用。如果只显示 `Client` 后报 `Cannot connect to the Docker daemon`，请等待 Docker Desktop 完全启动，或在 Docker Desktop 里切到 Linux containers。

Linux / 阿里云服务器可安装 Docker Engine 和 Compose plugin：

```bash
docker version
docker compose version
```

### 2. 准备环境变量

首次启动前先准备根目录 `.env`：

macOS / Linux:

```bash
cp backend/.env.example .env
```

Windows PowerShell:

```powershell
Copy-Item backend/.env.example .env
```

如果需要内嵌高德地图，把浏览器侧 key 放到根目录 `.env`。这些 `VITE_*` 变量会在前端镜像构建时写入静态包，修改后需要重新 build：

```dotenv
VITE_AMAP_WEB_KEY=
VITE_AMAP_SECURITY_JS_CODE=
VITE_AMAP_URI_SRC=arcadegent_web
```

真实数据和运行缓存仍放在宿主机 `data/` 目录。该目录会挂载到后端容器内的 `/app/data`，但不会进入镜像。

### 3. 启动

启动：

```bash
docker compose up --build
```

后台启动：

```bash
docker compose up -d --build
```

打开：

- Web: `http://localhost:8080`
- Health: `http://localhost:8080/health`
- Backend Swagger: `http://localhost:8000/docs`

默认端口只绑定到宿主机 `127.0.0.1`，便于后续由宿主机 Nginx 统一对外反代。如需临时从局域网访问，可以启动时覆盖绑定地址：

macOS / Linux:

```bash
WEB_BIND=0.0.0.0 BACKEND_BIND=0.0.0.0 docker compose up --build
```

Windows PowerShell:

```powershell
$env:WEB_BIND="0.0.0.0"
$env:BACKEND_BIND="0.0.0.0"
docker compose up --build
```

Windows CMD:

```bat
set WEB_BIND=0.0.0.0
set BACKEND_BIND=0.0.0.0
docker compose up --build
```

### 4. 常用命令

```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f web
docker compose down
```

如果 Windows / macOS 首次构建时拉取基础镜像较慢，可以先确认 Docker Desktop 已登录并能访问 Docker Hub；也可以配置镜像加速器后再执行 `docker compose up --build`。

## 服务器部署提示

推荐的迁移形态是：Docker Compose 在服务器本机启动前后端，宿主机 Nginx 对外监听 `80/443`，并把 API、SSE 和静态页面分别反代到本机端口。

1. 在服务器安装 Docker、Docker Compose plugin 和 Nginx。
2. 上传代码、根目录 `.env`，以及私有数据目录；如果使用数据库读模型，则确认 `.env` 中数据库变量可用。
3. 在项目根目录执行 `docker compose up -d --build`。
4. 参考 `deploy/nginx/arcadegent.conf.example` 配置站点，把 `server_name example.com` 改成实际域名。
5. 执行 `sudo nginx -t && sudo systemctl reload nginx`。
6. 安全组只需要开放 `80/443`；`8000/8080` 默认只监听 `127.0.0.1`，无需对公网开放。

SSE 事件流依赖长连接，Nginx 配置里 `/api/stream/` 已关闭 buffering，并把 `proxy_read_timeout` 调高；如果再接一层负载均衡，也要保留同样的长连接设置。

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

## 数据说明

公开仓库不包含真实机厅数据、抓取产物、运行缓存或生产数据库迁移。后端只要求运行时提供兼容的数据源：

- JSONL 模式：设置 `ARCADE_DATA_SOURCE=jsonl`，并让 `ARCADE_DATA_JSONL` 指向本地私有 JSONL 文件。
- 数据库模式：设置 `ARCADE_DATA_SOURCE=supabase`，并配置对应数据库连接变量。
- Docker 模式：宿主机 `data/` 会挂载到后端容器的 `/app/data`，适合放置本地私有 JSONL 和运行缓存。

所有密钥只写入本机 `.env` 或部署环境变量，不提交到仓库。

## 常用开发命令

后端测试：

```bash
cd backend
python -m pytest -q
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
- 公开仓库不附带真实数据集；本地体验完整检索能力需要自行准备兼容数据源。
- MCP discovery 依赖第三方服务返回的 tool schema，远端命名变化时可能需要手动指定 `route_tool_name`。
- 没有高德 Web JS key 时，前端仍可列表检索并生成高德跳转 URI，但内嵌地图不可用。
