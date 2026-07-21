"""
test_entity_resolution.py — Unit tests for the resolve_entity_filters agent tool.

Matches plan section: "3. Entity Resolution Tool (test_entity_resolution.py)"
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_retrieval(state: dict):
    from app.agents.actions.retrieval.retrieval import Retrieval

    r = Retrieval.__new__(Retrieval)
    r.state = state
    r.writer = None
    return r


def _state(evs=None, org_id: str = "org1") -> dict:
    return {
        "org_id": org_id,
        "entity_vector_store": evs,
        "logger": MagicMock(),
    }


def _hit(entity_id: str, entity_type: str, name: str, score: float = 0.85) -> dict:
    return {"entityId": entity_id, "entityType": entity_type, "name": name, "score": score}


# ===========================================================================
# TestResolveEntityFiltersTool
# ===========================================================================


class TestResolveEntityFiltersTool:

    @pytest.mark.asyncio
    async def test_resolve_single_facet_returns_top_k_matches(self):
        """query_facets=['engineering'] -> returns Engineering dept + Engineering topic with scores."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(
            return_value=[
                _hit("dept_eng", "department", "Engineering", 0.91),
                _hit("topic_eng", "topic", "Software Engineering", 0.78),
            ]
        )
        r = _make_retrieval(_state(mock_evs))

        result = json.loads(await r.resolve_entity_filters(query_facets=["engineering"]))

        assert result["status"] == "success"
        hits = result["resolved"]["engineering"]
        assert len(hits) == 2
        names = {h["name"] for h in hits}
        assert "Engineering" in names

    @pytest.mark.asyncio
    async def test_resolve_multiple_facets_returns_per_facet_results(self):
        """query_facets=['engineering', 'OAuth'] -> separate results per facet."""
        async def mock_search(query, org_id, entity_types=None, top_k=5, score_threshold=0.35):
            if query == "engineering":
                return [_hit("dept_eng", "department", "Engineering")]
            if query == "OAuth":
                return [_hit("topic_oauth", "topic", "OAuth")]
            return []

        mock_evs = AsyncMock()
        mock_evs.search_entities = mock_search
        r = _make_retrieval(_state(mock_evs))

        result = json.loads(
            await r.resolve_entity_filters(query_facets=["engineering", "OAuth"])
        )

        assert "engineering" in result["resolved"]
        assert "OAuth" in result["resolved"]
        assert result["resolved"]["engineering"][0]["entityId"] == "dept_eng"
        assert result["resolved"]["OAuth"][0]["entityId"] == "topic_oauth"

    @pytest.mark.asyncio
    async def test_resolve_with_entity_type_filter_narrows_results(self):
        """entity_types=['department'] passed to search_entities."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(
            return_value=[_hit("dept_eng", "department", "Engineering")]
        )
        r = _make_retrieval(_state(mock_evs))

        await r.resolve_entity_filters(
            query_facets=["engineering"], entity_types=["department"]
        )

        call_kwargs = mock_evs.search_entities.call_args.kwargs
        assert call_kwargs.get("entity_types") == ["department"]

    @pytest.mark.asyncio
    async def test_resolve_unknown_facet_returns_empty_with_low_scores(self):
        """Nonexistent facet -> empty results, no error."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(return_value=[])
        r = _make_retrieval(_state(mock_evs))

        result = json.loads(
            await r.resolve_entity_filters(query_facets=["xyzzy_nonexistent"])
        )

        assert result["status"] == "success"
        assert result["resolved"]["xyzzy_nonexistent"] == []
        assert "proceed without" in result["hint"].lower() or "no entities" in result["hint"].lower()

    @pytest.mark.asyncio
    async def test_resolve_ambiguous_facet_returns_multiple_candidates(self):
        """'security' should return both department and topic candidates."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(
            return_value=[
                _hit("dept_security", "department", "IT & Security", 0.88),
                _hit("topic_security", "topic", "Security", 0.82),
            ]
        )
        r = _make_retrieval(_state(mock_evs))

        result = json.loads(await r.resolve_entity_filters(query_facets=["security"]))

        hits = result["resolved"]["security"]
        types = {h["entityType"] for h in hits}
        assert "department" in types
        assert "topic" in types

    @pytest.mark.asyncio
    async def test_resolve_person_by_name_returns_correct_entity(self):
        """query_facets=['John Smith'] -> person entity returned."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(
            return_value=[_hit("person_john", "person", "John Smith", 0.95)]
        )
        r = _make_retrieval(_state(mock_evs))

        result = json.loads(await r.resolve_entity_filters(query_facets=["John Smith"]))

        hits = result["resolved"]["John Smith"]
        assert hits[0]["entityType"] == "person"
        assert "John Smith" in hits[0]["name"]

    @pytest.mark.asyncio
    async def test_resolve_returns_graph_db_ids_for_downstream_filtering(self):
        """Returned entityId values should be valid strings."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(
            return_value=[_hit("cat_abc123", "category", "Backend", 0.9)]
        )
        r = _make_retrieval(_state(mock_evs))

        result = json.loads(await r.resolve_entity_filters(query_facets=["backend"]))

        entity_id = result["resolved"]["backend"][0]["entityId"]
        assert isinstance(entity_id, str)
        assert entity_id == "cat_abc123"

    @pytest.mark.asyncio
    async def test_resolve_with_empty_entity_collection_returns_graceful_empty(self):
        """Empty vector collection -> tool returns empty results, no error."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(return_value=[])
        r = _make_retrieval(_state(mock_evs))

        result = json.loads(await r.resolve_entity_filters(query_facets=["engineering"]))

        assert result["status"] == "success"
        assert result["resolved"]["engineering"] == []

    @pytest.mark.asyncio
    async def test_resolve_respects_org_id_isolation(self):
        """org_id must be passed to search_entities for proper isolation."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(return_value=[])
        r = _make_retrieval(_state(mock_evs, org_id="org_B"))

        await r.resolve_entity_filters(query_facets=["engineering"])

        call_kwargs = mock_evs.search_entities.call_args.kwargs
        assert call_kwargs.get("org_id") == "org_B"

    @pytest.mark.asyncio
    async def test_resolve_when_entity_vector_store_not_available(self):
        """When entity_vector_store is None, tool returns graceful response."""
        r = _make_retrieval(_state(evs=None))

        result = json.loads(await r.resolve_entity_filters(query_facets=["engineering"]))

        assert result["status"] == "success"
        assert "not available" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_resolve_empty_facets_returns_error(self):
        """Empty facets list should return an error response."""
        r = _make_retrieval(_state())

        result = json.loads(await r.resolve_entity_filters(query_facets=[]))

        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_resolve_facet_search_exception_returns_empty_for_that_facet(self):
        """Per-facet exception -> empty result for that facet, no crash."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(side_effect=RuntimeError("boom"))
        r = _make_retrieval(_state(mock_evs))

        result = json.loads(await r.resolve_entity_filters(query_facets=["engineering"]))

        assert result["status"] == "success"
        assert result["resolved"]["engineering"] == []

    @pytest.mark.asyncio
    async def test_resolve_hint_mentions_filter_params_when_hits_found(self):
        """hint text should mention category_ids, topic_ids etc. when hits exist."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(
            return_value=[_hit("cat1", "category", "Engineering")]
        )
        r = _make_retrieval(_state(mock_evs))

        result = json.loads(await r.resolve_entity_filters(query_facets=["engineering"]))

        hint = result["hint"].lower()
        assert "category_ids" in hint or "topic_ids" in hint

    @pytest.mark.asyncio
    async def test_resolve_uses_default_top_k_when_not_specified(self):
        """Default top_k=5 should be forwarded to search_entities."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(return_value=[])
        r = _make_retrieval(_state(mock_evs))

        await r.resolve_entity_filters(query_facets=["engineering"])

        call_kwargs = mock_evs.search_entities.call_args.kwargs
        assert call_kwargs.get("top_k") == 5
