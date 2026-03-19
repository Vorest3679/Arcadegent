# Issue: 前端需要补齐 Markdown 渲染与地图视图 renderer 子智能体

- 日期: 2026-03-19
- 优先级: P2
- 状态: Open / Backlog
- 影响范围: `apps/web/src`, `backend/app/agent`, `backend/app/agent/context`, `backend/app/agent/subagents`

## 背景

当前前端对 LLM 回复主要按纯文本展示，缺少两类关键渲染能力：

1. 基础层：把 markdown 回复正确渲染出来
2. 扩展层：当返回的是机厅集合、地图位置与导航信息时，能够切到更结构化的视图

用户还提出了一个更进一步的方向：

- 增加 `view_renderer` 子智能体
- 该子智能体专门负责生成受控的 `tsx/html`
- 它只暴露受限 `bash` 和指定 UI 组件 / 模板
- 执行必须在沙箱内完成，最终产物再交给主 agent 和前端加载

## 当前现状

- 前端当前展示重点仍是文本聊天气泡和阶段流。
- 没有看到对 markdown 解析库的集成。
- 也没有“地图卡片视图”或“导航卡片模板”的渲染协议。
- 当前 agent 架构中还没有专门面向视图生成的 worker。

## 为什么这是问题

1. 回复信息损失

- 如果模型已经返回 markdown 结构，当前纯文本展示会损失层级、列表、强调等信息。

2. 地图类结果不适合只靠纯文本承载

- 机厅列表、经纬度、路线入口，本质上更适合结构化卡片和地图可视化。

3. 视图生成职责尚未被架构化

- 如果未来想让 Agent 参与生成前端片段，没有独立 renderer worker，会把职责继续塞进总结环节。

## 建议方案

### 1. 先补齐 markdown 渲染基础层

前端建议：

- 使用 `marked` 解析 markdown
- 再配合 HTML sanitization 处理，避免直接注入不可信内容

这一步的目标很明确：

- 让普通文本回答先具备标题、列表、强调、链接等基础表现

### 2. 为地图场景定义受控视图协议

建议不要直接让主 agent 向前端吐任意 HTML，而是先定义一层 render payload，例如：

```json
{
  "type": "arcade_map_view",
  "title": "北京适合下班后去的机厅",
  "arcades": [],
  "actions": [
    {
      "label": "高德导航",
      "url": "https://uri.amap.com/navigation?..."
    }
  ]
}
```

前端可以基于该 payload 渲染：

- 地图标点
- 机厅信息卡片
- 跳转高德的导航按钮

### 3. 再引入 `view_renderer` worker

推荐将该 worker 定位为：

- 输入：结构化数据、受控模板、允许使用的 UI 组件
- 输出：受控的 `tsx/html` 产物或 render manifest

其职责不是“自由发挥写网页”，而是：

- 在模板边界内组装页面
- 为前端生成可加载的视图产物

### 4. 沙箱与权限必须前置

这一点是本 issue 的硬约束。

建议：

- `view_renderer` 仅允许访问白名单 UI 库与模板目录
- 仅开放受限 `bash`
- 禁止访问业务数据以外的敏感目录
- 如果最终允许前端加载 HTML，必须通过 sandboxed iframe、严格 CSP 或等价隔离方案

## 产物协议建议

`view_renderer` 返回给主 agent 的对象建议包含：

- `view_type`
- `template_id`
- `html` 或 `component_code`
- `assets`
- `render_data`
- `validation_result`

主 agent 再决定：

- 直接把 `render_data` 交给前端已有模板
- 或把受控产物登记后返回给前端加载

## 推荐实施顺序

1. 前端先接入 markdown 渲染与安全清洗。
2. 定义地图卡片视图的 render payload schema。
3. 前端先用固定模板渲染该 schema。
4. 再引入 `view_renderer` worker，让它在模板边界内生成可加载产物。

## 风险与边界

- 如果直接渲染模型返回的任意 HTML，风险过高，不建议作为第一版。
- 如果没有稳定的 render schema，`view_renderer` 输出会难以消费和测试。
- renderer worker 的成功率虽然可能高，但仍需要模板、样式和组件白名单来兜住边界。

## 验收标准

- 普通 assistant 回复支持 markdown 渲染。
- 机厅地图结果可切换为结构化地图 + 卡片视图。
- 卡片支持一键跳转到高德进行导航。
- `view_renderer` worker 的执行在沙箱内完成，且只能访问白名单 UI 资源。
- 主 agent 能接收 renderer 的最终产物，并把它交给前端安全加载。

## 关联文档

- [链式 subagent 改为主 agent hub](./2026-03-19-main-agent-hub-architecture.md)
- [会话执行异步化与 SSE 预备改造](./2026-03-19-chat-async-and-sse-readiness.md)
