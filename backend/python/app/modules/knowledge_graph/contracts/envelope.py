"""Typed extraction envelope — the shared output contract for all three
extraction modes (KG Clean Rebuild plan, Part B / Part E).

Extraction never deduplicates or assigns global identity: every entity and
relationship here is document-scoped (``local_id``). Canonicalisation lives
entirely in the indexing pipeline (``modules/knowledge_graph/indexing``),
which consumes envelopes and is the only place global ``node_id``s are
minted. Keeping this boundary strict is what lets extraction and indexing
evolve independently.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app.utils.time_conversion import get_epoch_timestamp_in_ms


class ExtractionMode(str, Enum):
    """Which of the three routed extraction strategies produced an envelope."""
    ONTOLOGY_GUIDED = "ontology_guided"
    DOMAIN_SCHEMA_FREE = "domain_schema_free"
    GENERIC_SCHEMA_FREE = "generic_schema_free"


class TextSpan(BaseModel):
    """Character offsets into the source document for provenance/highlighting."""
    start: int | None = None
    end: int | None = None
    text: str | None = None


class ExtractedEntity(BaseModel):
    """A single entity mention, scoped to one document.

    ``type`` is always a free string at this layer — for ``ontology_guided``
    envelopes the routing/validation layer additionally checks it against the
    referenced ontology's closed enum before the envelope is accepted.
    """
    local_id: str = Field(description="Document-scoped id, stable within this envelope only")
    type: str = Field(description="Raw extracted type name")
    type_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    is_novel_type: bool = Field(
        default=False,
        description="True when the extractor emitted a type outside the seed vocabulary "
        "(domain_schema_free) — tallied by governance for promotion (Part F).",
    )
    canonical_type_uri: str | None = Field(
        default=None, description="Set once resolved against an ontology/registry entry"
    )
    name: str = Field(description="Display name / surface form")
    attributes: dict[str, Any] = Field(default_factory=dict)
    text_span: TextSpan | None = None
    extraction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractedRelationship(BaseModel):
    """A single relationship mention between two entities in the same envelope."""
    local_id: str
    type: str
    type_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    subject_local_id: str
    object_local_id: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    extraction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractionProvenance(BaseModel):
    extractor_version: str = ""
    model_id: str | None = None
    extracted_at: int = Field(
        default_factory=get_epoch_timestamp_in_ms, description="Epoch ms, matching Record timestamps"
    )


class ExtractionEnvelope(BaseModel):
    """Structurally validated output of a single extraction run over one document.

    Every mode produces this exact shape (Part B, design principle #2:
    "typed at the envelope level, not necessarily at the value level").
    """
    extraction_id: str
    document_id: str
    org_id: str = Field(description="Multi-tenant scope — required, never inferred downstream")
    extraction_mode: ExtractionMode
    ontology_id: str | None = None
    domain: str = ""
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    provenance: ExtractionProvenance = Field(default_factory=ExtractionProvenance)

    @field_validator("ontology_id")
    @classmethod
    def _ontology_id_requires_ontology_mode(cls, v: str | None, info: ValidationInfo) -> str | None:
        mode = info.data.get("extraction_mode")
        if v and mode is not None and mode != ExtractionMode.ONTOLOGY_GUIDED:
            raise ValueError("ontology_id may only be set when extraction_mode is ontology_guided")
        return v

    def entity_by_local_id(self, local_id: str) -> ExtractedEntity | None:
        return next((e for e in self.entities if e.local_id == local_id), None)

    def is_valid_relationship_graph(self) -> bool:
        """True when every relationship's endpoints reference entities in this envelope."""
        known_ids = {e.local_id for e in self.entities}
        return all(
            r.subject_local_id in known_ids and r.object_local_id in known_ids
            for r in self.relationships
        )
