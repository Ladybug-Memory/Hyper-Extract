# LadybugDB 存储

Hyper-Extract 使用 **LadybugDB**（一个可嵌入列式图数据库）作为其主要存储引擎。每个知识抽象（KA）——节点、边、元数据以及提取模板——都存储在 LadybugDB 中。磁盘上的 JSON 文件（`data.json`、`metadata.json`）是向后兼容的次要表示形式。

---

## 快速开始

数据库是完全自动化的。首次提取后，所有内容都会自动存储，无需任何额外设置：

```bash
# 1. 配置 LLM + 嵌入器（一次性）
he config llm --api-key YOUR_KEY
he config embedder --api-key YOUR_KEY

# 2. 提取并保存 — LadybugDB 自动存储
he parse examples/zh/tesla.md -t general/biography_graph -o ./output --lang zh

# 3. 下次访问时从 LadybugDB 重新加载
he show ./output
he talk ./output -q "特斯拉发明了什么？"
```

无需配置。数据库默认位于 `~/.hyperextract/he.lbdb`（可通过 `HYPER_EXTRACT_DB_PATH` 或 `he config db <路径>` 覆盖）。

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
he parse examples/zh/tesla.md -t general/biography_graph --lang zh --subgraph tesla_bio

# 同时保存到两者（文件系统和子图）
he parse examples/zh/tesla.md -o ./output --lang zh --subgraph tesla_bio
```

内部执行 `CREATE GRAPH tesla_bio; USE GRAPH tesla_bio;`，然后创建模式并将所有数据插入该子图。

**每个子图是一个单独的 LadybugDB 数据库文件** — `he.<名称>.lbdb` — 在主 `he.lbdb` 的目录中注册。使用 `show_graphs()` 列出它们。

必须提供 `--output` / `-o`（文件系统目录）或 `--subgraph`（子图名称）中的至少一个。两者不能同时使用 — 请选择一种存储模式。

| 功能 | 平面模式（`-o <目录>`） | 子图（`--subgraph <名称>`） |
|------|------------------------|----------------------------|
| 文件 | `he.lbdb`（共享） | `he.<名称>.lbdb`（独立） |
| 表结构 | 所有 KA 共享 | 每 KA 隔离 |
| 删除 | 按 `ka_id` 删除行 | `DROP GRAPH <名称>`（即时） |
| 列出 | `MATCH (ka:KnowledgeAbstract)` | `CALL show_graphs()` 或 `list_ka_subgraphs()` |

### 配置数据库路径

默认情况下，LadybugDB 的主数据库位于 `~/.hyperextract/he.lbdb`。您可以使用以下命令配置自定义路径：

```bash
# 查看当前数据库路径
he config db

# 设置自定义路径
he config db /path/to/my.lbdb

# 重置为默认路径
he config db --unset
```

### 列出、查看和删除子图

使用 `he list subgraph` CLI 命令来显示所有已注册的子图：

```bash
he list subgraph
```

查看子图的元数据和统计信息：

```bash
he info --subgraph tesla_bio
```

使用 OntoSight 可视化子图：

```bash
he show --subgraph tesla_bio
```

编程方式访问：

```python
from hyperextract.ladybug_db import list_ka_subgraphs, delete_ka_subgraph

# 列出主数据库中注册的所有子图
subgraphs = list_ka_subgraphs()
print(subgraphs)  # 例如 ["tesla_bio", "my_other_ka"]

# 删除子图（删除文件和目录条目）
delete_ka_subgraph("tesla_bio")
```

**提示：** 所有子图命令（`he list subgraph`、`he info --subgraph`、`he show --subgraph`）都操作于 `he config db` 配置的数据库。使用 `he config db <路径>` 切换到不同的 LadybugDB 数据库。

### 子图内部存储布局

```
文件: he.tesla_bio.lbdb
└── Entity (NODE TABLE)
    ├── {id: "尼古拉·特斯拉", entity_type: "person",    data: {name: "尼古拉·特斯拉", ...}}
    ├── {id: "交流电机",       entity_type: "invention", data: {name: "交流电机", ...}}
    ├── Relates 边存储在 REL TABLE 中
    │   ├── FROM "尼古拉·特斯拉" TO "交流电机" (invented)
    │   └── ...
    └── {id: "_meta",          entity_type: "metadata",  data: {template: "...", lang: "zh"}}
```

边使用 LadybugDB 的 `REL TABLE` 进行存储，包含类型化关系列（`time`、`space`、`confidence`、`data`）。

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

边使用 LadybugDB 的 `REL TABLE` 进行存储，包含类型化关系列（`time`、`space`、`confidence`、`data`）。

## 完整端到端流程

```
用户输入
    │
    ▼
┌──────────────────┐
│  1. 配置          │  he config llm --api-key YOUR_KEY
│  LLM + 嵌入器     │  he config embedder --api-key YOUR_KEY
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  2. 解析文本      │  he parse input.txt -t general/graph -o ./output --lang zh
│                  │     或: he parse input.txt --subgraph my_graph ...
│  ┌────────────┐  │
│  │ 分块        │  │  将长文本拆分为可管理的片段
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ 提取节点/边  │  │  提示 + LLM → 结构化 JSON
│  │             │  │
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ 去重与合并   │  │  OMem LLM 合并器（处理重叠块）
│  │             │  │  解析错误时优雅降级
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ 保存到       │  │
│  │ LadybugDB   │  │  平面模式 + 可选的子图（--subgraph）
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
│  he show --subgraph <名称> │  可视化子图
│  he info --subgraph <名称> │  查看子图信息
│  he list subgraph │  列出所有子图
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

# 或通过 CLI 命令（持久化）
he config db /path/to/my.lbdb
```

### 从损坏的数据库中恢复

如果进程在写入时被终止，数据库可能进入不一致状态：

```bash
rm -f ~/.hyperextract/he.lbdb*
```

### 数据库路径管理

```bash
# 查看当前数据库路径
he config db

# 设置自定义路径
he config db /path/to/my.lbdb

# 重置为默认路径
he config db --unset
```

**注意：** 数据库路径也可以通过 `HYPER_EXTRACT_DB_PATH` 环境变量设置。如果两者都设置了，环境变量优先级更高。

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
