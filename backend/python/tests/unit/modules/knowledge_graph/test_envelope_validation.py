"""Unit tests for envelope validation/quarantine (KG Clean Rebuild plan, Phase 4)."""

from app.modules.knowledge_graph.contracts.envelope import (
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionEnvelope,
    ExtractionMode,
)
from app.modules.knowledge_graph.contracts.ontology import (
    OntologyDefinition,
    OntologyEntityType,
    OntologyRelationshipType,
)
from app.modules.knowledge_graph.extraction.validation import validate_envelope


def _envelope(entities, relationships=None, mode=ExtractionMode.GENERIC_SCHEMA_FREE, ontology_id=None) -> ExtractionEnvelope:
    return ExtractionEnvelope(
        extraction_id="ext-1",
        document_id="doc-1",
        org_id="org-1",
        extraction_mode=mode,
        ontology_id=ontology_id,
        entities=entities,
        relationships=relationships or [],
    )


def _ontology() -> OntologyDefinition:
    return OntologyDefinition(
        ontology_id="legal-v1",
        version="1.0.0",
        org_id="org-1",
        domain="legal",
        entity_types=[OntologyEntityType(name="Party"), OntologyEntityType(name="Obligation")],
        relationship_types=[
            OntologyRelationshipType(name="hasObligation", domain_type="Party", range_type="Obligation")
        ],
    )


class TestStructuralValidation:
    def test_blank_name_entity_is_quarantined(self):
        envelope = _envelope([ExtractedEntity(local_id="e1", type="person", name="   ")])

        result = validate_envelope(envelope)

        assert result.envelope.entities == []
        assert len(result.quarantined) == 1
        assert result.quarantined[0].reason == "blank_name"
        assert result.quarantined[0].item_type == "entity"
        assert result.is_clean is False

    def test_valid_entity_passes_through(self):
        envelope = _envelope([ExtractedEntity(local_id="e1", type="person", name="Bob")])

        result = validate_envelope(envelope)

        assert len(result.envelope.entities) == 1
        assert result.quarantined == []
        assert result.is_clean is True

    def test_relationship_with_unknown_subject_is_quarantined(self):
        envelope = _envelope(
            entities=[ExtractedEntity(local_id="e1", type="person", name="Bob")],
            relationships=[
                ExtractedRelationship(
                    local_id="r1", type="knows", subject_local_id="missing", object_local_id="e1"
                )
            ],
        )

        result = validate_envelope(envelope)

        assert result.envelope.relationships == []
        assert len(result.quarantined) == 1
        assert "subject_local_id" in result.quarantined[0].reason

    def test_relationship_referencing_quarantined_entity_is_also_quarantined(self):
        envelope = _envelope(
            entities=[
                ExtractedEntity(local_id="e1", type="person", name=""),
                ExtractedEntity(local_id="e2", type="person", name="Bob"),
            ],
            relationships=[
                ExtractedRelationship(
                    local_id="r1", type="knows", subject_local_id="e1", object_local_id="e2"
                )
            ],
        )

        result = validate_envelope(envelope)

        entity_quarantine = [q for q in result.quarantined if q.item_type == "entity"]
        rel_quarantine = [q for q in result.quarantined if q.item_type == "relationship"]
        assert len(entity_quarantine) == 1
        assert len(rel_quarantine) == 1
        assert result.envelope.relationships == []

    def test_valid_relationship_passes_through(self):
        envelope = _envelope(
            entities=[
                ExtractedEntity(local_id="e1", type="person", name="Bob"),
                ExtractedEntity(local_id="e2", type="org", name="Acme"),
            ],
            relationships=[
                ExtractedRelationship(
                    local_id="r1", type="works_at", subject_local_id="e1", object_local_id="e2"
                )
            ],
        )

        result = validate_envelope(envelope)

        assert len(result.envelope.relationships) == 1
        assert result.quarantined == []


class TestOntologyGuidedValidation:
    def test_entity_type_outside_closed_enum_is_quarantined(self):
        envelope = _envelope(
            entities=[ExtractedEntity(local_id="e1", type="NotARealType", name="Widget")],
            mode=ExtractionMode.ONTOLOGY_GUIDED,
            ontology_id="legal-v1",
        )

        result = validate_envelope(envelope, ontology=_ontology())

        assert result.envelope.entities == []
        assert "closed enum" in result.quarantined[0].reason

    def test_entity_type_in_closed_enum_passes(self):
        envelope = _envelope(
            entities=[ExtractedEntity(local_id="e1", type="Party", name="Acme Corp")],
            mode=ExtractionMode.ONTOLOGY_GUIDED,
            ontology_id="legal-v1",
        )

        result = validate_envelope(envelope, ontology=_ontology())

        assert len(result.envelope.entities) == 1
        assert result.quarantined == []

    def test_relationship_type_outside_closed_enum_is_quarantined(self):
        envelope = _envelope(
            entities=[
                ExtractedEntity(local_id="e1", type="Party", name="Acme"),
                ExtractedEntity(local_id="e2", type="Obligation", name="Pay invoice"),
            ],
            relationships=[
                ExtractedRelationship(
                    local_id="r1", type="NotARealRelation", subject_local_id="e1", object_local_id="e2"
                )
            ],
            mode=ExtractionMode.ONTOLOGY_GUIDED,
            ontology_id="legal-v1",
        )

        result = validate_envelope(envelope, ontology=_ontology())

        assert result.envelope.relationships == []
        assert "closed enum" in result.quarantined[0].reason

    def test_schema_free_modes_are_not_type_constrained(self):
        """Same off-vocabulary type name must NOT be quarantined outside
        ontology_guided mode — that's precisely what schema-free means."""
        envelope = _envelope(
            entities=[ExtractedEntity(local_id="e1", type="AnythingGoes", name="Widget")],
            mode=ExtractionMode.GENERIC_SCHEMA_FREE,
        )

        result = validate_envelope(envelope, ontology=None)

        assert len(result.envelope.entities) == 1
        assert result.quarantined == []


class TestQuarantinePayloadAudit:
    def test_quarantined_item_carries_full_payload_for_audit(self):
        envelope = _envelope(
            entities=[ExtractedEntity(local_id="e1", type="person", name="", attributes={"x": 1})]
        )

        result = validate_envelope(envelope)

        assert result.quarantined[0].payload["local_id"] == "e1"
        assert result.quarantined[0].payload["attributes"] == {"x": 1}
        assert result.quarantined[0].extraction_id == "ext-1"
        assert result.quarantined[0].document_id == "doc-1"
        assert result.quarantined[0].quarantined_at > 0
