"""Unit tests for SemanticLLMEntityResolver (KG Clean Rebuild plan, Phase 3).

Covers the Part C policy table: hard-key/canonical auto-merge, semantic
(name-similarity) blocking bands, LLM adjudication of the ambiguous band,
and fail-closed-to-distinct behaviour on any error.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.knowledge_graph.contracts.envelope import (
    ExtractedEntity,
    ExtractionEnvelope,
    ExtractionMode,
)
from app.modules.knowledge_graph.indexing.resolution import (
    ResolutionThresholds,
    SemanticLLMEntityResolver,
    _LLMMergeDecision,
)

MODULE = "app.modules.knowledge_graph.indexing.resolution"


def _entity(local_id="e1", name="Bob Smith", etype="person", attributes=None, is_novel=False) -> ExtractedEntity:
    return ExtractedEntity(
        local_id=local_id,
        type=etype,
        name=name,
        attributes=attributes or {},
        is_novel_type=is_novel,
    )


def _envelope(entities, org_id="org-1") -> ExtractionEnvelope:
    return ExtractionEnvelope(
        extraction_id="ext-1",
        document_id="doc-1",
        org_id=org_id,
        extraction_mode=ExtractionMode.GENERIC_SCHEMA_FREE,
        entities=entities,
    )


def _candidate(entity_id="c1", name="Bob Smith", score=0.5, aliases=None, canonical_name=None) -> dict:
    return {
        "entityId": entity_id,
        "entityType": "person",
        "name": name,
        "canonicalName": canonical_name,
        "aliases": aliases or [],
        "score": score,
        "parentEntityId": None,
        "parentEntityType": None,
    }


def _make_resolver(candidates=None) -> SemanticLLMEntityResolver:
    entity_vector_store = AsyncMock()
    entity_vector_store.search_entities = AsyncMock(return_value=candidates or [])
    return SemanticLLMEntityResolver(
        entity_vector_store=entity_vector_store,
        config_service=MagicMock(),
        logger=MagicMock(),
    )


class TestNoCandidates:
    @pytest.mark.asyncio
    async def test_no_candidates_creates_new_entity(self):
        resolver = _make_resolver(candidates=[])
        result = await resolver.resolve_envelope(_envelope([_entity()]))

        assert result.merges[0].is_new_node is True
        assert result.merges[0].matched_signal == "new"
        assert "e1" in result.local_to_canonical

    @pytest.mark.asyncio
    async def test_blank_name_creates_new_entity_without_search(self):
        resolver = _make_resolver()
        entity = _entity(name="   ")

        await resolver.resolve_envelope(_envelope([entity]))

        resolver.entity_vector_store.search_entities.assert_not_awaited()


class TestHardKeyMatch:
    @pytest.mark.asyncio
    async def test_hard_key_email_match_auto_merges_no_llm(self):
        candidate = _candidate(aliases=["bob@acme.com"])
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(attributes={"email": "Bob@Acme.com"})

        with patch(f"{MODULE}.get_llm_for_role") as mock_llm:
            result = await resolver.resolve_envelope(_envelope([entity]))

        mock_llm.assert_not_called()
        merge = result.merges[0]
        assert merge.matched_signal == "hard_key"
        assert merge.canonical_node_id == "c1"
        assert merge.confidence == 1.0

    @pytest.mark.asyncio
    async def test_hard_key_present_but_no_candidate_match_falls_through(self):
        candidate = _candidate(name="Someone Else", aliases=["other@acme.com"])
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(name="Totally Different Name", attributes={"email": "bob@acme.com"})

        result = await resolver.resolve_envelope(_envelope([entity]))

        # Falls through hard-key + exact-name to similarity scoring; low
        # similarity against a dissimilar name -> new entity.
        assert result.merges[0].is_new_node is True


class TestExactCanonicalNameMatch:
    @pytest.mark.asyncio
    async def test_exact_name_match_auto_merges_no_llm(self):
        candidate = _candidate(name="Bob Smith")
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(name="Bob Smith", etype="person")

        with patch(f"{MODULE}.get_llm_for_role") as mock_llm:
            result = await resolver.resolve_envelope(_envelope([entity]))

        mock_llm.assert_not_called()
        assert result.merges[0].matched_signal == "canonical_name"
        assert result.merges[0].confidence == 1.0

    @pytest.mark.asyncio
    async def test_exact_match_is_case_and_whitespace_insensitive(self):
        candidate = _candidate(name="  bob smith  ")
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(name="Bob Smith")

        result = await resolver.resolve_envelope(_envelope([entity]))

        assert result.merges[0].matched_signal == "canonical_name"


class TestSimilarityBands:
    @pytest.mark.asyncio
    async def test_low_similarity_creates_new_entity(self):
        candidate = _candidate(name="Zebra Corporation")
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(name="Acme Inc", etype="record_group")

        result = await resolver.resolve_envelope(_envelope([entity]))

        assert result.merges[0].is_new_node is True

    @pytest.mark.asyncio
    async def test_ambiguous_band_triggers_llm_adjudication(self):
        # "Acme Corp" vs "Acme Corporation" — similar but not identical.
        candidate = _candidate(name="Acme Corporation")
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(name="Acme Corp", etype="record_group")

        decision = _LLMMergeDecision(decision="merge", confidence=0.8, reason="same company")
        with patch(f"{MODULE}.get_llm_for_role", AsyncMock(return_value=(MagicMock(), {}))):
            with patch(f"{MODULE}.invoke_with_structured_output_and_reflection", AsyncMock(return_value=decision)):
                result = await resolver.resolve_envelope(_envelope([entity]))

        assert result.merges[0].matched_signal == "llm_adjudicated"
        assert result.merges[0].canonical_node_id == "c1"
        assert len(result.llm_adjudications) == 1
        assert result.llm_adjudications[0].decision == "merge"

    @pytest.mark.asyncio
    async def test_ambiguous_band_llm_distinct_creates_new_entity(self):
        candidate = _candidate(name="Acme Corporation")
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(name="Acme Corp", etype="record_group")

        decision = _LLMMergeDecision(decision="distinct", confidence=0.6, reason="different entity")
        with patch(f"{MODULE}.get_llm_for_role", AsyncMock(return_value=(MagicMock(), {}))):
            with patch(f"{MODULE}.invoke_with_structured_output_and_reflection", AsyncMock(return_value=decision)):
                result = await resolver.resolve_envelope(_envelope([entity]))

        assert result.merges[0].is_new_node is True

    @pytest.mark.asyncio
    async def test_high_similarity_non_person_auto_merges_no_llm(self):
        # Identical after normalization would hit exact-match first, so use
        # a near-duplicate that clears the hard threshold but isn't exact.
        candidate = _candidate(name="Acme Corporation Inc")
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(name="Acme Corporation In", etype="record_group")

        with patch(f"{MODULE}.get_llm_for_role") as mock_llm:
            result = await resolver.resolve_envelope(_envelope([entity]))

        mock_llm.assert_not_called()
        assert result.merges[0].matched_signal == "canonical_name"
        assert result.merges[0].is_new_node is False


class TestPersonNeverAutoMerges:
    @pytest.mark.asyncio
    async def test_person_high_similarity_still_routes_to_llm(self):
        """Even above the hard threshold, PERSON entities must never
        auto-merge on name similarity alone."""
        candidate = _candidate(name="Bob Smithh", entity_id="person-c1")
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(name="Bob Smith", etype="person")

        decision = _LLMMergeDecision(decision="merge", confidence=0.9, reason="same person")
        llm_mock = AsyncMock(return_value=(MagicMock(), {}))
        with patch(f"{MODULE}.get_llm_for_role", llm_mock):
            with patch(f"{MODULE}.invoke_with_structured_output_and_reflection", AsyncMock(return_value=decision)):
                result = await resolver.resolve_envelope(_envelope([entity]))

        llm_mock.assert_awaited_once()
        assert result.merges[0].matched_signal == "llm_adjudicated"


class TestFailClosed:
    @pytest.mark.asyncio
    async def test_llm_exception_fails_closed_to_distinct(self):
        candidate = _candidate(name="Acme Corporation")
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(name="Acme Corp", etype="record_group")

        with patch(f"{MODULE}.get_llm_for_role", AsyncMock(side_effect=RuntimeError("LLM down"))):
            result = await resolver.resolve_envelope(_envelope([entity]))

        assert result.merges[0].is_new_node is True
        assert result.llm_adjudications == []

    @pytest.mark.asyncio
    async def test_unparseable_llm_response_fails_closed_to_distinct(self):
        candidate = _candidate(name="Acme Corporation")
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(name="Acme Corp", etype="record_group")

        with patch(f"{MODULE}.get_llm_for_role", AsyncMock(return_value=(MagicMock(), {}))):
            with patch(f"{MODULE}.invoke_with_structured_output_and_reflection", AsyncMock(return_value=None)):
                result = await resolver.resolve_envelope(_envelope([entity]))

        assert result.merges[0].is_new_node is True

    @pytest.mark.asyncio
    async def test_vector_search_exception_fails_closed_to_distinct(self):
        resolver = _make_resolver()
        resolver.entity_vector_store.search_entities = AsyncMock(
            side_effect=RuntimeError("vector db down")
        )
        entity = _entity()

        result = await resolver.resolve_envelope(_envelope([entity]))

        assert result.merges[0].is_new_node is True


class TestNovelTypeTracking:
    @pytest.mark.asyncio
    async def test_novel_type_tallied_regardless_of_merge_outcome(self):
        candidate = _candidate(name="Bob Smith")
        resolver = _make_resolver(candidates=[candidate])
        entity = _entity(name="Bob Smith", etype="vip_customer", is_novel=True)

        result = await resolver.resolve_envelope(_envelope([entity]))

        assert "vip_customer" in result.novel_types

    @pytest.mark.asyncio
    async def test_novel_type_not_duplicated_across_entities(self):
        e1 = _entity(local_id="e1", name="A", etype="vip_customer", is_novel=True)
        e2 = _entity(local_id="e2", name="B", etype="vip_customer", is_novel=True)
        resolver = _make_resolver(candidates=[])

        result = await resolver.resolve_envelope(_envelope([e1, e2]))

        assert result.novel_types.count("vip_customer") == 1


class TestMultipleEntitiesInEnvelope:
    @pytest.mark.asyncio
    async def test_one_entity_failure_does_not_block_others(self):
        good_entity = _entity(local_id="e1", name="Bob Smith")
        candidate = _candidate(name="Bob Smith")

        entity_vector_store = AsyncMock()

        async def search_side_effect(*, query, org_id, entity_types, top_k):
            if query == "Bob Smith":
                return [candidate]
            raise RuntimeError("boom")

        entity_vector_store.search_entities = AsyncMock(side_effect=search_side_effect)
        resolver = SemanticLLMEntityResolver(
            entity_vector_store=entity_vector_store,
            config_service=MagicMock(),
            logger=MagicMock(),
        )
        bad_entity = _entity(local_id="e2", name="Explodes")

        with patch(f"{MODULE}.get_llm_for_role") as mock_llm:
            result = await resolver.resolve_envelope(_envelope([good_entity, bad_entity]))

        mock_llm.assert_not_called()
        assert result.canonical_id_for("e1") == "c1"
        assert result.canonical_id_for("e2") is not None
        e2_merge = next(m for m in result.merges if m.local_id == "e2")
        assert e2_merge.is_new_node is True


class TestThresholdsConfigurable:
    @pytest.mark.asyncio
    async def test_custom_thresholds_widen_auto_merge_band(self):
        """With a very low hard threshold, even a loose match auto-merges
        for non-PERSON types."""
        candidate = _candidate(name="Totally Different")
        resolver = SemanticLLMEntityResolver(
            entity_vector_store=AsyncMock(search_entities=AsyncMock(return_value=[candidate])),
            config_service=MagicMock(),
            logger=MagicMock(),
            thresholds=ResolutionThresholds(soft=0.0, hard=0.0),
        )
        entity = _entity(name="Anything At All", etype="record_group")

        with patch(f"{MODULE}.get_llm_for_role") as mock_llm:
            result = await resolver.resolve_envelope(_envelope([entity]))

        mock_llm.assert_not_called()
        assert result.merges[0].is_new_node is False
