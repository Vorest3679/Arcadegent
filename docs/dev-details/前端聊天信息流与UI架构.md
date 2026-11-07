# 前端聊天信息流与 UI 架构

本文说明 Web 端聊天视图的数据结构（单一信息流数组 + 工具状态表）、SSE 事件如何折叠进信息流、实时 run 与历史的交接，以及 UI 层（Tailwind CSS v4 + shadcn/ui）的约定。

## 目标

- 聊天从用户发送开始，按到达顺序展示：用户消息 → 中间回复 → 工具调用 → 最终回复 → 地图 → 错误。
- 所有条目放在一个数组里，SSE 事件到达后折叠（追加或原地更新）进去；不再用多个数组分别保存历史、实时步骤、进度和流式文本再合并。
- 工具的完成/失败异步到达，用独立的状态表跟踪，条目本身只保存身份信息。
- 最终回复到达后中间回复不折叠，全部保留可见。

## 状态划分

| Store | 内容 |
| --- | --- |
| `stores/chatFeedStore.ts` | 当前会话的 `items: FeedItem[]` 与 `toolStatus: Record<key, ToolStatus>` |
| `stores/runStore.ts` | 正在跟随的 run 的传输状态：连接、当前阶段、`awaitingAssistant`（锁定输入框）、run id、重连提示 |
| `stores/sessionStore.ts` | 会话列表、当前会话 id/状态、加载与发送标记 |
| `stores/appStore.ts` | 视图模式、侧边栏、输入框内容 |

## 侧栏：会话搜索

- “历史会话”右侧是搜索入口；点击后入口向右展开为输入框，图标随左边界左移，右侧出现取消按钮。取消或按 Escape 清空关键词并恢复历史列表。
- 搜索状态只存在于 `AppSidebar`（`searchOpen`、`inputValue`、`keyword`、`searchResults`）。`inputValue` 跟随输入框实时变化；`keyword` 是已提交的检索词。关键词变化触发 `useEffect`，用 `lib/sessionSearch.ts` 过滤当前会话列表；会话列表因发送、删除或后台刷新变化时，同一 effect 重新计算。
- 键盘拼音/中文输入法组合期间只更新 `inputValue`，不提交 `keyword`；等 `compositionend`（失焦作为移动端兜底）后再提交，避免 `xingji` 这类未转换拼音串提前触发过滤。直接以英文模式输入的拼音仍会即时检索。
- 命中范围是标题、预览和 `ChatSessionSummary.artifact_shops`。机厅名称按普通文本匹配；带拼音的机厅再走一个独立分支，先去掉 `-`、空格等分隔符，再比较 `name_pinyin`，因此 `yin-he`、`yinhe` 都能命中“银河电玩城”。
- 搜索结果沿用普通会话行样式，不另做反色区分；选中后该行使用普通激活样式。无论输入框是否有关键词，选中会话都会退出搜索模式并收起输入框。没有命中时显示“没有匹配的会话”。

## 信息流条目

定义在 `lib/chatFeed.ts`：

```ts
type FeedItem =
  | { kind: "user"; content }
  | { kind: "assistant"; content; outputId; streaming; final }
  | { kind: "tool"; callId; name; agent }
  | { kind: "map"; artifacts }
  | { kind: "error"; message };
// 每个条目都带 key、round（所属用户消息的序号）、at
```

- `assistant.final=false` 是中间回复，`final=true` 是该轮最终回复；`streaming=true` 表示文本仍在到达。
- 工具条目不保存状态。`toolStatus[item.key]` 取值为 `running | completed | failed | cancelled`，`ToolCallRow` 只订阅自己那一项。

### 稳定 key

key 由轮次和条目在轮次中的位置推出，实时与历史两条路径生成相同的 key：

| 条目 | key |
| --- | --- |
| 用户消息 | `r{n}:user` |
| 回复（中间或最终） | `r{n}:a{j}`，j 为该轮第几条回复 |
| 工具 | `r{n}:t:{call_id}` |
| 地图 | `r{n}:map` |
| 错误 | `r{n}:e{j}` |

交接时用历史替换实时信息流，React 按 key 复用已有节点，用户气泡和已显示的回复不会重新挂载或闪烁。

## SSE 折叠规则

传输层 `lib/sse/runStream.ts` 负责信封校验、按游标去重、有限次重连，并按 `output_id` 组装 `assistant.token`。`hooks/useRunStream.ts` 把回调转成对信息流的纯函数更新：

| 输入 | 处理 |
| --- | --- |
| `onText(outputId, text)` | `applyOutputText`：按 `output_id` 找当前轮回复，写入**完整文本**；没有则追加 `streaming` 回复。传完整文本而非增量，回放或重载后结果一致 |
| `onOutputSealed(outputId)` | `sealOutput`：新的 `output_id` 出现，上一段结束 |
| `tool.started` | 追加工具条目，状态置 `running`；同时结束仍在 streaming 的文本（工具调用前的文字已经完整） |
| `tool.completed` / `tool.failed` | 只更新 `toolStatus`；只有 `running` 或尚未出现的工具会变更，回放的 started 和迟到的 completed 不覆盖终态 |
| `assistant.completed` | 用 `reply` 覆盖对应 `output_id`（或最后一条 streaming 回复）的文本并标记 `final`；没有匹配则追加 |
| `session.failed` | 同上处理兜底 `reply`，再追加错误条目 |
| 地图相关事件 | `mapArtifactsForEvent` 计算后 upsert 当前轮唯一的 `map` 条目 |
| 终态 `run.state` | `finishRun`：停止所有 streaming；仍在运行的工具标记为 `failed`（取消时为 `cancelled`） |

`useSmoothText` 只做展示层平滑：streaming 期间每帧补出剩余文本的一部分，结束后立即显示全文；信息流里保存的始终是真实文本。

## 展示分组

信息流数组本身保持扁平，分组只在渲染层（`components/chat/ChatFeedList.tsx` 的 `toSegments`）进行：

- **工具组**：同一轮中两段回复之间连续的工具调用合成一组（`ToolGroup`），key 为 `g:` 加组内第一个工具条目的 key，组变长或交接后都不变。
- **默认展开状态**：只有 SSE 流仍打开（`runStore.runOpen`：`startStream` 置为 true，进入终态、放弃重连或本地提交时置为 false）的那一轮工具组默认展开。流结束后收起为“调用了 n 个工具”，有失败时附带“n 个失败”。历史与之后的新轮次同样默认收起。
- **手动开关优先**：用户手动展开或收起的状态按组 key 记录在 `ChatFeedList` 中，切换会话时清空。因为 key 稳定，交接和后续新轮次都不会改变用户选择的状态。
- **地图位置**：一轮的地图条目始终显示在该轮所有条目之后，避免实时 run 中途插入的地图把工具组切开。

地图卡片一旦有可绘制的内容（`domain.arcade.results_ready` 带来的候选机厅，或 `navigation.route_ready` 带来的路线），就在卡片上方显示一张不可交互的高德缩略图，不必等 run 结束；路线规划中只显示紧凑的一行。缩略图是 `AgentMapCard` 的 `preview` 变体（禁用拖拽和缩放、不加控件，带路线或标记点），点击打开大图面板。缩略图在卡片接近视口时才挂载（`useInView`），长会话不会一次性创建多个地图实例。

## 交接与恢复

- **发送**：乐观追加用户条目，`awaitingAssistant=true` 锁定输入框；dispatch 失败时恢复发送前的信息流快照、把文字放回输入框，并追加错误条目。
- **终态**：重新拉取 session detail，用 `feedFromDetail` 替换信息流，再释放输入框。detail 加载失败时 `commitRunLocally` 直接把当前信息流作为历史。
- **刷新时 run 仍在运行**：无游标订阅会从头回放整个 run，因此先用 `withoutOpenRound` 去掉历史中未完成轮次的步骤，再由回放重建，避免中间回复重复。
- **`stream.reset`**：服务端无法回放，加载 detail 但不重连，流从游标继续。detail 中还没有正在输出的那段文本（服务端只持久化已结束的输出），所以用 `mergeOpenRound` 保留实时信息流里当前轮 detail 缺少的条目及其工具状态，之后的 token 按 `output_id` 继续更新被保留的条目。若直接用 detail 替换，正在输出的文本会消失，直到交接才重新出现。
- **错误**：运行失败、加载失败、发送失败、重连耗尽都作为 `error` 条目进入信息流，不再使用单独的错误横幅。

## 工具白名单

`lib/sse/chatStream.ts` 的 `TOOL_LABEL` 是白名单：白名单工具显示中文别名，并在名称前显示对应的 lucide 图标（`FeedItems.tsx` 的 `TOOL_ICON`）。运行中图标高亮并带脉冲动画，结束后变为静态。非白名单工具（如 MCP 工具）照常列出，显示原名、不带图标。行尾状态图标都带 `role="img"` 和中文 `aria-label`（执行中/已完成/失败/已取消）。

新增内建工具时，在 `TOOL_LABEL` 和 `TOOL_ICON` 中同时登记。

## UI 层约定

- **样式**：Tailwind CSS v4（`@tailwindcss/vite`）+ shadcn/ui（`components.json`，组件位于 `src/components/ui/`），动画类来自 `tw-animate-css`。原有的分模块 CSS 已移除，全局样式只保留 `src/styles.css`。
- **主题**：沿用原有品牌色，以 oklch 写成 shadcn token（`--primary` 品牌绿、`--accent` 浅绿选中面、`--highlight` 琥珀强调色、`--destructive` 错误色），背景保留原来的渐变。
- **字体**：全站统一使用 `--font-sans` 系统字体栈（PingFang SC / Noto Sans SC / 微软雅黑等），代码使用 `--font-mono`；不加载远程字体。
- **React 18 兼容**：shadcn 生成的组件按 React 19 编写（`ref` 作为普通 prop）。在 React 18 下，凡是需要被传 ref 的组件都要改用 `forwardRef`，目前是 `Textarea` 和 `SheetOverlay`。`SheetOverlay` 是 Radix Portal 的直接子节点，`Presence` 需要拿到它的 DOM 节点来判断退出过渡是否结束；拿不到时 Sheet 关闭后可能一直不卸载，控制台会出现 “Function components cannot be given refs” 警告。新增 shadcn 组件后，留意这条警告。
- **布局**：宽屏（≥1024px）侧边栏固定在左侧；窄屏改为左侧 Sheet 抽屉，由顶栏的「打开侧边栏」按钮打开。聊天内容居中、最大宽度 3xl，输入框固定在底部（Enter 发送，Shift+Enter 换行，输入法组合输入时不发送）。地图大图见 [智能体地图结果渲染](./智能体地图结果渲染.md)。
- **测试选择器**：E2E 使用 `data-testid` 与 `data-kind` / `data-final` / `data-streaming` / `data-status` 属性，不依赖样式类名。辅助函数见 `apps/web/tests/e2e/test-support.ts`。

## shadcn/ui 在本项目中的使用

官网入口：[shadcn/ui](https://ui.shadcn.com/)、[组件目录](https://ui.shadcn.com/docs/components)、[Vite 安装指南](https://ui.shadcn.com/docs/installation/vite)。

本项目采用 shadcn/ui 的本地组件源码模式，基础组件位于 `apps/web/src/components/ui/`，业务组件通过 `@/components/ui/...` 导入。`@/` 映射到 `apps/web/src/`，不是 npm 包名。`apps/web/components.json` 配置为 `new-york` 风格、TypeScript、Lucide 图标、CSS 变量主题，且 `rsc: false`。当前组件使用 Radix UI 体系；查阅官网时应选择 Radix UI 版本，下面的链接均明确指向该版本。

> 一句话：仓库里没有 `shadcn` npm 包，组件是复制进来的源码；新增依赖分成 Tailwind（样式）、Radix（行为）、Lucide（图标）和类名工具 `cn` 四层，删掉任意一层都要重写对应能力。完整清单和审查记录见下文。

### 已引入组件与使用位置

下表中的组件文件相对于 `apps/web/src/components/ui/`，使用位置相对于 `apps/web/src/components/`。

| 组件文件 | 本项目使用位置与用途 | 底层实现 | 官网文档 |
| --- | --- | --- | --- |
| `button.tsx` | `AppSidebar`、`AppTopbar`、`chat/ChatComposer`、`arcade/ArcadeSearchPanel`、`map/MapActionBar`：新建、搜索、发送、分页、地图操作等按钮 | 原生 button；本地变体表；`asChild` 使用 Radix Slot | [Button](https://ui.shadcn.com/docs/components/radix/button) |
| `input.tsx` | `arcade/ArcadeSearchPanel`：机厅名称、页码等输入 | 原生 input | [Input](https://ui.shadcn.com/docs/components/radix/input) |
| `textarea.tsx` | `chat/ChatComposer`：多行聊天输入，配合业务组件处理高度、发送和输入法行为 | 原生 textarea；本地使用 `forwardRef` 适配 React 18 | [Textarea](https://ui.shadcn.com/docs/components/radix/textarea) |
| `native-select.tsx` | `arcade/ArcadeSearchPanel`：省市区、游戏、排序等选择 | 原生 select、option、optgroup，配合 Lucide 下拉图标 | [Native Select](https://ui.shadcn.com/docs/components/radix/native-select) |
| `checkbox.tsx` | `arcade/ArcadeSearchPanel`：机厅筛选条件 | Radix Checkbox | [Checkbox](https://ui.shadcn.com/docs/components/radix/checkbox) |
| `label.tsx` | `arcade/ArcadeSearchPanel`：通过 `htmlFor` 关联表单字段 | Radix Label | [Label](https://ui.shadcn.com/docs/components/radix/label) |
| `card.tsx` | `arcade/ArcadeSearchPanel`、`arcade/ArcadeDetailPanel`：检索与详情容器 | 原生 div 的结构和样式封装 | [Card](https://ui.shadcn.com/docs/components/radix/card) |
| `badge.tsx` | `arcade/ArcadeSearchPanel`、`chat/FeedItems`、`map/AgentMapCard`：结果、阶段与地图状态标签 | 原生 span；本地变体表；支持 Radix Slot | [Badge](https://ui.shadcn.com/docs/components/radix/badge) |
| `alert.tsx` | `chat/FeedItems` 的 `ErrorMessage`：信息流中的错误提示 | 带 alert 语义的容器；本地变体表 | [Alert](https://ui.shadcn.com/docs/components/radix/alert) |
| `sheet.tsx` | `AppSidebar`：窄屏左侧导航；`chat/MapPanel`：宽屏右侧地图、窄屏底部地图面板 | Radix Dialog，封装遮罩、Portal、标题、描述和关闭按钮 | [Sheet](https://ui.shadcn.com/docs/components/radix/sheet) |

组件名称相似不代表使用了同名官方组件：`AppSidebar` 是项目自行组合的侧栏，未引入官方 Sidebar；`ArcadeSearchPanel` 内的 `Field` 是本地标签封装，未引入官方 Field。地图底部面板使用 `Sheet side="bottom"`，未引入 Drawer。`ChatComposer`、`ChatFeedList`、`FeedItems`、`ToolGroup` 与聊天信息流模型均为项目自身实现，未引入 AI Elements 的聊天或 Prompt Input 组件。

### 依赖从哪来：shadcn 不是 npm 依赖

shadcn/ui 采用「把组件源码复制进仓库」的模式，`package.json` 里没有 `shadcn` 运行时包；实际安装的是支撑这些源码的基础设施。相对改造前，`package.json` 新增 6 个运行时依赖和 1 个开发依赖，均能在官网当前文档中找到出处：

| 依赖 | 层次 | 本项目中的职责 | 删掉的后果 |
| --- | --- | --- | --- |
| `tailwindcss`、`@tailwindcss/vite` | 样式引擎（构建期） | 生成工具类 CSS，通过 `vite.config.ts` 的插件接入 Vite | 所有 `className` 失效 |
| `tw-animate-css` | 动画工具（构建期） | 提供 Sheet 的 `animate-in`、`slide-in-from-*` 等进入退出动画类（Tailwind v4 不再内置） | Sheet 失去进退场动画 |
| `radix-ui` | 行为原语（运行时） | Sheet（Dialog）、Checkbox、Label，以及 Button/Badge 的 `asChild`（Slot） | 要改用原生元素并自己实现焦点陷阱、ESC 关闭、滚动锁定 |
| `lucide-react` | 图标（运行时） | 业务界面、工具状态与 shadcn 模板图标；`components.json` 的 `iconLibrary` 也是 Lucide | 要改回内联 SVG，且与组件模板不一致 |
| `cn` | 类名工具（运行时） | `src/lib/utils.ts` 的 `cn()`：条件拼接 + 消解 Tailwind 冲突类（零依赖，官方用它替代 `clsx` + `tailwind-merge`） | 业务组件的 `className` 覆盖会互相打架 |
| `@types/node` | 类型（开发期） | `vite.config.ts` 中 `node:path`、`node:url` 的类型 | 只影响编辑器提示，不影响构建 |

官网依据：[Vite 安装指南](https://ui.shadcn.com/docs/installation/vite)（Tailwind 两个包、`@types/node` 与 `@/*` 别名）、[手动安装](https://ui.shadcn.com/docs/installation/manual)（`cn`、`lucide-react`、`tw-animate-css` 等；本项目未采用其中的 `class-variance-authority`，见下）、组件 registry（Button、Sheet 等条目声明 `radix-ui`）。

官网的组件源码使用 `cva`，但它不在 Demo/Usage 片段里：演示只展示 `<Alert>` 的调用方式，切到 Manual 标签才能看到 `alert.tsx`，三种模板（radix / base / aria）都以 `import { cva } from "class-variance-authority"` 开头，例如 [aria-nova/alert.json](https://ui.shadcn.com/r/styles/aria-nova/alert.json)。本项目为了减少依赖没有保留 `cva`，Button/Badge/Alert 改用本地 variant 表；因此这三个组件与官网模板有差异，取舍见审查记录。

官网的「手动安装」页还会出现 `shadcn` 和 `cn` 两个包名：本项目用 `pnpm dlx shadcn@latest` 临时调用 CLI，不把 `shadcn` 写进依赖；`cn` 已是项目依赖，取舍见下方的依赖审查记录。

运行环境：前端需要 Node >= 22（`cn` 的 engines 要求）。CI 的 `.github/workflows/ci.yml` 和 `apps/web/Dockerfile` 都固定 Node 22；本地 Node 20 会在安装时收到引擎警告。

业务组件使用 `variant`、`size` 与 `className` 配置基础组件；公共外观在 `components/ui/` 中维护，业务状态与交互留在业务组件、hooks 和 stores。颜色优先使用 `bg-card`、`text-primary`、`text-muted-foreground` 等主题 token，主题变量统一维护在 `src/styles.css`。

### 为什么 `node_modules` 看起来更大

依赖条目只多了 7 个，安装树却明显变大，主要来自两处：

- `radix-ui` 是 Radix 的聚合包，一个条目会带入 55 个 `@radix-ui/*` 传递包。改成逐个安装 `@radix-ui/react-*` 会把 `package.json` 从 1 条变成 4 条，传递包数量只小幅下降；以后运行 `shadcn add` 仍会装回聚合包，所以保留。
- Tailwind v4 的构建链会带入 `lightningcss`、`jiti` 等构建期包；`lucide-react` 安装体积约 48 MB，但 Vite 只打包实际 import 的图标。

生产产物（`pnpm run build`）约 392 KB JS（gzip 130 KB）与 49 KB CSS（gzip 9.6 KB），主要体积来自 React、高德 loader 与聊天逻辑，而不是 shadcn 组件。

### 依赖审查记录

- 2026-11：删除未使用的 `src/components/ui/separator.tsx`。没有业务组件引用它；`radix-ui` 的 Separator 原语仍随聚合包存在，因此 `package.json` 不需要改。
- 2026-11：Node 20 → 22（`.github/workflows/ci.yml`、`apps/web/Dockerfile`），并改用官网推荐的 `cn` 替代 `clsx` + `tailwind-merge`。直接依赖从 2 个减为 1 个。产物 gzip 约 +2.8 KB，换来少一个直接依赖和更快的类名合并。
- 2026-11：移除 `class-variance-authority`。Button/Badge/Alert 改为本地 variant 表，`clsx` 也随之从依赖树消失（此前只剩 cva 依赖它），产物 gzip 再减约 0.4 KB。代价是这三个组件与官网模板分叉；以后 `shadcn add` 带入使用 cva 的组件时，要么重装 cva，要么按同样方式改写。
- 没有移除 `radix-ui`：需要把 Sheet/Checkbox/Label/Slot 换成原生实现，会丢失焦点陷阱、ESC 关闭、滚动锁定等行为，收益不值得。
- 没有移除 `lucide-react`：安装体积大，但生产包按需 tree-shake；改回内联 SVG 会让业务组件更难维护。

### `"use client"` 与 React 18 兼容

当前 `label.tsx` 顶部保留了 `"use client"`，与 shadcn 的 [Label 上游模板](https://github.com/shadcn-ui/ui/blob/main/apps/v4/registry/new-york-v4/ui/label.tsx) 一致。

它是 [React Server Components 的客户端边界指令](https://react.dev/reference/rsc/use-client)，Next.js App Router 等支持 RSC 的环境会识别它。本项目使用 React 18、Vite 与 `ReactDOM.createRoot`，没有配置 RSC 构建链，因此这行没有实际作用，可以移除；它不是 Radix 或 shadcn 提供的语法糖，也不意味着项目依赖 Next.js。

引入或更新模板时还需检查 React 版本兼容：当前 `ChatComposer` 需要访问 textarea DOM，故 `Textarea` 已使用 `forwardRef` 转发 ref。不要直接套用 React 19 将 ref 作为普通 prop 的写法；参见 [React forwardRef 文档](https://react.dev/reference/react/forwardRef)。

## shadcn CLI 注意事项

- 本仓库 `tsconfig.json` 配置 `@/*` 路径别名，Vite 中有对应 alias。
- 组件 registry 声明 `cn`，本项目也已安装它。项目内组件统一从 `@/lib/utils` 导入 `cn`（该文件再导出 `cn` 包）；`shadcn add` 生成的组件如果直接 `import { cn } from "cn"`，可以直接使用，也可以改成 `@/lib/utils` 以保持统一。
- `shadcn add` 生成的新组件如果 import `cva`，本项目当前没有安装它：要么把该组件的变体改成和 Button/Badge/Alert 一样的本地映射，要么重新加入 `cva` 并更新本文。
- 添加组件后确认 `package.json` 没有混入无关依赖。
