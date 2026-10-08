"""Domain entities persisted to the blob and projected onto the graph."""

from __future__ import annotations

import logging
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.values import TypedValue
from app.modules.named_entities.keys import KEY_SCHEME

SCHEMA_VERSION = 1
EXTRACTOR_VERSION = "ner-1"

logger = logging.getLogger(__name__)

Grounding = Literal["exact", "fuzzy"]
ExtractorName = Literal["pattern", "value", "agent", "single_call"]
TerminationReason = Literal[
    "deterministic",
    "finish_ok",
    "no_progress",
    "budget_turns",
    "budget_tokens",
    "timeout",
    "llm_error",
    "single_call",
    "empty",
]
ExtractionStatus = Literal["COMPLETED", "PARTIAL", "FAILED", "SKIPPED"]

# The records schema accepts any bounded uppercase string so a new status needs no
# schema release; this set is the gate that keeps writers inside the known values.
RECORD_ENTITY_STATUSES = frozenset({"NOT_STARTED", "IN_PROGRESS", *get_args(ExtractionStatus)})


def validate_record_entity_status(status: str) -> str:
    if status not in RECORD_ENTITY_STATUSES:
        raise ValueError(f"Unknown entity extraction status: {status!r}")
    return status


class Mention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_index: int
    block_id: str = ""
    char_start: int
    char_end: int
    surface: str
    grounding: Grounding = "exact"
    extractor: ExtractorName
    extractor_version: str = EXTRACTOR_VERSION
    evidence_score: float = 1.0


class NamedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EntityKind
    tags: list[str] = Field(default_factory=list)
    display_name: str
    norm_key: str
    value: TypedValue | None = None
    normalized_raw: str = ""
    mentions: list[Mention] = Field(default_factory=list)


class ExtractionStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: int = 0
    deterministic: int = 0
    submitted: int = 0
    accepted: int = 0
    rejected: int = 0
    dropped_cap: int = 0
    turns: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    windows_total: int = 0
    windows_run: int = 0
    windows_failed: int = 0
    chars_truncated: bool = False
    rejected_reasons: dict[str, int] = Field(default_factory=dict)
    extract_ms: int = 0


class NamedEntityExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    extractor_version: str = EXTRACTOR_VERSION
    strategy: str = "deterministic"
    termination_reason: TerminationReason = "deterministic"
    status: ExtractionStatus = "COMPLETED"
    entities: list[NamedEntity] = Field(default_factory=list)
    stats: ExtractionStats = Field(default_factory=ExtractionStats)
    # What relative values were resolved against and how keys were built, so a
    # stored extraction can be normalized and keyed again without the model.
    reference_time_ms: int | None = None
    tz: str = "UTC"
    key_scheme: int = KEY_SCHEME


def _model_for(candidates: list[type[BaseModel]], data: dict) -> type[BaseModel] | None:
    if len(candidates) == 1:
        return candidates[0]
    tag = data.get("value_type")
    for model in candidates:
        field = model.model_fields.get("value_type")
        if field is not None and field.default == tag:
            return model
    return None


def _prune_value(annotation: Any, value: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _prune_value(get_args(annotation)[0], value)
    if origin in (Union, UnionType):
        models = [arg for arg in get_args(annotation) if isinstance(arg, type) and issubclass(arg, BaseModel)]
        rest = [arg for arg in get_args(annotation) if arg not in models and arg is not type(None)]
        if models and isinstance(value, dict):
            model = _model_for(models, value)
            return _prune(model, value) if model else value
        return _prune_value(rest[0], value) if len(rest) == 1 and not models else value
    if origin is list and isinstance(value, list):
        (item,) = get_args(annotation) or (Any,)
        return [_prune_value(item, entry) for entry in value]
    if isinstance(annotation, type) and issubclass(annotation, BaseModel) and isinstance(value, dict):
        return _prune(annotation, value)
    return value


def _prune(model: type[BaseModel], data: dict) -> dict:
    return {
        name: _prune_value(field.annotation, data[name])
        for name, field in model.model_fields.items()
        if name in data
    }


def read_stored_extraction(payload: Any) -> NamedEntityExtraction | None:
    """Parse an extraction written by this or a newer build: a blob, or the
    extraction service's reply. The write models reject what they do not know, so
    a newer build's field, kind, status or termination reason would make the whole
    payload unreadable here. Unknown fields are dropped; an entity this build
    cannot read is dropped on its own rather than guessed; an unknown status reads
    as PARTIAL and an unknown reason as deterministic. Returns None for a payload
    that is not an extraction."""
    if not isinstance(payload, dict):
        return None
    data = _prune(NamedEntityExtraction, payload)
    raw_entities = data.pop("entities", [])
    raw_stats = data.pop("stats", {})
    if not isinstance(raw_entities, list):
        return None
    coerced: list[str] = []
    if "status" in data and data["status"] not in get_args(ExtractionStatus):
        data["status"] = "PARTIAL"
        coerced.append("status")
    if "termination_reason" in data and data["termination_reason"] not in get_args(TerminationReason):
        data["termination_reason"] = "deterministic"
        coerced.append("termination_reason")
    try:
        extraction = NamedEntityExtraction.model_validate(data)
    except (ValueError, TypeError):
        return None
    try:
        stats = ExtractionStats.model_validate(raw_stats)
    except (ValueError, TypeError):
        stats = ExtractionStats()
        coerced.append("stats")
    entities: list[NamedEntity] = []
    for raw in raw_entities:
        try:
            entities.append(NamedEntity.model_validate(raw))
        except (ValueError, TypeError):
            continue
    dropped = len(raw_entities) - len(entities)
    status = extraction.status
    if dropped and status == "COMPLETED":
        status = "PARTIAL"
    if dropped or coerced:
        logger.warning(
            "named-entity extraction read with values this build does not know: entities_dropped=%d coerced=%s",
            dropped,
            ",".join(coerced) or "-",
        )
    return extraction.model_copy(update={"entities": entities, "stats": stats, "status": status})
