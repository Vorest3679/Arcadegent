# Dynamic Tool Registry Implementation Details

## 文档目的

这份文档记录本次 builtin 动态工具注册表重构的完整开发思路。

重点不是只描述“最后变成了什么”，而是完整回答下面这些问题：

- 改造前的真实问题是什么
- 为什么这次选择一次性重构，而不是继续做兼容层
- 从旧结构迁移到新结构时，具体改了哪些边界
- 每一步修改背后的判断依据是什么
- 当前结构解决了什么，还故意没有解决什么

因为这次是一次性 Git 更新，所以这里刻意把“实现路径”也写出来，方便后续回看 commit、review 历史或继续做插件化扩展时直接复用。

## 决策前提

本次改造有一个非常关键的前提：

- 这是一次性代码库重构，不要求保留旧 manifest 格式兼容。

这条前提直接影响了方案选择。

如果必须兼容旧结构，常见做法通常会变成：

- provider 同时支持 `tools.json` 和 `tools_manifest.json`
- 同时支持 `schema_model` 与 `input_schema`
- 同时支持 `handler` 与 `executor`
- 在运行时加入更多“如果是旧格式就这样，否则那样”的分支

这种写法短期看起来稳妥，但会把这次本来应该“拔干净”的重构做成一个长期过渡层。结果是：

- 代码变复杂
- 心智模型变复杂
- 文档必须同时解释两套格式
- 后续再想做工具包安装/卸载时还要继续背旧债

既然这次明确允许一次性更新，那么最合适的策略就是：

- 直接删除旧入口
- 直接切换新模型
- 让 provider、schema、executor 的职责边界一次到位

这也是本次没有保留旧 `tools.json` 兼容分支的根本原因。

## 改造前的真实问题

在这次重构之前，builtin tool 虽然已经部分“manifest 化”，但本质上仍然是半动态系统。

### 1. manifest 只外置了“索引”，没有外置“工具本身”

旧结构里，manifest 大致是这种语义：

- 写工具名
- 写描述
- 写 `schema_model = 某个 Pydantic 模型`
- 写 `handler = 某个中心执行函数`

也就是说，manifest 本身并不携带 schema，也不携带真正的执行局部性。它只是一个“引用表”。

### 2. schema 仍然依赖中心 Python 登记

旧方案里，新增一个 builtin tool 时仍然需要：

- 回到 `app.agent.tools.schemas`
- 新增一个 Pydantic 模型
- 再把它挂到中心化注册映射里

这意味着 schema 并不跟着工具走，而是被集中登记在一个中心模块里。

### 3. 执行逻辑仍然依赖中心 handler 登记

旧方案里，新增一个 builtin tool 时还需要：

- 回到中心 `handlers.py`
- 新增 `execute_xxx`
- 再由 manifest 去引用它

这会带来一个典型问题：

- manifest 看起来很动态
- 但真正的业务入口仍然集中在一个越来越大的 Python 文件里

这不是插件式插拔，而是“把硬编码从 registry 挪到 handler 文件里”。

### 4. 结果是新增一个工具仍然要改多处

在旧结构下，新增 builtin tool 通常还要碰这些地方：

1. 中心 schema 文件
2. 中心 handler 文件
3. manifest
4. 测试

这显然不符合“工具局部自描述”的目标。

## 本次重构的核心目标

这次不是简单想把 JSON 拆得更碎，而是要真正把 builtin tool 收敛成 provider 可装配的局部单元。

最终目标可以归结成三条：

### 1. schema 跟着工具走

新增工具时，不再需要回到中心 `schemas.py` 里登记参数模型。

### 2. 执行逻辑跟着工具走

新增工具时，不再需要回到中心 `handlers.py` 里登记执行函数。

### 3. provider 只做装配，不做业务中心

`BuiltinToolProvider` 只负责：

- 读取 manifest
- 解析 tool definitions
- 构建 validator
- 调用 executor

它不再承担任何“知道每个工具细节”的中心职责。

## 改造前后的结构对比

### 旧结构

```text
tools.json
  -> schema_model: app.agent.tools.schemas:SomeArgs
  -> handler: app.agent.tools.builtin.handlers:execute_some_tool

app.agent.tools.schemas
  -> Pydantic 模型定义
  -> 中心 schema 注册

app.agent.tools.builtin.handlers
  -> execute_db_query
  -> execute_geo_resolve
  -> execute_route_plan
  -> execute_summary
  -> ...
```

问题是：

- schema 虽然叫“动态”，实际上还是中心登记
- handler 虽然叫“动态”，实际上还是中心登记

### 新结构

```text
backend/app/agent/tools/builtin/
  provider.py
  tools_manifest.json
  executor_utils.py
  executors/
    db_query.py
    geo_resolve.py
    route_plan.py
    summary.py
    select_next_subagent.py
  schemas/
    db_query_tool.json
    geo_resolve_tool.json
    route_plan_tool.json
    summary_tool.json
    select_next_subagent.json
```

新结构的关键变化是：

- `tools_manifest.json` 只做“总索引”
- `schemas/*.json` 才是工具 definition 本体
- `executors/*.py` 才是工具自己的执行入口
- `executor_utils.py` 只保留纯公共小工具

## 本次重构的实施步骤

下面按实际开发顺序记录这次是怎么一步步改过来的。

### 第 1 步：确认真正的中心耦合点

最开始先做的不是改 JSON，而是确认“系统到底还卡在哪些中心点上”。

结论很明确：

- `ToolRegistry` 已经基本 provider 化
- 真正没拔干净的是 builtin provider 仍然引用中心 schema 和中心 handler

所以本次重构的切入点不是继续改 `ToolRegistry`，而是把 builtin provider 的输入与执行链彻底去中心化。

### 第 2 步：放弃旧 manifest 兼容

一旦确认这是一次性重构，就立刻做了一个重要选择：

- 不再让 `BuiltinToolProvider` 同时支持旧 `tools.json` 和新结构

这样做带来的直接好处是：

- provider 代码可以保持单一模型
- 文档不用双写
- 测试只覆盖最终格式
- 后续继续做 bundle/plugin 能力时没有旧结构包袱

### 第 3 步：把总 manifest 与单工具 definition 拆开

新的结构被拆成两层：

#### 层 1：`tools_manifest.json`

只负责两件事：

1. 声明共享 `services`
2. 声明要加载哪些 tool JSON

这层不再内联每个工具的完整 schema。

#### 层 2：`schemas/*.json`

每个 tool 一个 JSON definition，包含：

- `name`
- `kind`
- `description`
- `executor`
- `input_schema`
- `capabilities`
- `metadata`

这样改完以后，一个工具自己的描述和执行入口已经能在本地文件级闭环，不需要回到中心文件。

### 第 4 步：把 schema 从 Pydantic 中心登记改成 JSON Schema 自描述

这是这次改造里最实质的一步。

旧结构的问题不是 Pydantic 不好，而是：

- 只要 schema 还必须先在中心 Python 模块里定义
- 工具就仍然不具备局部自描述能力

因此这次选择：

- 让 `schemas/*.json` 直接携带 `input_schema`
- provider 直接读取这份 JSON Schema
- `ToolDescriptor` 直接暴露它给模型使用

为了让这件事在运行时可用，还补了一层通用 JSON Schema 校验：

- 读取 JSON
- 应用 `default`
- 递归解析 `$ref`
- 处理 `anyOf` / `oneOf`
- 用 `jsonschema` 做最终校验

这层逻辑被收进了 [`backend/app/agent/tools/schemas.py`](../../backend/app/agent/tools/schemas.py)。

注意这里的 `schemas.py` 已经不再是“某几个 builtin tool 的参数模型中心”，而是“provider-neutral 的 JSON Schema 工具层”。

### 第 5 步：把中心 handler 改成 per-tool executor

旧结构最大的问题之一，是所有 builtin tool 的执行逻辑都在一个中心 `handlers.py` 里。

这次改成：

- `executors/db_query.py`
- `executors/geo_resolve.py`
- `executors/route_plan.py`
- `executors/summary.py`
- `executors/select_next_subagent.py`

每个工具都通过 import path 绑定自己的入口，例如：

- `app.agent.tools.builtin.executors.db_query:execute`
- `app.agent.tools.builtin.executors.summary:execute`

这样做的直接结果是：

- provider 不再需要按工具名分发
- 新增工具时不再需要回中心文件补 `execute_xxx`
- 工具逻辑的演进范围被限制在各自 executor 文件里

### 第 6 步：收紧“共享 helper”的边界

在拆完 per-tool executor 之后，出现了一个自然问题：

- 有些跨工具复用的小逻辑要不要保留公共模块？

答案是可以保留，但必须严格约束边界。

因此最后保留了 [`executor_utils.py`](../../backend/app/agent/tools/builtin/executor_utils.py)。

它只允许放：

- 文本截断
- region code/name 归一化
- 无工具归属的纯函数

它不允许重新变成一个换壳的 `handlers.py`。

这一步其实很重要，因为如果不刻意收口，共享模块很容易再次长成“新的中心业务文件”。

### 第 7 步：调整 provider 的职责边界

在新结构下，[`provider.py`](../../backend/app/agent/tools/builtin/provider.py) 的职责被重新定义为：

1. 加载 `tools_manifest.json`
2. 解析共享 `services`
3. 读取每个 `schemas/*.json`
4. 构造 `ToolDescriptor`
5. 构造 validator
6. 调 executor

它不再负责：

- 知道某个工具的参数模型类
- 知道某个工具的中心 handler 名字
- 做任何按工具名分支的业务判断

也就是说，provider 现在真正变成了“装配器”，而不是“半个业务中心”。

### 第 8 步：补齐 registry 错误模型

因为 builtin 校验已经不再依赖 Pydantic，所以 runtime 里需要新增一类 provider-neutral 的验证错误。

于是这次同时做了两件事：

- 在 `app.agent.tools.base` 中引入 `ToolInputValidationError`
- 在 `ToolRegistry.execute()` 中统一捕获并转换为 `validation_error`

这样 builtin tool 的 schema 校验就不再与某种具体类型系统强绑定。

### 第 9 步：更新文档与测试

这次不是只改代码，还同步做了三类文档/测试更新：

1. manifest 写法文档

- [`docs/guidings/builtin-tool-manifest-guide.md`](../guidings/builtin-tool-manifest-guide.md)

2. issue 收口

- issue 追踪材料保留在本地归档，不作为对外文档入口。

3. provider 测试

- `test_builtin_tool_provider.py`
- `test_tool_registry.py`

这一步的作用不是“补形式”，而是确保这次一次性重构在代码、文档和 issue 追踪层都闭环。

## 关键文件映射

为了方便后续查历史，这里把最关键的文件迁移关系列出来。

### 旧入口到新入口

- 旧：`backend/app/agent/tools/builtin/tools.json`
  新：`backend/app/agent/tools/builtin/tools_manifest.json`

- 旧：`schema_model = app.agent.tools.schemas:SomeArgs`
  新：`input_schema = schemas/*.json 内联 JSON Schema`

- 旧：`handler = app.agent.tools.builtin.handlers:execute_xxx`
  新：`executor = app.agent.tools.builtin.executors.xxx:execute`

### 中心化文件到局部文件

- 旧：`backend/app/agent/tools/schemas.py` 负责 builtin 参数模型
  新：`backend/app/agent/tools/schemas.py` 只负责通用 JSON Schema helper

- 旧：`backend/app/agent/tools/builtin/handlers.py`
  新：删除；逻辑拆到 `executors/*.py`，共享纯函数迁到 `executor_utils.py`

## 当前运行时链路

本次改造完成后，builtin tool 的运行时链路如下：

```text
tools_manifest.json
  -> provider 读取 services 与 tool paths
  -> provider 读取 schemas/*.json
  -> provider 构造 ToolDescriptor + validator + executor
  -> ToolRegistry 汇总 definitions
  -> 模型触发 tool call
  -> ToolRegistry 做权限与参数校验
  -> BuiltinToolProvider 调用对应 executor
  -> executor 通过 BuiltinToolContext 获取 service
  -> executor 返回结构化结果
```

这条链路里已经没有：

- 中心 schema 注册表
- 中心 handler 分发表

## 当前结构的收益

重构完成后，新增一个 builtin tool 的最小步骤已经变成：

1. 在 `schemas/` 下新增一个 tool JSON
2. 在 `executors/` 下新增一个 executor
3. 在 `tools_manifest.json` 中把该 tool JSON 路径加入 `tools`
4. 如果需要共享依赖，再补 `services`

不再需要：

- 修改中心 `schemas.py` 去补 Pydantic 模型
- 修改中心 `handlers.py` 去补执行函数
- 修改 `ToolRegistry` 去新增 builtin 字段或分支

真正节省的不是“文件数量”，而是“中心登记点的数量”。

## 这次故意没有做的事

这次重构虽然已经把 builtin tool 做成了局部自描述，但它还不是完整的插件包管理系统。

本次故意没有做：

- 目录扫描自动发现 tool bundle
- zip 包一键解析并加载 tool schema / executor
- tool bundle 安装与卸载命令
- bundle 级版本约束与签名校验
- 热插拔生命周期管理

原因很简单：

- 这些问题已经超出“去中心化 builtin tool 注册”的范围
- 如果在这次重构里一起做，风险会明显放大
- 当前最重要的是先把 builtin provider 的边界拉直

## 后续扩展方向

在当前结构基础上，下一阶段最自然的扩展方向有两个。

### 1. zip 一键解析并加载 tool bundle

目标是让一组：

- `schemas/*.json`
- `executors/*.py`
- `tools_manifest.json`

被打成一个可分发单元，再由 provider 负责解包、校验、装配。

### 2. 一键卸载 tools

目标是让已安装的 tool bundle 可以被整体移除，并联动：

- registry refresh
- 本地安装状态清理
- 健康状态更新

这两个方向都值得做，但它们应被视为新的插件管理问题，而不是本次 issue 的残留尾巴。

## 结论

这次动态工具注册表改造的真正完成标志，不是“配置搬到 JSON 里了”，而是下面三条同时成立：

1. schema 不再依赖中心 Python 登记
2. 执行逻辑不再依赖中心 handler 登记
3. provider 只做装配，不做具体工具业务判断

从这个角度看，这次重构真正完成的不是“文件拆分”，而是 builtin tool 的职责重构。

也正因为这三条边界被拉直，builtin tool 才真正开始接近一个可插拔 provider，而不再只是“披着 manifest 外衣的半静态系统”。
