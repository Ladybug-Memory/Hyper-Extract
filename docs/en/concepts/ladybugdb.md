# LadybugDB Storage

Hyper-Extract uses **LadybugDB** (an embeddable columnar graph database) as its primary storage engine. Every Knowledge Abstract (KA) — the nodes, edges, metadata, and even the extraction templates — lives in LadybugDB. The JSON files on disk (`data.json`, `metadata.json`) are a backward-compatible secondary representation.

---

## Quick Start

The database is fully automatic. After your first extraction, everything is stored without any extra setup:

```bash
# 1. Configure LLM + embedder (one-time)
he config llm --api-key YOUR_KEY
he config embedder --api-key YOUR_KEY

# 2. Extract and save — LadybugDB stores it automatically
he parse examples/en/tesla.md -t general/biography_graph -o ./output --lang en

# 3. Re-load from LadybugDB on next visit
he show ./output
he talk ./output -q "What did Tesla invent?"
```

No configuration required. The database lives at `~/.hyperextract/he.lbdb` by default (override via `HYPER_EXTRACT_DB_PATH` or `he config db <path>`).

---

## Database Schema

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

- **Template nodes** store extraction template definitions (loaded from YAML on startup or migrated via `he scripts migrate-templates`).
- **KnowledgeAbstract nodes** are the top-level container for each extraction run — they hold the template name, language, type, and metadata.
- **Entity nodes** store individual extracted items (people, locations, inventions, etc.).
- **Relates edges** store relationships between entities (invented, employed_by, etc.).

---

## Subgraph Storage (Isolated Per-KA Graphs)

Beyond the flat schema above, each KA can also be stored in its own **isolated LadybugDB subgraph**. This gives each extraction its own namespace with no shared tables.

```bash
# Save to a LadybugDB subgraph (no filesystem directory needed)
he parse examples/en/tesla.md -t general/biography_graph --lang en --subgraph tesla_bio

# Save to both (filesystem and subgraph)
he parse examples/en/tesla.md -o ./output --lang en --subgraph tesla_bio
```

This runs `CREATE GRAPH tesla_bio; USE GRAPH tesla_bio;` internally, then creates the schema and inserts all data inside that subgraph.

**Each subgraph is a separate LadybugDB database file** — `he.<name>.lbdb` — registered in the catalog of the main `he.lbdb`. Use `show_graphs()` to list them.

Either `--output` / `-o` (filesystem directory) or `--subgraph` (subgraph name) must be provided. They cannot be used together — choose one storage mode.

| Feature | Flat Schema (`-o <dir>`) | Subgraph (`--subgraph <name>`) |
|---------|--------------------------|--------------------------------|
| File | `he.lbdb` (shared) | `he.<name>.lbdb` (separate) |
| Tables | Shared across all KAs | Isolated per KA |
| Deletion | Delete rows by `ka_id` | `DROP GRAPH <name>` (instant) |
| Listing | `MATCH (ka:KnowledgeAbstract)` | `CALL show_graphs()` or `list_ka_subgraphs()` |

### Configuring the Database Path

By default, LadybugDB stores its main database at `~/.hyperextract/he.lbdb`. You can configure a custom path with:

```bash
# View current database path
he config db

# Set a custom path
he config db /path/to/my.lbdb

# Reset to default
he config db --unset
```

### Listing, Inspecting, and Deleting Subgraphs

Use the `he list subgraph` CLI command to show all registered subgraphs:

```bash
he list subgraph
```

Inspect a subgraph's metadata and statistics:

```bash
he info --subgraph tesla_bio
```

Visualize a subgraph with OntoSight:

```bash
he show --subgraph tesla_bio
```

Programmatic access:

```python
from hyperextract.ladybug_db import list_ka_subgraphs, delete_ka_subgraph

# List all subgraphs registered in the main database
subgraphs = list_ka_subgraphs()
print(subgraphs)  # e.g. ["tesla_bio", "my_other_ka"]

# Delete a subgraph (drops the file and catalog entry)
delete_ka_subgraph("tesla_bio")
```

**Tip:** All subgraph commands (`he list subgraph`, `he info --subgraph`, `he show --subgraph`) operate on the database configured by `he config db`. Use `he config db <path>` to point to a different LadybugDB database.

### Storage Layout Inside a Subgraph

```
File: he.tesla_bio.lbdb
└── Entity (NODE TABLE)
    ├── {id: "Nikola Tesla", entity_type: "person",    data: {name: "Nikola Tesla", ...}}
    ├── {id: "AC motor",     entity_type: "invention", data: {name: "AC motor", ...}}
    ├── Relates edges stored in REL TABLE
    │   ├── FROM "Nikola Tesla" TO "AC motor" (invented)
    │   └── ...
    └── {id: "_meta",        entity_type: "metadata",  data: {template: "...", lang: "en"}}
```

Edges are stored using LadybugDB's `REL TABLE` with typed relationships (`time`, `space`, `confidence`, `data` columns).

### Programmatic subgraph access

```python
from hyperextract.ladybug_db import (
    store_ka_in_subgraph,
    load_ka_from_subgraph,
    list_ka_subgraphs,
    delete_ka_subgraph,
)

# Store
store_ka_in_subgraph(
    graph_name="tesla_bio",
    data_dict={"nodes": [...], "edges": [...]},
    metadata={"template": "general/biography_graph", "lang": "en"},
)

# Load
data, meta = load_ka_from_subgraph("tesla_bio")
print(f"{len(data['nodes'])} nodes, {len(data['edges'])} edges")

# List all registered subgraphs
all_sgs = list_ka_subgraphs()
print(all_sgs)  # e.g. ["tesla_bio", ...]

# Delete
delete_ka_subgraph("tesla_bio")
```

Edges are stored using LadybugDB's `REL TABLE` with typed relationships (`time`, `space`, `confidence`, `data` columns).

## Full Flow End-to-End

```
User Input
    │
    ▼
┌──────────────────┐
│  1. Configure    │  he config llm --api-key YOUR_KEY
│  LLM + Embedder  │  he config embedder --api-key YOUR_KEY
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  2. Parse Text   │  he parse input.txt -t general/graph -o ./output --lang en
│                  │     or: he parse input.txt --subgraph my_graph ...
│  ┌────────────┐  │
│  │ Chunk text  │  │  Split long text into manageable pieces
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ Extract     │  │  Prompt + LLM → structured JSON
│  │ nodes/edges │  │
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ Deduplicate │  │  OMem LLM merger (handles overlapping chunks)
│  │ & merge     │  │  Falls back gracefully on parse errors
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ Save to     │  │
│  │ LadybugDB   │  │  Flat schema + optional subgraph (--subgraph)
│  │ + filesystem│  │
│  └────────────┘  │
└──────────────────┘
         │
         ▼
┌──────────────────┐
│  3. Explore      │
│                  │
│  he show ./output│  Visualize with OntoSight
│  he search ...   │  Semantic search over vector index
│  he talk ...     │  Chat with the knowledge abstract
│  he build-index  │  Build/reindex FAISS index
│  he feed ...     │  Append more documents
│  he show --subgraph <name> │  Visualize a subgraph
│  he info --subgraph <name> │  Inspect a subgraph
│  he list subgraph │  List all subgraphs
└──────────────────┘
```

---

## Managing the Database

### Location

```bash
# Default
~/.hyperextract/he.lbdb

# Custom (set before any command)
export HYPER_EXTRACT_DB_PATH=/path/to/my.lbdb

# Or via CLI command (persistent)
he config db /path/to/my.lbdb
```

### Recovering from a Corrupted Database

If a process is killed mid-write, the database may enter an inconsistent state:

```bash
rm -f ~/.hyperextract/he.lbdb*
```

### Database Path Management

```bash
# View current database path
he config db

# Set a custom path
he config db /path/to/my.lbdb

# Reset to default
he config db --unset
```

**Note:** The database path can also be set via the `HYPER_EXTRACT_DB_PATH` environment variable. If both are set, the environment variable takes precedence.

The database will be recreated empty on the next run. Templates will need to be re-migrated.

### Template Management

Extraction templates (the YAML files in `hyperextract/templates/presets/`) are stored in LadybugDB so they can be queried and loaded without filesystem access.

#### Automatic Loading on First Use

When you run `he list template` or `he parse -t <template>`, the system checks LadybugDB first. If the database is empty, you'll see:

```
Warning: Could not load templates from LadybugDB: ...
Run the migration script to populate the database.
```

#### Migrating Templates to LadybugDB

Populate LadybugDB with all built-in templates:

```bash
uv run python -m hyperextract.scripts.migrate_templates
```

This scans `hyperextract/templates/presets/` for every `*.yaml` file, converts them to LadybugDB records, and verifies the result:

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

#### Listing Templates

The `he list template` command reads from LadybugDB and supports filtering:

```bash
# List all templates
he list template

# Filter by type
he list template --autotype graph

# Search by keyword
he list template --query biography

# Filter by language
he list template --lang en
he list template --lang all
```

#### Programmatic Access

```python
from hyperextract.ladybug_db import (
    store_template,
    get_template,
    list_templates,
    delete_template,
)

# List all
all_tmpl = list_templates()

# Get one
cfg = get_template("general/biography_graph")

# Store a custom template (from a dict)
store_template({
    "name": "my_custom_template",
    "domain": "custom",
    "type": "graph",
    ...
})

# Delete
delete_template("custom/my_custom_template")
```
