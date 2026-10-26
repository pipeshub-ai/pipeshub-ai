"""Typed properties of a record: the schema a connector declares and the values a record holds.

A schema (``MetadataSchema``) belongs to one connector instance and is
versioned; each value names where it came from (``source``) and whether the
source system provided it or a pipeline stage extracted it (``provenance``).

Value encoding, as stored in ``properties.json``:

- ``DATETIME``: epoch milliseconds (UTC), as every timestamp on ``Record``.
  ``validate_values`` also takes an ISO-8601 string with an explicit offset
  (``2026-01-02T03:04:05Z``) and converts it; one without an offset is refused,
  since its instant is unknown.
- ``FLOAT``: an int is widened to a float; NaN, infinities and ints past the
  float range are refused (JSON cannot carry them as floats).
- ``STRING``, every ``STRING_LIST`` item and every enum value: at most
  ``MAX_STRING_BYTES`` bytes of UTF-8 (text with a lone surrogate is refused).
  A list holds at most ``MAX_LIST_ITEMS``.
"""

import math
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.vector_db.const import const as vector_payload
from app.services.vector_db.models import VectorChunkMetadata, VectorChunkPayload

MAX_STRING_BYTES = 1024
MAX_LIST_ITEMS = 64
MAX_PROPERTIES = 256
_INT_MIN, _INT_MAX = -(2**63), 2**63 - 1
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

PROPERTY_NAME_PATTERN = r"^[a-z][a-zA-Z0-9_]{0,63}$"

#: Names the pipeline itself writes beside a record's properties (point payloads,
#: manifests, mapping nodes); a property of the same name would shadow one.
PIPELINE_FIELD_NAMES = frozenset({
    "orgId", "virtualRecordId", "recordId", "blockId", "blockIndex", "blockType",
    "isBlock", "isBlockGroup", "rev", "contentRev", "currentRev", "previousRev",
    "manifestDocId", "schemaId", "connectorId", "connectorIds", "recordGroupIds",
    "rootRecordGroupIds", "metadata", "page_content",
})


def _vector_payload_keys() -> frozenset[str]:
    # Every payload field the vector store indexes or writes on a point, from its
    # own constants and models, so a field added there is reserved here without a
    # second list to keep in step.
    keys = {
        value for name, value in vars(vector_payload).items()
        if name.endswith("_FIELD") and isinstance(value, str)
    }
    keys.update(key for key, _ in vector_payload.PAYLOAD_KEYWORD_INDEXES)
    keys.update(f.name for model in (VectorChunkMetadata, VectorChunkPayload) for f in fields(model))
    # "metadata.orgId" is stored as "orgId" inside the point's metadata.
    return frozenset(key.rsplit(".", 1)[-1] for key in keys)


RESERVED_PROPERTY_NAMES: frozenset[str] = PIPELINE_FIELD_NAMES | _vector_payload_keys()


class PropertyType(str, Enum):
    STRING = "STRING"
    STRING_LIST = "STRING_LIST"
    INT = "INT"
    FLOAT = "FLOAT"
    BOOL = "BOOL"
    DATETIME = "DATETIME"
    ENUM = "ENUM"


_SEARCHABLE_TYPES = frozenset({PropertyType.STRING, PropertyType.STRING_LIST})

SemanticLabel = Literal["title", "author", "created_at", "modified_at", "url", "status", "owner"]


def _utf8_len(text: str) -> int | None:
    """Bytes of ``text`` as UTF-8; None when it holds a lone surrogate, which
    UTF-8 cannot carry and a stored ``properties.json`` could not read back."""
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def string_fits(value: object) -> bool:
    """Whether ``value`` is a string a STRING property (or list item) stores."""
    if not isinstance(value, str):
        return False
    size = _utf8_len(value)
    return size is not None and size <= MAX_STRING_BYTES


class PropertyDefinition(BaseModel, frozen=True):
    name: str = Field(pattern=PROPERTY_NAME_PATTERN)
    type: PropertyType
    display_label: str = Field(min_length=1, max_length=256)
    #: Implies ``filterable``; left unset, ``filterable`` follows it.
    facetable: bool = False
    filterable: bool = False
    #: STRING and STRING_LIST only: the value is added to the record's searchable text.
    searchable: bool = False
    retrievable: bool = True
    enum_values: tuple[str, ...] = ()
    semantic_label: SemanticLabel | None = None

    @model_validator(mode="before")
    @classmethod
    def _facetable_filters(cls, data: object) -> object:
        if isinstance(data, dict) and data.get("facetable") and "filterable" not in data:
            return {**data, "filterable": True}
        return data

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, name: str) -> str:
        if name in RESERVED_PROPERTY_NAMES:
            raise ValueError(f"property name {name!r} is reserved")
        return name

    @model_validator(mode="after")
    def _consistent(self) -> "PropertyDefinition":
        if self.facetable and not self.filterable:
            raise ValueError(f"property {self.name!r}: facetable requires filterable")
        if self.searchable and self.type not in _SEARCHABLE_TYPES:
            raise ValueError(f"property {self.name!r}: only string properties can be searchable")
        if self.type is PropertyType.ENUM:
            if not self.enum_values:
                raise ValueError(f"property {self.name!r}: an enum needs enum_values")
            if len(set(self.enum_values)) != len(self.enum_values):
                raise ValueError(f"property {self.name!r}: enum_values repeat")
            if any(not v or not string_fits(v) for v in self.enum_values):
                raise ValueError(f"property {self.name!r}: an enum value is empty or too long, or not UTF-8")
        elif self.enum_values:
            raise ValueError(f"property {self.name!r}: enum_values are for enum properties only")
        return self


class MetadataSchema(BaseModel, frozen=True):
    #: ``{orgId}:{connectorId}:{version}`` for a registered schema.
    schema_id: str = Field(min_length=1)
    properties: tuple[PropertyDefinition, ...] = Field(max_length=MAX_PROPERTIES)

    @model_validator(mode="after")
    def _unique(self) -> "MetadataSchema":
        names = [p.name for p in self.properties]
        if len(set(names)) != len(names):
            raise ValueError("property names repeat")
        labels = [p.semantic_label for p in self.properties if p.semantic_label]
        if len(set(labels)) != len(labels):
            raise ValueError("a semantic label is given to more than one property")
        return self

    def get_property(self, name: str) -> PropertyDefinition | None:
        return next((p for p in self.properties if p.name == name), None)


class MetadataProvenance(str, Enum):
    #: Given by the source system (a connector, or a caller pushing content).
    PROVIDED = "PROVIDED"
    #: Inferred by a pipeline stage.
    EXTRACTED = "EXTRACTED"


MetadataScalar = str | int | float | bool | list[str]


class MetadataValue(BaseModel, frozen=True):
    value: MetadataScalar
    provenance: MetadataProvenance
    #: Who wrote it: ``connector:jira``, ``classify@4``.
    source: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class RecordMetadata(BaseModel):
    """``properties.json``: ``{"schemaId", "values"}``."""

    model_config = ConfigDict(populate_by_name=True)

    schema_id: str | None = Field(default=None, alias="schemaId")
    values: dict[str, MetadataValue] = Field(default_factory=dict)

    def to_json_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def merged_with(self, other: "RecordMetadata", *, source: str) -> "RecordMetadata":
        """These values with ``other``'s, as written by ``source``.

        ``other`` is ``source``'s whole output: every value in it must name
        ``source``, and a value ``source`` wrote before that ``other`` no longer
        has is dropped. A key held by another source is kept, except that a
        provided value replaces an extracted one: a stage never overwrites what
        the source system said.
        """
        if self.schema_id and other.schema_id and self.schema_id != other.schema_id:
            raise ValueError(f"cannot merge values of schema {other.schema_id!r} into {self.schema_id!r}")
        foreign = sorted(k for k, v in other.values.items() if v.source != source)
        if foreign:
            raise ValueError(f"{source!r} cannot write values of another source: {', '.join(foreign)}")
        merged = {k: v for k, v in self.values.items() if v.source != source or k in other.values}
        for key, incoming in other.values.items():
            current = merged.get(key)
            if (
                current is None
                or current.source == source
                or (current.provenance is MetadataProvenance.EXTRACTED
                    and incoming.provenance is MetadataProvenance.PROVIDED)
            ):
                merged[key] = incoming
        return RecordMetadata(schema_id=self.schema_id or other.schema_id, values=merged)


class MetadataValidationError(ValueError):
    """A value or name that does not fit the schema; ``property`` names it."""

    def __init__(self, property_name: str, message: str) -> None:
        super().__init__(f"property {property_name!r}: {message}")
        self.property = property_name


def _string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise MetadataValidationError(name, "expected a string")
    size = _utf8_len(value)
    if size is None:
        raise MetadataValidationError(name, "not valid UTF-8 text (a lone surrogate)")
    if size > MAX_STRING_BYTES:
        raise MetadataValidationError(name, f"longer than {MAX_STRING_BYTES} bytes")
    return value


def _datetime_ms(name: str, value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        if not _INT_MIN <= value <= _INT_MAX:
            raise MetadataValidationError(name, "timestamp out of range")
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise MetadataValidationError(name, "expected epoch milliseconds or an ISO-8601 time") from None
        if parsed.tzinfo is None:
            raise MetadataValidationError(name, "an ISO-8601 time needs a UTC offset")
        return (parsed - _EPOCH) // timedelta(milliseconds=1)
    raise MetadataValidationError(name, "expected epoch milliseconds or an ISO-8601 time")


def coerce_value(prop: PropertyDefinition, value: object) -> MetadataScalar:
    """``value`` as ``prop`` stores it. Raises ``MetadataValidationError``."""
    name = prop.name
    match prop.type:
        case PropertyType.STRING:
            return _string(name, value)
        case PropertyType.ENUM:
            text = _string(name, value)
            if text not in prop.enum_values:
                raise MetadataValidationError(name, f"{text!r} is not one of its values")
            return text
        case PropertyType.STRING_LIST:
            if not isinstance(value, (list, tuple)):
                raise MetadataValidationError(name, "expected a list of strings")
            if len(value) > MAX_LIST_ITEMS:
                raise MetadataValidationError(name, f"more than {MAX_LIST_ITEMS} items")
            return [_string(name, item) for item in value]
        case PropertyType.INT:
            if not isinstance(value, int) or isinstance(value, bool):
                raise MetadataValidationError(name, "expected an integer")
            if not _INT_MIN <= value <= _INT_MAX:
                raise MetadataValidationError(name, "integer out of range")
            return value
        case PropertyType.FLOAT:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise MetadataValidationError(name, "expected a number")
            try:
                number = float(value)
            except OverflowError:
                raise MetadataValidationError(name, "number out of range") from None
            if not math.isfinite(number):
                raise MetadataValidationError(name, "expected a finite number")
            return number
        case PropertyType.BOOL:
            if not isinstance(value, bool):
                raise MetadataValidationError(name, "expected true or false")
            return value
        case PropertyType.DATETIME:
            return _datetime_ms(name, value)
    raise MetadataValidationError(name, f"unknown type {prop.type}")


def validate_values(
    schema: MetadataSchema,
    raw: dict[str, object],
    *,
    source: str,
    provenance: MetadataProvenance = MetadataProvenance.PROVIDED,
    confidence: float | None = None,
) -> RecordMetadata:
    """``raw`` (property name to value) checked against ``schema``.

    A None value is an absent one. Raises ``MetadataValidationError`` naming the
    first property, in sorted order, that is reserved, unknown or does not fit.
    """
    values: dict[str, MetadataValue] = {}
    for name in sorted(raw):
        if name in RESERVED_PROPERTY_NAMES:
            raise MetadataValidationError(name, "the name is reserved")
        prop = schema.get_property(name)
        if prop is None:
            raise MetadataValidationError(name, f"not in schema {schema.schema_id}")
        if raw[name] is None:
            continue
        values[name] = MetadataValue(
            value=coerce_value(prop, raw[name]), provenance=provenance, source=source, confidence=confidence,
        )
    return RecordMetadata(schema_id=schema.schema_id, values=values)


def searchable_lines(schema: MetadataSchema, metadata: RecordMetadata) -> list[str]:
    """``"<display label>: <value>"`` for each searchable property with a value, in schema order."""
    lines: list[str] = []
    for prop in schema.properties:
        held = metadata.values.get(prop.name)
        if not prop.searchable or held is None:
            continue
        value = held.value
        if isinstance(value, list):
            text = ", ".join(item for item in value if item)
        elif isinstance(value, str):
            text = value
        else:
            continue
        if text.strip():
            lines.append(f"{prop.display_label}: {text}")
    return lines
