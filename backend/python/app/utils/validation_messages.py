"""Plain-language wording for FastAPI/Pydantic request validation errors.

Pydantic's stock messages ("Field required", "Input should be a valid integer")
name no field, and clients show ``msg`` as is, so each entry keeps its shape
(``loc``, ``type``, ``ctx``) and only ``msg`` is reworded.
"""

import re
from collections.abc import Sequence
from typing import Any

_REQUEST_PARTS = {"body", "query", "path", "header", "cookie"}
_CUSTOM_PREFIXES = ("Value error, ", "Assertion failed, ")
GENERIC_SUMMARY = "Some of the information sent isn't valid. Check the form and try again."


def field_label(loc: Sequence[Any]) -> str:
    parts = list(loc)
    if parts and parts[0] in _REQUEST_PARTS:
        parts = parts[1:]
    names = [part for part in parts if isinstance(part, str)]
    if not names:
        return "The request"
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", names[-1])
    words = re.sub(r"[_\-]+", " ", words).strip().lower()
    return words[:1].upper() + words[1:] if words else "The request"


def _plural(count: object, noun: str) -> str:
    return f"{count} {noun}" if str(count) == "1" else f"{count} {noun}s"


def friendly_message(error: dict[str, Any]) -> str:
    kind = error.get("type", "")
    ctx = error.get("ctx") or {}
    msg = str(error.get("msg", ""))
    label = field_label(error.get("loc") or ())

    for prefix in _CUSTOM_PREFIXES:
        if msg.startswith(prefix):
            return msg[len(prefix):]

    if kind == "missing":
        return f"{label} is required."
    if kind == "json_invalid":
        return "The request couldn't be read. Refresh the page and try again."
    if kind in ("string_type", "string_sub_type"):
        return f"{label} must be text."
    if kind in ("int_type", "int_parsing", "int_from_float"):
        return f"{label} must be a whole number."
    if kind in ("float_type", "float_parsing", "decimal_type", "decimal_parsing"):
        return f"{label} must be a number."
    if kind in ("bool_type", "bool_parsing"):
        return f"{label} must be true or false."
    if kind in ("list_type", "tuple_type", "set_type"):
        return f"{label} must be a list."
    if kind in ("dict_type", "model_type", "model_attributes_type"):
        return f"{label} must be a group of fields."
    if kind == "string_too_short":
        minimum = ctx.get("min_length", 1)
        return f"{label} can't be empty." if str(minimum) == "1" else f"{label} must be at least {minimum} characters."
    if kind == "string_too_long":
        return f"{label} must be at most {ctx.get('max_length')} characters."
    if kind == "too_short":
        return f"Add at least {_plural(ctx.get('min_length', 1), 'item')} to {label.lower()}."
    if kind == "too_long":
        return f"{label} can have at most {_plural(ctx.get('max_length'), 'item')}."
    if kind == "greater_than_equal":
        return f"{label} must be at least {ctx.get('ge')}."
    if kind == "greater_than":
        return f"{label} must be more than {ctx.get('gt')}."
    if kind == "less_than_equal":
        return f"{label} must be at most {ctx.get('le')}."
    if kind == "less_than":
        return f"{label} must be less than {ctx.get('lt')}."
    if kind in ("literal_error", "enum"):
        expected = ctx.get("expected")
        return f"{label} must be one of: {expected}." if expected else f"{label} isn't one of the allowed values."
    if kind == "extra_forbidden":
        return f"{label} isn't accepted here."
    return f"{label} isn't valid."


def friendly_validation_errors(errors: Sequence[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Return a one-line summary and the errors with each ``msg`` reworded."""
    reworded = [{**error, "msg": friendly_message(error)} for error in errors]
    messages = list(dict.fromkeys(error["msg"] for error in reworded))
    summary = " ".join(messages) if messages else GENERIC_SUMMARY
    return summary, reworded
