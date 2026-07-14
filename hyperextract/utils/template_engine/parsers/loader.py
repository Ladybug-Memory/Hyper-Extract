"""Config loader and template configuration models.

Templates are now loaded from LadybugDB instead of YAML files.
The legacy YAML files under templates/presets/ have been migrated to LadybugDB.
"""

from pathlib import Path
from typing import Dict, List, Union
from pydantic import BaseModel

from .schemas.base import VALID_AUTOTYPES, FieldSchema
from .schemas.naive import (
    NaiveGuidelineSchema,
    NaiveOutputSchema,
    NaiveOptionsSchema,
    NaiveDisplaySchema,
    NaiveIdentifierSchema,
)
from .schemas.graph import (
    GraphGuidelineSchema,
    GraphOutputSchema,
    GraphOptionsSchema,
    GraphDisplaySchema,
    GraphIdentifiersSchema,
)

# Import from LadybugDB manager for template operations
from hyperextract.ladybug_db import (
    get_template,
    list_templates,
)


class TemplateCfg(BaseModel):
    """Template configuration loaded from LadybugDB."""

    language: str | List[str] = "en"
    name: str
    type: VALID_AUTOTYPES
    tags: List[str]
    description: str | Dict[str, str]
    output: NaiveOutputSchema | GraphOutputSchema
    guideline: NaiveGuidelineSchema | GraphGuidelineSchema
    identifiers: NaiveIdentifierSchema | GraphIdentifiersSchema | None = None
    options: NaiveOptionsSchema | GraphOptionsSchema | None = None
    display: NaiveDisplaySchema | GraphDisplaySchema


def _localize_data(
    value: str | List[str] | Dict[str, str | List[str]],
    language: str,
) -> str:
    """Get multilingual text value, supports list format

    Args:
        value: Multilingual value, supports str, list or dict format
        language: Target language code

    Returns:
        str: Converted str
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(f"{i + 1}. {item}" for i, item in enumerate(value))
    # Fall back to English (then empty) when the requested language is absent,
    # so the return is always a str — a missing key otherwise returns None.
    dict_value = value.get(language, value.get("en"))
    if isinstance(dict_value, str):
        return dict_value
    if isinstance(dict_value, list):
        return "\n".join(f"{i + 1}. {item}" for i, item in enumerate(dict_value))
    return ""


def _localize_field(field: FieldSchema, language: str) -> FieldSchema:
    """Localize a single field."""
    return FieldSchema(
        name=field.name,
        type=field.type,
        description=_localize_data(field.description, language),
        required=field.required,
        default=field.default,
    )


def _localize_naive_output(
    output: NaiveOutputSchema, language: str
) -> NaiveOutputSchema:
    """Localize NaiveOutputSchema."""
    fields = [_localize_field(f, language) for f in output.fields]
    return NaiveOutputSchema(
        description=_localize_data(output.description, language),
        fields=fields,
    )


def _localize_output(
    output: NaiveOutputSchema | GraphOutputSchema,
    language: str,
    autotype: VALID_AUTOTYPES,
) -> NaiveOutputSchema | GraphOutputSchema:
    """Localize output configuration."""
    if autotype in ("model", "list", "set"):
        return _localize_naive_output(output, language)
    return GraphOutputSchema(
        description=_localize_data(output.description, language),
        entities=_localize_naive_output(output.entities, language),
        relations=_localize_naive_output(output.relations, language),
    )


def _localize_guideline(
    guideline: NaiveGuidelineSchema | GraphGuidelineSchema,
    language: str,
    autotype: VALID_AUTOTYPES,
) -> NaiveGuidelineSchema | GraphGuidelineSchema:
    """Localize guideline configuration."""
    if autotype in ("model", "list", "set"):
        return NaiveGuidelineSchema(
            target=_localize_data(guideline.target, language),
            rules=_localize_data(guideline.rules, language),
        )
    return GraphGuidelineSchema(
        target=_localize_data(guideline.target, language),
        rules_for_entities=_localize_data(guideline.rules_for_entities, language),
        rules_for_relations=_localize_data(guideline.rules_for_relations, language),
        rules_for_time=_localize_data(guideline.rules_for_time, language)
        if guideline.rules_for_time
        else None,
        rules_for_location=_localize_data(guideline.rules_for_location, language)
        if guideline.rules_for_location
        else None,
    )


def localize_template(config: TemplateCfg, language: str) -> TemplateCfg:
    """Convert multilingual template config to single-language config.

    Args:
        config: Original multilingual config
        language: Target language code (e.g., 'zh', 'en')

    Returns:
        TemplateCfg: Single-language config with all multilingual fields converted to strings

    Examples:
        >>> config = load_template_config("general/graph")
        >>> config_zh = localize_template(config, "zh")
        >>> print(config_zh.description)  # str
    """

    return TemplateCfg(
        language=language,
        name=config.name,
        type=config.type,
        tags=config.tags,
        description=_localize_data(config.description, language),
        output=_localize_output(config.output, language, config.type),
        guideline=_localize_guideline(config.guideline, language, config.type),
        identifiers=config.identifiers,
        options=config.options,
        display=config.display,
    )


def load_template_config(name: str) -> TemplateCfg:
    """Load and validate template configuration from LadybugDB.

    Args:
        name: Template name (e.g., "general/graph" or "/path/to/template.yaml").

    Returns:
        TemplateCfg instance.

    Raises:
        FileNotFoundError: If template not found in LadybugDB.
        ValueError: If template configuration is invalid.
    """
    # Support legacy file path loading for backward compatibility
    if isinstance(name, (str, Path)) and (
        str(name).endswith(".yaml") or Path(name).exists()
    ):
        # Legacy YAML loading path - deprecated but kept for transition
        import yaml

        path = Path(name)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {name}")

        with open(path, "r", encoding="utf-8") as f:
            template = TemplateCfg(**yaml.safe_load(f))
    else:
        # Load from LadybugDB
        template_dict = get_template(name)
        if template_dict is None:
            raise FileNotFoundError(
                f"Template '{name}' not found in LadybugDB. "
                f"Run migration script to load templates."
            )

        # Convert dict to TemplateCfg
        template = _template_dict_to_cfg(template_dict)

    # Validate and localize the template for each language
    existing_languages = (
        template.language
        if isinstance(template.language, list)
        else [template.language]
    )
    for lang in existing_languages:
        try:
            localize_template(template, lang)
        except Exception as e:
            raise ValueError(
                f"The template configuration is not valid for language {lang}: {e}"
            )

    return template


def load_template(file_path: Union[str, Path]) -> TemplateCfg:
    """Load and validate template configuration (legacy YAML path).

    Deprecated: Use load_template_config() instead.
    Kept for backward compatibility.

    Args:
        file_path: Path to YAML file.

    Returns:
        TemplateCfg instance.
    """
    return load_template_config(file_path)


def list_template_configs(
    filter_by_type: str = None,
    filter_by_tag: str = None,
    filter_by_query: str = None,
    filter_by_language: str = None,
) -> Dict[str, "TemplateCfg"]:
    """List template configurations with optional filters.

    Args:
        filter_by_type: Filter by autotype (e.g., "graph", "list", "model")
        filter_by_tag: Filter by tag
        filter_by_query: Search in template name/description
        filter_by_language: Filter by language (e.g., "zh", "en")

    Returns:
        Dict mapping template name to TemplateCfg
    """
    templates_dict = list_templates(
        filter_by_type=filter_by_type,
        filter_by_tag=filter_by_tag,
        filter_by_query=filter_by_query,
        filter_by_language=filter_by_language,
    )

    result = {}
    for name, tdict in templates_dict.items():
        try:
            cfg = _template_dict_to_cfg(tdict)
            result[name] = cfg
        except Exception as e:
            print(f"Failed to convert template '{name}': {e}")

    return result


def _template_dict_to_cfg(data: dict) -> TemplateCfg:
    """Convert a template dict from LadybugDB to a TemplateCfg instance.

    Args:
        data: Template data dict retrieved from LadybugDB.

    Returns:
        TemplateCfg instance.
    """
    # The data dict may have keys like "name", "domain", "type", "tags",
    # "description", "language", "output", "guideline", "identifiers",
    # "options", "display". Also may include "_key".

    cfg_data = {}

    # Basic fields
    cfg_data["name"] = data.get("name", "unknown")
    if "/" in cfg_data["name"]:
        cfg_data["name"] = cfg_data["name"].split("/", 1)[1]

    cfg_data["type"] = data.get("type", "graph")
    cfg_data["tags"] = data.get("tags", [])

    # Description can be a string or dict
    desc = data.get("description", "")
    cfg_data["description"] = desc

    # Language
    lang = data.get("language", "en")
    cfg_data["language"] = lang

    # Parse output - needs to match the schema structure
    output_raw = data.get("output", {})
    if isinstance(output_raw, str):
        output_raw = _safe_json_load(output_raw, {})
    cfg_data["output"] = output_raw

    # Parse guideline
    guideline_raw = data.get("guideline", {})
    if isinstance(guideline_raw, str):
        guideline_raw = _safe_json_load(guideline_raw, {})
    cfg_data["guideline"] = guideline_raw

    # Parse identifiers (optional)
    identifiers_raw = data.get("identifiers")
    if identifiers_raw is not None:
        if isinstance(identifiers_raw, str):
            identifiers_raw = _safe_json_load(identifiers_raw, {})
        # Only set if non-empty
        if identifiers_raw:
            cfg_data["identifiers"] = identifiers_raw

    # Parse options (optional)
    options_raw = data.get("options")
    if options_raw is not None:
        if isinstance(options_raw, str):
            options_raw = _safe_json_load(options_raw, {})
        if options_raw:
            cfg_data["options"] = options_raw

    # Parse display (optional)
    display_raw = data.get("display")
    if display_raw is not None:
        if isinstance(display_raw, str):
            display_raw = _safe_json_load(display_raw, {})
        if display_raw:
            cfg_data["display"] = display_raw

    return TemplateCfg(**cfg_data)


def _safe_json_load(value: str, default=None):
    """Safely parse a JSON string."""
    import json
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError, TypeError):
        return default if default is not None else value


__all__ = [
    "TemplateCfg",
    "load_template",
    "load_template_config",
    "localize_template",
    "list_template_configs",
]
