"""A tool call's arguments checked against the tool's input schema at the gateway (C3.30, TG-05).

A schema that names no ``$schema`` is read as JSON Schema 2020-12, the dialect of the ``/pipeshub/tools`` files
(OWD-6); one that names another draft is read as that draft. Every error names the field, what is wrong and how to
fix it, so an engine corrects the call in one turn. A schema the validator cannot read is not a reason to refuse
the call: the tool's own checks still run behind the gateway.

The tools forgive what a model often sends, and so does this check, at any depth: ``null`` for an optional field
(read as absent), an enum value in other case or spacing (read as the member it names), and a number or boolean
sent as a string (``"5"``, ``"true"``: the MCP adapter and the connectors' Pydantic models coerce it). The check reads
the forgiven form; the tool still receives the arguments as sent.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from jsonschema import Draft202012Validator, FormatChecker, SchemaError
from jsonschema import exceptions as _exceptions
from jsonschema.validators import validator_for

from harness_core.tools.base import fuzzy_match_enum

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from jsonschema.exceptions import ValidationError
    from jsonschema.protocols import Validator
    from pydantic import JsonValue

    BestMatch = Callable[[Iterable[ValidationError]], ValidationError | None]

# Enough for a model to fix a call in one turn; a call with more is usually the wrong tool.
MAX_FIELD_ERRORS: Final = 8
_SHOWN_VALUE: Final = 60
_SHOWN_CHOICES: Final = 12
_DESCRIPTION_HINT: Final = 100

_WORDS: Final[Mapping[str, str]] = {
    "minimum": "at least",
    "exclusiveMinimum": "more than",
    "maximum": "at most",
    "exclusiveMaximum": "less than",
    "minLength": "at least",
    "maxLength": "at most",
    "minItems": "at least",
    "maxItems": "at most",
    "minProperties": "at least",
    "maxProperties": "at most",
}

_INTEGER = re.compile(r"[+-]?\d+")
_best_match = cast("BestMatch", _exceptions.best_match)  # pyright: ignore[reportUnknownMemberType]


@dataclass(frozen=True, slots=True)
class FieldError:
    """One invalid argument: ``field`` is a dotted path (``filters.since``, ``items[2].name``); ``""`` is the
    arguments object itself."""

    field: str
    problem: str
    fix: str

    def as_json(self) -> dict[str, JsonValue]:
        return {"field": self.field, "problem": self.problem, "fix": self.fix}


def _path(parts: Iterable[str | int]) -> str:
    out = ""
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else part)
    return out


def _join(parent: str, child: str) -> str:
    return f"{parent}.{child}" if parent else child


def _shown(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _SHOWN_VALUE else text[: _SHOWN_VALUE - 3] + "..."


def _json_type(value: object) -> str:
    match value:
        case None:
            return "null"
        case bool():
            return "boolean"
        case int():
            return "integer"
        case float():
            return "number"
        case str():
            return "string"
        case list():
            return "array"
        case dict():
            return "object"
        case _:
            return type(value).__name__


def _object(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _items(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def _choices(values: Iterable[object]) -> str:
    listed = [_shown(v) for v in values]
    more = len(listed) - _SHOWN_CHOICES
    return ", ".join(listed[:_SHOWN_CHOICES]) + (f" (and {more} more)" if more > 0 else "")


def _property_hint(schema: Mapping[str, object], name: str) -> str:
    prop = _object(_object(schema.get("properties")).get(name))
    kind = prop.get("type")
    bits = [kind] if isinstance(kind, str) else []
    description = " ".join(str(prop.get("description", "")).split())
    if description:
        bits.append(description[:_DESCRIPTION_HINT])
    return f" ({': '.join(bits)})" if bits else ""


def _field_errors(error: ValidationError) -> list[FieldError]:
    """``required`` names every missing field at once; jsonschema reports each with the same instance."""
    if error.validator in ("anyOf", "oneOf") and error.context:
        deeper = _best_match(error.context)
        if deeper is not None:
            return _field_errors(deeper)
    schema = _object(error.schema)
    at = _path(cast("Iterable[str | int]", error.absolute_path))
    given = _object(error.instance)
    if error.validator == "required" and isinstance(error.instance, dict):
        return [
            FieldError(_join(at, str(name)), "is required", f"add `{name}`{_property_hint(schema, str(name))}")
            for name in _items(error.validator_value)
            if str(name) not in given
        ]
    return [_field_error(error, at, schema)]


def _field_error(error: ValidationError, at: str, schema: Mapping[str, object]) -> FieldError:
    keyword = str(error.validator)
    expected: object = error.validator_value
    value = cast("object", error.instance)
    word = _WORDS.get(keyword, "")
    match keyword:
        case "type":
            wanted = " or ".join(map(str, _items(expected))) or str(expected)
            return FieldError(at, f"must be {wanted}, not {_json_type(value)}", f"send {wanted}")
        case "enum":
            return FieldError(at, f"{_shown(value)} is not an allowed value", f"use one of: {_choices(_items(expected))}")
        case "const":
            return FieldError(at, f"must be {_shown(expected)}", f"send {_shown(expected)}")
        case "additionalProperties" | "unevaluatedProperties":
            allowed = sorted(_object(schema.get("properties")))
            extra = sorted(set(_object(value)) - set(allowed))
            name = extra[0] if extra else ""
            fix = f"remove `{name}`" + (f"; allowed: {', '.join(allowed)}" if allowed else "")
            return FieldError(_join(at, name), "is not an argument of this tool", fix)
        case "pattern":
            return FieldError(
                at, f"{_shown(value)} does not match the pattern {expected}", f"send a value matching {expected}"
            )
        case "format":
            return FieldError(at, f"{_shown(value)} is not a valid {expected}", f"send a valid {expected}")
        case "minLength" | "maxLength":
            return FieldError(
                at, f"must be {word} {expected} characters long", f"send a string {word} {expected} characters long"
            )
        case "minItems" | "maxItems":
            return FieldError(at, f"must have {word} {expected} items", f"send {word} {expected} items")
        case "minProperties" | "maxProperties":
            return FieldError(at, f"must have {word} {expected} fields", f"send {word} {expected} fields")
        case "minimum" | "exclusiveMinimum" | "maximum" | "exclusiveMaximum":
            return FieldError(at, f"{_shown(value)} is out of range", f"send a number {word} {expected}")
        case "multipleOf":
            return FieldError(at, f"{_shown(value)} is not a multiple of {expected}", f"send a multiple of {expected}")
        case "uniqueItems":
            return FieldError(at, "has repeated items", "send each item once")
        case "dependentRequired" | "dependencies":
            return FieldError(at, error.message, "add the arguments it names")
        case _:
            return FieldError(
                at, error.message, "read the tool's schema (`ph tools describe <tool>`) and send what it asks for"
            )


def _scalar(kinds: set[str], text: str) -> object:
    """``text`` as the number or boolean the schema wants, or ``text`` itself."""
    stripped = text.strip()
    if "integer" in kinds and _INTEGER.fullmatch(stripped):
        return int(stripped)
    if "number" in kinds:
        try:
            number = float(stripped)
        except ValueError:
            number = math.nan
        if math.isfinite(number):
            return number
    if "boolean" in kinds and stripped.lower() in ("true", "false"):
        return stripped.lower() == "true"
    return text


def _lenient(schema: Mapping[str, object], value: object) -> object:
    """``value`` as the tool will read it (the module docstring's list)."""
    kind = schema.get("type")
    kinds = {str(k) for k in _items(kind)} or {str(kind)}
    if isinstance(value, str) and "string" not in kinds:
        value = _scalar(kinds, value)
    members = _items(schema.get("enum"))
    if members and value not in members:
        matched = fuzzy_match_enum(value, [str(m) for m in members])
        value = value if matched is None else matched
    if isinstance(value, dict):
        properties = _object(schema.get("properties"))
        required = {str(name) for name in _items(schema.get("required"))}
        return {
            key: _lenient(_object(properties.get(key)), item)
            for key, item in _object(cast("object", value)).items()
            if item is not None or key in required
        }
    if isinstance(value, list):
        items = _object(schema.get("items"))
        return [_lenient(items, item) for item in _items(cast("object", value))]
    return value


class ArgumentCheck:
    """Validators compiled once per tool schema; ``check`` answers ``()`` for valid arguments."""

    def __init__(self) -> None:
        self._compiled: dict[str, tuple[Mapping[str, JsonValue], Validator | None]] = {}

    def _validator(self, name: str, schema: Mapping[str, JsonValue]) -> Validator | None:
        cached = self._compiled.get(name)
        if cached is not None and cached[0] == schema:
            return cached[1]
        document = dict(schema)
        cls = validator_for(document, default=Draft202012Validator)
        try:
            cls.check_schema(document)
            validator: Validator | None = cls(document, format_checker=FormatChecker())
        except SchemaError:
            validator = None
        self._compiled[name] = (schema, validator)
        return validator

    def readable(self, name: str, schema: Mapping[str, JsonValue]) -> bool:
        return self._validator(name, schema) is not None

    def check(
        self, name: str, schema: Mapping[str, JsonValue], arguments: Mapping[str, JsonValue]
    ) -> tuple[FieldError, ...]:
        validator = self._validator(name, schema)
        if validator is None:
            return ()
        found: list[FieldError] = []
        for error in validator.iter_errors(cast("dict[str, Any]", _lenient(schema, dict(arguments)))):
            found.extend(e for e in _field_errors(error) if e not in found)
        return tuple(sorted(found, key=lambda e: e.field)[:MAX_FIELD_ERRORS])


def invalid_arguments_text(tool: str, errors: Iterable[FieldError]) -> str:
    lines = [f"The arguments for {tool} are not valid; nothing was run."]
    for error in errors:
        where = f"`{error.field}`" if error.field else "the arguments"
        lines.append(f"- {where} {error.problem}. Fix: {error.fix}.")
    lines.append("Correct the arguments and call the tool again.")
    return "\n".join(lines)


__all__ = ["MAX_FIELD_ERRORS", "ArgumentCheck", "FieldError", "invalid_arguments_text"]
