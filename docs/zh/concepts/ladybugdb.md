# LadybugDB 存储

Hyper-Extract 使用 **LadybugDB**（一个可嵌入列式图数据库）作为其主要存储引擎。每个知识抽象（KA）——节点、边、元数据以及提取模板——都存储在 LadybugDB 中。磁盘上的 JSON 文件（`data.json`、`metadata.json`）是向后兼容的次要表示形式。

---

## 快速开始

数据库是完全自动化的。首次提取后，所有内容都会自动存储，无需任何额外设置：

```bash
# 1. 配置 LLM + 嵌入器（一次性）
he config llm --provider opencode-go -k $OPENCODE_API_KEY --model minimax-m3
he config embedder --provider openrouter -k $OPENROUTER_API_KEY

# 2. 提取并保存 — LadybugDB 自动存储
he parse examples/zh/tesla.md -t general/biography_graph -o ./output --lang zh

# 3. 下次访问时从 LadybugDB 重新加载
he show ./output
he talk ./output -q "特斯拉发明了什么？"
```

无需配置。数据库默认位于 `~/.hyperextract/he.lbdb`（可通过 `HYPER_EXTRACT_DB_PATH` 覆盖）。

---

## 数据库模式

```
┌───────────────────┐     ┌───────────────────┐
│    Template       │     │ KnowledgeAbstract │
│───────────────────│     │───────────────────│
│ name (PK)         │     │ id (PK)           │
│ domain            │     │ template_name     │
│ type              │     │ lang              │
│ tags              │     │ type              │
│ description       │     │ created_at        │
│ language          │     │ updated_at        │
│ output            │     │ metadata (JSON)   │
│ guideline         │     └───────────────────┘
│ identifiers       │           │
│ opts              │           │ 1
│ display           │           │
└───────────────────┘           │ *
                   ┌────────────┴──────────────┐
                   │          Entity            │
                   │────────────────────────────│
                   │ id (PK)                    │
                   │ ka_id                      │
                   │ entity_type                │
                   │ data (JSON)                │
                   └────────────────────────────┘
                            │
                            │ *
                   ┌────────┴────────┐
                   │    Relates      │
                   │─────────────────│
                   │ FROM Entity     │
                   │ TO Entity       │
                   │ ka_id           │
                   │ relation_type   │
                   │ data (JSON)     │
                   └─────────────────┘
```

- **Template 节点**：存储提取模板定义（从 YAML 文件加载）。
- **KnowledgeAbstract 节点**：每次提取的顶层容器，包含模板名称、语言、类型和元数据。
- **Entity 节点**：存储单个提取项（人物、地点、发明等）。
- **Relates 边**：存储实体之间的关系（发明、受雇于等）。

---

## 子图存储（每 KA 隔离图）

除了上述平面模式外，每个 KA 还可以存储在自己的**隔离 LadybugDB 子图**中。这为每次提取提供了独立的命名空间，没有共享表。

```bash
# 仅保存到 LadybugDB 子图（无需文件系统目录）
he parse examples/zh/tesla.md -t general/biography_graph --lang zh --db tesla_bio

# 同时保存到两者
he parse examples/zh/tesla.md -o ./output --lang zh --db tesla_bio
```

内部执行 `CREATE GRAPH tesla_bio; USE GRAPH tesla_bio;`，然后创建模式并将所有数据插入该子图。

**每个子图是一个单独的 LadybugDB 数据库文件** — `he.<名称>.lbdb` — 在主 `he.lbdb` 的目录中注册。使用 `show_graphs()` 列出它们。

必须提供 `--output` / `-o`（文件系统目录）或 `--db`（子图名称）中的至少一个。两者也可以同时使用。

| 功能 | 平面模式（`-o <目录>`） | 子图（`--db <名称>`） |
|------|------------------------|----------------------|
| 文件 | `he.lbdb`（共享） | `he.<名称>.lbdb`（独立） |
| 表结构 | 所有 KA 共享 | 每 KA 隔离 |
| 删除 | 按 `ka_id` 删除行 | `DROP GRAPH <名称>`（即时） |
| 列出 | `MATCH (ka:KnowledgeAbstract)` | `CALL show_graphs()` 或 `list_ka_subgraphs()` |

### 列出和删除子图

```python
from hyperextract.ladybug_db import list_ka_subgraphs, delete_ka_subgraph

# 列出主数据库中注册的所有子图
subgraphs = list_ka_subgraphs()
print(subgraphs)  # 例如 ["tesla_bio", "my_other_ka"]

# 删除子图（删除文件和目录条目）
delete_ka_subgraph("tesla_bio")
```

### 子图内部存储布局

```
文件: he.tesla_bio.lbdb
└── Entity (NODE TABLE)
    ├── {id: "尼古拉·特斯拉", entity_type: "person",    data: {name: "尼古拉·特斯拉", ...}}
    ├── {id: "交流电机",       entity_type: "invention", data: {name: "交流电机", ...}}
    ├── {id: "_e0",            entity_type: "edge",      data: {_source_id: "尼古拉·特斯拉", _target_id: "交流电机", ...}}
    ├── {id: "_e1",            entity_type: "edge",      data: {...}}
    └── {id: "_meta",          entity_type: "metadata",  data: {template: "...", lang: "zh"}}
```

边存储为 `entity_type='edge'` 的 `Entity` 行，因为 LadybugDB 的 `REL TABLE` 与其子图实现无法正常配合使用。

### 编程方式访问子图

```python
from hyperextract.ladybug_db import (
    store_ka_in_subgraph,
    load_ka_from_subgraph,
    list_ka_subgraphs,
    delete_ka_subgraph,
)

# 存储
store_ka_in_subgraph(
    graph_name="tesla_bio",
    data_dict={"nodes": [...], "edges": [...]},
    metadata={"template": "general/biography_graph", "lang": "zh"},
)

# 加载
data, meta = load_ka_from_subgraph("tesla_bio")
print(f"{len(data['nodes'])} 个节点, {len(data['edges'])} 条边")

# 列出所有注册的子图
all_sgs = list_ka_subgraphs()
print(all_sgs)  # 例如 ["tesla_bio", ...]

# 删除
delete_ka_subgraph("tesla_bio")
```

边存储为 `entity_type='edge'` 的 `Entity` 行，因为 LadybugDB 的 `REL TABLE` 在子图内部无法正常工作。

---

## LLM 响应缓存

每个 LLM 调用都会缓存在 `~/.he/llm_cache/responses.db` 的本地 SQLite 数据库中。缓存键基于完整的提示文本（包括工具定义），因此：

- **解析错误后重新运行**相同的提取会立即返回缓存结果 — 零 API 成本。
- **调试失败的合并**（例如 `<think>` 标签包装）不会在重试时再次消耗令牌。
- 缓存在 CLI 调用之间持久存在。

首次创建 LLM 客户端时自动启用。无需配置。

```
~/.he/
├── config.toml         # LLM / 嵌入器配置
└── llm_cache/
    └── responses.db    # SQLite 缓存（自动创建）
```

---

## LLM 和嵌入器配置

LadybugDB 是存储层 — 它不替代 LLM 或嵌入器。您需要单独配置它们。

### 提供商预设

Hyper-Extract 内置了多个提供商的预设：

| 提供商 | 基础 URL | 默认 LLM | 默认嵌入器 |
|--------|----------|-------------|------------------|
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` | `text-embedding-3-small` |
| `bailian` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.6-plus` | `text-embedding-v4` |
| `vllm` | *（必需）* | *（必需）* | *（必需）* |
| `anthropic` / `claude` | *（原生 SDK）* | `claude-opus-4-8` | *（无 — 搭配 openai）* |
| `opencode-go` | `https://opencode.ai/zen/go/v1` | `minimax-m3` | *（无）* |
| `openrouter` | `https://openrouter.ai/api/v1` | `deepseek/deepseek-v4-flash` | *（无）* |

### CLI 配置

```bash
# 交互式设置
he config init -p openai -k sk-...

# 或单独设置
he config llm --provider opencode-go -k $OPENCODE_API_KEY --model minimax-m3
he config embedder --provider openai -k $OPENAI_API_KEY

# 覆盖基础 URL
he config llm --provider vllm -u http://localhost:8000/v1 -k dummy -m Qwen/Qwen3.5-9B

# 查看当前配置
he config show
```

设置保存在 `~/.he/config.toml`：

```toml
[llm]
provider = "opencode-go"
model = "minimax-m3"
api_key = "sk-..."

[embedder]
provider = "openai"
model = "text-embedding-3-small"
api_key = "sk-..."
```

### API 密钥解析

按以下顺序解析密钥（第一个非空值胜出）：

1. **配置文件**（`~/.he/config.toml`）— 通过 `he config llm -k ...` 设置
2. **环境变量** — 按提供商检查：

| 提供商 | 环境变量（按顺序检查） |
|--------|-----------------------|
| `openai` | `OPENAI_API_KEY` |
| `bailian` | `OPENAI_API_KEY` |
| `vllm` | `OPENAI_API_KEY` |
| `anthropic` / `claude` | `ANTHROPIC_API_KEY`、`CLAUDE_API_KEY` |
| `opencode-go` | `OPENCODE_GO_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |

如果找不到密钥，验证将失败并显示清晰的错误消息。

### 结构化输出方法

所有提取都使用 `with_structured_output(method="function_calling")`。不支持函数调用的模型（例如某些提供商上的 `deepseek-v4-flash`）将返回 400 错误 — 请切换到兼容的模型，如 `minimax-m3` 或 `deepseek-v4-pro`。

---

## 完整端到端流程

```
用户输入
    │
    ▼
┌──────────────────┐
│  1. 配置          │  he config llm --provider ... -k ... --model ...
│  LLM + 嵌入器     │  he config embedder --provider ... -k ...
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  2. 解析文本      │  he parse input.txt -t general/graph -o ./output --lang zh
│                  │
│  ┌────────────┐  │
│  │ 分块        │  │  将长文本拆分为可管理的片段
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ 提取节点/边  │  │  with_structured_output(method="function_calling")
│  │             │  │  提示 + LLM → 结构化 JSON
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ 去重与合并   │  │  OMem LLM 合并器（处理重叠块）
│  │             │  │  解析错误时优雅降级
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ 保存到       │  │
│  │ LadybugDB   │  │  平面模式 + 可选的子图（--db）
│  │ + 文件系统   │  │
│  └────────────┘  │
└──────────────────┘
         │
         ▼
┌──────────────────┐
│  3. 探索         │
│                  │
│  he show ./output│  使用 OntoSight 可视化
│  he search ...   │  基于向量索引的语义搜索
│  he talk ...     │  与知识抽象对话
│  he build-index  │  构建/重建 FAISS 索引
│  he feed ...     │  追加更多文档
└──────────────────┘
```

---

## 管理数据库

### 位置

```bash
# 默认
~/.hyperextract/he.lbdb

# 自定义（在任何命令前设置）
export HYPER_EXTRACT_DB_PATH=/path/to/my.lbdb
```

### 从损坏的数据库中恢复

如果进程在写入时被终止，数据库可能进入不一致状态：

```bash
rm -f ~/.hyperextract/he.lbdb*
```

下次运行时将重新创建空数据库。模板需要重新迁移。

### 模板管理

提取模板（`hyperextract/templates/presets/` 中的 YAML 文件）存储在 LadybugDB 中，无需文件系统即可查询和加载。

#### 首次使用自动加载

当您运行 `he list template` 或 `he parse -t <template>` 时，系统会先检查 LadybugDB。如果数据库为空，您将看到：

```
Warning: Could not load templates from LadybugDB: ...
Run the migration script to populate the database.
```

#### 迁移模板到 LadybugDB

将所有内置模板填充到 LadybugDB：

```bash
uv run python -m hyperextract.scripts.migrate_templates
```

此命令扫描 `hyperextract/templates/presets/` 中的每个 `*.yaml` 文件，将其转换为 LadybugDB 记录，并验证结果：

```
  ✓ general/graph (graph.yaml)
  ✓ general/biography_graph (biography_graph.yaml)
  ✓ general/list (list.yaml)
  ...

Migrated 24 templates to LadybugDB

Verifying...
Found 24 templates in LadybugDB:
  - general/biography_graph
  - general/graph
  - general/list
  - ...
```

#### 列出模板

`he list template` 命令从 LadybugDB 读取并支持筛选：

```bash
# 列出所有模板
he list template

# 按类型筛选
he list template --autotype graph

# 按关键词搜索
he list template --query biography

# 按语言筛选
he list template --lang en
he list template --lang all
```

#### 编程方式访问

```python
from hyperextract.ladybug_db import (
    store_template,
    get_template,
    list_templates,
    delete_template,
)

# 列出所有
all_tmpl = list_templates()

# 获取单个
cfg = get_template("general/biography_graph")

# 存储自定义模板（从字典）
store_template({
    "name": "my_custom_template",
    "domain": "custom",
    "type": "graph",
    ...
})

# 删除
delete_template("custom/my_custom_template")
```
