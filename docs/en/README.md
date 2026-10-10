# Arcadegent Documentation

[简体中文](../README.md) · English · [日本語](../ja/README.md)

This is the public documentation index. It contains two categories:

- [Guides](guidings/): usage and extension guides.
- [Engineering details](dev-details/): implemented or near-complete engineering details.

See the [project README](../../README.md) for installation, running, deployment, and API entry points.

## Usage and extension

| Document | Description |
| --- | --- |
| [Writing the Built-in Tool Manifest](guidings/内建工具清单编写指南.md) | Manifest, services, and schema format for adding built-in tools |
| [Skill Configuration and Extension Guide](guidings/技能配置与扩展指南.md) | Skill format, discovery, on-demand reading, and context isolation |

## Engineering details

| Document | Description |
| --- | --- |
| [Rendering Agent Map Results](dev-details/智能体地图结果渲染.md) | Backend artifact contract and frontend map rendering |
| [Browser Geolocation and Reverse Geocoding](dev-details/浏览器定位与逆地理编码.md) | Geolocation, reverse geocoding, and Agent context injection |
| [Session Run Lifecycle and SSE](dev-details/会话运行生命周期与SSE.md) | Accepting, cancelling, and finishing runs; SSE replay and termination |
| [ReAct Runtime Core Logic](dev-details/ReAct运行时核心逻辑.md) | Main-agent loop, streamed model output, and output_id rules |
| [Agent Context Payload Design](dev-details/智能体上下文载荷设计.md) | Agent context payload structure and constraints |
| [Dynamic Tool Registration: Implementation Notes](dev-details/动态工具注册实现.md) | Built-in and MCP tool registration flow |
| [Skill Resource Directory Support](dev-details/技能资源目录支持.md) | Skill resource-directory capabilities and extension boundaries |
| [Evaluation Workbench: Architecture and Implementation](dev-details/评测工作台架构与实现.md) | Offline contracts, online evaluation, evidence, and report boundaries |

## Public documentation boundary

Do not publish or commit real arcade data, scraping outputs, runtime caches, QA/evaluation reports, database exports, production .env files, API keys, Supabase service-role keys, map-service keys, private data paths, temporary issue discussions, or local absolute paths.

Describe data paths using general terms such as “JSONL-compatible data source,” “private data directory,” and “database read model.” Keep specific filenames, batch outputs, and import scripts in local documentation.
