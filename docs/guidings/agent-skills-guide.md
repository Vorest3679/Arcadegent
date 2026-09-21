# Agent Skills 配置与扩展

Arcadegent 按 [Agent Skills 格式规范](https://agentskills.io/specification) 组织技能，采用 [官方接入指南](https://agentskills.io/client-implementation/adding-skills-support) 的渐进加载流程：启动/轮次开始发现名称和描述，模型选择后读取正文，需要时再读取引用资源。技能文件不需要注册到 Python 列表或 tools manifest。

## 新增技能

默认目录是 `backend/app/agent/context/skills/`。新增一个直接子目录，例如：

```text
arcade-price-explanation/
├── SKILL.md
├── references/
│   └── pricing.md
├── scripts/             # 可选；当前运行时只支持读取文本，不执行
└── assets/              # 可选；当前读取工具仅接受 UTF-8 文本
```

`SKILL.md` 示例：

```markdown
---
name: arcade-price-explanation
description: Explain arcade token and per-play prices. Use when a user asks about listed machine pricing.
metadata:
  author: arcadegent
  version: "1.0"
---

Read the current shop pricing and matching arcade fields.
See [pricing definitions](references/pricing.md) when distinguishing token price from per-play price.
Do not invent discounts that are absent from the observed results.
```

字段遵循标准：`name` 必填，最长 64 字符，与目录名一致；名称使用小写字母/数字/连字符，不允许首尾连字符或连续连字符，并按官方参考校验器支持 Unicode 字母数字与 NFKC 规范化。`description` 必填，非空且最长 1024 字符。可选字段是 `license`、`compatibility`（1–500 字符）、`metadata`（字符串键值映射）、`allowed-tools`（空格分隔的字符串）。扩展信息放入 `metadata`，顶层未知字段及重复 YAML 键会被拒绝。

Markdown 正文没有必需的章节结构。标准建议正文少于 500 行；这不是本项目的硬性行数限制。资源路径以技能目录为基准，不以引用文件所在子目录为基准。

下一次用户轮次、worker 执行或 `list_skills` 调用会扫描目录，发现新增、修改和删除。无需修改代码或重启；本轮已加载内容保持原快照。多个根目录中同名技能全部隔离，修复冲突并刷新后恢复。

## skill.config.py

`backend/skill.config.py` 是受信任的项目级 Python 配置文件，必须导出 `config = SkillConfig(...)`。启动时读取，修改后重启后端。技能目录内的 Python 文件不会作为配置加载。文件通过路径加载，因此点号文件名不会影响运行时。

```python
from app.agent.skills.config import SkillConfig

config = SkillConfig(
    roots=["app/agent/context/skills", "../.agents/skills"],
    disabled_skills=["experimental-skill"],
    agent_skills={
        "search_worker": ["search-result-reading", "response-composition"],
        "navigation_worker": [],
    },
    max_file_bytes=64 * 1024,
    max_loaded_bytes=256 * 1024,
)
```

- `roots` 的相对路径以 `skill.config.py` 所在目录为基准；支持多个目录，不自动扫描操作者主目录。
- 默认 `agent_skills={}`，所有 agent 可见全部技能。某 agent 出现在映射中时只允许列出的技能；空列表表示没有技能。全局禁用优先。
- 大小上限按 UTF-8 原文件字节计数，`SKILL.md` 包括 frontmatter。超限返回错误，不截断。重复读取同一规范化相对路径不重复计费。
- Docker 构建包含配置和默认技能目录。增加外部根目录时，需在容器内提供相应文件或挂载。

这些配置、冲突隔离、严格校验、刷新时机和大小限制是 Arcadegent 的宿主策略，不属于 Agent Skills 通用格式字段。官方接入指南允许宽松兼容；本项目按格式规范严格校验。`allowed-tools` 被解析保留，但不会增加现有 agent 的工具权限。资源目录的详细能力矩阵和未来扩展边界见 [Agent Skills 资源目录支持与扩展预留](../dev-details/agent-skills-resource-support.md)。

## 模型如何使用

基础 prompt 说明使用流程；`context_payload.directory.skills` 提供当前 agent 可用的 `name`、`description`。两个工具由现有 builtin manifest 注册：

```text
list_skills({})
  → {"skills": [{"name": "search-result-reading", "description": "..."}]}

read_skill({"name": "search-result-reading"})
  → {"name": "search-result-reading", "path": "SKILL.md", "status": "loaded"}

read_skill({"name": "arcade-price-explanation", "path": "references/pricing.md"})
  → {"name": "arcade-price-explanation", "path": "references/pricing.md", "status": "loaded"}
```

`read_skill` 省略 `path` 时默认为 `SKILL.md`。成功后，下一次模型请求的 loaded skill resources 区块包含正文或资源文本；工具历史只保存回执。目录已提供时不必重复调用 `list_skills`，只有需要刷新发现时才调用。

当前 agent 身份由 runtime 绑定，模型不能通过工具参数冒充其他 agent。读取只能访问可用技能内的文本，拒绝绝对路径、`..`、越界符号链接、非普通文件、非 UTF-8/二进制和超限内容。失败不登记为已加载，可调整请求后重试。

## 模块边界与迁移

- `SkillRegistry` 负责目录快照、标准元数据和受限读取，供工具与 context 共用。刷新通过锁保护，一次性替换目录；技能非法时记录错误码，不输出 YAML 内容或本机路径。
- `SkillExecution` 保存本轮正文和资源快照，不属于持久化 working memory。runtime 使用 task-local 绑定传递可信 agent 身份与执行状态，builtin executor 通过依赖注入取得 registry。
- main agent 每个用户轮次重置；每次 worker 调用从空状态开始。worker 结果和会话序列化不携带技能正文，主 agent 与 worker 不互相继承已加载资源。
- `ContextBuilder` 仅组织目录及本轮已加载资源，不负责文件发现、权限或执行。正文不重复写入历史消息或 recent tool results。

旧 `skills/*.md` 迁为 `skills/<name>/SKILL.md`，下划线名称改为连字符。原 YAML `skill_files` 和 profile 字段已移除，检测到旧 YAML 字段会明确报错，需迁到 `skill.config.py`。原 `ContextBuilder(skill_root=...)` 改为注入 `skill_registry`。

刷新是可取消的异步边界：runtime 必须先保存用户输入，再等待目录刷新，否则立即取消可能丢失本轮输入。该顺序由现有取消/继续会话回归覆盖。

## 验证范围

`evaluate/checks/test_skills.py` 覆盖标准字段、动态增删改、冲突、白名单、读取边界、容量、并发隔离和序列化；API 集成测试模拟模型，经过实际工具分发完成技能发现、正文与引用加载、业务查询、两次 worker 执行和下一轮重置。

使用后端虚拟环境运行 `python -m pytest -q`。这些验证不依赖真实模型；它们证明运行时契约与隔离行为，不代表已评估真实模型选择技能的准确率。

测试总数会随 pytest 配置、运行目录和选择的测试集变化，不作为固定契约。本次改造在仓库根目录运行 `backend/.venv/bin/python -m pytest -q`，结果为 264 项通过；`git diff --check` 通过。未执行真实模型选择质量评测或 Docker 镜像构建。
