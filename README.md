# Arcadegent

Arcadegent 是一个面向机厅检索、问答和路线建议的 Agent 应用。当前仓库包含 FastAPI 后端、React + Vite 前端、Bemanicn 抓取与 ETL 脚本，以及一套基于本地 JSONL 的查询与会话运行时。

## 当前能力

- Agent 对话：支持检索、附近推荐、路线规划三类意图，并通过 SSE 推送阶段切换、工具执行和回复流。
- 机厅检索：支持关键词搜索、地区级联筛选、机种数量排序和门店详情查看。
- 会话管理：支持历史会话列表、单会话详情和删除；会话会落到本地 JSON 文件，可跨服务重启保留。
- 导航集成：路线规划现在支持 `高德 MCP -> 高德 REST -> 离线估算` 的逐级回退。
- 数据处理：提供抓取脚本、ETL 规范化脚本和 QA 产物输出。

## 技术栈

- Backend: Python 3.11+, FastAPI, Pydantic, FastMCP
- Frontend: React 18, TypeScript, Vite
- Data: 本地 JSONL 读模型，附带 ETL 产物与 Supabase migration 草案
- Integration: OpenAI-compatible LLM provider、高德地图 MCP Server、高德 REST 路线 API

## 目录结构

```text
backend/              FastAPI 后端与 Agent 运行时
apps/web/             React + Vite 前端
scripts/              数据抓取与辅助脚本
scripts/etl/          ETL 脚本与测试
data/raw/             原始数据
data/processed/       ETL 产物
supabase/migrations/  数据库迁移草案
docs/                 计划、交接和开发说明
```

## 环境要求

- Python `>=3.11`
- Node.js `>=18`
- npm `>=9`

## 本地开发环境

以下命令默认在仓库根目录 `Arcadegent/` 执行。这个仓库最初主要按 Windows 环境写过说明，下面的步骤已经补成了 macOS / Linux / Windows 都可照着执行的版本。

### 1. 创建后端虚拟环境并安装依赖

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

### 3. 配置环境变量

后端启动时会自动读取仓库根目录下的 `.env`。建议先复制示例文件：

macOS / Linux:

```bash
cp backend/.env.example .env
```

Windows PowerShell:

```powershell
Copy-Item backend/.env.example .env
```

一个常见的最小配置如下：

```dotenv
APP_ENV=dev
ARCADE_DATA_JSONL=data/raw/bemanicn/shops_detail.jsonl

LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

MCP_AMAP_ENABLED=true
MCP_AMAP_BASE_URL=https://mcp.amap.com/mcp
MCP_AMAP_ROUTE_TOOL_NAME=

AMAP_API_KEY=
AMAP_BASE_URL=https://restapi.amap.com
MCP_AMAP_API_KEY=
```

说明：

- `ARCADE_DATA_JSONL` 是后端实际读取的数据源，默认指向 `data/raw/bemanicn/shops_detail.jsonl`。
- 未配置 `LLM_API_KEY` 时服务仍可启动，但 Agent 对话会明显退化，联调前建议配置。
- `MCP_AMAP_ENABLED=true` 时，后端会启用 FastMCP Client 并在启动时探测高德 MCP tools。
- `MCP_AMAP_ROUTE_TOOL_NAME` 可选。留空时后端会在启动时自动挑选 route tool；如果自动识别不准确，可以手动指定。
- `AMAP_API_KEY` 会被 MCP 和 REST fallback 共同复用；只配这一项就可以。
- `MCP_AMAP_API_KEY` 现在只是可选覆盖项。只有在你想让 MCP 和 REST 使用不同 key 时才需要单独填写。
- 当前推荐路径是高德官方 `Streamable HTTP` MCP endpoint，不需要额外安装 Node.js MCP server。

### 4. 启动后端 API

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

`/health` 现在会额外返回 `mcp.amap` 状态，包括：

- 是否启用 MCP
- 是否完成配置
- 当前发现到的 tools
- 自动或手动选中的 route tool
- 最近一次错误信息

如果高德 MCP 接通但没有成功选中路线工具，先看 `/health` 里的 `mcp.amap.available_tools`，再把正确的名字写回 `MCP_AMAP_ROUTE_TOOL_NAME`。

### 5. 启动前端

推荐在 `apps/web/.env.local` 里写：

```dotenv
VITE_API_BASE=http://localhost:8000
```

也可以直接用命令行临时设置：

macOS / Linux:

```bash
cd apps/web
VITE_API_BASE=http://localhost:8000 npm run dev
```

Windows PowerShell:

```powershell
cd apps/web
$env:VITE_API_BASE = "http://localhost:8000"
npm run dev
```

打开：`http://localhost:5173`

## 接口概览

- `GET /health`：健康检查、数据加载状态、AMap MCP 状态
- `GET /api/v1/arcades`：机厅列表、筛选、排序
- `GET /api/v1/arcades/{source_id}`：机厅详情
- `GET /api/v1/regions/provinces`：省份列表
- `GET /api/v1/regions/cities`：城市列表
- `GET /api/v1/regions/counties`：区县列表
- `POST /api/chat`：Agent 对话入口
- `GET /api/v1/chat/sessions`：会话列表
- `GET /api/v1/chat/sessions/{session_id}`：会话详情
- `DELETE /api/v1/chat/sessions/{session_id}`：删除会话
- `GET /api/stream/{session_id}`：SSE 实时事件流

## 数据处理

如果需要把原始 JSONL 规范化并生成 QA 产物，可以执行：

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

说明：

- 当前本地 API 默认直接读取 `ARCADE_DATA_JSONL` 指向的 JSONL 文件。
- ETL 的主要价值是做规范化、质量校验和中间产物沉淀，不是当前 API 的唯一前置步骤。

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

## 当前限制

- 机厅查询目前使用本地 JSONL 读模型，不是数据库在线查询。
- 高德 MCP tool 的自动识别依赖启动时 discovery；如果第三方 tool 命名发生变化，可能需要手动设置 `MCP_AMAP_ROUTE_TOOL_NAME`。
- 在没有 `AMAP_API_KEY`，且也没有单独提供 `MCP_AMAP_API_KEY` 的情况下，路线规划会退化为离线估算。
