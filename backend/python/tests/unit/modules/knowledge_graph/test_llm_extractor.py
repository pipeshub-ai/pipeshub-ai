"""Unit tests for LLMEnvelopeExtractor (KG Clean Rebuild plan, Phase 4)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.knowledge_graph.contracts.envelope import ExtractionMode
from app.modules.knowledge_graph.contracts.ontology import (
    OntologyDefinition,
    OntologyEntityType,
    OntologyRelationshipType,
)
from app.modules.knowledge_graph.extraction.llm_extractor import (
    LLMEnvelopeExtractor,
    SoftVocabularyTerm,
    _LLMExtractedEntity,
    _LLMExtractedRelationship,
    _LLMExtractionOutput,
)

MODULE = "app.modules.knowledge_graph.extraction.llm_extractor"


def _extractor() -> LLMEnvelopeExtractor:
    return LLMEnvelopeExtractor(config_service=MagicMock(), logger=MagicMock())


def _patch_llm(parsed_output: _LLMExtractionOutput | None):
    return (
        patch(f"{MODULE}.get_llm_for_role", AsyncMock(return_value=(MagicMock(), {"modelKey": "gpt-x"}))),
        patch(f"{MODULE}.invoke_with_structured_output_and_reflection", AsyncMock(return_value=parsed_output)),
    )


class TestGenericSchemaFree:
    @pytest.mark.asyncio
    async def test_extracts_entities_and_relationships(self):
        parsed = _LLMExtractionOutput(
            entities=[
                _LLMExtractedEntity(local_id="e1", type="person", name="Alice"),
                _LLMExtractedEntity(local_id="e2", type="organization", name="Acme"),
            ],
            relationships=[
                _LLMExtractedRelationship(
                    local_id="r1", type="works_at", subject_local_id="e1", object_local_id="e2"
                )
            ],
        )
        p1, p2 = _patch_llm(parsed)
        with p1, p2:
            envelope = await _extractor().extract(
                document_id="doc-1",
                org_id="org-1",
                domain="",
                text="Alice works at Acme.",
                mode=ExtractionMode.GENERIC_SCHEMA_FREE,
            )

        assert len(envelope.entities) == 2
        assert len(envelope.relationships) == 1
        assert envelope.extraction_mode == ExtractionMode.GENERIC_SCHEMA_FREE
        assert envelope.org_id == "org-1"
        assert envelope.is_valid_relationship_graph()

    @pytest.mark.asyncio
    async def test_generic_mode_never_flags_novel_type(self):
        parsed = _LLMExtractionOutput(
            entities=[_LLMExtractedEntity(local_id="e1", type="anything", name="Thing")]
        )
        p1, p2 = _patch_llm(parsed)
        with p1, p2:
            envelope = await _extractor().extract(
                document_id="doc-1", org_id="org-1", domain="", text="x",
                mode=ExtractionMode.GENERIC_SCHEMA_FREE,
            )

        assert envelope.entities[0].is_novel_type is False

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty_envelope_not_exception(self):
        with patch(f"{MODULE}.get_llm_for_role", AsyncMock(side_effect=RuntimeError("down"))):
            envelope = await _extractor().extract(
                document_id="doc-1", org_id="org-1", domain="", text="x",
                mode=ExtractionMode.GENERIC_SCHEMA_FREE,
            )

        assert envelope.entities == []
        assert envelope.relationships == []
        assert envelope.document_id == "doc-1"

    @pytest.mark.asyncio
    async def test_none_parsed_response_returns_empty_envelope(self):
        p1, p2 = _patch_llm(None)
        with p1, p2:
            envelope = await _extractor().extract(
                document_id="doc-1", org_id="org-1", domain="", text="x",
                mode=ExtractionMode.GENERIC_SCHEMA_FREE,
            )

        assert envelope.entities == []

    @pytest.mark.asyncio
    async def test_blank_entity_name_is_dropped(self):
        parsed = _LLMExtractionOutput(
            entities=[
                _LLMExtractedEntity(local_id="e1", type="person", name="   "),
                _LLMExtractedEntity(local_id="e2", type="person", name="Bob"),
            ]
        )
        p1, p2 = _patch_llm(parsed)
        with p1, p2:
            envelope = await _extractor().extract(
                document_id="doc-1", org_id="org-1", domain="", text="x",
                mode=ExtractionMode.GENERIC_SCHEMA_FREE,
            )

        assert len(envelope.entities) == 1
        assert envelope.entities[0].name == "Bob"

    @pytest.mark.asyncio
    async def test_relationship_with_dangling_reference_is_dropped(self):
        """A relationship referencing an entity that was dropped (e.g. blank
        name) must not survive into the envelope — it would otherwise fail
        ExtractionEnvelope.is_valid_relationship_graph()."""
        parsed = _LLMExtractionOutput(
            entities=[_LLMExtractedEntity(local_id="e1", type="person", name="Bob")],
            relationships=[
                _LLMExtractedRelationship(
                    local_id="r1", type="knows", subject_local_id="e1", object_local_id="e-missing"
                )
            ],
        )
        p1, p2 = _patch_llm(parsed)
        with p1, p2:
            envelope = await _extractor().extract(
                document_id="doc-1", org_id="org-1", domain="", text="x",
                mode=ExtractionMode.GENERIC_SCHEMA_FREE,
            )

        assert envelope.relationships == []
        assert envelope.is_valid_relationship_graph()


class TestDomainSchemaFree:
    @pytest.mark.asyncio
    async def test_type_in_vocabulary_is_not_novel(self):
        parsed = _LLMExtractionOutput(
            entities=[_LLMExtractedEntity(local_id="e1", type="Campaign", name="Q3 Launch")]
        )
        vocab = [SoftVocabularyTerm(name="Campaign", description="a marketing campaign")]
        p1, p2 = _patch_llm(parsed)
        with p1, p2:
            envelope = await _extractor().extract(
                document_id="doc-1", org_id="org-1", domain="marketing", text="x",
                mode=ExtractionMode.DOMAIN_SCHEMA_FREE, soft_vocabulary=vocab,
            )

        assert envelope.entities[0].is_novel_type is False

    @pytest.mark.asyncio
    async def test_type_outside_vocabulary_is_flagged_novel(self):
        parsed = _LLMExtractionOutput(
            entities=[_LLMExtractedEntity(local_id="e1", type="ProductRecall", name="Widget X")]
        )
        vocab = [SoftVocabularyTerm(name="Campaign")]
        p1, p2 = _patch_llm(parsed)
        with p1, p2:
            envelope = await _extractor().extract(
                document_id="doc-1", org_id="org-1", domain="marketing", text="x",
                mode=ExtractionMode.DOMAIN_SCHEMA_FREE, soft_vocabulary=vocab,
            )

        assert envelope.entities[0].is_novel_type is True


class TestOntologyGuided:
    def _ontology(self) -> OntologyDefinition:
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

    @pytest.mark.asyncio
    async def test_requires_ontology_argument(self):
        with pytest.raises(ValueError, match="requires an OntologyDefinition"):
            await _extractor().extract(
                document_id="doc-1", org_id="org-1", domain="legal", text="x",
                mode=ExtractionMode.ONTOLOGY_GUIDED, ontology=None,
            )

    @pytest.mark.asyncio
    async def test_ontology_id_is_stamped_on_envelope(self):
        parsed = _LLMExtractionOutput(
            entities=[_LLMExtractedEntity(local_id="e1", type="Party", name="Acme Corp")]
        )
        p1, p2 = _patch_llm(parsed)
        with p1, p2:
            envelope = await _extractor().extract(
                document_id="doc-1", org_id="org-1", domain="legal", text="x",
                mode=ExtractionMode.ONTOLOGY_GUIDED, ontology=self._ontology(),
            )

        assert envelope.ontology_id == "legal-v1"
        assert envelope.entities[0].is_novel_type is False
