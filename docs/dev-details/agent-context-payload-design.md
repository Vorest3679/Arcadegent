# Agent Context Payload Design

## 文档目的

记录 Arcadegent 在 agent 编排、tool 设计、context 组织上的长期技术约束。

这份文档不描述一次性 patch，而描述后续迭代都应遵守的设计哲学与落地方式。

## 设计哲学

### 1. 遵循 Unix 哲学

模块化，每个程序应该负责其独有的功能。

在本项目中，对应的工程约束是：

- `tool` 负责执行、查询、计算、格式化等确定性工作。
- `agent` 负责基于上下文、skills、tool observation 进行推理和生成。
- `ContextBuilder` 负责组织模型可消费的上下文，不负责替模型做模板式回答。
- `skill` 负责告诉模型“该怎么看结构化结果”，而不是在代码里写死回答模板。

直接推论：

- 不应在 `tool` 内部再次调用 LLM 形成“AI 套 AI”。
- 不应让某个模块同时承担 execution、reasoning、presentation 三种职责。
- 当某块数据过重或语义层次复杂时，应拆成目录和细节块，而不是继续往单一 payload 里堆字段。

### 2. Build LLM-served agent, not template agent

目标是让 LLM 服务于 agent，而不是把 agent 退化成模板拼接器。

在本项目中，对应的工程约束是：

- 最终回答应由 agent 基于结构化上下文生成，而不是依赖 `summary_tool` 这类模板式摘要器兜底。
- 模板只能用于 deterministic formatting，不能成为主推理路径。
- prompt 与 skill 应告诉模型“如何读取数据、如何组织回答”，而不是把所有回答形态提前写死。
- 当模型容易抓不到重点时，应优化上下文结构，而不是继续堆模板条件分支。

## 当前落地

### 1. summary 从 tool 回到 agent

当前实现中：

- `summary_tool` 保留为兼容性 deterministic formatter。
- 主回答路径由 `summary_agent` 完成。
- `search_agent` / `navigation_agent` 不再依赖 `summary_tool` 来完成最终用户回复。

这保证了：

- tool 仍然是工具，不是隐藏的二次 LLM 入口。
- 总结质量由 agent + skills + context 共同决定，而不是由固定模板决定。

### 2. context 采用“目录 + 具体信息”

`context_payload` 采用分层结构，而不是把所有字段平铺在一个 `memory_snapshot` 中。

核心结构：

- `directory`
- `query`
- `search_catalog`
- `shop_details`
- `route`

其中：

- `directory` 负责告诉模型有哪些 block、先读什么、当前回答焦点是什么。
- `search_catalog` 只放轻量目录信息，例如 `total`、`top_shops`、`detail_sections`。
- `shop_details` 只放具体店铺的重字段信息，例如 `transport`、`arcades`、`comment`。
- `route` 单独表达导航主轴，不与搜索结果混成同一层。

这样做的原因：

- 让模型先抓主轴，再决定是否下钻细节。
- 减少 `transport`、`arcades`、`comment` 直接与总量信息竞争注意力。
- 让 skill 可以稳定描述阅读顺序，而不是围绕一团混杂 JSON 打补丁。

### 3. skill 是说明书，不是模板库

`skills/*.md` 的作用是：

- 告诉模型先读 `directory`
- 告诉模型 `search_catalog` 和 `route` 是主回答锚点
- 告诉模型 `shop_details` 是补充细节块
- 告诉模型什么字段是 supporting detail，什么字段是 primary answer anchor

skill 的目标不是输出固定句式，而是提高模型在结构化上下文上的阅读稳定性。

## 后续开发约束

后续新增 tool、subagent、context block 时，默认遵守以下检查：

1. 这个模块是不是只做一类事情？
2. 这项能力应该属于 tool、agent、context builder 还是 skill？
3. 新字段是目录信息还是细节信息？
4. 如果字段很重，是否应进入 detail block，而不是 catalog block？
5. 是否正在把 agent 退化成模板 agent？
6. 是否出现了 tool 内部嵌套 LLM 的倾向？

## 结论

Arcadegent 的设计方向不是“模板拼起来像 agent”，而是：

- 用清晰职责边界构建 agent
- 用结构化上下文服务 LLM
- 用 skill 约束阅读方式
- 用 tool 保持 execution 的确定性

这也是后续 context、summary、subagent 继续演进时的默认技术路线。
