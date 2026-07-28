from __future__ import annotations

import json
from typing import Any

"""Defensive coercion helpers for `complete_structured()` results.

A JSON-schema `output_schema` passed to `complete_structured` (see
`models/base.py::SupportsStructuredComplete`) is a strong hint to the
model, not a validated guarantee — provider implementations typically force
it via a tool-use trick (see `AnthropicTransport.complete_structured`)
rather than a real schema validator. In practice, nested `array`/`object`
fields occasionally come back as a JSON-encoded STRING instead of the real
list/dict (more likely the deeper/less common the shape), which crashes a
naive `.get(...)` call on what turns out to be a `str`.

Every caller that walks a `complete_structured` response's nested
structure should coerce through these first, so a single malformed nested
field degrades to "skip this entry" / "treat as empty" rather than an
unhandled `AttributeError`/`TypeError` deep in application code.
"""

__all__ = ["coerce_list", "coerce_dict", "coerce_optional_str"]


def coerce_list(value: Any) -> list[Any]:
    """Coerce a should-be-list value, tolerating a JSON-encoded string
    (parsed and re-checked) — anything else not list-shaped degrades to an
    empty list rather than raising."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def coerce_dict(value: Any) -> dict[str, Any] | None:
    """Coerce a should-be-object value the same way `coerce_list` does for
    arrays. Returns None (rather than `{}`) on failure so callers can
    distinguish "empty object" from "not an object at all" and skip the
    entry instead of silently treating it as present-but-empty."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def coerce_optional_str(value: Any) -> str | None:
    """Coerce a should-be-optional-string value: None/"" stay None, an
    actual string passes through, anything else is stringified rather than
    left as a type a Pydantic `str | None` field would reject outright."""
    if value is None or isinstance(value, str):
        return value or None
    return str(value)
