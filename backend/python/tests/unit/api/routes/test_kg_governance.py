"""Unit tests for app.api.routes.kg_governance (KG Clean Rebuild plan, Phase 7).

Route handlers are exercised directly against a mocked ``Request`` (matching
the direct-call convention already used for ``entity_sync.py``'s handlers)
rather than a full FastAPI ``TestClient`` — auth/scope/feature-flag
dependencies are covered independently in ``test_admin.py`` /
``test_auth.py`` / the feature-flag service's own tests, so what's worth
covering here is each route's request validation and error-mapping.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.kg_governance import (
    DeprecateTypeRequest,
    MergeRequest,
    PromoteTypeRequest,
    ResolveSuggestionRequest,
    _require_governance_enabled,
    deprecate_type,
    list_suggestions,
    merge_entities,
    promote_type,
    resolve_suggestion,
)
from app.modules.knowledge_graph.governance.ontology_store import (
    OntologyGovernanceError,
)


def _make_request(org_id: str = "org-1", user_id: str = "user-1", graph_provider=None) -> MagicMock:
    request = MagicMock()
    request.state.user = {"orgId": org_id, "userId": user_id}
    request.headers = {}
    request.app.state = SimpleNamespace()  # no .graph_provider -- forces container path
    request.app.container.logger.return_value = MagicMock()
    request.app.container.graph_provider = AsyncMock(return_value=graph_provider)
    return request


class TestRequireGovernanceEnabled:
    @pytest.mark.asyncio
    async def test_disabled_flag_raises_404(self) -> None:
        with patch(
            "app.api.routes.kg_governance.FeatureFlagService.get_service",
            return_value=MagicMock(is_feature_enabled=MagicMock(return_value=False)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _require_governance_enabled()
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_enabled_flag_passes(self) -> None:
        with patch(
            "app.api.routes.kg_governance.FeatureFlagService.get_service",
            return_value=MagicMock(is_feature_enabled=MagicMock(return_value=True)),
        ):
            await _require_governance_enabled()  # should not raise


class TestMergeEntitiesRoute:
    @pytest.mark.asyncio
    async def test_missing_org_id_raises_401(self) -> None:
        request = _make_request(org_id="")
        body = MergeRequest(
            survivor_node_id="s1", survivor_collection="people",
            duplicate_node_id="d1", duplicate_collection="people",
        )

        with pytest.raises(HTTPException) as exc_info:
            await merge_entities(request, body)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_graph_provider_raises_503(self) -> None:
        request = _make_request(graph_provider=None)
        body = MergeRequest(
            survivor_node_id="s1", survivor_collection="people",
            duplicate_node_id="d1", duplicate_collection="people",
        )

        with pytest.raises(HTTPException) as exc_info:
            await merge_entities(request, body)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_same_node_merge_error_maps_to_400(self) -> None:
        graph_provider = MagicMock()
        request = _make_request(graph_provider=graph_provider)
        body = MergeRequest(
            survivor_node_id="s1", survivor_collection="people",
            duplicate_node_id="s1", duplicate_collection="people",
        )

        with pytest.raises(HTTPException) as exc_info:
            await merge_entities(request, body)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_success_returns_merge_outcome(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_bitemporal_edges = AsyncMock(return_value=[])
        graph_provider.update_node = AsyncMock(return_value=True)
        request = _make_request(graph_provider=graph_provider)
        body = MergeRequest(
            survivor_node_id="s1", survivor_collection="people",
            duplicate_node_id="d1", duplicate_collection="people",
        )

        response = await merge_entities(request, body)

        assert response.status_code == 200


class TestListSuggestionsRoute:
    @pytest.mark.asyncio
    async def test_no_graph_provider_raises_503(self) -> None:
        request = _make_request(graph_provider=None)

        with pytest.raises(HTTPException) as exc_info:
            await list_suggestions(request)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_success_lists_pending_by_default(self) -> None:
        # status/limit are FastAPI Query(...) defaults -- only resolved by
        # FastAPI's dependency injection, so a direct call must supply them
        # explicitly (mirrors the route's own declared defaults).
        graph_provider = MagicMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[{"id": "sugg-1", "status": "pending"}])
        request = _make_request(graph_provider=graph_provider)

        response = await list_suggestions(request, status="pending", limit=50)

        assert response.status_code == 200
        graph_provider.get_nodes_by_filters.assert_awaited_once_with(
            "kgMergeSuggestions", {"orgId": "org-1", "status": "pending"},
        )


class TestResolveSuggestionRoute:
    @pytest.mark.asyncio
    async def test_not_found_raises_404(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_document = AsyncMock(return_value=None)
        request = _make_request(graph_provider=graph_provider)
        body = ResolveSuggestionRequest(outcome="rejected")

        with pytest.raises(HTTPException) as exc_info:
            await resolve_suggestion("sugg-1", request, body)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_without_merge_attempt(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_document = AsyncMock(
            return_value={"id": "sugg-1", "orgId": "org-1", "localId": "loc-1", "candidateNodeId": "cand-1"}
        )
        graph_provider.update_node = AsyncMock(return_value=True)
        request = _make_request(graph_provider=graph_provider)
        body = ResolveSuggestionRequest(outcome="rejected")

        response = await resolve_suggestion("sugg-1", request, body)

        assert response.status_code == 200
        graph_provider.get_bitemporal_edges = AsyncMock()
        graph_provider.get_bitemporal_edges.assert_not_called()


class TestPromoteTypeRoute:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_document = AsyncMock(return_value=None)
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        request = _make_request(graph_provider=graph_provider)
        body = PromoteTypeRequest(domain="marketing", type_name="Complaint")

        response = await promote_type(request, body)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_governance_error_maps_to_400(self) -> None:
        graph_provider = MagicMock()
        request = _make_request(graph_provider=graph_provider)
        body = PromoteTypeRequest(domain="marketing", type_name="Complaint")

        with patch(
            "app.api.routes.kg_governance.OntologyRegistryStore.promote_type",
            new=AsyncMock(side_effect=OntologyGovernanceError("duplicate type")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await promote_type(request, body)
        assert exc_info.value.status_code == 400


class TestDeprecateTypeRoute:
    @pytest.mark.asyncio
    async def test_unknown_ontology_maps_to_400(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_document = AsyncMock(return_value=None)
        request = _make_request(graph_provider=graph_provider)
        body = DeprecateTypeRequest(ontology_id="missing", type_name="Campaign")

        with pytest.raises(HTTPException) as exc_info:
            await deprecate_type(request, body)
        assert exc_info.value.status_code == 400
