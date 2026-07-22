"""Shared helpers for converting structured (JSON/YAML) data into natural language.

Embedding models are trained on prose, not on ``{``, ``[``, ``:`` punctuation, so
raw structural dumps retrieve poorly. These helpers flatten nested dict/list
values into dot-path keyed scalars and render them as "key: value, ..." text —
the same natural-language-row convention already used by the CSV/Excel/SQL
table parsers (see ``app.utils.indexing_helpers.generate_simple_row_text``).
"""
from __future__ import annotations

import json
from typing import Any

from app.utils.indexing_helpers import generate_simple_row_text

# A dict/list value is classified as a leaf (flattened inline) up to this
# many nested levels; deeper structures are serialized as raw JSON text
# instead of being decomposed into further BlockGroups.
MAX_FLATTEN_DEPTH = 6

# Scalar strings longer than this are pulled out into their own TEXT block
# instead of being embedded inline in a "key: value, ..." sentence, so long
# prose fields (descriptions, notes) get proper sentence-level chunking
# rather than being buried inside a comma-joined row.
LONG_TEXT_THRESHOLD = 300

# An array is treated as a table (object_array) when at least this fraction
# of its items are dicts — tolerates a few stray nulls/strings without
# falling back to a flat JSON dump.
OBJECT_ARRAY_MIN_DICT_RATIO = 0.6


def classify_list(items: list) -> str:
    """Classify a non-empty list as ``object_array``, ``scalar_array`` or ``mixed_array``."""
    dict_items = [x for x in items if isinstance(x, dict)]
    if len(dict_items) == len(items):
        return "object_array"
    if not any(isinstance(x, (dict, list)) for x in items):
        return "scalar_array"
    if dict_items and len(dict_items) >= len(items) * OBJECT_ARRAY_MIN_DICT_RATIO:
        return "object_array"
    return "mixed_array"


def is_flat_dict(obj: dict) -> bool:
    """A dict is "flat" if none of its values need their own nested BlockGroup."""
    for value in obj.values():
        if isinstance(value, dict) and value:
            return False
        if isinstance(value, list) and value and classify_list(value) == "object_array":
            return False
    return True


def classify_value(value: Any) -> str:
    """Classify *value* to decide how the JSON/YAML walker should handle it.

    Returns one of: ``scalar``, ``empty``, ``flat_object``, ``nested_object``,
    ``object_array``, ``scalar_array``, ``mixed_array``.
    """
    if isinstance(value, dict):
        if not value:
            return "empty"
        return "flat_object" if is_flat_dict(value) else "nested_object"
    if isinstance(value, list):
        if not value:
            return "empty"
        return classify_list(value)
    return "scalar"


def flatten_leaf_dict(obj: dict, prefix: str = "") -> dict[str, Any]:
    """Flatten a dict of scalars/small nested structures into dot-path keys.

    Nested dicts are recursed into; nested lists are stringified in place
    (a list of dicts here means the caller already decided this subtree is
    "flat enough" to inline rather than promoting to its own TABLE group).
    """
    flat: dict[str, Any] = {}
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            if value:
                flat.update(flatten_leaf_dict(value, path))
            else:
                flat[path] = "{}"
        elif isinstance(value, list):
            flat[path] = stringify_scalar_array(value)
        else:
            flat[path] = value
    return flat


def stringify_scalar_array(items: list) -> str:
    """Render a list as a short comma-joined string for inline embedding."""
    if not items:
        return "[]"
    rendered = []
    for item in items:
        if isinstance(item, (dict, list)):
            rendered.append(json.dumps(item, default=str, ensure_ascii=False))
        else:
            rendered.append(str(item))
    return ", ".join(rendered)


def flatten_to_text(flat_fields: dict[str, Any]) -> str:
    """Render a flat dot-path -> scalar mapping as a natural-language sentence.

    Reuses the same "key: value, key2: value2" convention as CSV/Excel/SQL
    table rows so the indexing pipeline treats these consistently.
    """
    return generate_simple_row_text(flat_fields)


def object_to_sentence(item: dict) -> str:
    """Flatten a (possibly nested) dict — e.g. one element of a JSON array — into
    a single natural-language sentence suitable for a TABLE_ROW block.
    """
    if not isinstance(item, dict):
        return str(item)
    return flatten_to_text(flatten_leaf_dict(item))
