# Issue: 路线规划能力降级策略需要显式化，而不是静默退化

- 日期: 2026-03-19
- 优先级: P2
- 状态: Open / Backlog
- 影响范围: `backend/app/agent/tools/builtin/route_plan_tool.py`, `backend/app/api/http/health.py`, `apps/web/src`, `README.md`

## 背景

`README.md` 当前已明确写出：

- 在没有 `AMAP_API_KEY`，且也没有单独提供 `MCP_AMAP_API_KEY` 的情况下，路线规划会退化为离线估算。

这种回退策略保证了“功能不至于全挂”，但从产品和可观测性角度看，还缺一层显式能力标识。

## 当前现状

- 路线规划存在 `高德 MCP -> 高德 REST -> 离线估算` 的逐级回退
- 但用户侧未必能清晰知道当前返回的到底是真实导航结果，还是离线估算结果
- README 知道这件事，运行时和前端未必充分显式

## 为什么这是问题

1. 用户预期容易错位

- 用户可能以为自己拿到的是可直接导航的精确路线，实际却只是离线估算。

2. 运营与排障成本偏高

- 当路线“不准”时，很难第一时间判断是 provider 问题，还是已经走到 fallback。

3. 不利于前端展示

- 前端无法根据能力等级决定是否展示“去高德导航”按钮、精度提示或降级说明。

## 建议方案

### 1. 将路线结果显式标记能力级别

建议所有 route result 都带上：

- `provider`
- `capability_level`
- `is_fallback`
- `fallback_reason`

例如：

- `provider=amap_mcp`, `capability_level=full`
- `provider=amap_rest`, `capability_level=full`
- `provider=offline_estimate`, `capability_level=estimate`

### 2. 前端按能力等级展示

前端可据此决定：

- 是否展示“导航到高德”
- 是否显示“当前为估算路线，仅供参考”
- 是否提示用户补充 API key

### 3. 健康检查与日志同步暴露

`/health` 和关键日志建议输出：

- 当前可用 provider
- 当前 fallback 层级
- 缺失的配置项

## 验收标准

- 路线结果中能明确区分真实导航结果与估算结果。
- 前端能在估算模式下展示清晰提示，而不是默认等同真实导航。
- `/health` 能直观看到当前路线能力是否处于降级状态。
- 缺少 key 时，开发者能快速定位是配置问题而不是算法问题。

## 关联文档

- [高德 MCP tool 自动识别鲁棒性增强](./2026-03-19-amap-mcp-discovery-hardening.md)
- [当前 README 限制条目来源](../../README.md)
