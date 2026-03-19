# Issue: 本地 JSONL 读模型需要升级为可替换数据层

- 日期: 2026-03-19
- 优先级: P2
- 状态: Open / Backlog
- 影响范围: `backend/app/infra/db`, `backend/app/agent/tools/builtin/db_query_tool.py`, `README.md`

## 背景

`README.md` 当前已明确写出：

- 机厅查询目前使用本地 JSONL 读模型，不是数据库在线查询。

这条限制在 MVP 阶段合理，但随着检索能力、排序、会话规模和后续地图 / 推荐需求增加，单一 JSONL 读模型会逐步成为架构瓶颈。

## 当前现状

- `build_container()` 启动时直接用 JSONL 构建 `LocalArcadeStore`
- `DBQueryTool` 面向的是本地读模型
- 仓库中已经有 Supabase migration 草案，但主查询链路仍未切到数据库抽象

## 为什么这是问题

1. 查询能力受限

- JSONL 更适合静态读取，不适合复杂筛选、索引和在线更新。

2. 难以支撑后续在线化

- 若未来要支持收藏、热度、实时更新、个性化排序，本地 JSONL 会越来越吃力。

3. 数据层抽象尚未稳定

- 现在上层工具耦合在 `LocalArcadeStore` 上，不利于平滑切换到 SQLite / Postgres / Supabase。

## 建议方案

建议先做“仓储接口抽象”，而不是一次性把所有查询都迁库。

推荐分层：

```text
ArcadeRepository
  |- LocalJsonlArcadeRepository
  |- SqliteArcadeRepository
  |- SupabaseArcadeRepository
```

这样：

- 当前默认实现仍可继续跑 JSONL
- 但上层 `DBQueryTool` 与 API 不再依赖具体存储介质

## 推荐实施顺序

1. 定义 repository 接口和查询参数对象。
2. 让 `DBQueryTool` 改为依赖 repository 抽象。
3. 保留 JSONL 实现作为默认开发模式。
4. 增加 SQLite 或 Supabase 实现作为在线模式。

## 验收标准

- `DBQueryTool` 不再直接绑定 `LocalArcadeStore`。
- JSONL 与数据库实现可以通过配置切换。
- API 返回结构保持兼容。
- 现有检索、分页、排序测试在两种后端上都能跑通。

## 关联文档

- [当前 README 限制条目来源](../../README.md)
