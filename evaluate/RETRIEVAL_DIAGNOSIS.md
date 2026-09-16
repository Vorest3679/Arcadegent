# 本地数据核对与结果组装诊断（2026-09-14）

## 已完成的评测修正

- 每 attempt 模型请求 / 工具调用均为 40，主 Agent 与 worker 共享，时限保持 300 秒。当前 `.env` 全局请求上限为 6080（38 × 4 × 40），避免原 512 次全局预算提前截断全量运行；没有在本轮启动新付费评测。
- benchmark v4 共 38 个 case，使用完整的 `data/local/arcades.geocoded.sample.jsonl`（3650 家），不是只放标准答案门店的小样本。
- 24 个主要目标来自 8 城，每个目标的门店字段、机种名称和数量与 processed 文件核对；另 5 个沪京门店承担有坐标的比较 / 导航。来源和 SHA256 见 `datasets/public/benchmark.sources.json`。
- 本地只有 15 家有 GCJ02 坐标。不能给广州、天水等无坐标门店编造“最近一家”答案。附近比较限定候选，导航使用不同的真实起终点，避免原北京用例起终点相同却要求正距离。
- 原“南京南站”文本配杭州定位和杭州 oracle 的矛盾用例退出主集；原杭州东站“印象汇”虚构商户也退出主集。真实记录是奥体、金沙、西溪的“印象城”，不应擅自把用户说的“印象汇”视为其中一家。
- 天水真实目标为 4799、5028、6013；CHUNITHM 仅在 4799 有记录。济南高新万达 Play1（543）没有 CHUNITHM 记录，已改成负例。
- 去掉将精确工具参数拼写当唯一答案的断言，但保留结果实体、排除项、排序和工具执行证据检查。澄清用例允许没有 shops artifact；执行过检索的空结果仍必须证明当前轮来源。

## 结果组装的直接证据

旧运行 `20260914T034500Z-full-four-models`，DeepSeek 首例：
`snapshots/8ba9fdfed66549248284f53645c9f0e6/turn-1.json`。

主回复写“东站这家印象汇里有 2 家机厅（西湖那家已按你说的排除）”，但 `response.shops` 实际为 `[910002, 910001, 910003]`。worker 的自然语言 summary 也明确排除 910003，数据库两次查询仍都返回这三个 ID。

这条证据说明：模型已经做出文字层面的排除判断，API 的门店列表没有执行这个判断。不能把该失败单纯归因于模型不理解用户。

## 代码原因

1. `backend/app/agent/runtime/react_runtime.py::_apply_tool_memory` 把 `db_query_tool` 的整批 `shops` 写入 artifact；每次查询覆盖上次列表，既没有累计候选池，也没有单独的最终推荐集。
2. `_promote_worker_artifacts` 直接提升 worker 的 shops；`_build_worker_envelope` 将文字 summary 和结构化候选分别组装，没有核对两者所指的实体。
3. `_build_response` 直接取 `_memory_shops(...)[:20]` 映射成 DTO。它没有接受主 Agent 最终选择的 ID。mapper 在此只是忠实映射原始行。
4. `_memory_shops` 还会把 `shop` 详情补进 `shops`。所以一次详情查询也可能把本来不该展示的候选重新加回来。
5. `summary_tool` 的 executor 只返回 `reply`；runtime 也只写 `memory['reply']`。传入更短的摘要列表不能提交最终门店列表。search_worker 的 allowed_tools 中也没有 summary_tool。

## 修复应建立的协议

建议增加显式的结果提交操作，参数是有序 `selected_shop_ids`，包括空列表。运行时只能从有本轮检索证据的候选池解析 ID，拒绝不存在、重复或跨城市旧候选；模型不能提交自行编写的商户对象。提交结果独立存为最终展示 artifact，并带 turn index。主回复、前端卡片和下一轮“第一家”导航共同使用这一份结果。

需要覆盖：查询 3 家后提交 2 家；依次查不同商场后合并选择；显式选择空列表；详情不回填排除项；下一轮换城市不继承旧选择；导航“第一家”对应展示顺序。不要通过正则解析自然语言中的“排除”来修改门店，也不要放宽 hard pass 掩盖卡片错误。

已修复（2026-09-14）：新增 `result_selection_tool`。检索结果在当前轮累积为候选池；worker 以有序 `selected_shop_ids` 提交最终卡片，运行时只从该池解析 ID，拒绝跨轮、重复和未知 ID。`selected_shops` 是 API `response.shops`、worker 摘要和“第一家”导航的唯一优先来源；详情查询不会再把未选择门店回填进卡片。没有选择结果的旧调用保留候选列表回退，保证历史会话兼容；新的 search worker 路径被提示必须提交选择。

## 另一个已证实的工具环境问题

同一快照有两次 `unknown_tool:mcp__amap__maps_geo`。`evaluate/environment.py` 显式创建 `MCPToolGateway(servers=[])`，而 `search_worker.md` 和 db_query_tool 描述要求解析地标时调用 AMap MCP；search_worker 仅允许 `db_query_tool` 和 `mcp__*`。即使 `EVAL_MAP_MODE=live`，也只是给内置 REST 路径配置密钥，并未注册 MCP server。

已修复提示词与工具清单的不一致：worker 现在只会调用实际出现在当次工具定义中的 `mcp__*` 地理工具，未提供时退化为区域检索并说明不能确认距离；不再要求或示例调用未注册的 `mcp__amap__maps_geo`。评测仍不继承生产 MCP 或密钥。

已修复坐标语义：本地存储距离排序在目标门店只有另一坐标系时，会在计算前做 WGS84 / GCJ02 转换；浏览器 WGS84 原点不再直接与 GCJ02 门店坐标相减。转换是公开坐标偏移算法的近似逆变换，适合排序与直线距离提示；路线仍以高德返回的 GCJ02 几何为准。

旧评测已取消，保留部分证据。旧 v3 与新 v4 的通过率不可直接作模型提升对比；下一次全量模型评测应使用修复后的结果提交协议和工具提示。
