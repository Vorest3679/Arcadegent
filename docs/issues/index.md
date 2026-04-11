# Issue 索引

更新时间：2026-04-08

本目录用于沉淀可追踪的问题文档。当前先覆盖两类来源：

- 本轮新提出的 Agent 架构、前后端异步与渲染问题
- `README.md` 中已明确写出的“当前限制”

## 快速入口

| ID | 优先级 | 状态 | 文档 | 来源 |
| --- | --- | --- | --- | --- |
| AGT-001 | P1 | Open / Backlog | [subagent 结束信号与 pointer 解耦](./subagent-pointer-decoupling.md) | 本轮问题 1 |
| AGT-002 | P1 | Open / Backlog | [链式 subagent 改为主 agent hub](./main-agent-hub-architecture.md) | 本轮问题 2 |
| AGT-003 | P1 | Open / Backlog | [会话执行异步化与 SSE 预备改造](./chat-async-and-sse-readiness.md) | 本轮问题 3 |
| AGT-004 | P2 | Open / Backlog | [Markdown 渲染与地图视图 renderer 子智能体](./markdown-map-view-renderer.md) | 本轮问题 4 |
| ~~AGT-005~~ | P1 | Done / Closed | ~~[工具注册表从静态注入改为动态装配](./dynamic-tool-registry.md)~~ | 本轮问题 5 |
| AGT-006 | P2 | Open / Backlog | [本地 JSONL 读模型升级为可替换数据层](./storage-read-model-upgrade.md) | README 当前限制 |
| AGT-007 | P2 | Open / Backlog | [高德 MCP tool 自动识别鲁棒性增强](./amap-mcp-discovery-hardening.md) | README 当前限制 |
| AGT-008 | P2 | Open / Backlog | [路线规划能力降级策略显式化](./route-capability-degradation-visibility.md) | README 当前限制 |

## 建议阅读顺序

1. 先看 `AGT-002`，它定义主 agent / worker agent 的目标形态。
2. 再看 `AGT-001` 与 `AGT-005`，这两篇分别解决“怎么结束 subagent”和“怎么把工具系统改成能支撑该架构”。
3. `AGT-003` 负责把执行链路从同步改成异步，为后续真正流式输出铺路。
4. `AGT-004` 是视图生成与前端渲染扩展。
5. `AGT-006` 到 `AGT-008` 是 README 中已暴露的既有限制，适合并入中期 backlog。

## 补充讨论文档

- [主 agent / worker 二级多智能体改造方案（讨论稿）](./main-agent-worker-migration-plan.md)

## 维护规则

1. 新增 issue 文档时，在本页按时间倒序补充。
2. 每个 issue 文档至少包含：背景、现状、问题、建议方案、验收标准。
3. 如果某个 issue 已进入实施，建议补一篇 plan / handoff 文档并在 issue 底部互相链接。
