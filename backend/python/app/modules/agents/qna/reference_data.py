"""Single source of truth for referenceData field definitions and shared formatters."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ReferenceDataField:
    """One field in referenceData (core JSON keys or nested metadata)."""

    name: str
    description: str
    required: Literal["always", "when_available", "conditional"]
    is_core: bool = True
    condition: str | None = None


# Add new fields here; non-core fields live under `metadata` in stored JSON.
REFERENCE_DATA_FIELDS: list[ReferenceDataField] = [
    ReferenceDataField("name", "Human-readable display name", "always"),
    ReferenceDataField(
        "type",
        "Item category (site, file, notebook, page, issue, project, channel, message, etc.)",
        "always",
    ),
    ReferenceDataField(
        "app",
        "Source application (sharepoint, jira, confluence, slack, drive, gmail, teams, clickup, etc.)",
        "always",
    ),
    ReferenceDataField("id", "Technical ID from the tool result (notebook_id, file_id, etc.)", "when_available"),
    ReferenceDataField(
        "key",
        "Short alphanumeric key (e.g. Jira PA-123, project key PA)",
        "when_available",
        is_core=False,
    ),
    ReferenceDataField("webUrl", "Direct browser URL to the item", "when_available"),
    ReferenceDataField(
        "siteId",
        "SharePoint site_id from find_notebook — required for list_notebook_pages",
        "conditional",
        is_core=False,
        condition="SharePoint notebooks",
    ),
]

CORE_FIELDS = [f.name for f in REFERENCE_DATA_FIELDS if f.is_core]
METADATA_FIELDS = [f.name for f in REFERENCE_DATA_FIELDS if not f.is_core]


def _metadata_source(item: dict[str, object]) -> dict[str, object]:
    """Normalize metadata dict; merge legacy top-level metadata fields for older messages."""
    raw = item.get("metadata")
    out: dict[str, object] = {}
    if isinstance(raw, dict):
        out.update({str(k): v for k, v in raw.items() if v is not None and str(v).strip()})
    for fname in METADATA_FIELDS:
        if fname not in out and item.get(fname) not in (None, ""):
            out[fname] = item[fname]
    return out


def _field_value_for_display(item: dict[str, object], field: ReferenceDataField) -> str:
    if field.is_core:
        val = item.get(field.name)
    else:
        val = _metadata_source(item).get(field.name)
    if val is None:
        return ""
    s = str(val).strip()
    return s


def format_reference_data(
    all_reference_data: list[dict[str, object]],
    *,
    header: str = "## Reference Data (use these IDs/keys directly):",
    max_items: int = 10,
    log: logging.Logger | None = None,
) -> str:
    """Format reference data for planner / response prompts (grouped by app)."""
    if not all_reference_data:
        return ""

    by_app: dict[str, list[dict[str, object]]] = {}
    for item in all_reference_data:
        app = str(item.get("app", "unknown") or "unknown")
        by_app.setdefault(app, []).append(item)

    lines = [header]

    for app, items in by_app.items():
        parts: list[str] = []
        for item in items[:max_items]:
            name = str(item.get("name", "?") or "?")
            identifiers: list[str] = []
            for field in REFERENCE_DATA_FIELDS:
                val = _field_value_for_display(item, field)
                if val:
                    identifiers.append(f"{field.name}={val}")

            id_str = f" ({', '.join(identifiers)})" if identifiers else ""
            parts.append(f"{name}{id_str}")

        app_display = app.replace("_", " ").title()
        lines.append(f"**{app_display}**: {', '.join(parts)}")

    if log is not None:
        log.debug("📎 Reference data: %s items across %s apps", len(all_reference_data), len(by_app))

    return "\n".join(lines)


def generate_field_rules_table() -> str:
    """Markdown table for response_system_prompt (field rules section)."""
    req_label = {
        "always": "Always",
        "when_available": "When available",
        "conditional": "Conditional",
    }
    header = "| Field | Required | Description |\n|---|---|---|"
    rows: list[str] = [header]
    for f in REFERENCE_DATA_FIELDS:
        req = req_label[f.required]
        if f.condition:
            req = f"{req} ({f.condition})"
        loc = "Top-level JSON key" if f.is_core else "Inside `metadata` object"
        desc = f"{f.description} ({loc})"
        rows.append(f"| `{f.name}` | {req} | {desc} |")
    return "\n".join(rows)


def generate_field_instructions() -> str:
    """Bullet list for tool-results / agent context (locations explicit)."""
    lines: list[str] = []
    for f in REFERENCE_DATA_FIELDS:
        note = f" ({f.condition})" if f.condition else ""
        loc = "top-level" if f.is_core else "inside `metadata`"
        lines.append(f"- `{f.name}` ({loc}): {f.description}{note}")
    return "\n".join(lines)


def get_all_field_names() -> list[str]:
    return [f.name for f in REFERENCE_DATA_FIELDS]


def normalize_reference_data_items(items: object) -> list[dict[str, object]]:
    """Move extension fields from top-level into `metadata` for API/storage consistency."""
    if not isinstance(items, list):
        return []
    out: list[dict[str, object]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        meta: dict[str, str] = {}
        existing_meta = item.get("metadata")
        if isinstance(existing_meta, dict):
            for k, v in existing_meta.items():
                if v is not None and str(v).strip():
                    meta[str(k)] = str(v).strip()
        for fname in METADATA_FIELDS:
            if fname not in item:
                continue
            val = item.get(fname)
            if val is None:
                continue
            s = str(val).strip()
            if s:
                meta.setdefault(fname, s)
            item.pop(fname, None)
        if meta:
            item["metadata"] = meta
        out.append(item)
    return out
