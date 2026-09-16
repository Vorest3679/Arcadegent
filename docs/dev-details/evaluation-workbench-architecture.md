# Evaluate 评测工作台：架构与实现说明

`evaluate/` 是 Arcadegent 的评测工作台。它不实现另一套 Agent，而是以**受控配置、冻结数据、隔离依赖和可复核证据**包裹生产 `ProviderAdapter`、`ReactRuntime` 与工具注册链路，用来回答两个不同的问题：

1. 生产链路的协议与业务契约是否回归；
2. 在同一份冻结用例、数据和预算下，不同模型是否能可靠完成任务。

因此，离线回归通过不等于模型质量高；在线模型结果也不等于真实 HTTP/SSE/浏览器端到端体验。三者必须分开解读。

## 架构总览

```text
                         case YAML + 冻结 JSONL 数据
                                      │
                                      ▼
                            evaluate.config.load
                       校验 schema、profile、oracle 与预算
                                      │
                 ┌────────────────────┴────────────────────┐
                 ▼                                         ▼
     离线契约回归（check）                         在线模型评测（run）
  pytest + mock / fixture                         每个 profile 的顺序队列
                 │                                         │
                 ▼                                         ▼
   JUnit / JSON / Markdown 报告             隔离 container + 生产 runtime
                                               │        │          │
                                               ▼        ▼          ▼
                                          记录 provider  记录事件  轮次快照
                                               │        │          │
                                               └────────┴──────────┘
                                                            │
                                                            ▼
                                      硬约束判分 → 可选 LLM judge / 人工复核
                                                            │
                                                            ▼
                                     JSONL 证据、汇总、token/费用与图表报告
```

在线执行刻意复用后端的 runtime 和工具，而非在 `evaluate/` 里模拟“更聪明”的查询策略。评测容器则替换会话存储、provider、回放缓冲和外部依赖配置，以保证运行不读生产 `.env`、不连接生产 MCP 或会话库，也不把评测会话写入生产系统。

## 两条执行路径

### 1. 离线契约回归

入口为 `python -m evaluate check`。`evaluate/__main__.py` 调用 pytest，并将 `contracts`（仅 `evaluate/checks`）或 `backend`（额外包含后端测试）保存为一次不可覆盖的报告目录。

这一层关注确定性、可在 PR/CI 运行的证据，例如：

- 两种 provider 协议的原生 tool-call 往返、call ID 和 usage 保留；
- 鉴权、限流、超时、截断与非法响应的结构化失败语义；
- 工具参数准备失败、worker 调用、token 归属与持久化；
- 冻结查询 oracle、路线服务几何及失败时不伪造估算路线；
- 在线 runner 自身的隔离、预算、脱敏、复核和报告反例。

它的输出含 `junit.xml`、`pytest.log`、`summary.json` 和 `summary.md`，另记录 Git SHA、未提交变更 hash、测试/fixture 指纹和逐例耗时。退出码沿用 pytest；任何失败、收集错误、无选中用例或跳过项都会使报告不是完整通过。

### 2. 在线模型评测

入口为 `python -m evaluate validate`、`compatibility`、`run`、`grade`、`review` 与 `compare`。推荐顺序是先 `validate` 查看脱敏后的计划与缺失配置，再以 `compatibility` 用两次真实请求验证被测服务能完成原生工具调用，再执行 `run`。

`run` 按 `profile × case × repeat` 展开 attempt：不同 profile 并发，各 profile 内部按 case/repeat 顺序执行，使同模型时间线可复现。每个 attempt 都创建新内存 session、新地理缓存目录和新 container；一个多轮 case 才在自己的 session 内继承上下文。Agent 请求总预算由本次 run 共享，同时还有每 attempt 调用数、工具数、超时和请求间隔限制；judge 另有独立预算。

`compatibility` 不是业务成功率测试。它只验证 provider 能提出指定工具调用，并在第二次请求中使用不可预测的工具返回值；这可以尽早发现协议、tool-call ID 或消息格式不兼容。

## 文件职责

| 位置 | 职责 | 关键实现要点 |
| --- | --- | --- |
| `evaluate/__main__.py` | 离线 CLI 与报告包装器 | 选择 pytest suite、创建不可覆盖目录、汇总 JUnit，并写入可追溯元数据。非 `check` 命令转交在线 CLI。 |
| `evaluate/config.py` | 在线配置与 case 合约 | 只读取 `evaluate/.env` 和 `EVAL_*` 环境变量；Pydantic 校验 profile、用例、坐标、oracle ID 与参数断言，拒绝覆盖协议控制字段。 |
| `evaluate/online.py` | 在线编排、证据和报告 | 展开矩阵、调度 profile 队列、运行 attempt/judge、脱敏追加 JSONL、生成汇总和 SVG 图表。 |
| `evaluate/environment.py` | 生产 runtime 的评测隔离层 | 注入内存会话库、`RecordedProvider`、`RecordedReplay` 和空 MCP gateway；直接构造 `Settings`，避免 `Settings.from_env()` 带入生产依赖。 |
| `evaluate/scoring.py` | 硬约束判分与用量/费用聚合 | 校验工具证据、门店集合/顺序、artifact 新鲜度、路线来源与几何；质量评分不能覆盖硬失败。 |
| `evaluate/review.py` | 人工复核与已保存 run 对比 | 复核采用全量校验后的追加写入；仅裁定质量。对比要求数据、case 和地图模式指纹一致，再按 profile/case/repeat 配对。 |
| `evaluate/build_benchmark.py` | 开发集构建与来源审计 | 从经审核的本地快照生成正式 YAML，并核对门店、机种和来源 hash，避免直接修改生成物造成 oracle 漂移。 |
| `evaluate/datasets/public/*.yaml` | 可版本化 case 与 oracle | `smoke.yaml` 面向合成 smoke；`benchmark.yaml` 是冻结开发集；历史合成集仅作归档，不能用于新排名。 |
| `evaluate/fixtures/arcades/*.jsonl` | 确定性合成数据 | 供离线/烟雾测试使用，不把它当作实时或生产数据。 |
| `evaluate/checks/` | 评测器自身的回归测试 | 对 provider、runtime 证据、领域 oracle、在线编排和报告的反例建立防线。 |
| `evaluate/README.md`、`evaluate/ONLINE.md` | 操作说明和口径 | 前者说明离线/整体入口，后者说明在线配置、用例、judge、人工复核与费用边界。 |

`evaluate/reports/`、私有数据、缓存和 `.env` 均被 Git 忽略：报告可能包含提示词、工具结果或本地业务信息，不能作为公开文档或提交物。

## `online.py`：在线评测编排器

`evaluate/online.py` 是在线路径的总编排器。它将“模型配置 × 测试用例 × 重复次数”转换成彼此隔离的 attempt，调用生产 runtime，并把每次尝试的过程、判断和资源消耗沉淀为可复核报告。

```text
load config
    │
    ▼
plan：展开 profile × case × repeat，写入版本和预算指纹
    │
    ▼
run：不同 profile 并发；同一 profile 顺序执行
    │
    ▼
run_attempt：隔离 container/session → 逐轮 ReactRuntime.run_chat
    │                                      │
    │                                      ├─ RecordedProvider / RecordedReplay 写证据
    │                                      └─ 每轮快照交给 grade_turn 硬判分
    ▼
judge（可选）：基于裁剪后的工具事实与最终答复评质量
    │
    ▼
report：汇总通过率、失败原因、耗时、token、费用和图表
```

### 执行计划与并发模型

`plan(config)` 生成模型、用例数、重复次数、请求/工具/超时预算、地图和 judge 状态，以及 data/case SHA256。它是一次运行的环境快照，供后续复现和对比时核验。

`run(config, evidence)` 先完整展开 `profile × case × repeat`。所有计划项都会保存：即使模型缺配置、总预算耗尽或导航未启用 live map，也以 `not_run` 连同原因留在报告中，而不是从分母中静默消失。

调度方式是“**不同 profile 并发，同一 profile 内串行**”。前者减少多模型评测的墙钟时间，后者保持单个模型的 case 顺序、累计耗时和预算消耗易于解释。Agent 请求预算是本次 run 共享的全局上限；judge 使用独立预算，不能挤占 Agent 的调用配额。

### 单个 attempt 的生命周期

`run_attempt(...)` 代表一个模型完成一个 case 的一次独立尝试。它为 attempt 新建 container、内存 session、地理缓存目录和快照目录；只有同一个多轮 case 会继承自己的 session，模型之间、case 之间、repeat 之间都不共享状态。

每个 turn 的处理顺序为：

1. 记录本轮前的 session 边界；
2. 调用生产 `ReactRuntime.run_chat()`；
3. 序列化 session state 与最终响应 DTO，写入 turn snapshot；
4. 将 snapshot 交给 `grade_turn()`，检查业务结果与工具证据；
5. state 未完成时停止后续轮次，并把 attempt 标为失败。

attempt 受总请求数、单 attempt 调用数、工具调用数、请求间隔和整体超时共同约束。超时、取消、预算耗尽或普通异常都不会丢弃已经获得的事实：`finally` 中仍会保存耗时、usage 和部分 session 快照；中断的剩余计划项也会以未运行状态落盘。

### 关键辅助路径

- `Evidence` 是唯一的报告写入边界。它为每条 JSONL 记录添加 schema version、序号、UTC 时间与 record ID，并递归脱敏认证字段、配置密钥和 URL query 中的 token/key。
- `compatibility(...)` 是协议探针，而不是业务基准。它用两次真实 provider 请求检验模型能否提出指定工具调用，并在第二次请求中消费不可预测的工具返回值，从而尽早暴露 tool-call ID、消息格式或 provider 协议不兼容。
- `judge(...)` 仅将最终答复和裁剪后的真实工具事实交给质量模型，不传递嵌套的原始 transcript。judge 必须返回有效分数、理由及已有 evidence ID；高分不能覆盖硬规则失败。
- `report(...)` 读取已追加的调用记录，按 attempt/profile 归因 Agent 与 judge token、已确认费用和未定价请求，生成 JSON、Markdown 与累计加权通过分/成本图表。

这个模块最重要的判断原则是：回答措辞合理并不等于通过。在线结果必须同时满足当前轮工具记录、冻结 oracle、artifact 新鲜度及（对导航）路线来源和几何约束；provider 未返回完整 usage 或未填写价格的调用则明确标为未知，不能误记为免费。

## Case 与 oracle 的表达方式

每个 case 有 `id`、`group`、能力标签和一至多轮 `turns`。`Turn` 同时表达用户输入与可验证结果；目标是限制**交付结果**，而不是强迫模型走某一段思维链或精确查询文本。

| 字段 | 含义 |
| --- | --- |
| `shop_ids` | 要求结果集合完全相等；`ordered: true` 时顺序也相等。 |
| `required_shop_ids` / `forbidden_shop_ids` | 对开放结果集施加必须包含/排除约束。 |
| `ordered_prefix` | 只固定前几项，适合“最近的应排第一”这类要求。 |
| `required_tools` / `forbidden_tools` | 约束实际执行工具证据，而非回答里是否提到工具。 |
| `tool_argument_assertions` | 对某次工具 raw 或 prepared 参数作局部匹配；允许 runtime hydration/default 后的多种合理路径。 |
| `route_*` | 导航模式、GCJ02 起终点与路线约束；要求实际 provider 路线，禁止离线估算冒充结果。 |
| `answer_contains` / `answer_not_contains` | 只用于稳定、明确的文本要求，避免将文风误当作能力。 |

`grade_turn()` 从当前轮开始的 session state、工具记录、working-memory artifact 和最终 DTO 中取证。它会检查结果是否来自本轮成功工具调用、artifact 是否新鲜、路线是否来自 `route_plan_tool`，所以“回答看起来合理”不能绕过错误门店、陈旧结果或虚构路线。

## 证据、判分与报告

在线运行会在单独报告目录中保存：

- `manifest.json`：脱敏配置、代码/数据/case 指纹、Git SHA 和价格输入；
- `plan.json` 与 `attempts.jsonl`：计划矩阵及每项状态、快照和硬判分结果；
- `requests.jsonl`、`calls.jsonl`、`events.jsonl`：请求、响应/错误、usage 和独立事件账本；
- `snapshots/`：每轮 DTO、session state 和未完成 attempt 的部分证据；
- `scores.jsonl`、`summary.json`、`summary.md`：judge/人工质量判定和汇总；
- token、费用、耗时明细，以及“加权完整通过分—耗时/平均费用”图表。

证据写入采用追加 JSONL，并给每条记录 `record_id`、序号和 UTC 时间。写入前会清理认证字段、环境中的密钥值和 URL query 中的 key/token。`RecordedProvider` 无论调用成功或抛错，都会在 `finally` 写入 call 记录；`RecordedReplay` 独立于面向前端的有界 ReplayBuffer 保存事件，避免业务回放清理造成评测证据缺失。

判定分为两层：

1. **硬约束**：必须通过 `grade_turn()` 的数据、工具、artifact 和路线校验。
2. **质量**：可选 judge 只读取裁剪后的工具事实与最终答复，按事实准确、完整、清晰和无臆造评分；引用必须指向现有 evidence ID。也可导出模板后由人工复核。

完整通过要求硬约束通过，且若启用 judge，则质量也通过。人工或 LLM 的高分都不能覆盖硬失败。费用仅对 provider 返回完整 usage 且配置了单价的调用核算；缺 usage 或价格显示为未知，缓存/推理 token 是总 token 的子集，不能重复加总。

## 隔离与安全边界

- 评测不加载项目根 `.env`，dotenv 不插值，也不会修改进程环境。
- 会话只在内存中保存；MCP server 列表为空；默认地图关闭。导航仅在显式启用 live map 且提供评测专用 key 时执行。
- 模型 provider 协议必须显式选 `chat_completions` 或 `responses`；失败不会静默切换协议。
- 数据、case 和代码 hash 写入 manifest；比较两个 run 前必须确认数据、case、地图模式一致。
- 报告和私有 fixture 默认不提交。公开说明只描述架构和口径，不引用真实运行结果或私有数据路径。

## 当前覆盖与边界

已落地的重点是生产 runtime 复用、冻结数据查询、协议兼容探针、请求/工具预算、超时取消、独立事件证据、硬规则、可选 judge/人工复核、token/费用归因以及保存结果对比。

以下能力仍不应从当前结果中推断出来：

- 当前没有 live HTTP/SSE/browser runner，Playwright 仍是独立的模拟前端测试；因此不报告真实 TTFT、网络重连或用户端到端延迟。
- 开发集不是私有 holdout，新增或变更 oracle 需要领域审核；真实模型排名应基于冻结版本和重复运行审阅。
- 价格是事后核算，不是精确费用硬上限；超时或供应商未返回 usage 的消耗必须保留为未知。
- 不支持断点续跑；中断运行会落盘已有部分证据，重新执行会生成新 attempt。

## 维护顺序

1. 先为协议、工具、artifact 或路线改动补充/更新 `evaluate/checks`，保证离线回归可解释。
2. 再修改 `build_benchmark.py` 的经审核目标并重新生成开发集和来源指纹；不要直接修改正式生成 YAML。
3. 用 `validate` 和 `compatibility` 验证新增 profile，随后再运行小规模 smoke。
4. 进行模型比较时固定 case/data/map mode、模型配置和 repeat；对比输出中的未配对项不应被忽略。
5. 对质量门槛和 judge 先做人审校准，再把结果用于发布判断。

具体命令和环境变量见 [评测工作台 README](../../evaluate/README.md) 与 [在线评测说明](../../evaluate/ONLINE.md)。
