"""Check saved sync custom field values against the connector's schema (``add_sync_custom_field``)."""

import re
from typing import Any


def _is_blank(value: object) -> bool:
    if isinstance(value, list):
        return not any(str(v).strip() for v in value)
    return value is None or (isinstance(value, str) and not value.strip())


def _same(a: object, b: object) -> bool:
    # Booleans arrive as True or "true", numbers as 3 or "3".
    return str(a).strip().lower() == str(b).strip().lower()


def is_shown(field: dict[str, Any], values: dict[str, Any]) -> bool:
    """Whether the form shows ``field`` for these values (its ``showWhen``, with schema defaults filling gaps)."""
    return all(
        any(_same(values.get(other), allowed) for allowed in allowed_values)
        for other, allowed_values in (field.get("showWhen") or {}).items()
    )


def _whole_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    # ASCII digits only: str.isdigit() also passes "²", which int() rejects.
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
        return int(value.strip())
    return None


def _range_text(low: int | None, high: int | None) -> str:
    if low is not None and high is not None:
        return f"from {low:,} to {high:,}"
    return f"of at least {low:,}" if low is not None else f"of at most {high:,}"


def sync_custom_field_problems(
    schema_fields: list[dict[str, Any]], submitted: dict[str, Any], current: dict[str, Any]
) -> list[str]:
    """Messages for the ``submitted`` sync values that break their field's rules; ``current`` is the
    merged sync config, which decides which fields are shown. Fields not submitted are left alone, so
    a save is never refused for a value it didn't change."""
    defaults = {f["name"]: f.get("defaultValue") for f in schema_fields if f.get("name")}
    effective = {**defaults, **{k: v for k, v in current.items() if v is not None}}
    problems: list[str] = []
    for field in schema_fields:
        name = field.get("name")
        if name not in submitted or not is_shown(field, effective):
            continue
        value, label = submitted[name], field.get("displayName") or name
        if _is_blank(value):
            if field.get("required"):
                problems.append(f"{label}: This field is required.")
            continue
        validation = field.get("validation") or {}
        if field.get("fieldType") == "NUMBER" and ("min" in validation or "max" in validation):
            number = _whole_number(value)
            low, high = validation.get("min"), validation.get("max")
            if number is None or (low is not None and number < low) or (high is not None and number > high):
                problems.append(f"{label}: Enter a whole number {_range_text(low, high)}.")
        elif field.get("fieldType") == "SELECT" and field.get("options") and value not in field["options"]:
            problems.append(f"{label}: Choose one of the listed options.")
    return problems
