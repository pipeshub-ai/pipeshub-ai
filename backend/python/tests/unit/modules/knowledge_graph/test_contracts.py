"""Unit tests for app.modules.knowledge_graph.contracts (KG Clean Rebuild
plan, Phase 1 — "Contracts package").

These are pure Pydantic model tests: construction, validation guards, and
the small amount of derived logic each contract carries (bi-temporal
``is_active_as_of``, envelope endpoint validation, filter-contract coverage
filtering).
"""

import pytest
from pydantic import ValidationError

from app.models.entities import EntityTypeCategory
from app.utils.time_conversion import get_epoch_timestamp_in_ms

_DAY_MS = 24 * 60 * 60 * 1000
_HOUR_MS = 60 * 60 * 1000
from app.modules.knowledge_graph.contracts import (
    AttributeDataType,
    AttributeProfile,
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionEnvelope,
    ExtractionMode,
    FilterContractSource,
    FilterField,
    GraphEdge,
    GraphNode,
    LLMAdjudication,
    MergeRecord,
    OntologyDefinition,
    OntologyEntityType,
    OntologyRelationshipType,
    ResolutionResult,
)


# ---------------------------------------------------------------------------
# ExtractionEnvelope
# ---------------------------------------------------------------------------


class TestExtractionEnvelope:
    def _entity(self, local_id: str, name: str = "Alice") -> ExtractedEntity:
        return ExtractedEntity(local_id=local_id, type="Person", name=name)

    def test_valid_relationship_graph_true_when_endpoints_known(self):
        env = ExtractionEnvelope(
            extraction_id="e1",
            document_id="d1",
            org_id="org1",
            extraction_mode=ExtractionMode.GENERIC_SCHEMA_FREE,
            entities=[self._entity("l1"), self._entity("l2", "Bob")],
            relationships=[
                ExtractedRelationship(
                    local_id="r1", type="KNOWS", subject_local_id="l1", object_local_id="l2"
                )
            ],
        )
        assert env.is_valid_relationship_graph() is True

    def test_valid_relationship_graph_false_on_dangling_endpoint(self):
        env = ExtractionEnvelope(
            extraction_id="e1",
            document_id="d1",
            org_id="org1",
            extraction_mode=ExtractionMode.GENERIC_SCHEMA_FREE,
            entities=[self._entity("l1")],
            relationships=[
                ExtractedRelationship(
                    local_id="r1", type="KNOWS", subject_local_id="l1", object_local_id="missing"
                )
            ],
        )
        assert env.is_valid_relationship_graph() is False

    def test_entity_by_local_id_found_and_missing(self):
        env = ExtractionEnvelope(
            extraction_id="e1",
            document_id="d1",
            org_id="org1",
            extraction_mode=ExtractionMode.GENERIC_SCHEMA_FREE,
            entities=[self._entity("l1")],
        )
        assert env.entity_by_local_id("l1") is not None
        assert env.entity_by_local_id("nope") is None

    def test_ontology_id_rejected_outside_ontology_guided_mode(self):
        with pytest.raises(ValidationError):
            ExtractionEnvelope(
                extraction_id="e1",
                document_id="d1",
                org_id="org1",
                extraction_mode=ExtractionMode.GENERIC_SCHEMA_FREE,
                ontology_id="some-ontology",
            )

    def test_ontology_id_allowed_in_ontology_guided_mode(self):
        env = ExtractionEnvelope(
            extraction_id="e1",
            document_id="d1",
            org_id="org1",
            extraction_mode=ExtractionMode.ONTOLOGY_GUIDED,
            ontology_id="legal-contracts-v2",
        )
        assert env.ontology_id == "legal-contracts-v2"


# ---------------------------------------------------------------------------
# GraphEdge bi-temporality
# ---------------------------------------------------------------------------


class TestGraphEdgeBiTemporal:
    def _edge(self, **overrides) -> GraphEdge:
        base = dict(
            edge_id="e1",
            org_id="org1",
            subject_node_id="n1",
            object_node_id="n2",
            canonical_type="WORKS_AT",
            type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
        )
        base.update(overrides)
        return GraphEdge(**base)

    def test_is_current_true_for_fresh_edge(self):
        edge = self._edge()
        assert edge.is_current is True

    def test_is_current_false_once_invalidated(self):
        now = get_epoch_timestamp_in_ms()
        edge = self._edge(invalid_at=now, expired_at=now)
        assert edge.is_current is False

    def test_is_active_as_of_before_valid_at_is_false(self):
        future = get_epoch_timestamp_in_ms() + _DAY_MS
        edge = self._edge(valid_at=future)
        assert edge.is_active_as_of(get_epoch_timestamp_in_ms()) is False

    def test_is_active_as_of_after_invalidation_is_false(self):
        past = get_epoch_timestamp_in_ms() - 2 * _DAY_MS
        invalidated = get_epoch_timestamp_in_ms() - _DAY_MS
        edge = self._edge(
            valid_at=past, invalid_at=invalidated, expired_at=invalidated, created_at=past
        )
        assert edge.is_active_as_of(get_epoch_timestamp_in_ms()) is False
        # ...but it *was* active in the window between valid_at and invalid_at
        # (transaction time also needs to have started by then, hence created_at=past above).
        assert edge.is_active_as_of(past + _HOUR_MS) is True

    def test_is_active_as_of_true_for_current_edge(self):
        past = get_epoch_timestamp_in_ms() - _DAY_MS
        edge = self._edge(valid_at=past)
        assert edge.is_active_as_of(get_epoch_timestamp_in_ms()) is True


# ---------------------------------------------------------------------------
# GraphNode
# ---------------------------------------------------------------------------


class TestGraphNode:
    def test_minimal_construction(self):
        node = GraphNode(
            node_id="n1",
            org_id="org1",
            canonical_type="Person",
            type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
        )
        assert node.aliases == []
        assert node.provenance == []


# ---------------------------------------------------------------------------
# OntologyDefinition
# ---------------------------------------------------------------------------


class TestOntologyDefinition:
    def _ontology(self, **overrides) -> OntologyDefinition:
        base = dict(
            ontology_id="legal-contracts",
            version="1.0.0",
            org_id="org1",
            domain="legal",
            entity_types=[
                OntologyEntityType(name="Party"),
                OntologyEntityType(name="Obligation"),
            ],
        )
        base.update(overrides)
        return OntologyDefinition(**base)

    def test_version_must_be_semver(self):
        with pytest.raises(ValidationError):
            self._ontology(version="1.0")

    def test_relationship_type_referencing_unknown_entity_rejected(self):
        with pytest.raises(ValidationError):
            self._ontology(
                relationship_types=[
                    OntologyRelationshipType(
                        name="hasObligation", domain_type="Party", range_type="NotDeclared"
                    )
                ]
            )

    def test_relationship_type_referencing_known_entities_accepted(self):
        ont = self._ontology(
            relationship_types=[
                OntologyRelationshipType(
                    name="hasObligation", domain_type="Party", range_type="Obligation"
                )
            ]
        )
        assert len(ont.relationship_types) == 1

    def test_entity_type_names_and_lookup(self):
        ont = self._ontology()
        assert ont.entity_type_names() == {"Party", "Obligation"}
        assert ont.get_entity_type("Party") is not None
        assert ont.get_entity_type("Nope") is None


# ---------------------------------------------------------------------------
# FilterContract / AttributeProfile
# ---------------------------------------------------------------------------


class TestAttributeProfileToFilterContract:
    def test_low_coverage_fields_dropped(self):
        profile = AttributeProfile(
            org_id="org1",
            domain="marketing",
            canonical_type="Complaint",
            fields=[
                FilterField(field="product", coverage=0.9, data_type=AttributeDataType.STRING),
                FilterField(field="rare_field", coverage=0.05, data_type=AttributeDataType.STRING),
            ],
            coverage_floor=0.3,
        )
        contract = profile.to_filter_contract()
        assert contract.field_names() == {"product"}
        assert contract.source == FilterContractSource.INFERRED

    def test_low_confidence_flag_changes_source(self):
        profile = AttributeProfile(org_id="org1", domain="d", canonical_type="T")
        contract = profile.to_filter_contract(low_confidence=True)
        assert contract.source == FilterContractSource.INFERRED_LOW_CONFIDENCE

    def test_get_field_found_and_missing(self):
        profile = AttributeProfile(
            org_id="org1",
            domain="d",
            canonical_type="T",
            fields=[FilterField(field="amount", coverage=1.0)],
        )
        contract = profile.to_filter_contract()
        assert contract.get_field("amount") is not None
        assert contract.get_field("missing") is None


# ---------------------------------------------------------------------------
# Resolution contracts
# ---------------------------------------------------------------------------


class TestResolutionContracts:
    def test_resolution_result_canonical_lookup(self):
        result = ResolutionResult(
            local_to_canonical={"l1": "n1"},
            merges=[
                MergeRecord(
                    local_id="l1", canonical_node_id="n1", is_new_node=False,
                    matched_signal="hard_key",
                )
            ],
        )
        assert result.canonical_id_for("l1") == "n1"
        assert result.canonical_id_for("missing") is None

    def test_llm_adjudication_confidence_bounds(self):
        with pytest.raises(ValidationError):
            LLMAdjudication(
                local_id="l1", candidate_node_id="n1", decision="merge", confidence=1.5
            )
