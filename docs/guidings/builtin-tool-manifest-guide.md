# Builtin Tool Manifest 指南

这份文档面向需要维护 builtin 动态工具注册表的人，重点解释：

- [`backend/app/agent/tools/builtin/tools_manifest.json`](../../backend/app/agent/tools/builtin/tools_manifest.json)
- [`backend/app/agent/tools/builtin/schemas/db_query_tool.json`](../../backend/app/agent/tools/builtin/schemas/db_query_tool.json)

现在的结构不再把所有工具都内联到一个大 JSON，也不再依赖中心化的 `schemas.py` / `handlers.py` 去枚举每个 builtin tool。思路更接近插件系统：

1. `tools_manifest.json` 只负责声明共享 `services` 和要加载哪些 tool JSON。
2. 每个 tool 自己拥有一份独立 JSON，里面写清楚名字、描述、参数 schema、executor 和 metadata。
3. `BuiltinToolProvider` 启动时自动装配这些文件。

如果你不熟 JSON，可以先记住 4 条：

- JSON 里的字符串必须用双引号 `"..."`，不能用单引号。
- 对象里的每个字段都写成 `"字段名": 值`。
- 除了最后一项，前面的每一项后面都要有逗号。
- JSON 不支持注释，所以说明文字要写到文档里，不要写进文件。

## 1. 整体结构

### 1.1 `tools_manifest.json`

当前 manifest 结构如下：

```json
{
  "services": {
    "...": {}
  },
  "tools": [
    "schemas/db_query_tool.json",
    "schemas/route_plan_tool.json"
  ]
}
```

- `services`: 描述运行时要如何构造共享依赖服务。
- `tools`: 要加载的 tool JSON 文件路径，通常写相对路径。

可以把它理解成：

1. 先按 `services` 把依赖服务装配出来。
2. 再依次读取 `tools` 里的每个 JSON 文件，把它们注册成 builtin tools。

### 1.2 单个 tool JSON

最小示例如下：

```json
{
  "name": "summary_tool",
  "kind": "function",
  "description": "Format a deterministic text summary from structured search or navigation context.",
  "executor": "app.agent.tools.builtin.executors.summary:execute",
  "input_schema": {
    "type": "object",
    "properties": {
      "topic": {
        "enum": ["search", "navigation"],
        "type": "string"
      }
    },
    "required": ["topic"],
    "additionalProperties": false
  }
}
```

这意味着新增 builtin tool 时，通常只需要：

1. 在 `tools_manifest.json` 里加一个路径。
2. 新建一个 tool JSON。
3. 如果执行逻辑比较特殊，再在对应 Python 模块里加 executor。

## 2. `services` 怎么写

`services` 是一个对象，key 是服务名，value 是服务配置。

示例：

```json
{
  "services": {
    "amap_config": {
      "factory": "app.agent.tools.builtin.route_plan_tool:AMapConfig",
      "dependencies": {
        "api_key": {
          "env": "AMAP_API_KEY",
          "default": ""
        },
        "base_url": {
          "env": "AMAP_BASE_URL",
          "default": "https://restapi.amap.com"
        },
        "timeout_seconds": {
          "env": "AMAP_TIMEOUT_SECONDS",
          "default": 8.0,
          "cast": "float"
        }
      }
    }
  }
}
```

字段说明：

- `factory`: 必填。导入路径，格式必须是 `模块路径:对象名`。
- `dependencies`: 可选。传给 factory 的构造参数。
- `singleton`: 可选。默认 `true`，表示服务只创建一次并缓存。

### `dependencies` 支持哪些值

#### 2.1 直接引用另一个 service

```json
{
  "dependencies": {
    "store": "store",
    "amap_config": "amap_config"
  }
}
```

这里的 `"store"`、`"amap_config"` 都会被当作 service 名去解析。

#### 2.2 从环境变量读取

```json
{
  "dependencies": {
    "timeout_seconds": {
      "env": "AMAP_TIMEOUT_SECONDS",
      "default": 8.0,
      "cast": "float"
    }
  }
}
```

可选字段：

- `env`: 环境变量名。
- `default`: 环境变量不存在时的默认值。
- `cast`: 类型转换，目前支持 `int`、`float`、`bool`、`string`。

#### 2.3 显式引用

```json
{
  "dependencies": {
    "project_root": {
      "ref": "project_root"
    }
  }
}
```

`ref` 和直接写字符串引用 service 的效果接近，但更清晰，也支持点路径：

```json
{
  "dependencies": {
    "base_url": {
      "ref": "settings.amap_base_url"
    }
  }
}
```

#### 2.4 解析路径

```json
{
  "dependencies": {
    "prompt_path": {
      "path": "app/agent/context/skills/response_composition.md",
      "base": "project_root",
      "as_string": true
    }
  }
}
```

- `path`: 相对或绝对路径。
- `base`: 相对路径的基准 service，默认是 `project_root`。
- `as_string`: `true` 时返回字符串路径，否则返回 `Path` 对象。

#### 2.5 写死一个值

```json
{
  "dependencies": {
    "provider": {
      "value": "amap"
    }
  }
}
```

#### 2.6 嵌套对象或数组

如果 value 本身是对象或数组，loader 会递归解析里面的 `ref`、`env`、`path`、`value`。

## 3. 单个 tool JSON 怎么写

每个 tool JSON 都是一个独立对象。

字段说明：

- `name`: 必填。注册后的工具名，必须唯一。
- `kind`: 可选。当前一般写 `"function"`。
- `description`: 必填。给模型看的工具用途描述。
- `executor`: 必填。真正执行工具的 Python callable，格式为 `module.path:object_name`。
- `input_schema`: 必填。JSON Schema 本体，既用于给模型暴露 tool schema，也用于运行时校验。
- `capabilities`: 可选。字符串数组，用于补充能力标签。
- `metadata`: 可选。附加元信息，支持和 `dependencies` 类似的解析能力。

### `executor` 的格式

必须是：

```text
module.path:object_name
```

例如：

- `app.agent.tools.builtin.executors.route_plan:execute`
- `app.agent.tools.builtin.executors.summary:execute`

如果少了冒号 `:`，运行时会报 `invalid_import_path`。

## 4. `input_schema` 怎么写

`input_schema` 使用标准 JSON Schema。

当前 loader 会做两件事：

1. 先按 schema 里的 `default` 给缺失字段补默认值。
2. 再用 JSON Schema 做严格校验。

因此建议：

- 可选字段显式写 `default`。
- 不允许额外字段时写 `additionalProperties: false`。
- 需要枚举约束时直接用 `enum`。

### 一个带默认值的例子

```json
{
  "input_schema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "sort_order": {
        "type": "string",
        "enum": ["asc", "desc"],
        "default": "desc"
      }
    }
  }
}
```

如果模型没传 `sort_order`，运行时会自动补成 `"desc"`。

## 5. 一个完整工具示例

下面这个例子和仓库里的 `route_plan_tool` 比较接近：

```json
{
  "name": "route_plan_tool",
  "kind": "function",
  "description": "Plan a route from origin to destination.",
  "executor": "app.agent.tools.builtin.executors.route_plan:execute",
  "capabilities": ["builtin", "read_only", "navigation"],
  "metadata": {
    "guide": {
      "path": "app/agent/context/skills/navigation_result_reading.md",
      "base": "project_root",
      "as_string": true
    }
  },
  "input_schema": {
    "$defs": {
      "LocationArgs": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "lng": { "type": "number" },
          "lat": { "type": "number" }
        },
        "required": ["lng", "lat"]
      }
    },
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "provider": {
        "type": "string",
        "enum": ["amap", "google", "none"]
      },
      "mode": {
        "type": "string",
        "enum": ["walking", "driving"]
      },
      "origin": {
        "$ref": "#/$defs/LocationArgs"
      },
      "destination": {
        "$ref": "#/$defs/LocationArgs"
      }
    },
    "required": ["provider", "mode", "origin", "destination"]
  }
}
```

## 6. 新增一个 builtin tool 的最小步骤

1. 在 [`backend/app/agent/tools/builtin/tools_manifest.json`](../../backend/app/agent/tools/builtin/tools_manifest.json) 的 `tools` 数组里加一个新文件路径。
2. 在 [`backend/app/agent/tools/builtin/schemas`](../../backend/app/agent/tools/builtin/schemas) 下新建一个 tool JSON，写好 `name`、`description`、`executor` 和 `input_schema`。
3. 如果需要新的依赖服务，再回到 manifest 的 `services` 里补 factory 和 dependencies。
4. 如果执行逻辑是特化的，在 [`backend/app/agent/tools/builtin/executors`](../../backend/app/agent/tools/builtin/executors) 下新增一个 executor 模块。

## 7. 当前设计的取舍

这套结构的核心收益是：

- 新增 builtin tool 不再需要改中心化 `schemas.py`。
- 新增 builtin tool 不再需要改中心化 `handlers.py`。
- 工具 schema 现在就是 JSON 文件本身，更接近 provider/plugin 的自描述结构。
- 一个 tool 的描述、参数和执行入口可以收拢到同一个局部目录里，维护更轻。

当前仍然保留的一点现实取舍是：

- 真正执行逻辑如果足够复杂，依然需要 Python executor。

但这个 Python 逻辑已经从“必须登记到中心化 handlers”变成“只归属于自己的工具模块”，这就是这次拆分最重要的收益。
