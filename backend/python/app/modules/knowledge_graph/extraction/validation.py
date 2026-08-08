"""Envelope validation + quarantine (KG Clean Rebuild plan, Part B §3.3).

Every extracted envelope is validated before it is allowed to reach
resolution. Validation failures are quarantined — dropped from the returned
envelope and recorded with a reason — never silently coerced into a
different type or dropped without a trace.

Shared structural rules (all modes):
  - an entity must have a non-blank name
  - a relationship's ``subject_local_id``/``object_local_id`` must reference
    entities present in the same envelope

Mode-specific rule:
  - ``ontology_guided`` additionally requires every entity/relationship
    ``type`` to be a member of the referenced ontology's closed enum
"""

from typing import Any

from pydantic import BaseModel, Field

from app.modules.knowledge_graph.contracts.envelope import (
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionEnvelope,
    ExtractionMode,
)
from app.modules.knowledge_graph.contracts.ontology import OntologyDefinition
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class QuarantinedItem(BaseModel):
    """One entity or relationship rejected by validation, kept for audit /
    governance review rather than silently discarded."""
    extraction_id: str
    document_id: str
    local_id: str
    item_type: str = Field(description="'entity' | 'relationship'")
    reason: str
    payload: dict[str, Any]
    quarantined_at: int = Field(default_factory=get_epoch_timestamp_in_ms, description="Epoch ms")


class EnvelopeValidationResult(BaseModel):
    envelope: ExtractionEnvelope
    quarantined: list[QuarantinedItem] = Field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.quarantined


def validate_envelope(
    envelope: ExtractionEnvelope,
    ontology: OntologyDefinition | None = None,
) -> EnvelopeValidationResult:
    """Validate *envelope* in place, returning a new envelope with invalid
    entities/relationships stripped plus the quarantine record for each.
    """
    quarantined: list[QuarantinedItem] = []
    valid_entity_ids: set[str] = set()
    kept_entities = []

    ontology_types = ontology.entity_type_names() if ontology else None

    for entity in envelope.entities:
        reason = _entity_rejection_reason(entity, envelope.extraction_mode, ontology_types)
        if reason:
            quarantined.append(
                QuarantinedItem(
                    extraction_id=envelope.extraction_id,
                    document_id=envelope.document_id,
                    local_id=entity.local_id,
                    item_type="entity",
                    reason=reason,
                    payload=entity.model_dump(),
                )
            )
            continue
        kept_entities.append(entity)
        valid_entity_ids.add(entity.local_id)

    ontology_rel_types = (
        {r.name for r in ontology.relationship_types} if ontology else None
    )
    kept_relationships = []
    for rel in envelope.relationships:
        reason = _relationship_rejection_reason(
            rel, envelope.extraction_mode, valid_entity_ids, ontology_rel_types
        )
        if reason:
            quarantined.append(
                QuarantinedItem(
                    extraction_id=envelope.extraction_id,
                    document_id=envelope.document_id,
                    local_id=rel.local_id,
                    item_type="relationship",
                    reason=reason,
                    payload=rel.model_dump(),
                )
            )
            continue
        kept_relationships.append(rel)

    cleaned = envelope.model_copy(
        update={"entities": kept_entities, "relationships": kept_relationships}
    )
    return EnvelopeValidationResult(envelope=cleaned, quarantined=quarantined)


def _entity_rejection_reason(
    entity: ExtractedEntity, mode: ExtractionMode, ontology_types: set[str] | None
) -> str | None:
    if not entity.name.strip():
        return "blank_name"
    if (
        mode == ExtractionMode.ONTOLOGY_GUIDED
        and ontology_types is not None
        and entity.type not in ontology_types
    ):
        return f"type '{entity.type}' not in ontology closed enum"
    return None


def _relationship_rejection_reason(
    rel: ExtractedRelationship,
    mode: ExtractionMode,
    valid_entity_ids: set[str],
    ontology_rel_types: set[str] | None,
) -> str | None:
    if rel.subject_local_id not in valid_entity_ids:
        return f"subject_local_id '{rel.subject_local_id}' does not reference a valid entity"
    if rel.object_local_id not in valid_entity_ids:
        return f"object_local_id '{rel.object_local_id}' does not reference a valid entity"
    if (
        mode == ExtractionMode.ONTOLOGY_GUIDED
        and ontology_rel_types is not None
        and rel.type not in ontology_rel_types
    ):
        return f"type '{rel.type}' not in ontology closed enum"
    return None
