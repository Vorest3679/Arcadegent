# 在线评测

评测器直接调用生产 `ProviderAdapter` 和 `ReactRuntime`，复用生产工具注册与查询逻辑；每个 attempt 有独立内存会话和地理缓存。主 Agent 和 worker 使用同一被测 profile。不会加载项目根 `.env`、Supabase 或生产 MCP 配置，也不会向生产数据库写入会话。

## 1. 填写模型配置

使用 Python 3.11+，在项目根目录安装后端依赖并初始化配置：

```bash
python -m pip install -e './backend[dev]'
python -m evaluate init-env
```

`evaluate/.env` 已为本工作区创建，可直接编辑。首先填写：

本工作区另已准备好 `evaluate/.venv`，无需使用项目原来的 Python 3.9 环境；可先 `source evaluate/.venv/bin/activate`，或把下文 `python` 替换为 `evaluate/.venv/bin/python`。

```dotenv
EVAL_LLM_API_KEY=你的密钥
EVAL_LLM_BASE_URL=https://你的服务地址/v1
EVAL_LLM_MODEL=账号实际支持的模型ID
EVAL_LLM_API_MODE=chat_completions
```

Responses 服务使用 `EVAL_LLM_API_MODE=responses`。base URL 不包含 `/chat/completions` 或 `/responses` 后缀，不在 URL 中放 key。协议显式选择，失败不静默切换。不同服务支持的 model ID、参数和账号可用性应以你的供应商配置为准。

若服务不接受 temperature，设置 `EVAL_LLM_SEND_TEMPERATURE=false`。需要 `max_completion_tokens` 时修改 `EVAL_LLM_TOKEN_PARAMETER`。思考配置等可通过 `EVAL_LLM_EXTRA_PARAMETERS` 填入 JSON 对象，不能覆盖 tools、messages、model 等核心协议字段。没有思考参数的默认 profile 不代表强制关闭供应商默认思考模式。

shell 中已有的 `EVAL_*` 变量优先于此文件；其他生产变量不读取，dotenv 不插值、不修改进程环境。`.env` 被 Git 忽略，初始化时权限为 `0600`，重复初始化不会覆盖已有内容。

## 2. 检查并运行

```bash
python -m evaluate validate
python -m evaluate compatibility
python -m evaluate run
```

`validate` 不调用模型，展示脱敏计划与缺配置的 profile；缺配置返回退出码 2。`compatibility` 每个配置完整的 profile 最多发起两次真实请求，检查原生工具调用往返，要求回答使用工具返回的随机标记；它是基础兼容性探针，不代表全部工具能力通过。`run` 会产生真实 API 消耗，默认运行 3 个检索 smoke case。建议先完成兼容性探针再跑业务用例；业务运行本身也会保存协议失败。

不同模型的队列默认并发执行，同一模型内部仍按 case 顺序串行，保证其 attempt 时间线可复现；每次模型请求前间隔 1 秒。Agent 请求预算仍是所有并发队列共享的全局上限，默认 40 次；每 attempt 最多 40 次模型请求及 40 次工具调用（主 Agent 与 worker 共享预算）。输出 token 上限为每次请求 1024。每 attempt 300 秒超时，取消在途 async 请求并保存部分证据；供应商可能已产生消耗而未返回 usage，这部分明确记为未知。全量 38 case × 4 模型 × 1 repeat 如需每个 attempt 都允许用满 40 次请求，设置 `EVAL_MAX_REQUESTS=6080`；这是最坏情况下的次数上限，并非实际请求数或费用硬上限。价格只用于事后核算。

在线报告保存到 `evaluate/reports/<run_id>/`，不会覆盖旧目录：

- `manifest.json`：配置、协议、代码/data/case 指纹、价格输入（密钥脱敏）。
- `plan.json`、`attempts.jsonl`：全部计划项及执行结果；缺配置、地图不可用、预算未启动项保留为 `not_run`。
- `requests.jsonl`、`calls.jsonl`：请求开始、响应、错误、原始/标准化 usage；主 Agent、worker、judge 的调用可定位。
- `events.jsonl`：独立追加事件，不受业务 ReplayBuffer 重置/容量影响。
- `snapshots/`：各轮公共 DTO、会话状态与未完成会话的部分证据。
- `scores.jsonl`、`summary.json`、`summary.md`：硬约束、质量评分与按模型 token/费用汇总。
- `model-cost-summary.json`：每模型的已知 token、已确认价格和每 task 均价；`weighted-score-vs-average-task-cost.svg`：加权完整通过分与每 task 均价的散点图。

报告保存必要提示词、工具结果和回答供复核；如果换成私有案例，这些本地报告也是私有数据。API key、认证字段和 URL query key 会脱敏。报告与私有数据目录被 Git 忽略。

`run` 只有所有计划项硬约束通过，并且启用 judge 时所有质量评分通过，才返回 0；未运行、执行失败或未完成评分返回 1。运行中 Ctrl+C 返回 130 并落盘部分结果。模型重复执行不复用回答；目前不支持断点续跑，重新运行会创建全新 attempt。

## 3. 多模型与完整开发集

模板中预留 DeepSeek、Luna、Kimi、MiMo 的独立变量。分别填写对应的 `API_KEY`、`BASE_URL`、`MODEL` 和 `API_MODE`：

```dotenv
EVAL_MODELS=deepseek,luna,kimi,mimo
EVAL_DEEPSEEK_API_KEY=...
EVAL_DEEPSEEK_BASE_URL=...
EVAL_DEEPSEEK_MODEL=...
```

其他 profile 使用相同前缀规则。价格、温度、鉴权头、额外参数也可设置成 `EVAL_KIMI_*` 等形式；这些字段不继承 default profile 的值。可只填一个模型，或用 CLI 选择子集：

```bash
python -m evaluate run --models deepseek,kimi --cases datasets/public/benchmark.yaml --repeat 3
```

所有数据/用例配置中的相对路径以 `evaluate/` 为基准；`--output`、`--run`、`--env-file` 的相对路径以当前目录为基准。

v4 开发集包含 38 个真实本地数据 case：32 检索、4 导航、2 健壮性，含换城市多轮查询。使用时必须同时设置 `EVAL_CASES=datasets/public/benchmark.yaml` 和 `EVAL_DATA=../data/local/arcades.geocoded.sample.jsonl`。该数据有 3650 家门店，只有 15 家有坐标；距离比较仅限题目指定且有坐标的沪京门店，路线端点使用本地 GCJ02，浏览器定位使用 WGS84。其他城市不编造距离或实时营业状态。

24 个主要目标覆盖广州、深圳、南京、济南、武汉、合肥、杭州、天水，另有 5 个沪京坐标目标。`benchmark.sources.json` 记录目标与数据 SHA256；运行 `evaluate/.venv/bin/python -m evaluate.build_benchmark` 会核对 processed 门店、机种名称与数量后重新生成。测试会检测来源文件或已生成用例漂移，数据更新时需要重新审核 oracle。

旧版虚构商户与错误地标假设保留在 `benchmark-v3-synthetic.archive.yaml`，仅供解释历史报告，不用于新排名。`smoke.yaml` 仍配合合成 fixture 做契约检查。真实开发集也尚未经领域负责人审核，不能当私有 holdout；本地没有机种记录不代表现实中绝对没有该机种。每轮独立检查返回实体、排序、工具证据和 artifact 新鲜度，允许满足同一结果的不同查询路径，不再把某个精确 title 参数字符串当成唯一正确解。

当前口语场景包括“天水有没有机厅玩的”、南京大风旧称与趣玩汇新称、宝安壹方城与龙华壹方天地消歧、只比较指定门店的“这里附近舞萌在哪”、跨城市切换，以及同名门店排除。修改正式开发集时编辑 `evaluate/build_benchmark.py` 中的问题和目标，再生成 YAML 和来源清单；不要只改 YAML 导致来源审核失效。

新增用例时，优先写清楚可验证的业务约束，而不是把模型的唯一思考路径写死：

```yaml
- id: user-phrase-with-oracle
  group: retrieval
  description: 为什么这个口语问题容易出错
  capabilities: [title-alias, entity-disambiguation]
  turns:
  - message: 杭州奥体印象城星际传奇有舞萌吗？金沙和西溪这次先不看。
    shop_ids: [1342]
    required_tools: [db_query_tool]
```

`shop_ids` 要求返回集合完全相等，适合稳定、封闭的结果集。`required_shop_ids` 和 `forbidden_shop_ids` 适合含同名或较远干扰项的口语题；`ordered_prefix` 只固定最前几家。`tool_argument_assertions` 是某次工具调用的局部匹配：`raw` 评模型原始参数，`prepared` 评 hydration/default 后的实际执行参数。它不限定 main agent 是否经过 worker，也不限定额外的无害工具调用。`answer_contains` 和 `answer_not_contains` 只用于明确、稳定的文本要求，避免把某一种措辞误判为能力。

导航默认未运行。确需真实高德路线时设置：

```dotenv
EVAL_MAP_MODE=live
EVAL_AMAP_API_KEY=高德Web服务密钥
```

导航用例明确给出 GCJ02 起终点，检查真实 REST 结果、模式、当前轮来源、路径几何和起终点容差 100 米；不生成离线估算。在线地图测量受外部服务影响，应与纯冻结查询结果分组审阅。其他地理补全不访问线上 geocoding。

## 4. LLM 质量评分、重评和人工复核

```dotenv
EVAL_JUDGE_ENABLED=true
EVAL_JUDGE_API_KEY=评分模型密钥
EVAL_JUDGE_BASE_URL=https://评分服务/v1
EVAL_JUDGE_MODEL=评分模型ID
EVAL_JUDGE_API_MODE=chat_completions
EVAL_JUDGE_MAX_REQUESTS=20
EVAL_QUALITY_THRESHOLD=75
```

judge 可以使用与被测模型不同的供应商。每个有结果的 attempt 最多判一次，独立预算和 token 记账；主判分 rubric 为事实准确性 40、完整性 30、清晰度 20、无臆造 10。非法 JSON、超范围分数、空理由或伪造证据引用均判为 grader error。启用前应先用人工样本校准，75 是可调起始阈值。

未启用 judge 时只报告硬约束通过，质量和完整成功不会伪造为通过。judge 高分不能覆盖错误机厅、过期 artifact 或虚构路线等硬失败。

```bash
python -m evaluate grade --run evaluate/reports/某次运行
python -m evaluate review export --run evaluate/reports/某次运行
# 编辑生成的 review-template.jsonl：填写 quality_pass、reviewer、reason。
python -m evaluate review import --run evaluate/reports/某次运行 --file 我的复核.jsonl
python -m evaluate compare --baseline evaluate/reports/基线 --candidate evaluate/reports/候选
```

`grade` 只读取旧结果调用 judge，不重跑 Agent，追加评分日志后刷新自动汇总；重新判分仍单独消耗请求和 token。人工回填整批校验后追加到 `human_reviews.jsonl`，同一 attempt 以最新复核为准，单独生成 `human_summary.json`；保留自动评分，不修改硬约束。

比较要求 dataset、case、地图模式一致，按同名 profile/case/repeat 配对并报告回退、改善及未配对数量；不自动给出置信区间或显著性判断。

## 5. 费用与验证边界

每个 profile 可填写每百万 token 的 input/output/cached input 价格。缺价格或 usage 不完整时费用为 `null`；不同模型请使用相同币种。缓存与 reasoning 是子集，不能再次叠加到 total。Agent 和 judge 的使用量分开报告；每次实际请求都保留，不把失败请求自动当免费。judge 单次成功评分的费用保存于 `scores.jsonl`。

本次测试使用 HTTP MockTransport + 真实生产 runtime/工具完成全链路，没有填写或使用你的真实供应商密钥。这个在线 CLI 已具备实际请求能力，但真实模型质量仍需你填好配置后运行得到。现有 Playwright 测试继续验证模拟 UI；这里不提供在线 HTTP/SSE/browser runner，也不把 runtime 耗时当成 UI 延迟或 provider TTFT。
