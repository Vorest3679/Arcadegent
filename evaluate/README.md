# 评测工作台

在线评测已提供独立配置、生产 runtime 执行、多模型重复运行、token/费用报告、LLM 质量判分与人工复核。先编辑 `evaluate/.env`，完整说明见 [ONLINE.md](ONLINE.md)。本工作区已建立 `evaluate/.venv`，可直接从项目根目录运行：

```bash
evaluate/.venv/bin/python -m evaluate validate
evaluate/.venv/bin/python -m evaluate compatibility
evaluate/.venv/bin/python -m evaluate run
```

密钥与环境均不提交 Git；其他机器请按在线说明安装 Python 3.11+ 依赖。

## 确定性测试

这里实现 `docs/plans/vertical-evaluation-workbench.md` 的离线回归入口与证据输出，复用生产 `ProviderAdapter`、`ReactRuntime`、JSONL 查询仓库和在线路线工具。合成数据不对应真实商户，不使用生产数据或模型密钥。

## 运行

需要 Python 3.11+ 和后端 dev 依赖。从项目根目录执行：

```bash
python -m pip install -e './backend[dev]'
python -m evaluate check --suite contracts
python -m evaluate check --suite backend
python -m evaluate check --suite contracts -k native_tool_round_trip
```

`contracts` 运行本模块的确定性检查，自动阻止 socket 外连。`backend` 额外运行现有后端单元与集成测试，其中 MCP 使用本地 fixture 进程。后端目录中仍有历史 Git 忽略的本地测试，所以不同 checkout 的 backend 总数可能不同；`contracts` 的源代码和 fixture 均可提交。项目根 `pytest.ini` 也已收录 `evaluate/checks`。

每次运行创建独立 `evaluate/reports/<run_id>/`，含 `junit.xml`、`pytest.log`、`summary.json` 和 `summary.md`。`--output` 可指定新目录，相对路径相对于项目根目录；已存在目录禁止覆盖。退出码保留 pytest 语义：失败、收集错误或未选中测试均非零。跳过项独立统计，并使报告 `complete=false`。报告保留 Git SHA、已跟踪代码变更 hash、测试与 fixture 文件 hash、Python 版本和逐例耗时。

浏览器沿用前端 Playwright 安装和 pnpm 锁文件，不新增 Node workspace。前端固定使用 `pnpm 10.33.0`，版本写在 `apps/web/package.json` 的 `packageManager` 字段；安装方式见 [pnpm 官方说明](https://pnpm.io/installation)：

```bash
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web test:e2e
```

首次使用需执行 `pnpm --dir apps/web exec playwright install chromium`。浏览器使用模拟地图 SDK、SSE 和 HTTP fixture；未知 `/api/` 请求会使测试失败。测试失败 trace 位于 `apps/web/test-results/`。

`.github/workflows/evaluation-checks.yml` 为相关 PR 和手动运行配置了后端、浏览器检查，并保留报告 14 天；不需要模型或地图密钥。本次仅新增工作流文件，未推送或运行远端 CI。

2026-09-14 本地验证：契约及在线框架检查 **56 项通过**，含这些检查的后端测试 **200 项通过**，Playwright **4 项通过**。后端依赖有 2 条上游弃用警告，无失败或跳过。

## 覆盖范围

| 检查 | 证据与断言 |
| --- | --- |
| 双协议工具往返 | MockTransport 接收真实 adapter 请求；检查第二次请求的原生 call ID、工具结果、推理字段与完整历史 |
| 服务商错误与截断 | 401/429/503、HTML、空对象、数组、超时；结构化失败、无协议偷换、无密钥泄漏；截断保留 usage |
| 工具准备失败 | 未知工具、prepare 异常、非法 JSON；started/failed 配对，保留原始参数与 hydration 证据 |
| Token 与持久化 | 主 Agent/worker 按请求记账；cached/reasoning 不叠加到 input/output；缺失 usage 不伪造为零；序列化保存错误证据 |
| 冻结领域 oracle | 底层存储的 14 项确定性检索契约；在线开发集另有 32 个口语化业务 case |
| 路线工具 | 步行/驾车保留服务端路径及指标；鉴权失败和无路径时不返回估算路线 |
| 既有生产回归 | 原生历史、多轮隔离、取消任务、worker 失败、过期 route、不完整输出和会话归属等，复用 backend 测试 |
| 浏览器 | 列表与 marker、导航链接、渐进 route、刷新后详情恢复、缺少几何时不绘制虚构路线 |

## 与完整评测方案的边界

这些结果只说明确定性契约是否通过。14 项检索检查不是让 Agent 理解自然语言的 14 个 benchmark attempt；fixture 路线不是实时地图成功；模拟 SSE 不测真实服务 TTFT、网络重连或端到端延迟。

在线模块现已提供 model profile 选择、隔离 runtime runner、32 个合成开发 case、独立追加调用与事件证据、请求/工具/时间预算、LLM judge/人工复核、可填价格核算与同名 profile 配对比较。新增回归覆盖真实工具执行、串会话、预算、取消、判分引用、脱敏和计费反例。

32 个用例仍需人工审核；60-case holdout、完整跨层 TraceSink、精确费用硬预算、断点续跑和 live HTTP/SSE/browser runner 尚未实现。真实模型兼容性与质量排名需要填写配置后运行，当前没有伪造的线上成绩。
