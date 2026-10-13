"""The schema the model fills. Flat, required, no unions.

Provider structured-output support diverges on ``anyOf``, nullable fields
and numeric constraints. This shape stays inside the intersection: strings,
one inline enum, integers, and ``extra=forbid``.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.named_entities.domain.kinds import EntityKind

WireKind = Literal[
    "person",
    "person_type",
    "organization",
    "event",
    "product",
    "skill",
    "location",
    "city",
    "state",
    "country_region",
    "address",
    "date",
    "date_range",
    "date_time",
    "duration",
    "currency",
    "percentage",
    "age",
    "dimension",
    "email",
    "url",
    "phone",
    "ip",
]


class WireEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="Exact surface form copied verbatim from the block. Never paraphrase.")
    block: int = Field(description="n from the [B<n>] tag where the text appears.")
    kind: WireKind
    normalized: str = Field(
        description=(
            "Leave ''. Dates and amounts are read from text, so text must contain the date or amount itself."
        )
    )

    @model_validator(mode="before")
    @classmethod
    def _lenient(cls, data: object) -> object:
        """The published schema stays strict (structured output needs every field
        required); reading is lenient, so an omitted hint, a capitalised kind, "B3"
        for block 3 or an extra key never costs the model an entity."""
        if not isinstance(data, dict):
            return data
        item = {key: value for key, value in data.items() if key in cls.model_fields}
        if item.get("normalized") is None:
            item["normalized"] = ""
        if isinstance(item.get("kind"), str):
            item["kind"] = item["kind"].strip().casefold()
        block = item.get("block")
        if isinstance(block, str) and block.strip().lstrip("Bb").isdigit():
            item["block"] = int(block.strip().lstrip("Bb"))
        return item


class EntitySubmission(BaseModel):
    """Entities for the blocks just read. May be called more than once."""

    model_config = ConfigDict(extra="forbid")

    entities: list[WireEntity] = Field(
        description="In order of appearance; at most 100 per call."
    )


def wire_item_schema() -> dict[str, Any]:
    """One submitted entity, as a tool argument schema: inline, no ``$defs``."""
    fields = WireEntity.model_fields
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": fields["text"].description},
            "block": {"type": "integer", "description": fields["block"].description},
            "kind": {"type": "string", "enum": list(get_args(WireKind))},
            "normalized": {"type": "string", "description": fields["normalized"].description},
        },
        "required": ["text", "block", "kind"],
    }


def parse_wire_kind(raw: str) -> EntityKind | None:
    try:
        return EntityKind(str(raw).casefold())
    except ValueError:
        return None
