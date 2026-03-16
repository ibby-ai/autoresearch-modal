"""Generate docs/generated/db-schema.md from the typed runtime settings surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args, get_origin

from agent_sandbox.config.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "docs" / "generated" / "db-schema.md"


def format_annotation(annotation: Any) -> str:
    """Render a readable type name for Markdown output."""
    origin = get_origin(annotation)
    if origin is None:
        return getattr(annotation, "__name__", str(annotation))

    args = ", ".join(format_annotation(arg) for arg in get_args(annotation))
    origin_name = getattr(origin, "__name__", str(origin))
    return f"{origin_name}[{args}]"


def format_default(value: Any) -> str:
    if value == "":
        return '""'
    return str(value)


def build_markdown() -> str:
    lines = [
        "# Generated Schema Snapshot",
        "",
        "This repository does not currently own a first-party application database.",
        "Until that changes, this generated artifact snapshots the typed runtime settings surface that agents most often need to inspect.",
        "",
        "- Source: `agent_sandbox/config/settings.py` (`Settings`)",
        "- Regenerate with: `uv run python scripts/generate_db_schema.py`",
        "",
        "## Current Persistence Status",
        "",
        "- Database tables: none",
        "- Durable state lives in Modal volumes and upstream `results.tsv` files",
        "",
        "## Typed Runtime Settings",
        "",
        "| Field | Type | Default | Description |",
        "| --- | --- | --- | --- |",
    ]

    for field_name, field in Settings.model_fields.items():
        description = field.description or ""
        lines.append(
            f"| `{field_name}` | `{format_annotation(field.annotation)}` | "
            f"`{format_default(field.default)}` | {description} |"
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- If this repo gains a database or durable structured store, replace this placeholder workflow with a real schema exporter.",
            "- Keep this file generated; do not hand-edit it.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(build_markdown(), encoding="utf-8")


if __name__ == "__main__":
    main()
