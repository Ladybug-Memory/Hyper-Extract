"""Script to migrate YAML template files into LadybugDB.

Run this once to populate LadybugDB with all template definitions.
"""

import sys
import json
from pathlib import Path


def migrate_templates():
    """Load all YAML template files into LadybugDB."""
    # Import YAML lazily since it may not be installed in all environments
    try:
        import yaml
    except ImportError:
        print("PyYAML is required. Install it with: pip install pyyaml")
        sys.exit(1)

    from hyperextract.ladybug_db import (
        LadybugDBManager,
        store_template,
        list_templates,
    )

    presets_dir = (
        Path(__file__).parent.parent / "templates" / "presets"
    )

    if not presets_dir.exists():
        print(f"Templates directory not found: {presets_dir}")
        return

    count = 0
    for file_path in sorted(presets_dir.rglob("*.yaml")):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not isinstance(data, dict):
                print(f"  Skipping {file_path}: not a valid template")
                continue

            # Add domain info
            domain = file_path.parent.relative_to(presets_dir).parts[0]
            data["_domain"] = domain

            # Store in LadybugDB
            store_template(data)
            count += 1
            print(f"  ✓ {domain}/{data.get('name', '?')} ({file_path.name})")

        except Exception as e:
            print(f"  ✗ {file_path}: {e}")

    print(f"\nMigrated {count} templates to LadybugDB")

    # Verify
    print("\nVerifying...")
    templates = list_templates()
    print(f"Found {len(templates)} templates in LadybugDB:")
    for name in sorted(templates.keys()):
        print(f"  - {name}")


if __name__ == "__main__":
    migrate_templates()
