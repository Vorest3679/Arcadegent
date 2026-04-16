# Agent 地图 Artifacts 渲染说明

更新时间：2026-04-15

## 目标

这份说明整理后端如何把地图相关 artifacts 暴露给前端，以及前端如何把这些 artifacts 加载成地图组件。

当前实现遵循一个边界：

- Assistant 正文继续作为 Markdown 渲染。
- 地图不从模型自由输出 HTML / TSX。
- 地图由后端结构化 artifacts 驱动，前端用固定 React 组件渲染。

也就是说，最终体验是：

```text
assistant markdown text
  + structured map artifacts
  -> ChatPanel
  -> AgentMapCard
  -> AmapMapCanvas / AmapShopMarkers / AmapRouteOverlay / MapActionBar
```

## 数据入口

前端有两条入口接收地图 artifacts。

### 1. 会话详情恢复

页面加载历史会话或最终刷新会话详情时，调用：

```text
GET /api/v1/chat/sessions/{session_id}
```

后端返回的 `ChatSessionDetail` 中，地图相关字段是：

```json
{
  "shops": [],
  "route": null,
  "client_location": null,
  "destination": null,
  "view_payload": null
}
```

前端在 `App.tsx` 中通过 `mapArtifactsFromSession(detail)` 转成统一的 `ChatMapArtifacts`：

```ts
type ChatMapArtifacts = {
  shops: ArcadeSummary[];
  route?: RouteSummary | null;
  client_location?: ClientLocationContext | null;
  destination?: ArcadeSummary | null;
  view_payload?: Record<string, unknown> | null;
  route_pending?: boolean;
};
```

只有存在 `route`、`destination`、`shops` 或 `view_payload` 时，才会把 artifacts 写入 `activeMapArtifacts`。

### 2. SSE 渐进路线事件

导航过程中，后端会发送：

```text
navigation.route_ready
```

事件 `data` 当前按 `RouteSummary` 处理，例如：

```json
{
  "provider": "amap",
  "mode": "walking",
  "distance_m": 1280,
  "duration_s": 960,
  "origin": {
    "lng": 121.4,
    "lat": 31.2,
    "coord_system": "wgs84",
    "source": "client",
    "precision": "approx"
  },
  "destination": {
    "lng": 121.475,
    "lat": 31.228,
    "coord_system": "gcj02",
    "source": "route",
    "precision": "approx"
  },
  "polyline": [],
  "hint": null
}
```

前端在 `App.tsx` 中通过 `coerceStreamRoute(envelope.data)` 做轻量校验，成功后写入：

```ts
setActiveMapArtifacts((previous) => ({
  shops: previous?.shops ?? [],
  route,
  client_location: previous?.client_location ?? null,
  destination: previous?.destination ?? null,
  view_payload: previous?.view_payload ?? { version: 1, scene: "agent_route" },
  route_pending: true
}));
```

这样路线卡片可以在最终 assistant 文本之前先显示。

最终收到 `assistant.completed` 后，前端会重新拉取 session detail，`route_pending` 会被会话详情中的完整 artifacts 覆盖为 false。

## 后端 artifacts 契约

### `shops`

用于候选机厅地图卡片。

每个机厅至少需要：

```json
{
  "source_id": 101,
  "name": "Arcade One",
  "address": "Nanjing East Road",
  "city_name": "上海市",
  "geo": {
    "gcj02": {
      "lng": 121.475,
      "lat": 31.228,
      "coord_system": "gcj02",
      "source": "geocode",
      "precision": "approx"
    },
    "source": "geocode",
    "precision": "approx"
  }
}
```

前端只会把 `geo.gcj02` 作为地图 marker 坐标。没有 `geo.gcj02` 的机厅会保留在列表里，但不会画 marker。

### `route`

用于路线卡片。

```json
{
  "provider": "amap",
  "mode": "walking",
  "distance_m": 1280,
  "duration_s": 960,
  "origin": {
    "lng": 121.4,
    "lat": 31.2,
    "coord_system": "wgs84",
    "source": "client",
    "precision": "approx"
  },
  "destination": {
    "lng": 121.475,
    "lat": 31.228,
    "coord_system": "gcj02",
    "source": "route",
    "precision": "approx"
  },
  "polyline": [
    {
      "lng": 121.476,
      "lat": 31.229,
      "coord_system": "gcj02",
      "source": "route",
      "precision": "approx"
    }
  ],
  "hint": null
}
```

注意：

- 高德地图展示统一使用 `gcj02`。
- 如果 `origin` 是浏览器定位，后端可能返回 `wgs84`，前端会在渲染前转换为近似 `gcj02`。
- 如果 `polyline` 为空，前端会用 `origin + destination` 生成一条 fallback 线。

### `client_location`

用于构造高德导航 URI 的起点。

```json
{
  "lng": 121.4,
  "lat": 31.2,
  "accuracy_m": 25,
  "city": "上海市",
  "region_text": "上海市"
}
```

`client_location` 的语义仍是浏览器定位，即 WGS84。进入地图或 URI 之前，前端负责转换。

### `destination`

用于路线卡片展示终点名称、地址，以及优先选择终点坐标。

如果 `destination.geo.gcj02` 存在，前端优先使用它作为高德导航终点；否则退回 `route.destination`。

### `view_payload`

`view_payload` 是轻量展示指令，不承载任意 HTML。

当前建议字段：

```json
{
  "version": 1,
  "scene": "agent_route",
  "title": "从当前位置前往 Arcade One"
}
```

`scene` 当前支持：

- `agent_candidates`
- `agent_route`

如果没有 `view_payload.scene`，前端会按规则推断：

- 有 `route` 时视为 `agent_route`
- 否则有 `shops` 时视为 `agent_candidates`

## 前端渲染链路

### 1. `App.tsx`

职责：

- 从 session detail 提取 `ChatMapArtifacts`
- 从 SSE `navigation.route_ready` 提前生成 pending route artifacts
- 把 `activeMapArtifacts` 传给 `ChatPanel`

关键状态：

```ts
const [activeMapArtifacts, setActiveMapArtifacts] = useState<ChatMapArtifacts | null>(null);
```

### 2. `ChatPanel.tsx`

职责：

- 正常渲染 user / assistant Markdown 消息
- 当 `mapArtifacts` 存在时，在 assistant 回复下方追加 `AgentMapCard`
- 如果回复还在 streaming，则也允许先显示地图卡片

这意味着地图卡片是 Markdown 正文的增强层，不替代正文。

### 3. `AgentMapCard.tsx`

职责：

- 判断 scene：`agent_candidates` 或 `agent_route`
- 规范化路线坐标
- 生成地图中心点、标题、副标题和高德跳转动作
- 分发到底层地图组件

候选机厅场景：

```text
AgentMapCard
  -> AmapMapCanvas
  -> AmapShopMarkers
  -> MapActionBar
```

路线场景：

```text
AgentMapCard
  -> AmapMapCanvas
  -> AmapRouteOverlay
  -> MapActionBar
```

### 4. 地图基础组件

`AmapMapCanvas`：

- 加载 AMap JS API
- 创建 / 销毁 map 实例
- 暴露 `AmapRuntime`

`AmapShopMarkers`：

- 根据 `shops[*].geo.gcj02` 画 marker
- 支持 selected marker
- 点击 marker 后更新选中机厅

`AmapRouteOverlay`：

- 根据 `route.polyline` 画路线
- 根据 `route.origin` / `route.destination` 画起终点
- 坐标非 GCJ02 时先走前端标准化

`MapActionBar`：

- 渲染 `网页打开`
- 渲染 `打开高德 App`

## Markdown 里的推荐写法

当前不建议让模型在 Markdown 里直接输出：

```html
<AgentMapCard ... />
```

也不建议允许任意 HTML，因为这会绕过 React 的类型、坐标转换和 URI 构造逻辑。

推荐做法是保持：

```markdown
我给你找到了几家候选机厅，地图卡片在下面。
```

地图由同一条 assistant turn 附带的 structured artifacts 渲染。

如果后续确实希望“Markdown 里声明地图”，建议只支持白名单 code fence，例如：

````markdown
```arcadegent-map
{
  "version": 1,
  "scene": "agent_route",
  "title": "从当前位置前往 Arcade One"
}
```
````

解析规则也应该是：

1. 只识别 `arcadegent-map` fence。
2. 只允许 JSON。
3. JSON 只作为 `view_payload` 或 `ChatMapArtifacts` 的补充。
4. 最终仍然交给 `AgentMapCard` 渲染。
5. 不允许 HTML、JS、style 或任意组件名。

这样可以保留 Markdown 的可读性，又不会把地图渲染变成不受控的模型输出。

## 最小后端输出示例

候选机厅地图：

```json
{
  "shops": [
    {
      "source_id": 101,
      "name": "Arcade One",
      "address": "Nanjing East Road",
      "city_name": "上海市",
      "geo": {
        "gcj02": {
          "lng": 121.475,
          "lat": 31.228,
          "coord_system": "gcj02",
          "source": "geocode",
          "precision": "approx"
        },
        "source": "geocode",
        "precision": "approx"
      }
    }
  ],
  "view_payload": {
    "version": 1,
    "scene": "agent_candidates",
    "title": "候选机厅"
  }
}
```

路线地图：

```json
{
  "shops": [],
  "route": {
    "provider": "amap",
    "mode": "walking",
    "distance_m": 1280,
    "duration_s": 960,
    "origin": {
      "lng": 121.4,
      "lat": 31.2,
      "coord_system": "wgs84",
      "source": "client",
      "precision": "approx"
    },
    "destination": {
      "lng": 121.475,
      "lat": 31.228,
      "coord_system": "gcj02",
      "source": "route",
      "precision": "approx"
    },
    "polyline": [],
    "hint": null
  },
  "destination": {
    "source_id": 101,
    "name": "Arcade One",
    "address": "Nanjing East Road",
    "geo": null
  },
  "client_location": {
    "lng": 121.4,
    "lat": 31.2,
    "region_text": "上海市"
  },
  "view_payload": {
    "version": 1,
    "scene": "agent_route",
    "title": "从当前位置前往 Arcade One"
  }
}
```

## 调试检查清单

如果地图卡片没有出现，先检查：

1. `ChatSessionDetail` 是否包含 `shops`、`route`、`destination` 或 `view_payload`。
2. `App.tsx` 的 `activeMapArtifacts` 是否非空。
3. `ChatPanel` 是否收到 `mapArtifacts`。
4. `AgentMapCard` 是否推断出正确 scene。
5. 前端是否配置 `VITE_AMAP_WEB_KEY`，或测试环境是否安装了 `window.__ARCADEGENT_AMAP_MOCK__`。
6. 候选 marker 是否存在 `shop.geo.gcj02`。
7. 路线是否至少存在 `route.destination`，或者 `destination.geo.gcj02`。

