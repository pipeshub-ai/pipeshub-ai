"""
Generic YAML-schema-based response validator for integration tests.

Loads a YAML file that describes the expected shape of an API response
(field names, types, formats, enums, nested objects) and validates an
actual response dict against it.  Fully typed — no ``Any``, ``dict``,
or ``Untyped`` in the schema DSL.

Usage
-----
::

    from response_validator import load_yaml_schema, assert_response_matches_schema

    schema = load_yaml_schema("schemas/org_document_response.yaml")
    assert_response_matches_schema(response_json, schema)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Union

import yaml

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

VALID_TYPES = frozenset({"string", "integer", "number", "boolean", "object", "array", "null"})
VALID_FORMATS = frozenset({"email", "objectid", "date", "datetime"})


@dataclass
class FieldSchema:
    """Schema definition for a single response field."""

    type: Optional[str] = None
    required: bool = True
    enum: Optional[List[str]] = None
    format: Optional[str] = None
    properties: Optional[dict[str, "FieldSchema"]] = None
    items: Optional["FieldSchema"] = None
    one_of: Optional[List["FieldSchema"]] = None


@dataclass
class ResponseSchema:
    """Top-level YAML response schema."""

    name: str
    description: str
    fields: dict[str, FieldSchema]


@dataclass
class ValidationError:
    """A single validation failure."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_OBJECTID_RE = re.compile(r"^[a-f\d]{24}$", re.IGNORECASE)
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

_INTEGRATION_TESTS_ROOT = Path(__file__).resolve().parent.parent


def _parse_field(raw: dict[str, object]) -> FieldSchema:
    """Parse a single field definition from YAML dict into ``FieldSchema``."""
    one_of_raw = raw.get("oneOf")
    one_of: Optional[List[FieldSchema]] = None
    if isinstance(one_of_raw, list):
        one_of = [_parse_field(branch) for branch in one_of_raw]

    props_raw = raw.get("properties")
    properties: Optional[dict[str, FieldSchema]] = None
    if isinstance(props_raw, dict):
        properties = {k: _parse_field(v) for k, v in props_raw.items()}

    items_raw = raw.get("items")
    items: Optional[FieldSchema] = None
    if isinstance(items_raw, dict):
        items = _parse_field(items_raw)

    field_type = raw.get("type")
    if field_type is not None:
        field_type = str(field_type)

    fmt = raw.get("format")
    if fmt is not None:
        fmt = str(fmt)

    enum_raw = raw.get("enum")
    enum: Optional[List[str]] = None
    if isinstance(enum_raw, list):
        enum = [str(e) for e in enum_raw]

    required = raw.get("required", True)
    if not isinstance(required, bool):
        required = True

    return FieldSchema(
        type=field_type,
        required=required,
        enum=enum,
        format=fmt,
        properties=properties,
        items=items,
        one_of=one_of,
    )


def load_yaml_schema(file_path: Union[str, Path]) -> ResponseSchema:
    """
    Load and parse a YAML response schema file.

    Parameters
    ----------
    file_path:
        Path to the YAML file.  If relative, resolved from the
        ``integration-tests/`` root directory.

    Returns
    -------
    ResponseSchema
    """
    path = Path(file_path)
    if not path.is_absolute():
        path = _INTEGRATION_TESTS_ROOT / path

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    name: str = raw.get("name", path.stem)
    description: str = raw.get("description", "")
    fields_raw: dict[str, object] = raw.get("fields", {})

    fields: dict[str, FieldSchema] = {}
    for key, value in fields_raw.items():
        if isinstance(value, dict):
            fields[key] = _parse_field(value)

    return ResponseSchema(name=name, description=description, fields=fields)


# ---------------------------------------------------------------------------
# Validation engine
# ---------------------------------------------------------------------------

def _js_typeof(value: object) -> str:
    """Map a Python value to the YAML schema type name."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_matches(value: object, expected: str) -> bool:
    actual = _js_typeof(value)
    if actual == expected:
        return True
    # integer is also a valid number
    if expected == "number" and actual == "integer":
        return True
    return False


def _format_matches(value: str, fmt: str) -> bool:
    if fmt == "email":
        return bool(_EMAIL_RE.match(value))
    if fmt == "objectid":
        return bool(_OBJECTID_RE.match(value))
    if fmt == "datetime":
        return bool(_DATETIME_RE.match(value))
    if fmt == "date":
        return bool(_DATE_RE.match(value))
    return True


def _validate_field(
    value: object,
    schema: FieldSchema,
    path: str,
    errors: List[ValidationError],
) -> None:
    # oneOf: at least one branch must match
    if schema.one_of:
        for branch in schema.one_of:
            branch_errors: List[ValidationError] = []
            _validate_field(value, branch, path, branch_errors)
            if not branch_errors:
                return
        type_names = [b.type or "unknown" for b in schema.one_of]
        errors.append(ValidationError(
            path=path,
            message=f"value did not match any of the allowed types: {' | '.join(type_names)}",
        ))
        return

    # Type check
    if schema.type and not _type_matches(value, schema.type):
        errors.append(ValidationError(
            path=path,
            message=f'expected type "{schema.type}" but got "{_js_typeof(value)}"',
        ))
        return

    # Enum check
    if schema.enum and schema.type == "string" and isinstance(value, str):
        if value not in schema.enum:
            errors.append(ValidationError(
                path=path,
                message=f'value "{value}" is not one of [{", ".join(schema.enum)}]',
            ))

    # Format check
    if schema.format and isinstance(value, str):
        if not _format_matches(value, schema.format):
            errors.append(ValidationError(
                path=path,
                message=f'value "{value}" does not match format "{schema.format}"',
            ))

    # Nested object
    if schema.type == "object" and schema.properties and isinstance(value, dict):
        _validate_fields(value, schema.properties, path, errors)

    # Array items
    if schema.type == "array" and schema.items and isinstance(value, list):
        for idx, item in enumerate(value):
            _validate_field(item, schema.items, f"{path}[{idx}]", errors)


def _validate_fields(
    data: dict[str, object],
    fields: dict[str, FieldSchema],
    base_path: str,
    errors: List[ValidationError],
) -> None:
    for key, schema in fields.items():
        field_path = f"{base_path}.{key}" if base_path else key
        value = data.get(key)

        if value is None:
            if schema.required:
                # null is acceptable for nullable oneOf
                if key in data:
                    if schema.one_of and any(b.type == "null" for b in schema.one_of):
                        continue
                    if schema.type == "null":
                        continue
                errors.append(ValidationError(
                    path=field_path,
                    message="required field is missing",
                ))
            continue

        _validate_field(value, schema, field_path, errors)


def validate_response(
    data: dict[str, object],
    schema: ResponseSchema,
) -> List[ValidationError]:
    """
    Validate ``data`` against the loaded YAML ``schema``.

    Returns
    -------
    List[ValidationError]
        Empty list means the response is valid.
    """
    errors: List[ValidationError] = []
    _validate_fields(data, schema.fields, "", errors)
    return errors


def assert_response_matches_schema(
    data: dict[str, object],
    schema: ResponseSchema,
) -> None:
    """
    Assert that ``data`` conforms to ``schema``.

    Raises ``AssertionError`` with a human-readable summary on failure.
    """
    errors = validate_response(data, schema)
    if errors:
        details = "\n".join(f"  - {e}" for e in errors)
        raise AssertionError(
            f'Response does not match schema "{schema.name}":\n{details}'
        )
