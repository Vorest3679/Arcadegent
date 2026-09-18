# Arcadegent Agent 协作说明

本文件是仓库级协作约定，适用于本项目。开始修改前先阅读相关源码、测试和文档；

## 项目与技术栈

Arcadegent 是面向音游机厅检索、Agent 问答和路线建议的全栈应用。

- 后端：Python 3.11+、FastAPI、Pydantic、httpx、FastMCP；pytest 测试。
- Agent：兼容 OpenAI 的模型接口、ReAct runtime、YAML subagent 定义、JSON Schema 工具注册；支持内建工具和 MCP 工具。
- 前端：React 18、TypeScript、Vite、Zustand；marked 与 DOMPurify 处理 Markdown；Playwright 做端到端测试。
- 地图：高德 Web JS API、REST API 和 MCP endpoint。
- 数据与持久化：JSONL 或可选 Supabase 机厅读模型；会话存储使用 Supabase；运行期仍有本地缓存。
- 其他：`scripts/` 包含采集、ETL 和同步脚本；`evaluate/` 是离线契约与在线模型评测工作台；Docker Compose 用于部署。

主要边界：`backend/app/api/` 处理 HTTP/SSE；`backend/app/agent/` 包含编排、上下文、运行时和工具；`backend/app/infra/` 封装外部模型与存储接入；`backend/app/services/` 放应用服务和数据映射；`apps/web/src/api/` 管理 API 访问，`hooks/` 管理会话与流，`stores/` 管理前端状态，`components/` 负责界面与地图展示。

## 修改原则

1. **保持模块低耦合。** 通过明确的 DTO、协议、接口和依赖注入连接模块。业务层不要依赖具体数据库、HTTP 客户端或 UI 组件；前端组件不要自行复制 API、会话状态或地图业务逻辑。避免跨层直接读写内部状态。新增依赖前先确认是否能通过已有边界完成。
2. **保护安全与隐私。** 不提交或输出 `.env`、密钥、Supabase service role key、真实数据、抓取产物、生产数据、会话内容和个人绝对路径。后端密钥不得进入 `VITE_*` 变量或浏览器包；验证外部输入、限制工具权限和外部请求范围，错误信息与日志不得泄露凭据或敏感 payload。涉及 SQL/HTTP/MCP/Markdown 时使用参数化、校验或净化方式。
3. **保持改动聚焦。** 修改前查清调用链和已有约定；同步更新受影响的测试、README 或工程文档。避免无关重构、重复实现和大范围格式化。
4. **较大工程先计划。** 若工作跨多个模块、涉及架构/数据迁移/接口契约，或预计包含多个独立阶段，先整理目标、模块边界、步骤、风险与验收标准，和用户讨论并确认计划后再实施。小型修复可直接开始。

## 查找文档和本地记录

部分本地文档目录被 `.gitignore` 忽略，普通 `rg --files` 或 Git 文件列表可能看不到它们。遇到重要架构、工程决策或排障记录时，先查看仓库内公开文档，再用目录导航与 `ls`、`grep` 检索忽略目录；不要因此把私有材料强行加入版本控制。例如：

```bash
cd docs
ls -la
grep -RniE '关键词|模块名|错误信息' dev-details guidings plans issues instruction 2>/dev/null
cd ..
```

如果目标目录不存在，先用 `ls -la` 查看实际目录。也可用 `git check-ignore -v <路径>` 判断忽略规则。`docs/plans/`、`docs/issues/`、`docs/instruction/`、`docs/others/` 属于本地归档范围，按现有文档政策处理。公开且适合复用的工程说明写入 `docs/dev-details/`，使用指南写入 `docs/guidings/`，并按需更新 `docs/README.md`。记录问题现象、根因、修复方案、影响边界和验证结果；去除密钥、私有数据、生产批次和绝对路径。

## 开发、排障与验证

- 后端测试从仓库根目录运行：`python -m pytest`；聚焦时可运行 `python -m pytest backend/app/tests/path/to/test_file.py`。`pytest.ini` 还包含 `evaluate/checks` 和 `scripts/etl/tests`。
- 前端命令在 `apps/web/` 执行：`npm run build`、`npm run test:e2e`。E2E 使用 `apps/web/playwright.config.ts`，启动本地 Vite 服务并保留失败 trace。
- 前端行为、布局、路由、请求/响应、SSE 或地图交互有改动时，使用 Playwright 或可用的浏览器操控能力做端到端验证，从用户操作一路检查到界面结果和相关网络/后端行为。不能只凭构建通过判断正确。若真实第三方地图/模型凭据不可用，明确记录替代验证范围，不伪称完成了真实服务验证。
- 按改动范围运行必要的测试：后端改动跑相关 pytest；前端改动至少跑构建和相关 Playwright 用例；接口或跨层改动同时验证两端及相关集成测试。报告实际执行的命令和结果。
- 同一问题连续尝试修复三次仍未解决时，暂停盲目改动，检查调用链、状态转换、输入输出和复现条件；在最接近故障边界的位置加入临时日志作为断点，日志只记录诊断所需且已脱敏的信息。定位并修复后立即删除临时日志，再运行回归验证并检查 diff，确保没有调试输出残留。
- 重要 debug、工程或技术结论要及时记录到合适的文档位置。记录应能帮助后来者复现诊断思路和理解模块边界；不要记录秘密值或不必要的用户数据。

## 分支、提交与审查流程

1. 在与工作类型对应的分支开发，例如 `feat/<简述>`、`debug/<简述>` 或 `fix/<简述>`；不要直接在 `main` 上开发。
2. 完成代码和相关测试后，先提供改动摘要、验证结果与 diff，交由用户审查。用户确认代码无问题且测试正常后，再将开发内容提交到对应分支。
3. 提交后等待二次审查；根据审查意见修正并重新验证。二次审查通过后再发起 PR，将对应分支合入 GitHub 主分支。
4. 不跳过审查门槛、不把未验证改动合入主分支。提交信息应说明改动目的；PR 描述包含行为变化、测试证据及已知限制。
