# Arcadegent Docs

这里是公开仓库的文档入口。对外只公开两类文档：

- `guidings/`：面向使用和扩展的指南。
- `dev-details/`：已经落地或接近落地的工程细节。

计划、issue、历史草稿、数据链路细节和迁移材料只作为本地归档，不进入公开文档入口。

## 推荐阅读顺序

1. [项目 README](../README.md)：安装、运行、部署和 API 入口。
2. [Builtin Tool Manifest 指南](./guidings/builtin-tool-manifest-guide.md)：新增内建工具时的 manifest / schema 写法。
3. [Agent 地图 Artifacts 渲染说明](./dev-details/agent-map-artifacts-rendering.md)：后端 artifacts 契约与前端地图渲染。
4. [浏览器定位与逆地理编码](./dev-details/browser-location-reverse-geocoding.md)：定位、逆地理和 agent 上下文注入链路。
5. [Agent Context Payload Design](./dev-details/agent-context-payload-design.md)：agent 上下文 payload 的结构和约束。
6. [动态工具注册实现说明](./dev-details/dynamic-tool-registry-implementation.md)：builtin 与 MCP 工具注册链路。

## 公开边界

以下内容不要写入公开文档或提交到仓库：

- 真实机厅数据、抓取产物、运行缓存、QA 报告和数据库导出。
- 生产 `.env`、API key、Supabase service role key、地图服务密钥。
- 可反推出私有数据规模、抓取批次或生产库结构的细节。
- 计划草稿、issue 讨论、上线清单、临时调试输出、截图、浏览器 traces 和本机绝对路径。

如果必须描述数据链路，使用“兼容 JSONL 数据源”“私有数据目录”“数据库读模型”这类抽象表述；具体文件名、批次产物和导入脚本放在私有文档里。

## 文档维护约定

1. README 只保留用户真正需要的安装、运行、部署和排障信息。
2. 新增对外指南放入 `guidings/`。
3. 新增对外工程细节放入 `dev-details/`。
4. 含真实数据路径、抓取批次、数据库迁移、计划或 issue 讨论的材料只放本地归档。
