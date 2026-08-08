"""Unit tests for RoutingEngine (KG Clean Rebuild plan, Phase 4)."""

from unittest.mock import AsyncMock

import pytest

from app.modules.knowledge_graph.contracts.envelope import ExtractionMode
from app.modules.knowledge_graph.contracts.ontology import (
    OntologyDefinition,
    OntologyStatus,
)
from app.modules.knowledge_graph.routing.engine import RoutingEngine


def _ontology(status=OntologyStatus.ACTIVE, ontology_id="legal-v1") -> OntologyDefinition:
    return OntologyDefinition(
        ontology_id=ontology_id,
        version="1.0.0",
        org_id="org-1",
        domain="legal",
        status=status,
    )


class TestActiveOntologyRouting:
    @pytest.mark.asyncio
    async def test_active_ontology_routes_to_ontology_guided(self):
        lookup = AsyncMock()
        lookup.get_active_ontology = AsyncMock(return_value=_ontology())
        engine = RoutingEngine(ontology_lookup=lookup)

        decision = await engine.decide(
            document_id="doc-1", org_id="org-1", domain="legal", doc_type="contract"
        )

        assert decision.mode == ExtractionMode.ONTOLOGY_GUIDED
        assert decision.ontology_id == "legal-v1"
        assert decision.rule_matched == "rule_1_active_ontology"

    @pytest.mark.asyncio
    async def test_draft_ontology_is_not_used(self):
        """A draft (not-yet-published) ontology must not route documents to
        ontology_guided — only ACTIVE ontologies are applicable."""
        lookup = AsyncMock()
        lookup.get_active_ontology = AsyncMock(return_value=_ontology(status=OntologyStatus.DRAFT))
        engine = RoutingEngine(ontology_lookup=lookup)

        decision = await engine.decide(
            document_id="doc-1", org_id="org-1", domain="legal", doc_type="contract"
        )

        assert decision.mode != ExtractionMode.ONTOLOGY_GUIDED

    @pytest.mark.asyncio
    async def test_no_ontology_lookup_configured_skips_rule_1(self):
        engine = RoutingEngine(ontology_lookup=None)

        decision = await engine.decide(
            document_id="doc-1", org_id="org-1", domain="legal", doc_type="contract"
        )

        assert decision.mode == ExtractionMode.GENERIC_SCHEMA_FREE


class TestDomainVocabularyRouting:
    @pytest.mark.asyncio
    async def test_domain_with_vocabulary_routes_to_domain_schema_free(self):
        engine = RoutingEngine(domain_vocabularies={"marketing": ["Campaign", "Complaint"]})

        decision = await engine.decide(
            document_id="doc-1", org_id="org-1", domain="marketing", doc_type="email"
        )

        assert decision.mode == ExtractionMode.DOMAIN_SCHEMA_FREE
        assert decision.rule_matched == "rule_2_domain_vocabulary"

    @pytest.mark.asyncio
    async def test_empty_vocabulary_list_falls_back_to_generic(self):
        engine = RoutingEngine(domain_vocabularies={"marketing": []})

        decision = await engine.decide(
            document_id="doc-1", org_id="org-1", domain="marketing", doc_type="email"
        )

        assert decision.mode == ExtractionMode.GENERIC_SCHEMA_FREE

    @pytest.mark.asyncio
    async def test_unregistered_domain_falls_back_to_generic(self):
        engine = RoutingEngine(domain_vocabularies={"marketing": ["Campaign"]})

        decision = await engine.decide(
            document_id="doc-1", org_id="org-1", domain="engineering", doc_type="design_note"
        )

        assert decision.mode == ExtractionMode.GENERIC_SCHEMA_FREE
        assert decision.rule_matched == "rule_3_fallback_generic"


class TestRulePrecedence:
    @pytest.mark.asyncio
    async def test_active_ontology_takes_precedence_over_domain_vocabulary(self):
        lookup = AsyncMock()
        lookup.get_active_ontology = AsyncMock(return_value=_ontology())
        engine = RoutingEngine(
            ontology_lookup=lookup, domain_vocabularies={"legal": ["Party"]}
        )

        decision = await engine.decide(
            document_id="doc-1", org_id="org-1", domain="legal", doc_type="contract"
        )

        assert decision.mode == ExtractionMode.ONTOLOGY_GUIDED

    @pytest.mark.asyncio
    async def test_decision_is_scoped_to_document_and_org(self):
        engine = RoutingEngine()

        decision = await engine.decide(
            document_id="doc-42", org_id="org-9", domain="engineering", doc_type="design_note"
        )

        assert decision.document_id == "doc-42"
        assert decision.org_id == "org-9"
        assert decision.routing_id
        assert decision.decided_at > 0
