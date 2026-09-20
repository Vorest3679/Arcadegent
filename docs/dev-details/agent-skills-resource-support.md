# Agent Skills 资源目录支持与扩展预留

Arcadegent 遵循 [Agent Skills 目录约定](https://agentskills.io/specification)：每个技能是一个目录，必须包含 `SKILL.md`，并可以携带 `scripts/`、`references/`、`assets/` 以及其他文件或目录。

这份文档记录宿主运行时对这些目录的实际支持范围。Agent Skills 标准规定目录和文件的组织方式，不规定宿主必须提供脚本执行器、二进制资产传输或特定工具名称。

## 当前支持矩阵

| 路径 | 标准用途 | 当前状态 | 读取方式 |
| --- | --- | --- | --- |
| `SKILL.md` | 元数据与技能指令 | 已支持 | 发现阶段读取 frontmatter；激活时读取 Markdown 正文 |
| `references/` | 按需加载的参考资料 | 已支持 | `read_skill(name, path="references/...")`，UTF-8 文本 |
| `scripts/` | 技能附带的可执行脚本 | 目录兼容，执行未启用 | 可以读取脚本文本；不会执行、导入或赋予脚本新的工具权限 |
| `assets/` | 模板、图片、数据等静态资源 | 目录兼容，文本读取已支持 | 当前只接受 UTF-8 文本；二进制文件返回安全错误 |
| 其他文件/目录 | 技能作者自定义资源 | 已支持文本读取 | 使用技能根目录下的规范化相对路径 |

因此，下面的结构可以直接被发现：

```text
skill-name/
├── SKILL.md
├── scripts/
│   └── extract.py
├── references/
│   └── REFERENCE.md
├── assets/
│   ├── template.txt
│   └── example.json
└── examples/
    └── output.md
```

发现阶段只扫描配置 root 的直接子目录，并寻找名称严格为 `SKILL.md` 的文件。资源目录不会单独出现在技能目录中；资源只有在模型激活某个技能后，通过同一个技能名和相对路径读取。

## 统一安全边界

所有资源都经过 `SkillRegistry`：

- 路径必须相对于技能根目录，拒绝绝对路径、`..`、Windows 路径语法、越界符号链接和非普通文件。
- 文件必须是有效 UTF-8 文本，拒绝二进制控制字符、FIFO 和超出 `max_file_bytes` 的内容。
- 单次 agent 执行受 `max_loaded_bytes` 限制；同一技能和规范化路径重复读取使用本轮快照，不重复计费。
- 资源读取遵循 agent 白名单和全局禁用规则。`allowed-tools` frontmatter 只作为标准元数据保存，不会扩大 Arcadegent 的工具权限。
- `SkillExecution` 保存本轮已经加载的正文和资源；不会把原文写入持久化会话、工具历史或 worker 回传包。

## 后续扩展预留

后续如果需要完整支持标准目录中的可执行或二进制资源，应在现有 `SkillRegistry` / `SkillExecution` 边界上扩展，而不是让模型直接访问文件系统：

1. **脚本执行器**：新增显式的 `run_skill_script` 能力，要求脚本路径 allowlist、解释器 allowlist、超时、输出大小、工作目录隔离和网络/文件系统权限策略。`allowed-tools` 只能参与声明和过滤，不能单独授权执行。
2. **二进制资产读取**：新增独立的 `read_skill_asset` 返回类型，区分 UTF-8 文本、图片、音频和其他二进制，并设置 MIME、大小和传输上限。`read_skill` 保持文本契约不变。
3. **资源清单**：可在 discovery 记录中增加受限的资源索引或在激活后提供 `list_skill_resources`，避免模型猜测路径；索引不得泄露技能 root 的宿主绝对路径。
4. **版本与缓存**：为 `SkillResource` 增加内容哈希和技能快照版本，使脚本输出、二进制资产和长任务能够审计到同一份技能快照。
5. **信任策略**：项目级、用户级和组织级 roots 可分别配置可信度；不可信 root 默认只显示元数据，激活和执行需显式授权。

这些能力目前不属于本次改造的默认行为。新增资源类型时必须保持现有 `read_skill` 文本接口向后兼容，并为路径安全、权限、大小、取消和 worker 隔离补充契约测试。

## 作者约定

技能作者可以现在就按标准创建完整目录，并在 `SKILL.md` 中使用相对路径引用资源。对于当前运行时，请把需要模型阅读的资料保存为 UTF-8 文本；把脚本当作供未来执行器使用的源码；把图片、音频等二进制资产作为未来能力的预留文件，并不要假设模型当前可以读取它们。

标准格式和配置方式见 [Agent Skills 配置与扩展](../guidings/agent-skills-guide.md)。
