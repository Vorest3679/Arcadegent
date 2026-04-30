# 浏览器定位与高德逆地理链路

更新时间：2026-03-20

## 背景

为了让 agent 在会话启动时感知用户当前位置的“隐藏信息”，前端会在新会话首条消息发送前采集浏览器定位；如果缓存位置不可复用，则由后端调用高德 REST 逆地理接口，把地区信息补齐后再注入 agent 上下文。

设计目标：

- 高德 API Key 只保留在后端，不暴露给浏览器。
- 浏览器定位和地区解析解耦：前端只负责拿坐标，后端负责逆地理。
- agent 在 instruction 里能直接看到坐标和地区，而不是只能从工具返回里间接推断。
- 定位失败或高德不可用时，对话仍可继续，只是地区信息会缺失。

## 总体链路

```text
页面打开
  -> 前端后台预热定位并写入本地缓存

新会话首条消息发送
  -> 前端再次请求浏览器定位
  -> 若与缓存位置等价，则直接复用缓存中的地区信息
  -> 否则 POST /api/location/reverse-geocode
  -> 后端调用高德 /v3/geocode/regeo
  -> 返回 province/city/district/township/formatted_address
  -> 前端把 location 注入 POST /api/chat/sessions
  -> 后端把 client_location 写入 session memory
  -> ContextBuilder 显式注入 instruction
```

## 前端链路

相关文件：

- `apps/web/src/lib/clientLocation.ts`
- `apps/web/src/api/client.ts`
- `apps/web/src/App.tsx`

### 0. 浏览器定位前置条件

浏览器的 `navigator.geolocation` 在公网域名下只会在 HTTPS 安全上下文中工作；`localhost` / `127.0.0.1` 是开发环境例外。线上如果通过 `http://` 打开页面，浏览器通常不会弹定位授权框，前端会按定位失败处理，最终不会向会话请求注入 `location`。

部署时需要确认：

- 站点用 `https://` 访问，80 端口跳转到 443。
- 外层 Nginx、CDN 或 iframe 容器没有用 `Permissions-Policy` 禁止 `geolocation`。
- 用户浏览器没有在站点设置里保留“拒绝定位”的旧权限。

### 1. 页面打开时预热缓存

`App.tsx` 在初始化时调用 `warmupClientLocationCache()`，让页面打开后先做一次后台定位预热。

`clientLocation.ts` 中的关键逻辑：

- `getCurrentBrowserCoordinates(maximumAge)`：调用浏览器 `navigator.geolocation.getCurrentPosition`
- `readLocationCache()` / `writeLocationCache()`：从 `localStorage` 读写缓存
- `areEquivalentLocations()`：用 haversine 距离判断新旧坐标是否可视为同一位置
- `warmupClientLocationCache()`：页面打开时预热
- `resolveClientLocationForSessionStart()`：新会话发送前再取一次最新坐标

当前实现把“相同位置”定义为 30 米以内，避免浏览器定位微小抖动导致频繁逆地理。

### 2. 会话启动前决定是否调后端逆地理

`resolveLocation()` 的逻辑是：

1. 读取缓存位置
2. 再次请求浏览器当前坐标
3. 如果缓存位置和当前坐标等价，且缓存里已有地区信息，则直接复用
4. 否则调用 `reverseGeocodeLocation(coords)`
5. 把新的坐标 + 地区信息重新写回缓存

### 3. 前端调用的后端接口

接口地址：

```text
POST /api/location/reverse-geocode
```

请求体：

```json
{
  "lng": 121.4737,
  "lat": 31.2304,
  "accuracy_m": 35
}
```

响应体：

```json
{
  "lng": 121.4737,
  "lat": 31.2304,
  "accuracy_m": 35,
  "province": "上海市",
  "city": "上海市",
  "district": "黄浦区",
  "township": "外滩街道",
  "adcode": "310101",
  "formatted_address": "上海市黄浦区外滩街道...",
  "region_text": "上海市黄浦区外滩街道...",
  "resolved": true
}
```

## 后端链路

相关文件：

- `backend/app/protocol/messages.py`
- `backend/app/api/http/location.py`
- `backend/app/services/amap_reverse_geocoder.py`
- `backend/app/core/container.py`

### 1. DTO 定义

`messages.py` 中新增了三类模型：

- `ClientLocationContext`
- `ReverseGeocodeRequest`
- `ReverseGeocodeResponse`

其中 `ChatRequest.location` 现在就是 `ClientLocationContext`，所以前端把逆地理结果回传给会话接口后，后端可以直接带进 agent 运行时。

### 2. FastAPI 入口

`location.py` 暴露了：

```text
POST /api/location/reverse-geocode
```

这个接口只做两件事：

- 校验请求体
- 调用 `container.reverse_geocoder.reverse_geocode(request)`

### 3. 容器装配

`container.py` 在启动阶段创建 `AMapReverseGeocoder`，复用已有的高德 REST 配置：

- `AMAP_API_KEY`
- `AMAP_BASE_URL`
- `AMAP_TIMEOUT_SECONDS`

因此路线规划 REST fallback 和逆地理解析共用同一套高德配置。

### 4. 调用高德 REST

`AMapReverseGeocoder.reverse_geocode()` 会发起如下请求：

```text
GET {AMAP_BASE_URL}/v3/geocode/regeo
  ?key=...
  &location={lng},{lat}
  &extensions=base
  &roadlevel=0
```

当前实现使用 Python 标准库 `urllib.request.urlopen()` 直接请求。

### 5. 解析高德返回

服务会从高德返回里提取：

- `province`
- `city`
- `district`
- `township`
- `adcode`
- `formatted_address`

然后组装 `region_text`，并返回给前端。

特殊处理：

- 直辖市场景下，高德的 `city` 可能返回空数组，此时会回退为 `province`
- 如果未配置 `AMAP_API_KEY`、网络失败、或返回格式异常，会直接返回 `resolved=false`

## 注入 agent 上下文

相关文件：

- `backend/app/agent/runtime/react_runtime.py`
- `backend/app/agent/context/context_builder.py`

### 1. 写入 session memory

`ReactRuntime` 在收到 `ChatRequest` 后，会把 `request.location` 存进：

```text
state.working_memory["client_location"]
```

这样即使后续轮次没有再次上传定位，session 里也还能保留这份上下文。

### 2. 注入 instruction

`ContextBuilder` 会优先读取当前 request 的 `location`，否则回退到 `working_memory["client_location"]`。

然后做两层注入：

- `runtime_hint.client_location`
- 一段显式的 `Client location context:` instruction 文本

这样 agent 启动时能直接看到：

- WGS84 经度纬度
- 浏览器精度半径
- 省 / 市 / 区 / 街道
- 格式化地址

从而更容易感知“附近”“当前城市”“路线起点”“区域偏好”之类的隐含信息。

## 降级与容错

- 非 HTTPS 公网页面：浏览器不会弹定位授权，前端不会注入 location
- 浏览器拒绝定位：前端会回退到缓存；没有缓存时则不注入 location
- 高德逆地理失败：只保留经纬度，不阻塞发消息
- 后端未配置 `AMAP_API_KEY`：`resolved=false`，聊天仍可继续

## 关键代码入口

- 前端定位缓存：`apps/web/src/lib/clientLocation.ts`
- 前端逆地理 API：`apps/web/src/api/client.ts`
- 前端会话发送：`apps/web/src/App.tsx`
- DTO：`backend/app/protocol/messages.py`
- 后端接口：`backend/app/api/http/location.py`
- 高德逆地理服务：`backend/app/services/amap_reverse_geocoder.py`
- 运行时注入：`backend/app/agent/runtime/react_runtime.py`
- instruction 注入：`backend/app/agent/context/context_builder.py`
