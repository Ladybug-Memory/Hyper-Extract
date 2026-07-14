"""Template Gallery - Manages discovery and loading of knowledge extraction templates.

Templates are now loaded from LadybugDB instead of YAML files.
The legacy presets directory is still checked for migration purposes.
"""

from pathlib import Path
from typing import Dict, Optional

from .parsers import TemplateCfg, load_template_config, list_template_configs


class Gallery:
    """Template Gallery.

    Templates are stored in LadybugDB and loaded on demand.
    Legacy YAML file loading is deprecated.

    Usage Examples:
        from hyperextract.utils.template_engine import Gallery

        # Get template by path
        config = Gallery.get("general/graph")

        # List all templates (returns Dict[str, TemplateCfg])
        all_templates = Gallery.list()

        # List templates with filters
        graph_templates = Gallery.list(filter_by_type="graph")
        zh_templates = Gallery.list(filter_by_language="zh")
    """

    _instance: Optional["Gallery"] = None

    def __init__(self):
        self._configs: Dict[str, TemplateCfg] = {}

    @classmethod
    def get(cls, path: str) -> Optional[TemplateCfg]:
        """Get template configuration by path.

        Args:
            path: Template path
                If no domain is specified, "general/" is assumed.
                Only templates in the "general/" domain are supported.
                Other domains are not supported.
                (e.g., "general/graph" or "graph")

        Returns:
            TemplateCfg or None if not found
        """
        # First check in-memory cache
        if cls._instance:
            if "/" in path:
                config = cls._instance._configs.get(path)
                if config:
                    return config
            else:
                config = cls._instance._configs.get(f"general/{path}")
                if config:
                    return config

        # Fall back to direct LadybugDB lookup
        try:
            return load_template_config(path)
        except (FileNotFoundError, Exception):
            return None

    @classmethod
    def list(
        cls,
        filter_by_query: str = None,
        filter_by_type: str = None,
        filter_by_tag: str = None,
        filter_by_language: str = None,
    ) -> Dict[str, "TemplateCfg"]:
        """List templates with optional filters.

        Args:
            filter_by_query: Search in template name/description
            filter_by_type: Filter by autotype (e.g., "graph", "list", "model")
            filter_by_tag: Filter by tag
            filter_by_language: Filter by language (e.g., "zh", "en")

        Returns:
            Dict mapping template name to TemplateCfg
        """
        # Load from LadybugDB with filters
        return list_template_configs(
            filter_by_type=filter_by_type,
            filter_by_tag=filter_by_tag,
            filter_by_query=filter_by_query,
            filter_by_language=filter_by_language,
        )

    @classmethod
    def refresh(cls) -> None:
        """Refresh the gallery cache from LadybugDB."""
        if cls._instance:
            cls._instance._configs = list_template_configs()


def _init_gallery() -> Gallery:
    """Initialize the gallery by loading templates from LadybugDB."""
    gallery = Gallery()
    Gallery._instance = gallery

    # Load templates from LadybugDB into cache
    try:
        gallery._configs = list_template_configs()
    except Exception as e:
        print(f"Warning: Could not load templates from LadybugDB: {e}")
        print("Run the migration script to populate the database.")

    return gallery


Gallery._instance = _init_gallery()
