"""LadybugDB database manager for Hyper-Extract.

Replaces YAML template files and JSON file storage with a graph database.
Templates and Knowledge Abstracts (KAs) are stored as nodes and relationships
in LadybugDB, an embeddable columnar graph database.

Schema overview:
  - Template nodes: store knowledge extraction template definitions
  - KA (Knowledge Abstract) nodes: store extracted knowledge with metadata
  - KA entities stored as typed nodes with relationships between them
"""

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ladybug as lb

from hyperextract.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Singleton database manager
# ---------------------------------------------------------------------------

_DB_PATH_ENV = "HYPER_EXTRACT_DB_PATH"
_DEFAULT_DB_PATH = Path.home() / ".hyperextract" / "he.lbdb"


class LadybugDBManager:
    """Singleton manager for LadybugDB connections and schema."""

    _instance: Optional["LadybugDBManager"] = None
    _lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.environ.get(_DB_PATH_ENV, str(_DEFAULT_DB_PATH))
        self.db_path = db_path
        self._db: Optional[lb.Database] = None
        self._conn: Optional[lb.Connection] = None
        self._init_schema()

    @classmethod
    def get_instance(
        cls, db_path: Optional[str] = None
    ) -> "LadybugDBManager":
        """Get or create the singleton LadybugDBManager instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path=db_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (useful for testing)."""
        with cls._lock:
            if cls._instance is not None:
                try:
                    cls._instance.close()
                except Exception:
                    pass
                cls._instance = None

    @property
    def connection(self) -> lb.Connection:
        """Get the database connection, creating it if needed."""
        if self._conn is None:
            self._connect()
        return self._conn

    def _connect(self) -> None:
        """Establish connection to the database."""
        path = self.db_path if self.db_path != ":memory:" else ":memory:"
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = lb.Database(path)
        self._conn = lb.Connection(self._db)
        logger.info("Connected to LadybugDB at %s", path)

    @property
    def database(self) -> lb.Database:
        """The underlying LadybugDB Database object.

        Used to create temporary connections for subgraph operations.
        """
        if self._db is None:
            self._connect()
        return self._db

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        self._db = None

    def _init_schema(self) -> None:
        """Initialize database schema (tables, indexes)."""
        conn = self.connection

        # Load JSON extension for storing complex data as JSON
        try:
            conn.execute("INSTALL JSON")
            conn.execute("LOAD EXTENSION JSON")
        except Exception:
            # JSON extension may already be loaded
            pass

        # --- Template storage ---
        # Complex fields stored as JSON-serialized STRINGs (LadybugDB JSON extension
        # returns values in a non-standard format, so we store valid JSON strings)
        try:
            conn.execute(
                """CREATE NODE TABLE IF NOT EXISTS Template (
                    name STRING,
                    domain STRING,
                    type STRING,
                    tags STRING,
                    description STRING,
                    language STRING,
                    output STRING,
                    guideline STRING,
                    identifiers STRING,
                    opts STRING,
                    display STRING,
                    PRIMARY KEY (name)
                )"""
            )
        except Exception as e:
            logger.warning("Could not create Template table: %s", e)

        # --- Knowledge Abstract storage ---
        try:
            conn.execute(
                """CREATE NODE TABLE IF NOT EXISTS KnowledgeAbstract (
                    id STRING,
                    template_name STRING,
                    lang STRING,
                    type STRING,
                    created_at STRING,
                    updated_at STRING,
                    metadata STRING,
                    PRIMARY KEY (id)
                )"""
            )
        except Exception as e:
            logger.warning("Could not create KnowledgeAbstract table: %s", e)

        # --- Entity (node) storage ---
        try:
            conn.execute(
                """CREATE NODE TABLE IF NOT EXISTS Entity (
                    id STRING,
                    ka_id STRING,
                    entity_type STRING,
                    data STRING,
                    PRIMARY KEY (id)
                )"""
            )
        except Exception as e:
            logger.warning("Could not create Entity table: %s", e)

        # --- Relationship (edge) storage ---
        try:
            conn.execute(
                """CREATE REL TABLE IF NOT EXISTS Relates (
                    FROM Entity TO Entity,
                    ka_id STRING,
                    relation_type STRING,
                    data STRING
                )"""
            )
        except Exception as e:
            logger.warning("Could not create Relates table: %s", e)

        # --- Index for fast KA lookups ---
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entity_ka ON Entity (ka_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rel_ka ON Relates (ka_id)"
            )
        except Exception:
            pass

        logger.info("LadybugDB schema initialized")


# =========================================================================
# Template operations
# =========================================================================


def _ensure_template_name(name: str, domain: str) -> str:
    """Build a unique template key as 'domain/name'."""
    return f"{domain}/{name}"


def store_template(template_data: Dict[str, Any]) -> None:
    """Store a template definition in LadybugDB.

    Args:
        template_data: Template configuration dict (from TemplateCfg.model_dump()).
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    name = template_data.get("name", "unknown")
    domain = template_data.get("_domain", "general")
    full_name = _ensure_template_name(name, domain)

    # Base64-encode JSON fields to prevent LadybugDB from reformatting them
    tags_str = _encode_base64_json(template_data.get("tags", []))
    desc_str = _encode_base64_json(template_data.get("description", ""))
    lang_str = _encode_base64_json(template_data.get("language", "en"))
    output_str = _encode_base64_json(template_data.get("output", {}))
    guide_str = _encode_base64_json(template_data.get("guideline", {}))
    idents_str = _encode_base64_json(template_data.get("identifiers", {}))
    opts_str = _encode_base64_json(template_data.get("options", {}))
    disp_str = _encode_base64_json(template_data.get("display", {}))

    conn.execute(
        """MERGE (t:Template {name: $name})
           ON MATCH SET
               t.domain = $domain,
               t.type = $type,
               t.tags = $tags,
               t.description = $description,
               t.language = $language,
               t.output = $output,
               t.guideline = $guideline,
               t.identifiers = $identifiers,
               t.opts = $opts,
               t.display = $display
           ON CREATE SET
               t.domain = $domain,
               t.type = $type,
               t.tags = $tags,
               t.description = $description,
               t.language = $language,
               t.output = $output,
               t.guideline = $guideline,
               t.identifiers = $identifiers,
               t.opts = $opts,
               t.display = $display
        """,
        parameters={
            "name": full_name,
            "domain": domain,
            "type": template_data.get("type", "graph"),
            "tags": tags_str,
            "description": desc_str,
            "language": lang_str,
            "output": output_str,
            "guideline": guide_str,
            "identifiers": idents_str,
            "opts": opts_str,
            "display": disp_str,
        },
    )
    logger.debug("Stored template: %s", full_name)


def get_template(name: str) -> Optional[Dict[str, Any]]:
    """Retrieve a template definition from LadybugDB.

    Args:
        name: Template name (e.g., "general/graph" or "graph").

    Returns:
        Template config dict or None if not found.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    if "/" not in name:
        # Try both domain-qualified and unqualified
        result = conn.execute(
            "MATCH (t:Template) WHERE t.name = $name RETURN t.*",
            parameters={"name": f"general/{name}"},
        )
        rows = result.get_all()
        if not rows:
            result = conn.execute(
                "MATCH (t:Template) WHERE t.name = $name RETURN t.*",
                parameters={"name": name},
            )
            rows = result.get_all()
    else:
        result = conn.execute(
            "MATCH (t:Template) WHERE t.name = $name RETURN t.*",
            parameters={"name": name},
        )
        rows = result.get_all()

    if not rows:
        return None

    return _row_to_template_dict(rows[0])


def list_templates(
    filter_by_type: Optional[str] = None,
    filter_by_tag: Optional[str] = None,
    filter_by_query: Optional[str] = None,
    filter_by_language: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """List templates matching optional filters.

    Returns:
        Dict mapping template full name to template config dict.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    conditions = []
    params = {}

    if filter_by_type:
        conditions.append("t.type = $type")
        params["type"] = filter_by_type

    # Tag filtering: tags is a JSON array, use CONTAINS or string matching
    if filter_by_tag:
        conditions.append("contains(t.tags, $tag)")
        params["tag"] = filter_by_tag

    where_clause = (
        "WHERE " + " AND ".join(conditions) if conditions else ""
    )

    query = f"MATCH (t:Template) {where_clause} RETURN t.* ORDER BY t.name"
    result = conn.execute(query, parameters=params)
    rows = result.get_all()

    templates = {}
    for row in rows:
        td = _row_to_template_dict(row)
        key = td.get("_key", td.get("name", "unknown"))
        # Apply query filter (case-insensitive name/description search)
        if filter_by_query:
            q = filter_by_query.lower()
            name_match = q in td.get("name", "").lower()
            desc = td.get("description", "")
            if isinstance(desc, dict):
                desc_str = json.dumps(desc, ensure_ascii=False).lower()
            else:
                desc_str = str(desc).lower()
            desc_match = q in desc_str
            if not name_match and not desc_match:
                continue

        # Apply language filter
        if filter_by_language:
            lang = td.get("language", [])
            if isinstance(lang, list):
                if filter_by_language not in lang:
                    continue
            elif lang != filter_by_language:
                continue

        templates[key] = td

    return templates


def delete_template(name: str) -> bool:
    """Delete a template from LadybugDB.

    Returns:
        True if deleted, False if not found.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    # Cypher in Kuzu/Ladybug: use MATCH + DELETE
    result = conn.execute(
        "MATCH (t:Template) WHERE t.name = $name RETURN count(*)",
        parameters={"name": name},
    )
    count = result.get_all()[0][0]
    if count == 0:
        return False

    conn.execute(
        "MATCH (t:Template) WHERE t.name = $name DELETE t",
        parameters={"name": name},
    )
    return True

def _encode_base64_json(data: Any) -> str:
    """Encode data as base64 JSON."""
    import base64
    json_str = json.dumps(data, ensure_ascii=False, default=str)
    return base64.b64encode(json_str.encode("utf-8")).decode("ascii")


def _decode_base64_json(value: str) -> Any:
    """Decode base64 JSON string back to Python object."""
    import base64
    if not value:
        return value
    try:
        decoded = base64.b64decode(value.encode("ascii")).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError, TypeError):
            return value


def _row_to_template_dict(row: Any) -> Dict[str, Any]:
    """Convert a LadybugDB result row to a template config dict.

    Complex fields are base64-encoded to prevent LadybugDB reformatting.

    Args:
        row: A single row from a LadybugDB query result.

    Returns:
        Template configuration dictionary.
    """
    if isinstance(row, dict):
        data = dict(row)
    elif isinstance(row, (list, tuple)) and len(row) == 1 and isinstance(row[0], dict):
        data = dict(row[0])
    elif isinstance(row, (list, tuple)):
        data = _map_template_row_to_columns(row)
    else:
        data = {}

    result = {}
    encoded_fields = {"tags", "description", "language", "output", "guideline",
                      "identifiers", "opts", "display"}
    for key, val in data.items():
        clean_key = key.replace("t.", "") if isinstance(key, str) and key.startswith("t.") else key
        # Map 'opts' column back to 'options' for API compatibility
        if clean_key == "opts":
            clean_key = "options"
        if clean_key in encoded_fields and isinstance(val, str):
            result[clean_key] = _decode_base64_json(val)
        else:
            result[clean_key] = val

    if "_key" not in result and "name" in result:
        result["_key"] = result["name"]

    return result


def _map_template_row_to_columns(row: List) -> Dict[str, Any]:
    """Map a flat template row to column names."""
    column_names = [
        "name", "domain", "type", "tags", "description", "language",
        "output", "guideline", "identifiers", "opts", "display",
    ]
    data = {}
    for i, col in enumerate(column_names):
        if i < len(row):
            data[col] = row[i]
    return data


# =========================================================================
# Knowledge Abstract operations
# =========================================================================


def create_ka(
    template_name: str,
    lang: str,
    type_: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Create a new Knowledge Abstract node in LadybugDB.

    Args:
        template_name: Template name used for this KA.
        lang: Language code.
        type_: AutoType (graph, model, set, list, etc.).
        metadata: Additional metadata dict.

    Returns:
        The generated KA ID string.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    ka_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    meta_str = json.dumps(metadata or {}, ensure_ascii=False)

    conn.execute(
        """CREATE (ka:KnowledgeAbstract {
            id: $id,
            template_name: $template_name,
            lang: $lang,
            type: $type,
            created_at: $created_at,
            updated_at: $updated_at,
            metadata: $metadata
        })""",
        parameters={
            "id": ka_id,
            "template_name": template_name,
            "lang": lang,
            "type": type_,
            "created_at": now,
            "updated_at": now,
            "metadata": meta_str,
        },
    )

    logger.debug("Created KnowledgeAbstract: %s", ka_id)
    return ka_id


def update_ka_metadata(ka_id: str, metadata: Dict[str, Any]) -> None:
    """Update metadata for an existing KA.

    Args:
        ka_id: Knowledge Abstract ID.
        metadata: Metadata dict to merge.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    now = datetime.now().isoformat()
    meta_str = json.dumps(metadata, ensure_ascii=False)

    conn.execute(
        """MATCH (ka:KnowledgeAbstract {id: $id})
           SET ka.metadata = $metadata,
               ka.updated_at = $updated_at""",
        parameters={
            "id": ka_id,
            "metadata": meta_str,
            "updated_at": now,
        },
    )


def get_ka(ka_id: str) -> Optional[Dict[str, Any]]:
    """Get a Knowledge Abstract node by ID.

    Args:
        ka_id: Knowledge Abstract ID.

    Returns:
        KA dict or None if not found.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    result = conn.execute(
        "MATCH (ka:KnowledgeAbstract) WHERE ka.id = $id RETURN ka.*",
        parameters={"id": ka_id},
    )
    rows = result.get_all()
    if not rows:
        return None

    return _row_to_ka_dict(rows[0])


def get_ka_by_path(ka_path: str) -> Optional[Dict[str, Any]]:
    """Get a KA by its filesystem path (stored in metadata).

    Args:
        ka_path: Filesystem path to the KA directory.

    Returns:
        KA dict or None.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    # Search in metadata JSON for path
    # Use CAST to convert the JSON metadata to STRING for contains
    result = conn.execute(
        """MATCH (ka:KnowledgeAbstract)
           WHERE contains(CAST(ka.metadata AS STRING), $path)
           RETURN ka.*""",
        parameters={"path": ka_path},
    )
    rows = result.get_all()
    if rows:
        return _row_to_ka_dict(rows[0])
    return None


def delete_ka(ka_id: str) -> bool:
    """Delete a KA and all its entities/relationships.

    Args:
        ka_id: Knowledge Abstract ID.

    Returns:
        True if deleted.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    # Delete relationships first, then entities, then the KA
    conn.execute(
        """MATCH (e1:Entity)-[r:Relates]->(e2:Entity)
           WHERE r.ka_id = $id DELETE r""",
        parameters={"id": ka_id},
    )
    conn.execute(
        "MATCH (e:Entity) WHERE e.ka_id = $id DELETE e",
        parameters={"id": ka_id},
    )
    conn.execute(
        "MATCH (ka:KnowledgeAbstract) WHERE ka.id = $id DELETE ka",
        parameters={"id": ka_id},
    )
    return True


def store_entity(
    ka_id: str,
    entity_id: str,
    entity_type: str,
    data: Dict[str, Any],
) -> None:
    """Store an entity node in a KA.

    Args:
        ka_id: Knowledge Abstract ID.
        entity_id: Unique entity identifier within the KA.
        data: Entity data dict (serialized to JSON).
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    full_id = f"{ka_id}::{entity_id}"
    data_str = json.dumps(data, ensure_ascii=False, default=str)

    # Upsert entity using MERGE with ON MATCH/ON CREATE
    conn.execute(
        """MERGE (e:Entity {id: $id})
           ON MATCH SET
               e.ka_id = $ka_id,
               e.entity_type = $entity_type,
               e.data = $data
           ON CREATE SET
               e.ka_id = $ka_id,
               e.entity_type = $entity_type,
               e.data = $data
        """,
        parameters={
            "id": full_id,
            "ka_id": ka_id,
            "entity_type": entity_type,
            "data": data_str,
        },
    )


def store_entities_batch(
    ka_id: str,
    entities: List[Tuple[str, str, Dict[str, Any]]],
) -> None:
    """Store multiple entities in batch.

    Args:
        ka_id: Knowledge Abstract ID.
        entities: List of (entity_id, entity_type, data_dict) tuples.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    for entity_id, entity_type, data in entities:
        full_id = f"{ka_id}::{entity_id}"
        data_str = json.dumps(data, ensure_ascii=False, default=str)
        # Upsert entity using MERGE with ON MATCH/ON CREATE
        conn.execute(
            """MERGE (e:Entity {id: $id})
               ON MATCH SET
                   e.ka_id = $ka_id,
                   e.entity_type = $entity_type,
                   e.data = $data
               ON CREATE SET
                   e.ka_id = $ka_id,
                   e.entity_type = $entity_type,
                   e.data = $data
            """,
            parameters={
                "id": full_id,
                "ka_id": ka_id,
                "entity_type": entity_type,
                "data": data_str,
            },
        )


def store_relationship(
    ka_id: str,
    source_entity_id: str,
    target_entity_id: str,
    relation_type: str,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """Store a relationship between two entities in a KA.

    Args:
        ka_id: Knowledge Abstract ID.
        source_entity_id: Source entity ID.
        target_entity_id: Target entity ID.
        relation_type: Type/predicate of the relationship.
        data: Additional relationship data.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    source_full_id = f"{ka_id}::{source_entity_id}"
    target_full_id = f"{ka_id}::{target_entity_id}"
    data_str = json.dumps(data or {}, ensure_ascii=False, default=str)

    # MERGE relationship with ON MATCH/ON CREATE
    conn.execute(
        """MATCH (s:Entity {id: $src}), (t:Entity {id: $dst})
           MERGE (s)-[r:Relates]->(t)
           ON MATCH SET
               r.ka_id = $ka_id,
               r.relation_type = $rel_type,
               r.data = $data
           ON CREATE SET
               r.ka_id = $ka_id,
               r.relation_type = $rel_type,
               r.data = $data
        """,
        parameters={
            "src": source_full_id,
            "dst": target_full_id,
            "ka_id": ka_id,
            "rel_type": relation_type,
            "data": data_str,
        },
    )


def get_ka_entities(ka_id: str) -> List[Dict[str, Any]]:
    """Get all entities for a KA.

    Args:
        ka_id: Knowledge Abstract ID.

    Returns:
        List of entity data dicts.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    result = conn.execute(
        "MATCH (e:Entity) WHERE e.ka_id = $id RETURN e.data, e.entity_type ORDER BY e.id",
        parameters={"id": ka_id},
    )
    rows = result.get_all()

    entities = []
    for row in rows:
        if isinstance(row, (list, tuple)):
            data_raw = row[0]
            entity_type = row[1] if len(row) > 1 else None
        elif isinstance(row, dict):
            data_raw = row.get("e.data", row.get("data"))
            entity_type = row.get("e.entity_type", row.get("entity_type"))
        else:
            continue

        if isinstance(data_raw, str):
            try:
                data = json.loads(data_raw)
            except (json.JSONDecodeError, ValueError):
                data = {"value": data_raw}
        elif isinstance(data_raw, dict):
            data = data_raw
        else:
            data = {}

        if entity_type:
            data["_entity_type"] = entity_type
        entities.append(data)

    return entities


def get_ka_relationships(ka_id: str) -> List[Dict[str, Any]]:
    """Get all relationships for a KA.

    Args:
        ka_id: Knowledge Abstract ID.

    Returns:
        List of relationship dicts with source/target/type/data.
    """
    mgr = LadybugDBManager.get_instance()
    conn = mgr.connection

    result = conn.execute(
        """MATCH (s:Entity)-[r:Relates]->(t:Entity)
           WHERE r.ka_id = $id
           RETURN s.id AS source, t.id AS target,
                  r.relation_type AS rel_type, r.data AS data
           ORDER BY r.relation_type""",
        parameters={"id": ka_id},
    )
    rows = result.get_all()

    relationships = []
    for row in rows:
        if isinstance(row, (list, tuple)):
            source_full = row[0]
            target_full = row[1]
            rel_type = row[2]
            data_raw = row[3]
        elif isinstance(row, dict):
            source_full = row.get("source", "")
            target_full = row.get("target", "")
            rel_type = row.get("rel_type", "")
            data_raw = row.get("data", {})
        else:
            continue

        # Strip ka_id prefix from entity IDs
        prefix = f"{ka_id}::"
        source_id = source_full.replace(prefix, "") if source_full else ""
        target_id = target_full.replace(prefix, "") if target_full else ""

        if isinstance(data_raw, str):
            try:
                rel_data = json.loads(data_raw)
            except (json.JSONDecodeError, ValueError):
                rel_data = {}
        elif isinstance(data_raw, dict):
            rel_data = data_raw
        else:
            rel_data = {}

        relationships.append({
            "source": source_id,
            "target": target_id,
            "type": rel_type,
            "data": rel_data,
        })

    return relationships


def _row_to_ka_dict(row: Any) -> Dict[str, Any]:
    """Convert a LadybugDB result row to a KA dict.

    Metadata is stored as a JSON-serialized STRING and parsed back.
    """
    if isinstance(row, dict):
        data = dict(row)
    elif isinstance(row, (list, tuple)) and len(row) == 1 and isinstance(row[0], dict):
        data = dict(row[0])
    elif isinstance(row, (list, tuple)):
        data = _map_ka_row_to_columns(row)
    else:
        data = {}

    # Clean keys (remove table prefix if any) and parse metadata JSON
    result = {}
    for key, val in data.items():
        clean_key = (
            key.replace("ka.", "")
            if isinstance(key, str) and key.startswith("ka.")
            else key
        )
        if clean_key == "metadata" and isinstance(val, str):
            try:
                result[clean_key] = json.loads(val)
            except (json.JSONDecodeError, ValueError, TypeError):
                result[clean_key] = val
        else:
            result[clean_key] = val

    return result


def _map_ka_row_to_columns(row: List) -> Dict[str, Any]:
    """Map a flat KA row to column names."""
    column_names = [
        "id", "template_name", "lang", "type",
        "created_at", "updated_at", "metadata",
    ]
    data = {}
    for i, col in enumerate(column_names):
        if i < len(row):
            data[col] = row[i]
    return data


# =========================================================================
# Data path helpers (tying LadybugDB to legacy path-based access)
# =========================================================================


def store_ka_from_path(
    ka_path: str,
    template_name: str,
    lang: str,
    type_: str,
    data_dict: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Store a KA from serialized data, preserving backward compatibility.

    This is used by BaseAutoType.dump() to store KA data in LadybugDB.

    Args:
        ka_path: Filesystem path (used as identifier).
        template_name: Template name.
        lang: Language code.
        type_: AutoType.
        data_dict: The serialized data dict (from model_dump()).
        metadata: Additional metadata.

    Returns:
        KA ID string.
    """
    meta = dict(metadata or {})
    meta["path"] = str(ka_path)

    # Check if KA already exists for this path
    existing = get_ka_by_path(str(ka_path))
    if existing:
        ka_id = existing["id"]
        update_ka_metadata(ka_id, meta)
    else:
        ka_id = create_ka(template_name, lang, type_, meta)

    # Store entities and relationships based on type
    if type_ in ("graph", "hypergraph", "temporal_graph", "spatial_graph",
                  "spatio_temporal_graph"):
        # Graph type: has nodes/entities and edges/relations
        entities = data_dict.get("nodes", data_dict.get("entities", []))
        relations = data_dict.get("edges", data_dict.get("relations", []))

        # Store entities
        entity_batch = []
        for i, entity in enumerate(entities):
            entity_id = entity.get("name", entity.get("id", str(i)))
            entity_type = entity.get("type", entity.get("label", "entity"))
            entity_batch.append((entity_id, entity_type, entity))

        if entity_batch:
            store_entities_batch(ka_id, entity_batch)

        # Store relationships
        for relation in relations:
            source = relation.get("source", relation.get("startNode", {}))
            target = relation.get("target", relation.get("endNode", {}))
            rel_type = relation.get("type", relation.get("name", "related_to"))

            # Handle both string and dict source/target
            if isinstance(source, dict):
                source_id = source.get("name", source.get("id", ""))
            else:
                source_id = str(source)

            if isinstance(target, dict):
                target_id = target.get("name", target.get("id", ""))
            else:
                target_id = str(target)

            if source_id and target_id:
                store_relationship(
                    ka_id, source_id, target_id, rel_type, relation
                )
    elif type_ in ("model", "list", "set"):
        # Simple types: store as entities
        items = data_dict.get("items", data_dict.get("fields", [data_dict]))
        if isinstance(items, dict):
            items = [items]

        entity_batch = []
        for i, item in enumerate(items):
            if isinstance(item, dict):
                item_id = item.get("name", item.get("id", str(i)))
                item_type = type_
                entity_batch.append((item_id, item_type, item))

        if entity_batch:
            store_entities_batch(ka_id, entity_batch)

    return ka_id


def load_ka_from_db(
    ka_path: str, type_: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Load KA data from LadybugDB.

    Args:
        ka_path: Filesystem path to the KA (used to find the record).
        type_: AutoType to determine the structure.

    Returns:
        Tuple of (data_dict, metadata_dict) or (None, None) if not found.
    """
    existing = get_ka_by_path(str(ka_path))
    if not existing:
        return None, None

    ka_id = existing["id"]

    # Initialize data structure
    if type_ in ("graph", "hypergraph", "temporal_graph", "spatial_graph",
                  "spatio_temporal_graph"):
        entities = get_ka_entities(ka_id)
        relations = get_ka_relationships(ka_id)

        # Skip internal _entity_type field
        clean_entities = []
        for e in entities:
            clean = {k: v for k, v in e.items() if k != "_entity_type"}
            clean_entities.append(clean)

        data = {
            "nodes": clean_entities,
            "edges": relations,
        }
    elif type_ in ("model",):
        entities = get_ka_entities(ka_id)
        if entities:
            data = entities[0]  # Model is a single record
        else:
            data = {}
    else:
        # list, set
        entities = get_ka_entities(ka_id)
        clean_entities = []
        for e in entities:
            clean = {k: v for k, v in e.items() if k != "_entity_type"}
            clean_entities.append(clean)
        data = {"items": clean_entities}

    # Build metadata dict from KA node
    metadata = {
        "template": existing.get("template_name"),
        "lang": existing.get("lang"),
        "type": existing.get("type"),
        "created_at": existing.get("created_at"),
        "updated_at": existing.get("updated_at"),
    }
    extra_meta = existing.get("metadata")
    if isinstance(extra_meta, dict):
        metadata.update(extra_meta)

    return data, metadata


# =========================================================================
# Subgraph-based KA storage (strongly-typed per-KA graphs)
# =========================================================================
#
# Each subgraph is a separate ``he.<name>.lbdb`` database file created
# via ``CREATE GRAPH <name>`` on the main DB connection.  All DDL and
# DML go through the main DB connection with ``USE GRAPH``, so the
# subgraph is registered in the main catalog automatically.

import re


def _sanitize_graph_name(name: str) -> str:
    """Normalise an arbitrary string into a valid graph name."""
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if safe and safe[0].isdigit():
        safe = "g_" + safe
    return safe or "ka"


def _subgraph_conn(safe_name: str) -> lb.Connection:
    """Create a connection to the subgraph through the main DB.

    ``CREATE GRAPH`` if needed, then ``USE GRAPH``.  All DDL/DML
    go through the main ``lb.Database`` so the subgraph is
    registered in the catalog automatically.
    """
    mgr = LadybugDBManager.get_instance()
    conn = lb.Connection(mgr.database)
    try:
        conn.execute(f"CREATE GRAPH {safe_name}")
    except RuntimeError as e:
        if "already exists" not in str(e).lower():
            conn.close()
            raise
    conn.execute(f"USE GRAPH {safe_name}")
    return conn


def _init_ka_schema(conn: lb.Connection) -> None:
    """DDL: create Entity node table and Relates rel table."""
    try:
        conn.execute("INSTALL JSON")
        conn.execute("LOAD EXTENSION JSON")
    except Exception:
        pass

    conn.execute(
        """CREATE NODE TABLE IF NOT EXISTS Entity (
            id STRING,
            entity_type STRING,
            data JSON,
            PRIMARY KEY (id)
        )"""
    )
    conn.execute(
        """CREATE REL TABLE IF NOT EXISTS Relates (
            FROM Entity TO Entity,
            relation_type STRING,
            data JSON
        )"""
    )


def store_ka_in_subgraph(
    graph_name: str,
    data_dict: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Store KA data in a dedicated LadybugDB subgraph.

    Creates (or reuses) the subgraph via ``CREATE GRAPH`` on the main
    DB connection, runs DDL to create Entity / Relates tables, then
    inserts all nodes and edges.

    Args:
        graph_name: Name for the subgraph.
        data_dict: Serialised KA data dict (from ``model_dump()``).
        metadata: Optional metadata dict.
    """
    safe_name = _sanitize_graph_name(graph_name)

    conn = _subgraph_conn(safe_name)
    try:
        _init_ka_schema(conn)

        # ── Metadata ──
        if metadata:
            meta_json = json.dumps(metadata, ensure_ascii=False, default=str)
            conn.execute(
                "MERGE (e:Entity {id: '_meta'}) "
                "ON MATCH SET e.entity_type = 'metadata', e.data = $data "
                "ON CREATE SET e.entity_type = 'metadata', e.data = $data",
                parameters={"data": meta_json},
            )

        # ── Nodes / entities ──
        entities = data_dict.get("nodes", data_dict.get("entities", []))
        for i, entity in enumerate(entities):
            if not isinstance(entity, dict):
                continue
            entity_id = entity.get("name", entity.get("id", str(i)))
            entity_type = entity.get("type", entity.get("label", "entity"))
            conn.execute(
                "MERGE (e:Entity {id: $id}) "
                "ON MATCH SET e.entity_type = $type, e.data = $data "
                "ON CREATE SET e.entity_type = $type, e.data = $data",
                parameters={
                    "id": entity_id,
                    "type": entity_type,
                    "data": json.dumps(entity, ensure_ascii=False, default=str),
                },
            )

        # ── Edges / relations (REL TABLE) ──
        relations = data_dict.get("edges", data_dict.get("relations", []))
        for relation in relations:
            if not isinstance(relation, dict):
                continue

            source = relation.get("source", relation.get("startNode", {}))
            target = relation.get("target", relation.get("endNode", {}))
            rel_type = relation.get("type", relation.get("name", "related_to"))

            if isinstance(source, dict):
                source_id = source.get("name", source.get("id", ""))
            else:
                source_id = str(source)

            if isinstance(target, dict):
                target_id = target.get("name", target.get("id", ""))
            else:
                target_id = str(target)

            if not source_id or not target_id:
                continue

            edge_data = {
                k: v for k, v in relation.items()
                if k not in ("source", "target", "startNode", "endNode")
            }

            conn.execute(
                "MATCH (s:Entity {id: $src}), (t:Entity {id: $dst}) "
                "MERGE (s)-[r:Relates {relation_type: $rel_type}]->(t) "
                "ON MATCH SET r.data = $data "
                "ON CREATE SET r.data = $data",
                parameters={
                    "src": source_id,
                    "dst": target_id,
                    "rel_type": rel_type,
                    "data": json.dumps(edge_data, ensure_ascii=False, default=str),
                },
            )

        # ── Simple types (model / list / set) ──
        items = data_dict.get("items", data_dict.get("fields", []))
        if isinstance(items, dict):
            items = [items]
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_id = item.get("name", item.get("id", str(i)))
            conn.execute(
                "MERGE (e:Entity {id: $id}) "
                "ON MATCH SET e.entity_type = 'item', e.data = $data "
                "ON CREATE SET e.entity_type = 'item', e.data = $data",
                parameters={
                    "id": item_id,
                    "data": json.dumps(item, ensure_ascii=False, default=str),
                },
            )

        logger.info(
            "stored_ka_in_subgraph graph=%s entities=%d relations=%d",
            safe_name,
            len(entities),
            len(relations),
        )
    finally:
        conn.close()


def load_ka_from_subgraph(
    graph_name: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Load KA data from a subgraph's database file.

    Args:
        graph_name: Subgraph name (same as ``store_ka_in_subgraph``).

    Returns:
        ``(data_dict, metadata_dict)`` or ``(None, None)``.
    """
    safe_name = _sanitize_graph_name(graph_name)

    conn = _subgraph_conn(safe_name)

    try:
        _init_ka_schema(conn)

        # ── Metadata ──
        metadata = None
        try:
            r = conn.execute("MATCH (e:Entity {id: '_meta'}) RETURN e.data")
            rows = r.get_all()
            if rows:
                raw = rows[0][0] if isinstance(rows[0], (list, tuple)) else rows[0]
                if isinstance(raw, str):
                    metadata = json.loads(raw)
                elif isinstance(raw, dict):
                    metadata = raw
        except Exception:
            pass

        # ── Nodes ──
        r = conn.execute(
            "MATCH (e:Entity) WHERE e.id <> '_meta' AND e.entity_type <> 'item' "
            "RETURN e.data, e.entity_type"
        )
        nodes = []
        for row in r.get_all():
            data_raw = row[0] if isinstance(row, (list, tuple)) else row.get("e.data", row.get("data"))
            if isinstance(data_raw, str):
                try:
                    nodes.append(json.loads(data_raw))
                except json.JSONDecodeError:
                    nodes.append({"value": data_raw})
            elif isinstance(data_raw, dict):
                nodes.append(data_raw)

        # ── Edges from REL TABLE ──
        r = conn.execute(
            "MATCH (s:Entity)-[rel:Relates]->(t:Entity) "
            "RETURN s.id AS src, t.id AS dst, rel.relation_type AS rtype, rel.data AS rdata"
        )
        relations = []
        for row in r.get_all():
            if isinstance(row, (list, tuple)):
                src, dst, rtype, rdata_raw = row[0], row[1], row[2], row[3]
            else:
                src = row.get("src", "")
                dst = row.get("dst", "")
                rtype = row.get("rtype", "")
                rdata_raw = row.get("rdata", {})

            if isinstance(rdata_raw, str):
                try:
                    rdata = json.loads(rdata_raw)
                except (json.JSONDecodeError, ValueError):
                    rdata = {}
            elif isinstance(rdata_raw, dict):
                rdata = rdata_raw
            else:
                rdata = {}

            rdata["source"] = src
            rdata["target"] = dst
            rdata["type"] = rtype
            relations.append(rdata)

        # ── Items (list / set / model) ──
        r = conn.execute(
            "MATCH (e:Entity) WHERE e.entity_type = 'item' RETURN e.data"
        )
        items = []
        for row in r.get_all():
            data_raw = row[0] if isinstance(row, (list, tuple)) else row
            if isinstance(data_raw, str):
                try:
                    items.append(json.loads(data_raw))
                except json.JSONDecodeError:
                    items.append({"value": data_raw})
            elif isinstance(data_raw, dict):
                items.append(data_raw)

        # Build output in the same shape as load_ka_from_db
        if nodes and any(n.get("type") for n in nodes):
            data = {"nodes": nodes, "edges": relations}
        elif items:
            data = {"items": items}
        elif nodes:
            data = {"nodes": nodes, "edges": relations}
        else:
            data = {}

        return data, metadata

    finally:
        conn.close()


def list_ka_subgraphs() -> List[str]:
    """List registered subgraphs from the main DB catalog.

    Uses ``CALL show_graphs()`` on a fresh connection to the main
    database.  Subgraphs are registered by ``CREATE GRAPH`` inside
    ``store_ka_in_subgraph()``.

    Returns:
        List of subgraph names.
    """
    mgr = LadybugDBManager.get_instance()
    conn = lb.Connection(mgr.database)
    try:
        r = conn.execute("CALL show_graphs() RETURN *")
        return [row[0] for row in r.get_all()]
    finally:
        conn.close()


def delete_ka_subgraph(graph_name: str) -> bool:
    """Drop a subgraph from the main DB catalog.

    ``DROP GRAPH`` removes both the catalog entry and the underlying
    database file.

    Args:
        graph_name: Subgraph name.

    Returns:
        ``True`` if the subgraph existed.
    """
    safe_name = _sanitize_graph_name(graph_name)
    mgr = LadybugDBManager.get_instance()
    conn = lb.Connection(mgr.database)
    try:
        conn.execute(f"DROP GRAPH {safe_name}")
        logger.info("deleted_ka_subgraph graph=%s", safe_name)
        return True
    except Exception:
        return False
    finally:
        conn.close()


__all__ = [
    "LadybugDBManager",
    "store_template",
    "get_template",
    "list_templates",
    "delete_template",
    "create_ka",
    "update_ka_metadata",
    "get_ka",
    "get_ka_by_path",
    "delete_ka",
    "store_entity",
    "store_entities_batch",
    "store_relationship",
    "get_ka_entities",
    "get_ka_relationships",
    "store_ka_from_path",
    "load_ka_from_db",
    "store_ka_in_subgraph",
    "load_ka_from_subgraph",
    "list_ka_subgraphs",
    "delete_ka_subgraph",
]
