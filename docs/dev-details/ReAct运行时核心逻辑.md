# ReAct 运行时核心逻辑

本文说明 `ReactRuntime` 一次对话的执行主线。对应源码：`backend/app/agent/runtime/react_runtime.py`（应用门面）、`backend/app/agent/runtime/kernel.py`（通用内核）、`backend/app/agent/runtime/lifecycle.py`（作用域与 hook 分发）、`backend/app/agent/runtime/domain_port.py`、`backend/app/agent/llm/provider_adapter.py`、`backend/app/agent/llm/streaming.py`。run 的接受、取消和 SSE 传输见 [会话运行生命周期与 SSE](./会话运行生命周期与SSE.md)。

> 本文目前只写了主 agent 循环与模型输出的发布方式，worker、上下文构建、工具记忆等内容后续补充。

## 主 agent 循环

每轮固定先调用一次模型，等这次调用完整结束后，再决定执行工具、结束循环或报错退出：

```python
# _run_main_agent（简化）
while not guard.exhausted:
    output_id = _new_output_id()                 # 每次模型调用对应一个新的输出
    response, published = await self._call_main_model(output_id=output_id, ...)
    self._record_model_call(...)                 # transcript / usage / 工具参数证据

    if response.error is not None:
        break                                    # 模型出错：不执行本次的工具调用
    if response.tool_calls:
        await self._execute_tool_calls(...)      # spawn_agent 工具 → _run_worker
        if memory.get("reply"):                  # concludes_turn 工具的回复
            final_text = memory["reply"]; break
        continue                                 # 下一轮，新的 output_id
    if response.text:
        final_text = response.text.strip()
        final_output_id = output_id if published else None
        break
```

- 工具只在 `_call_main_model` 返回之后执行，此时模型这次调用已经完整结束，工具参数已拼接完整并只解析一次。流式只改变文本发出的时机，不改变循环结构。
- worker（`_run_worker`）始终调用 `complete()`，它的文本只进入证据，不发布为 `assistant.token`。

## 一次模型调用：`_call_main_model`

```python
async def _call_main_model(self, *, output_id, **request):
    if not adapter.streaming:                    # LLM_STREAM=false
        response = await adapter.complete(**request)
        if response.error is None and text:      # 调用结束后整段发一次
            publish("assistant.token", {"delta": text, "stream_mode": "synthetic"}, output_id)
        return response, published

    async with aclosing(adapter.stream(**request)) as events:   # LLM_STREAM=true
        async for event in events:
            if isinstance(event, TextDelta):     # 边收边发
                publish("assistant.token", {"delta": event.text, "stream_mode": "provider"}, output_id)
            elif isinstance(event, StreamDone):
                final = event.response           # 与 complete() 同形的 ModelResponse
    return final, published
```

`adapter.stream()` 产出四种事件，只有 `TextDelta` 会发布：

| 事件 | 处理 |
| --- | --- |
| `TextDelta(text)` | 立即以当前 `output_id` 发布 |
| `ReasoningDelta(text)` | 不发布，只进入证据 |
| `ToolCallDelta(index, ...)` | 不发布；由 provider 层按 index 拼接 |
| `StreamDone(response)` | 最后一个事件，带收集完整的结果 |

取消 run 时，`CancelledError` 在 `async for` 处抛出，`aclosing` 关闭生成器，生成器内的 `httpx` 流随之关闭，上游请求停止。

## 最终回复与 output_id

```python
# _run_chat_session（简化）
final_text, model_error, output_id = await self._run_main_agent(...)
if not final_text:
    final_text, output_id = domain.fallback_reply(...), None
if output_id is None:                            # concludes_turn 回复或兜底文案
    output_id = _new_output_id()
    publish("assistant.token", {"delta": final_text, "stream_mode": "synthetic"}, output_id)
publish("assistant.completed" or "session.failed", {"reply": final_text, ...}, output_id)
```

以“先说一句再调工具”为例，事件顺序是：

```text
第 1 轮  token(out_A,"我先") token(out_A,"查一下。") → tool.started → worker.* → tool.completed
第 2 轮  token(out_B,"找到了") token(out_B," Gamma。")
结束     assistant.completed(out_B, reply="找到了 Gamma。")
```

前端收到 `out_B` 的第一条 token 时，把 `out_A` 定格为“中间回复”。

### 模型上下文中的最终回复

每次模型调用都会写入一条 `payload.model` 证据回合（transcript、usage、工具参数）；最终文本还会另写一条 `payload.final` 的 assistant 回合，供界面与历史恢复使用。为避免下一轮请求把同一段最终文本发送两次，`_record_model_call` 在这段证据上标记 `payload.model.final_reply = true`，`ContextBuilder._tail_turns` 投影模型历史时跳过该证据回合：

```text
持久化：user → assistant(evidence, final_reply=true) → assistant(final)
下一轮模型消息：user → assistant(final)
```

只有这种最终回复证据会被跳过：优先看 `final_reply` 标记；对标记出现前已持久化的旧会话，如果无工具调用的证据文本与紧随其后的 final 回合一致，也按同一规则跳过。带工具调用的模型证据、与之配对的 tool 回合、失败的模型调用过滤规则以及 worker scope 的隔离都不变；transcript 与 usage 仍保存在会话状态中，只是不重复进入模型消息。

已知取舍：

- 模型中途出错时，已发出的部分文本仍留在界面上，随后兜底文案以新的 output_id 出现。
- 中间文本会被定格保留，需要靠 prompt 约束主 agent 不在工具调用前输出空洞客套话。

## 内核与领域端口

`ReactRuntime` 只是应用门面：把 `ChatRequest` 映射成 `TurnInput`，调用 `AgentKernel.run_turn`，再返回领域投影的 `ChatResponse`。通用执行主线在 `AgentKernel` 中，它不认识机厅业务，也不 import `ChatRequest` / `ChatResponse`；业务语义经 `TurnDomain` 注入（机厅实现为 `backend/app/domain/arcade/ArcadeTurnDomain`，由 `core/container.py` 传入）；不传时使用 `BaseTurnDomain`，内核仍能完整跑通一轮。

内核自己维护的不变量，不依赖领域实现：

| 不变量 | 内核做法 |
| --- | --- |
| 每轮开始清除上一轮的 `reply`、`last_error` | `reset_turn_scratch`，在 turn 钩子之前执行 |
| 工具失败写 `last_error`，成功清除 | 记录工具结果时由内核维护；worker 据此判定失败 |
| 结束本轮 | 工具描述符声明 `concludes_turn` capability，输出 `{"reply": str}` |
| 派生 worker | 工具声明 `spawn_agent` capability，输出 `{"worker", "task"}`；合法名单来自当前节点的 `AgentNode.spawnable`（`SubAgentBuilder.node(name)`），非法派发由内核返回失败信封 |

领域继承 `BaseTurnDomain`，只覆盖需要的部分。分两类（对应 Vue 中“生命周期钩子改状态”与“render / computed 产出数据”）：

**改状态：生命周期钩子**（详见下一节）

| 钩子 | 时机 | 典型用途 |
| --- | --- | --- |
| `on_turn_mount` | 内核重置本轮临时键之后、第一次调模型之前 | 按请求写入本轮领域记忆、判定 intent |
| `on_step_update` | 每个工具结果经内核记账之后（成功、失败都会触发） | 把工具输出投影进执行该工具的状态 |
| `on_worker_mount` | worker 状态创建后、第一次调模型之前 | 从只读的父状态挑选记忆写入 worker |

**产出数据：`TurnDomain` 协议的函数**

| 协议 | 方法 | 默认行为 |
| --- | --- | --- |
| `TurnMetadata` | `runtime_hints`、`session_event_fields`、`turn_summary` | 无附加字段 |
| `ToolObserver` | `tool_result_signal`（返回的事件排在 `tool.completed` 之前）、`artifacts_from_result`（工具未自带 artifacts 时的过渡映射） | 无事件、无 artifacts |
| `WorkerPolicy` | `worker_memory_promotions`（非 artifact 字段）、`build_worker_envelope` | 不提升额外字段、通用信封；artifact 由 `ArtifactSink` 提升 |
| `TurnPresenter` | `fallback_reply`、`turn_display_snapshot`、`build_response` | 通用兜底文本、无展示快照、`TurnReply` |

`backend/app/tests/integration/test_runtime_kernel_domain.py` 直接使用 `AgentKernel.run_turn`，以 `BaseTurnDomain` 和一个最小的非机厅领域验证上述约定。

### 包边界与端口（阶段 8）

执行内核模块（`agent/runtime/`、`agent/artifacts/`、`llm/model_gateway.py`、`tools/execution.py`、`context/turn_recorder.py`、`subagents/agent_node.py`）只 import `app.agent.*`。内核需要的外部能力都以端口形式定义在 agent 包内，应用层原位置再导出，调用方不改：

| 端口 | 定义位置 | 应用侧 |
| --- | --- | --- |
| `SessionStore`（`get_or_create_session`、`save_session`） | `agent/runtime/ports.py` | `infra/db/protocols.SessionStateRepository` 继承它，并补充历史列表、详情读取等依赖应用 DTO 的方法 |
| `RunPublisher`、`NullPublisher` | `agent/runtime/events.py` | `session/injector.py` 再导出 |
| `KernelEvent`（内核发布的 11 个事件名） | `agent/runtime/events.py` | `session/events.py` 以 `RunEvent` 名称再导出，与 `AppEvent`、`ControlEvent` 组成目录 |
| 日志 | `agent/_logging.py` | 目前转发 `infra.observability.logger`；外移时只换这一处 |

`SessionStateRepository` 没有整体搬入 agent 包：`list_session_summaries` 返回 `protocol.session_dto` 的 DTO，搬进来会把应用 DTO 带进内核，因此只把内核实际调用的两个方法拆成端口。

事件名分三类：内核事件（`KernelEvent`）、应用事件（`AppEvent`，目前只有沿用旧名的 `navigation.route_ready`）和领域事件。领域通过 `domain_event(namespace, name)` 得到 `domain.<namespace>.<name>`（两段都是 `[a-z][a-z0-9_]*`，不合法时在声明处抛 `ValueError`），再经 `ToolResultSignal.events` 发布；`session/events.is_known_event` 对整个命名空间放行，不必改目录。前端只认 `RUN_EVENT_NAMES` 中的事件，其余丢弃，领域事件需要界面支持时再在前端添加。

`backend/app/tests/integration/test_runtime_package_boundary.py` 用 AST 扫描上述模块（含 `TYPE_CHECKING` 与函数内导入），允许清单只有 `react_runtime.py`（应用适配器）和 `_logging.py`，并检查允许清单中的模块确实仍需越界。

## Artifacts（阶段 6）

`backend/app/agent/artifacts/` 把“工具产出了什么”和“怎么展示/提升”拆开。`Artifact{key, value, memory, display, scope, promote, append, append_key, invalidates}` 只携带策略，值本身对内核不透明；`ArtifactSink` 是唯一把策略写成 `working_memory["artifact_meta"]` 的地方：

| 字段 | 写入元数据 | 作用 |
| --- | --- | --- |
| `display` | `display` | 是否参与展示快照；响应与会话详情都读它，不再维护 `display_keys` 清单 |
| `scope` | `scope` | `turn` 在下一轮开始时由 `begin_turn` 删除；`session` 保留到同名 artifact 被替换或显式删除 |
| `promote` | `promote` | 是否允许 worker 的结果覆盖父状态；候选列表这类 worker 局部值设为 false |
| `invalidates` | `invalidates` | 新值会让哪些旧值失效（例如新搜索清掉上一轮 `selected_shops`/`shop`/`destination`）；worker 提升时同样生效 |
| `append` / `append_key` | — | 列表追加；`append_key` 由生产者提供（搜索候选传 `source_id`），标识可为整数或字符串（路径、UUID），其他类型按“缺少稳定标识”跳过；内核不硬编码业务字段 |

`memory=False` 目前不支持 display-only 存储，`apply` 会直接报错，避免静默丢数据。

`ArtifactSink` 的时机：

- `begin_turn`：内核在每个回合开始时无条件调用，按 `scope="turn"` 统一清理上一轮的值；没有 `scope` 元数据的老会话由领域用 `drop_legacy_turn_keys` 提供 `legacy_keys` 兜底。因此最小第三方领域即使不写生命周期钩子也不会把上一轮 artifact 带进下一轮。
- `apply`：工具/领域结果写入当前状态，记录 `turn_index`、`display`、`scope`、`promote`、`invalidates`；同一批 artifact 中产生的 key 不会被同批的 `invalidates` 删除。
- `promote`：只提升 worker 自己产生过（有元数据）的 artifact；失败 worker 的提升结果带 `status="partial"`，worker 从父状态复制的值不会被提升。父状态里被提升 artifact 声明为失效的旧值同时被删除，所以 worker 搜索没有选择新店时不会显示父状态上一轮的 `selected_shops`。非 artifact 的标量字段由 `TurnDomain.worker_memory_promotions` 返回；机厅领域只回写 worker 实际写入的字段（挂载时记录基线，工具处理处用 `mark_worker_memory_write` 标记写入），未运行的 worker 不会用旧快照覆盖并行兄弟的新查询。
- `display_snapshot` / `turn_snapshot`：前者给即时响应与会话详情（session scope 常驻，turn scope 取最近一次展示轮），后者给最终 assistant 回合的 `map_artifacts`（只有本轮确实更新过展示 artifact 才归档）。领域把两者投影成机厅自有的 `ArcadeDisplay`（`domain/arcade/display.py`），由 `ArcadeTurnDomain.session_display` 提供；该方法不属于内核端口，内核只经 `turn_display_snapshot` 拿到不透明 dict。没有 `display` 元数据的老会话按“展示 + session”兼容，不会被新 artifact 挤掉。

工具自带 artifacts 时，`ToolExecutionResult.artifacts` 优先；否则才调用 `TurnDomain.artifacts_from_result`。当前 `db_query_tool`、`result_selection_tool`、`route_plan_tool` 通过 `ToolOutcome` 自带 artifacts，过渡映射只剩 MCP 结果；worker 的结果由 `ArtifactSink.promote` 提升，不再从 `invoke_worker` 信封二次写入。`ProviderExecutionResult` 与 `MCPExecutionResult` 都携带 artifacts，`BuiltinToolProvider` 接受 `ToolOutcome` 或纯 dict（旧执行器无需改动）。

intent 在阶段 6 改为每轮推导：`IntentPolicy.classify` 先看显式 intent 与导航/附近信号；上一轮是 `navigate` 且还没有 route（典型是 worker 返回 `needs_input` 等起点）时，普通补充信息（“我在人民广场”）保留 `navigate`；一旦已有 route，普通追问回到 `search`，不再继承旧导航。`on_step_update` 末尾的 `IntentPolicy.finalize` 仍只在本轮产生 route artifact 时把标签改为 `navigate`。

会话详情不再由 API 直接读领域：`services/chat_session_detail.py` 声明窄协议 `ArcadeDisplaySource`（只有 `session_display`），由 container 注入 `ArcadeTurnDomain`，拿到 `ArcadeDisplay` 后统一映射 `ChatTurnMapArtifactsDto`/`ChatSessionDetailDto`；`api/http/chat.py` 只传 run 状态并调用该服务。v2 的“管理外壳 + 业务 `data`”仍见 `docs/plans/二级计划4-ReactRuntime执行内核重构.md` 第 9 节。

## 生命周期与作用域（阶段 2.7 设计）

阶段 2.7 核心已实施；本节记录设计，避免实现时把“生命周期”做成另一套隐式状态。

### 从 Vue3 借的是作用域，不是组件

Vue 的 `create → mount → update → unmount` 很适合描述一个对象从构造、绑定环境、更新到释放的过程。Agent 也有类似的层级，但它是异步、可取消、多层嵌套的：

```text
Session
  └─ Run
       └─ Turn
            └─ ReAct Step
                 └─ Worker（worker 自身也是一次小型 Run）
```

因此不照搬 `ref/reactive`、组件实例或 `provide/inject` 魔法，而是采用“作用域 + 状态机 + 显式 hook”：

| 阶段 | Agent 语义 |
| --- | --- |
| `create` | 分配状态、校验输入，不产生外部副作用 |
| `mount` | 绑定环境，加载 session/tool/skill，本轮配置生效 |
| `update` | 每个 ReAct step / tool result，更新 hints、memory、artifacts |
| `before_unmount` | 构建响应、flush 业务状态、准备展示快照 |
| `unmounted` | 清理资源、记录 trace；必须幂等，放在 `finally` |
| `error` | 异常/取消进入卸载流程，不吞原始错误 |

### ctx 的分栏

```text
ctx.env       长生命周期服务：provider、tools、skill_registry（不含 session store）
ctx.run       RunScope：session_id、state
ctx.turn      TurnScope：turn_index、state、request（不透明）、final_text
ctx.step      StepScope：index、StepPolicy、state
ctx.worker    WorkerScope：worker_name、run_id、state、parent_state（只读）
ctx.state     最内层作用域的 state（worker > step > turn > run）
ctx.memory    ctx.state 当前的 working_memory
ctx.events    通用事件发布口
ctx.response  before_unmount 时为已投影的响应（只读通知）
```

- `state` 是 `StateView`，每次读取反映 `AgentSessionState` 的当前值。钩子可以写的只有：`working_memory` 中除内核保留键之外的键（领域记忆），以及 `intent` 属性。其余属性（`status`、`turns`、替换 `working_memory` 对象等）赋值会抛 `AttributeError`；读取时返回深拷贝快照，列表以元组返回，修改历史 turn 的嵌套 payload 或 skill 执行状态不会影响内核。此前的浅层冻结只保护列表本身，会泄漏内部可变对象。
- 内核保留键 `reply`、`last_error`、`usage_totals`、`worker_runs`（`session_state.KERNEL_MEMORY_KEYS`）：dispatcher 在被覆盖的钩子前后比较这些键，变化即恢复原值并抛 `LifecycleViolation`，本轮按失败处理。未覆盖的默认钩子不做比较；最坏情况（20 条 worker 记录）每次约 4 ms。
- 领域辅助函数的状态参数标注为 `DomainState` 协议（`session_id`、`turn_index`、`working_memory`、可写 `intent`），钩子传入的 `StateView` 和单测直接构造的 `AgentSessionState` 都满足它。
- `parent_state` 是只读视图：`working_memory` 返回副本，`intent` 不可赋值；worker 只能改自己的隔离状态。
- session store 不在 `ctx.env` 中。
- `StepPolicy` 是“这一步怎么做”，目前只有 `runtime_hints`，在 step `create` 钩子里可写，合并进本步 provider hints。临时工具开关、停止条件、`TurnConfig`（model / reasoning_effort）等到真正接入时再加，不提前暴露不生效的字段。
- `memory` 在 `working_memory` 拆分为内核区/领域区之前是整份记忆（保留键受上面的检查保护）；拆分后改为领域命名空间。

`plan` 这个名字留给真正的 planner。以后做多步规划时，`ctx.plan` 应该是 `Plan(steps=[...])`，而不是一个配置袋。

### 两个容易放错的字段

这是设计讨论里最容易混淆的地方，单独记下：

- **`spawnable_workers` 不是 config，是拓扑。** 它表示“当前 agent 节点能派发哪些 worker”，应放到 `AgentNode.spawnable`；全局“存在哪些 worker”仍由 `SubAgentBuilder` 维护。2.7 起 `_run_worker` 只按当前节点的 `AgentNode.spawnable` 校验。若本轮要临时收紧，放 `StepPolicy`，不要放 `TurnConfig`。
- **`payload_mapper` 不是 config，是领域依赖。** `ArcadePayloadMapper` 带 geo resolver、缓存和 HTTP 配置，是长生命周期服务，只在 `ArcadeTurnDomain.build_response` 与 `ChatSessionDetailService` 的 DTO 映射中使用；应留在 `ArcadeTurnDomain` 构造注入，不能塞进通用 `TurnConfig`。否则内核被迫知道“payload mapper”这个机厅概念，每轮 config 还会携带服务对象。

### domain 如何进入内核

domain 进入内核时会被翻译成 core 能理解的概念，但语义由适配器保留：

| domain 语义 | core 概念 |
| --- | --- |
| `ChatRequest` | `TurnInput.request`（不透明） |
| 机厅 intent / memory | `ctx.memory["arcade"]`、`runtime_hints` |
| 店铺/路线产物 | `Artifact` 或命名空间 memory |
| `navigation.route_ready` | `ToolResultSignal.events`；新领域事件用 `domain.<ns>.<name>` |
| `domain.arcade.results_ready` | arcade 领域在 `result_selection_tool` 完成时经 `ToolResultSignal.events` 发布，`data.shops` 为选中机厅摘要 |
| `ArcadePayloadMapper` | domain 构造依赖，不进 core |
| worker 合法名单 | `AgentNode.spawnable` |
| `ChatResponse` | presenter 返回的 opaque result |

原则是：**不是把 domain 削平，而是让 core 只携带、不解释**。domain 在适配器内部继续使用自己的服务、类型和规则；跨边界只传 core DTO。

### 分发与收尾

`runtime/lifecycle.py` 计划提供 `LifecycleEvent`、`LifecycleDispatcher` 和默认 no-op 的 `BaseLifecycleHooks`：

- 内核只发 `(scope, phase, ctx)`，dispatcher 映射到 `on_turn_mount`、`on_turn_before_unmount` 等显式方法。
- 当前只注入一个 domain，hook 顺序就是调用点顺序；多 domain 的注册顺序/逆序执行留给后续。
- `unmounted` 恰好一次；异常和 `CancelledError` 也走 `before_unmount → unmounted`。
- main/worker 的整个 step（从 `on_step_start`、上下文构建到模型证据和工具结果处理）由统一 `try/finally` 收尾。避免只在模型调用和工具执行周围清理，导致更早的钩子或上下文异常遗漏 `on_step_end`。回归测试覆盖两个循环在开始钩子、上下文构建时的异常与取消，并检查清理次数和原始异常。
- `create/mount/update` 失败则中止并进入错误卸载；`before_unmount` 失败仍执行 `unmounted`；`unmounted` 失败只记录，不覆盖主错误。
- 改状态的逻辑只在钩子里（`on_turn_mount`、`on_step_update`、`on_worker_mount`），同一时机没有第二个入口；返回数据的策略（hints、`tool_result_signal`、worker envelope、`build_response`）仍是显式函数，由内核直接调用。`BaseLifecycleHooks` 的默认钩子都是空操作，覆盖时不需要调用 `super()`。
- 钩子可写之后，多个领域并存时的执行顺序会影响结果；接入第二个领域前须先实现“注册顺序挂载、逆序卸载”。
- worker 收尾：证据回写与 usage 合并恰好执行一次——正常路径在构建信封前执行；异常或取消时补做，并按 `ActiveWorkerSlots` 恢复父会话 `active_subagent`（还有 worker 在跑则取最近启动者，全部结束才回到 `main_agent`），run 的终态（failed / cancelled）仍交给 run/session 层记录。

2.7–2.8 已把这些边界显式化：`runtime/lifecycle.py` 提供 dispatcher、`StateView`、保留键检查和 `BaseLifecycleHooks`；`AgentKernel` 在 run/turn/step/worker 边界发事件；`AgentNode.spawnable` 是唯一的 worker 合法名单；`StepPolicy.runtime_hints` 已合并进 provider hints。剩余：model/reasoning_effort 映射到 provider 协议参数、session scope 事件、多 domain 钩子顺序、公共 `TurnContext/TurnState/ToolResult` 契约（独立成包的后续事项）。并行 worker 的父级顺序已在阶段 7 由 `ActiveWorkerSlots` 与 model-order commit 解决。

## 工具执行与 worker 派发（阶段 4、7）

### 一次工具调用

`tools/execution.py` 的 `ToolDispatcher` 负责与 runtime 无关的部分：参数准备、参数证据（`raw_arguments`、`parse_error`、`prepared_arguments`、`hydrated_fields`、`preparation_error`）以及真正执行。每个调用在 `submit` 时包成一个 `asyncio.Task`；准备失败也生成一个 `failed` 结果，因此 `tool.started` 与 `tool.failed` 仍然成对。

`ToolLifecycleHooks` 分成两个阶段：

- `after` 在调用自己的 task 内执行，只能替换结果或启动并发工作（`spawn_agent` 在这里启动 worker 的 `run`），不能写父状态、不能发布工具完成事件。
- `commit` 在父循环协程内、按模型调用顺序执行；worker 的合并、领域事件与 `tool.completed/failed` 都在这里发生。

```text
tool.started（submit，模型顺序）
  → 参数准备（失败则跳过执行）
  → 工具执行（task 内并发）
  → after（task 内；spawn_agent 启动 worker run）
  → commit（父循环，模型顺序）：
      spawn_agent 提交 worker（产物/证据/usage/信封）
      → 领域 tool_result_signal 事件 → tool.completed / tool.failed
      → 证据回合 → last_error 记账 → step update 生命周期钩子
```

- `runtime/events.py` 的 `ToolEventHooks` 是默认实现：`after` 只透传，`commit` 发布 `tool.started/completed/failed` 并记录日志。领域事件与 `completed_fields` 由构造时注入的 `signal` 提供，仍排在 `tool.completed` 之前。
- `_SpawnAgentHook` 只处理声明了 `spawn_agent` 的已完成结果。它把 worker 的 `run` 留在调用 task 内，把 `commit` 留到父循环；派发普通异常转成 `worker_dispatch_error` 的失败工具结果（错误文本压缩空白并截断到 280 字符），主 agent 收到 `tool.failed` 后可继续本轮；`CancelledError` 上抛。持久化或事件发布故障会在 `commit` 阶段再次出现，本轮仍会失败。
- 取消或任何非取消异常都会走 `supervisor.cancel_all()`：未完成的调用被取消并生成 `status=failed`、`error.type=cancelled` 的合成结果，从而关闭 `tool.started` 开出的 span。取消路径还会记录已完成兄弟调用的证据（只写一次）；非取消异常路径只保证清理与关闭 span，已记录的证据保留，不承诺补齐未记录的兄弟结果。`ToolDispatcher.cancel()` 只吞子任务自身的取消/异常，外层取消通过 `Task.cancelling()` 识别后重新上抛；子任务若因真实异常结束，结果保留为 `tool_execution_error`，不会被伪装成 `cancelled`。
- capability 常量（`spawn_agent`、`concludes_turn`，以及预留的 `background`、`programmatic`）定义在 `tools/execution.py`；`tools` 不 import `runtime`，只认 `ToolLifecycleHooks` 协议与 `runtime_context` 字典。

### 执行模式与监督器

`ToolDescriptor.execution_mode` 是工具自声明的并发属性，默认 `exclusive`；`ToolRegistry.execution_mode(tool_name)` 对未知工具返回 `exclusive`，批量入口 `execution_modes(names)` 每个模型步只收集一次描述符。`BuiltinToolProvider` 从工具 JSON 读取并校验该字段，MCP 工具显式保持 `exclusive`（以后可映射 MCP 的 `readOnlyHint`）。首批 `parallel` 为 `list_skills`、`read_skill`、`invoke_worker`；其余内置工具保持独占。

`runtime/supervisor.py` 的 `LoopSupervisor` 跟踪本批 `ToolTask`：

- `WaitPolicy(mode="all")` 是唯一生产策略：等待本批全部调用结束。
- `ToolActions` 把模型调用列表切成段：相邻的 `parallel` 调用同段并发，`exclusive` 调用单独成段并作为屏障。无论完成顺序如何，`commit` 与证据记录都按模型调用顺序进行。
- 取消等待协程时，`wait` 调用 `cancel_all()` 取消并终结所有被跟踪的调用，再让 `CancelledError` 继续传播；`ToolActions` 会记录已完成兄弟的结果，且不会留下悬挂的 asyncio task。
- `WaitPolicy(mode="first_or_interval", interval_s=5)` 的接口、`Settled.done/pending` 与单测骨架已就绪，但生产路径仍只用 `all`。后台调用的占位结果、跨步回送与 tool-call/result 配对规则只在 `supervisor.py` 末尾以设计说明保留，尚未接入。`all` 故意不在 `wait` 内释放任务，由记录方在 `settle`/记录后 `release`；`cancel_all` 对已 finalized 的调用是幂等的。`execution_modes()` 收集失败（例如 MCP 描述符不可用）时，`ToolActions` 降级为整批 `exclusive` 并记录无敏感字段日志，避免 step 在拿到任何工具结果前整体失败。

### worker：start / finish / commit

`runtime/worker_runner.py` 把原 `_run_worker` 拆成三段，并行 worker 才成立：

| 段 | 做什么 | 约束 |
| --- | --- | --- |
| `start` | 校验名单、建隔离 state、挂载、发 `worker.started`、跑子循环 | 只声明“最近启动的 worker”并写父 `active_subagent`；不合并父记忆。`CancelledError` 当场收尾（run 已中止）；普通异常暂存在 dispatch 上，交给 `finish` 按顺序恢复 |
| `finish` | 提交 worker；失败/取消时补做证据与 usage 回写、恢复父状态；`unmounted` 恰好一次 | 调用方在父循环协程、按模型调用顺序执行；同一 run 幂等 |
| `commit` | 产物提升（失败标记 `partial`，记录 `source_run_id`）、usage 合并、证据回写、构造信封、`worker.completed/failed`、释放 active slot | 只在父循环协程执行 |
| `spawn` | `start + finish` 的一次性入口 | 保留给非并发的直接调用 |

`ActiveWorkerSlots` 以父状态为键保存正在运行的 worker：新 worker 启动时成为 `active_subagent`，某个 worker 结束时取仍在运行的最近启动者，最后一个结束后恢复 `main_agent`。`add` 在各 worker 自己的任务中执行，因此这些方法保持同步且没有 await；`AgentKernel` 在 run 结束时清除该状态的 slots，避免泄漏的 slot 长期强引用 session state。`worker.started/completed/failed` 都带 `run_id` 与同值的 `worker_run_id`，前端据此区分并行 worker；`worker.completed/failed` 的 `active_subagent` 是提交后的下一个 active。

`SkillExecution.load` 用线程锁覆盖“查重 → 读取 → 累加 `loaded_bytes`”，所以并行 `read_skill` 不会重复加载、也不会绕过 `max_loaded_bytes`；`SkillRegistry` 自身的刷新/读取仍由 `RLock` 串行化。`SkillExecution` 的 `__deepcopy__` 复制资源快照但不复制锁，兼容会话存储与测试里的 deepcopy。

worker 的合法名单来自当前节点的 `AgentNode.spawnable`；非法派发在 `prepare` 之前返回失败信封，不产生 `worker.started`。

产物来源：`ArtifactSink.promote` 在 `artifact_meta` 写入 `source_run_id`；父级通过 `ArtifactSink.apply` 或直接 `set_working_memory_artifact` 覆盖同名值时，默认清除旧的 `source_run_id`，避免把父级新值误标成某个 worker 的产物。

`backend/app/tests/integration/test_worker_runner.py` 覆盖“worker 工具执行中取消后父状态恢复、已完成工具证据只出现一次、无悬挂 task”“`start` 期间不合并父记忆、`commit` 幂等”和“派发异常转成失败工具结果”。并行行为见 `test_runtime_supervisor.py`。共享的测试替身放在 `_runtime_kernel_test_support.py`。

## 配置

`LLM_STREAM`（或 provider profile 的 `stream`）默认 `false`。关闭时 provider 走非流式请求，界面仍按上面的 output_id 规则展示，只是每段文本在调用结束后一次性出现。
