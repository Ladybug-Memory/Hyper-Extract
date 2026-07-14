# LadybugDB Storage

Hyper-Extract uses **LadybugDB** (an embeddable columnar graph database) as its primary storage engine. Every Knowledge Abstract (KA) — the nodes, edges, metadata, and even the extraction templates — lives in LadybugDB. The JSON files on disk (`data.json`, `metadata.json`) are a backward-compatible secondary representation.

---

## Quick Start

The database is fully automatic. After your first extraction, everything is stored without any extra setup:

```bash
# 1. Configure LLM + embedder (one-time)
he config llm --provider opencode-go -k $OPENCODE_API_KEY --model minimax-m3
he config embedder --provider openrouter -k $OPENROUTER_API_KEY

# 2. Extract and save — LadybugDB stores it automatically
he parse examples/en/tesla.md -t general/biography_graph -o ./output --lang en

# 3. Re-load from LadybugDB on next visit
he show ./output
he talk ./output -q "What did Tesla invent?"
```

No configuration required. The database lives at `~/.hyperextract/he.lbdb` by default (override via `HYPER_EXTRACT_DB_PATH`).

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
he parse examples/en/tesla.md -o ./output --lang en \
  --db tesla_bio
```

This runs `CREATE GRAPH tesla_bio; USE GRAPH tesla_bio;` internally, then creates the schema and inserts all data inside that subgraph.

| Feature | Flat Schema (default) | Subgraph (`--db <name>`) |
|---------|----------------------|--------------------------|
| Tables | Shared across all KAs | Isolated per KA |
| Query isolation | Filter by `ka_id` | Full namespace isolation |
| Deletion | Delete rows by `ka_id` | `DROP GRAPH <name>` (instant) |
| Listing | `MATCH (ka:KnowledgeAbstract)` | Per-connection via `USE GRAPH` |

### Programmatic subgraph access

```python
from hyperextract.ladybug_db import (
    store_ka_in_subgraph,
    load_ka_from_subgraph,
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

# Delete
delete_ka_subgraph("tesla_bio")
```

**Storage layout inside a subgraph:**

```
Graph: tesla_bio
├── Entity {id: "Nikola Tesla", entity_type: "person", data: {...}}
├── Entity {id: "AC motor",     entity_type: "invention", data: {...}}
├── Entity {id: "_e0",          entity_type: "edge", data: {_source_id, _target_id, ...}}
├── Entity {id: "_e1",          entity_type: "edge", data: {...}}
└── Entity {id: "_meta",        entity_type: "metadata", data: {...}}
```

Edges are stored as `Entity` rows with `entity_type='edge'` because LadybugDB's `REL TABLE` does not work correctly inside subgraphs.

---

## LLM Response Cache

Every LLM call is cached in a local SQLite database at `~/.he/llm_cache/responses.db`. The cache is keyed by the full prompt text (including tool definitions), so:

- **Re-running the same extraction** after a parse error returns cached results instantly — zero API cost.
- **Debugging a failed merge** (e.g., `<think>` tag wrapping) doesn't burn tokens again on retry.
- The cache is persistent across CLI invocations.

Enabled automatically the first time an LLM client is created. No configuration needed.

```
~/.he/
├── config.toml         # LLM / embedder configuration
└── llm_cache/
    └── responses.db    # SQLite cache (auto-created)
```

---

## LLM & Embedder Configuration

LadybugDB is storage — it doesn't replace the LLM or embedder. You configure those separately.

### Provider Presets

Hyper-Extract ships with presets for several providers:

| Provider | Base URL | Default LLM | Default Embedder |
|----------|----------|-------------|------------------|
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` | `text-embedding-3-small` |
| `bailian` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.6-plus` | `text-embedding-v4` |
| `vllm` | *(required)* | *(required)* | *(required)* |
| `anthropic` / `claude` | *(native SDK)* | `claude-opus-4-8` | *(none — pair with openai)* |
| `opencode-go` | `https://opencode.ai/zen/go/v1` | `minimax-m3` | *(none)* |
| `openrouter` | `https://openrouter.ai/api/v1` | `deepseek/deepseek-v4-flash` | *(none)* |

### CLI Configuration

```bash
# Interactive setup
he config init -p openai -k sk-...

# Or individual settings
he config llm --provider opencode-go -k $OPENCODE_API_KEY --model minimax-m3
he config embedder --provider openai -k $OPENAI_API_KEY --model text-embedding-3-small

# Override base URL
he config llm --provider vllm -u http://localhost:8000/v1 -k dummy -m Qwen/Qwen3.5-9B

# View current config
he config show
```

Settings are saved to `~/.he/config.toml`:

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

### API Key Resolution

Keys are resolved in this order (first non-empty wins):

1. **Config file** (`~/.he/config.toml`) — set via `he config llm -k ...`
2. **Environment variables** — checked per provider:

| Provider | Env vars (checked in order) |
|----------|----------------------------|
| `openai` | `OPENAI_API_KEY` |
| `bailian` | `OPENAI_API_KEY` |
| `vllm` | `OPENAI_API_KEY` |
| `anthropic` / `claude` | `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY` |
| `opencode-go` | `OPENCODE_GO_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |

If no key is found, validation fails with a clear error message.

### Structured Output Method

All extraction uses `with_structured_output(method="function_calling")` under the hood for provider compatibility. Models that don't support function calling (e.g., `deepseek-v4-flash` on some providers) will return a 400 error — switch to a compatible model like `minimax-m3` or `deepseek-v4-pro`.

---

## Full Flow End-to-End

```
User Input
    │
    ▼
┌──────────────────┐
│  1. Configure    │  he config llm --provider ... -k ... --model ...
│  LLM + Embedder  │  he config embedder --provider ... -k ... --model ...
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  2. Parse Text   │  he parse input.txt -t general/graph -o ./output --lang en
│                  │
│  ┌────────────┐  │
│  │ Chunk text  │  │  Split long text into manageable pieces
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ Extract     │  │  with_structured_output(method="function_calling")
│  │ nodes/edges │  │  Prompt + LLM → structured JSON
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ Deduplicate │  │  OMem LLM merger (handles overlapping chunks)
│  │ & merge     │  │  Falls back gracefully on parse errors
│  └──────┬─────┘  │
│         ▼        │
│  ┌────────────┐  │
│  │ Save to     │  │
│  │ LadybugDB   │  │  Flat schema + optional subgraph (--db)
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
```

### Recovering from a Corrupted Database

If a process is killed mid-write, the database may enter an inconsistent state:

```bash
rm -f ~/.hyperextract/he.lbdb*
```

The database will be recreated empty on the next run. Templates will need to be re-migrated.

### Migrating Templates

Templates are loaded from YAML files on first use. To explicitly populate the database:

```bash
uv run python -m hyperextract.scripts.migrate_templates
```
