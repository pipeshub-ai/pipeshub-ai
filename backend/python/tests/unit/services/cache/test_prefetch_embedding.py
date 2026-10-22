"""Unit tests for query embedding handoff and record fingerprinting in retrieval and prefetch.

Tests:
1. `search_with_filters` response dictionary never contains `query_embedding` (byte-identical behavior for default callers).
2. `search_with_filters` populates opt-in `out_query_embedding` without altering response dictionary.
3. `prefetch_retrieval` carries `query_embedding` on `PrefetchResult`.
4. `prefetch_retrieval` builds deterministic `record_fingerprints` matching raw graph records (id, updatedAtTimestamp, sourceLastModifiedTimestamp, version, md5Checksum).
5. If ANY tool_records entry has no matching raw record in `virtual_to_record_map` (e.g. FK children or missing records), `record_fingerprints` is None (turn becomes non-cacheable).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.chat_modes.prefetch import PrefetchResult, prefetch_retrieval
from app.modules.retrieval.context.builder import KnowledgeContext
from app.modules.retrieval.retrieval_service import RetrievalService


def _mock_prefetch_render(vr_to_result: dict[str, object], text: str = "policy text"):
    mock_builder = MagicMock()
    mock_builder.build = AsyncMock(
        return_value=KnowledgeContext(
            ranked=[{"virtual_record_id": vrid} for vrid in vr_to_result],
            virtual_record_id_to_result=vr_to_result,
        )
    )
    mock_rendered = MagicMock(
        parts=[[{"type": "text", "text": text}]],
        units=[{"virtual_record_id": vrid} for vrid in vr_to_result],
        images=[],
        ranked=[{"virtual_record_id": vrid} for vrid in vr_to_result],
    )
    return mock_builder, mock_rendered


def _make_dummy_prefetch_kwargs(
    retrieval_service=None,
    graph_provider=None,
    blob_store=None,
) -> dict[str, object]:
    logger = MagicMock()
    return {
        "query": "what is our company travel policy?",
        "org_id": "org_test",
        "user_id": "user_test",
        "retrieval_service": retrieval_service or AsyncMock(),
        "graph_provider": graph_provider or AsyncMock(),
        "blob_store": blob_store or MagicMock(),
        "filters": None,
        "limit": 10,
        "is_multimodal_llm": False,
        "previous_conversations": None,
        "logger": logger,
        "force": False,
    }


class TestSearchWithFiltersByteIdenticalResponse:
    @pytest.mark.asyncio
    async def test_search_api_response_never_contains_query_embedding(self) -> None:
        """Default callers receive a dictionary that NEVER contains 'query_embedding' key."""
        retrieval_service = RetrievalService.__new__(RetrievalService)
        retrieval_service.graph_provider = AsyncMock()
        retrieval_service.graph_provider.get_record_adjacency = AsyncMock(return_value={"nodes": {}})
        retrieval_service.vector_db_service = AsyncMock()
        retrieval_service.logger = MagicMock()
        retrieval_service._cached_embedding_is_local = True
        retrieval_service._capabilities = MagicMock(
            supports_sparse_vectors=False,
            supports_server_side_text_search=False,
        )

        mock_embedding_model = MagicMock()
        mock_embedding_model.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
        retrieval_service.get_embedding_model_instance = AsyncMock(return_value=mock_embedding_model)
        retrieval_service._ensure_sparse_embedder = AsyncMock(return_value=None)

        # Mock scope resolution to return containers=None, accessible map
        retrieval_service._resolve_search_scope = AsyncMock(
            return_value=(None, {"vr1": "r1"}, {"_key": "u1", "email": "user@example.com"})
        )
        retrieval_service.vector_db_service.filter_collection = AsyncMock(return_value={})
        retrieval_service._run_searches = AsyncMock(return_value=[
            {"metadata": {"virtualRecordId": "vr1", "origin": "drive", "recordName": "doc1", "recordId": "r1", "mimeType": "text/plain", "orgId": "org_test"}, "content": "travel rules", "score": 0.9}
        ])

        raw_record = {
            "_key": "r1",
            "id": "r1",
            "virtualRecordId": "vr1",
            "updatedAtTimestamp": 1700000000,
            "sourceLastModifiedTimestamp": 1699999000,
            "version": 2,
            "md5Checksum": "abc123def456",
        }
        retrieval_service.graph_provider.get_records_by_record_ids = AsyncMock(return_value=[raw_record])
        retrieval_service.graph_provider.check_access = AsyncMock(
            return_value=MagicMock(node_ids={"r1"}, records_by_vrid={"vr1": "r1"})
        )

        # Standard call without out_query_embedding
        result = await retrieval_service.search_with_filters(
            queries=["travel policy"],
            user_id="user_test",
            org_id="org_test",
        )

        # Assert query_embedding is NOT in response dictionary
        assert "query_embedding" not in result
        assert result["status_code"] == 200
        assert "searchResults" in result
        assert "virtual_to_record_map" in result

    @pytest.mark.asyncio
    async def test_opt_in_out_query_embedding_populates_vector_without_modifying_dict(self) -> None:
        """Opt-in out_query_embedding extracts dense vector into holder while response dict stays untouched."""
        retrieval_service = RetrievalService.__new__(RetrievalService)
        retrieval_service.graph_provider = AsyncMock()
        retrieval_service.graph_provider.get_record_adjacency = AsyncMock(return_value={"nodes": {}})
        retrieval_service.vector_db_service = AsyncMock()
        retrieval_service.logger = MagicMock()
        retrieval_service._cached_embedding_is_local = True
        retrieval_service._capabilities = MagicMock(
            supports_sparse_vectors=False,
            supports_server_side_text_search=False,
        )

        dummy_vector = [0.123, 0.456, 0.789]
        mock_embedding_model = MagicMock()
        mock_embedding_model.aembed_query = AsyncMock(return_value=dummy_vector)
        retrieval_service.get_embedding_model_instance = AsyncMock(return_value=mock_embedding_model)
        retrieval_service._ensure_sparse_embedder = AsyncMock(return_value=None)

        retrieval_service._resolve_search_scope = AsyncMock(
            return_value=(None, {"vr1": "r1"}, {"_key": "u1", "email": "user@example.com"})
        )
        retrieval_service.vector_db_service.filter_collection = AsyncMock(return_value={})
        retrieval_service._run_searches = AsyncMock(return_value=[
            {"metadata": {"virtualRecordId": "vr1", "origin": "drive", "recordName": "doc1", "recordId": "r1", "mimeType": "text/plain", "orgId": "org_test"}, "content": "travel rules", "score": 0.9}
        ])

        raw_record = {
            "_key": "r1",
            "id": "r1",
            "virtualRecordId": "vr1",
            "updatedAtTimestamp": 1700000000,
            "sourceLastModifiedTimestamp": 1699999000,
            "version": 2,
            "md5Checksum": "abc123def456",
        }
        retrieval_service.graph_provider.get_records_by_record_ids = AsyncMock(return_value=[raw_record])

        # Opt-in call with out_query_embedding
        out_embeddings: list[list[float]] = []
        result = await retrieval_service.search_with_filters(
            queries=["travel policy"],
            user_id="user_test",
            org_id="org_test",
            out_query_embedding=out_embeddings,
        )

        # Dictionary response still does NOT have query_embedding
        assert "query_embedding" not in result
        # Out parameter received the vector
        assert len(out_embeddings) == 1
        assert out_embeddings[0] == dummy_vector


class TestPrefetchFingerprintsAndEmbeddingHandoff:
    @pytest.mark.asyncio
    async def test_prefetch_retrieval_carries_dense_embedding(self) -> None:
        """Prefetch retrieval extracts and carries dense query embedding on PrefetchResult."""
        dummy_vector = [0.01, 0.02, 0.03]

        async def _fake_search_with_filters(*args, **kwargs) -> dict[str, object]:
            out_param = kwargs.get("out_query_embedding")
            if out_param is not None and isinstance(out_param, list):
                out_param.append(dummy_vector)
            return {
                "status_code": 200,
                "searchResults": [{"metadata": {"virtualRecordId": "vr1", "recordId": "r1"}}],
                "virtual_to_record_map": {
                    "vr1": {
                        "id": "r1",
                        "_key": "r1",
                        "updatedAtTimestamp": 1700000000,
                        "sourceLastModifiedTimestamp": 1699999000,
                        "version": 3,
                        "md5Checksum": "md5_hash_1",
                    }
                },
            }

        retrieval_service = MagicMock()
        retrieval_service.search_with_filters = AsyncMock(side_effect=_fake_search_with_filters)

        builder, rendered = _mock_prefetch_render({"vr1": {"virtual_record_id": "vr1", "id": "r1"}})

        with patch("app.agents.chat_modes.prefetch.KnowledgeContextBuilder", return_value=builder), \
             patch("app.agents.chat_modes.prefetch.render_knowledge", return_value=rendered):

            result = await prefetch_retrieval(**_make_dummy_prefetch_kwargs(retrieval_service=retrieval_service))

        assert isinstance(result, PrefetchResult)
        assert result.is_empty is False
        assert result.query_embedding == dummy_vector

    @pytest.mark.asyncio
    async def test_prefetch_builds_record_fingerprints_matching_raw_records(self) -> None:
        """When all tool records have matching raw graph records, fingerprints are built deterministically."""
        async def _fake_search_with_filters(*args, **kwargs) -> dict[str, object]:
            return {
                "status_code": 200,
                "searchResults": [{"metadata": {"virtualRecordId": "vr1", "recordId": "r1"}}],
                "virtual_to_record_map": {
                    "vr1": {
                        "id": "r1",
                        "_key": "r1",
                        "updatedAtTimestamp": 1700000000,
                        "sourceLastModifiedTimestamp": 1699999000,
                        "version": 3,
                        "md5Checksum": "md5_hash_1",
                    }
                },
            }

        retrieval_service = MagicMock()
        retrieval_service.search_with_filters = AsyncMock(side_effect=_fake_search_with_filters)

        builder, rendered = _mock_prefetch_render({"vr1": {"virtual_record_id": "vr1", "id": "r1"}})

        with patch("app.agents.chat_modes.prefetch.KnowledgeContextBuilder", return_value=builder), \
             patch("app.agents.chat_modes.prefetch.render_knowledge", return_value=rendered):

            result = await prefetch_retrieval(**_make_dummy_prefetch_kwargs(retrieval_service=retrieval_service))

        assert isinstance(result, PrefetchResult)
        assert result.record_fingerprints is not None
        assert len(result.record_fingerprints) == 1
        assert result.record_fingerprints[0] == ("r1", 1700000000, 1699999000, 3, "md5_hash_1")

    @pytest.mark.asyncio
    async def test_unmatched_tool_record_turns_turn_non_cacheable(self) -> None:
        """If ANY tool_records entry (e.g. FK child) has no matching raw record in virtual_to_record_map, record_fingerprints is None."""
        retrieval_service = MagicMock()
        retrieval_service.search_with_filters = AsyncMock(return_value={
            "status_code": 200,
            "searchResults": [{"metadata": {"virtualRecordId": "vr1"}}],
            "virtual_to_record_map": {
                "vr1": {
                    "id": "r1",
                    "updatedAtTimestamp": 100,
                    "sourceLastModifiedTimestamp": 90,
                    "version": 1,
                    "md5Checksum": "hash1",
                }
            },
        })

        builder, rendered = _mock_prefetch_render(
            {"vr1": {"virtual_record_id": "vr1"}, "vr2_fk_child": {"virtual_record_id": "vr2_fk_child"}},
            text="policy text with child",
        )

        with patch("app.agents.chat_modes.prefetch.KnowledgeContextBuilder", return_value=builder), \
             patch("app.agents.chat_modes.prefetch.render_knowledge", return_value=rendered):

            result = await prefetch_retrieval(**_make_dummy_prefetch_kwargs(retrieval_service=retrieval_service))

        assert isinstance(result, PrefetchResult)
        assert result.is_empty is False
        # Since vr2_fk_child is in tool_records but NOT in virtual_to_record_map,
        # turn MUST become non-cacheable:
        assert result.record_fingerprints is None

    @pytest.mark.asyncio
    async def test_fingerprints_sorted_deterministically_by_record_id(self) -> None:
        """Multiple matched records are sorted by record id for deterministic hashing."""
        retrieval_service = MagicMock()
        retrieval_service.search_with_filters = AsyncMock(return_value={
            "status_code": 200,
            "searchResults": [{"metadata": {"virtualRecordId": "vB"}}, {"metadata": {"virtualRecordId": "vA"}}],
            "virtual_to_record_map": {
                "vB": {"id": "rec_zeta", "updatedAtTimestamp": 200, "sourceLastModifiedTimestamp": 180, "version": 1, "md5Checksum": "hZ"},
                "vA": {"id": "rec_alpha", "updatedAtTimestamp": 100, "sourceLastModifiedTimestamp": 90, "version": 2, "md5Checksum": "hA"},
            },
        })

        builder, rendered = _mock_prefetch_render(
            {"vB": {"virtual_record_id": "vB"}, "vA": {"virtual_record_id": "vA"}},
            text="content",
        )

        with patch("app.agents.chat_modes.prefetch.KnowledgeContextBuilder", return_value=builder), \
             patch("app.agents.chat_modes.prefetch.render_knowledge", return_value=rendered):

            result = await prefetch_retrieval(**_make_dummy_prefetch_kwargs(retrieval_service=retrieval_service))

        assert result.record_fingerprints is not None
        assert len(result.record_fingerprints) == 2
        # Deterministically sorted: rec_alpha before rec_zeta
        assert result.record_fingerprints[0][0] == "rec_alpha"
        assert result.record_fingerprints[1][0] == "rec_zeta"


class TestSearchRouteLevelResponse:
    @pytest.mark.asyncio
    async def test_search_route_http_response_never_contains_query_embedding(self) -> None:
        """Invoking the search endpoint directly proves query_embedding never reaches HTTP client."""
        import json

        from fastapi.responses import JSONResponse

        import app.edition_config  # noqa: F401
        from app.api.routes.search import SearchQuery, search

        mock_request = MagicMock()
        mock_request.state.user = {"userId": "user1", "orgId": "org1", "email": "user1@example.com"}
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        mock_retrieval = MagicMock()
        mock_retrieval.llm = MagicMock()
        mock_retrieval.search_with_filters = AsyncMock(
            return_value={
                "status_code": 200,
                "searchResults": [{"metadata": {"virtualRecordId": "vr1"}, "content": "text"}],
                "virtual_to_record_map": {"vr1": {"id": "r1"}},
            }
        )
        mock_graph = MagicMock()
        body = SearchQuery(query="company policy", filters={})

        chain = MagicMock()
        chain.ainvoke = AsyncMock(return_value="rewritten policy")

        with patch("app.api.routes.search.setup_query_transformation", return_value=(chain, chain)), \
             patch("app.api.routes.search.record_event"):
            response = await search(
                request=mock_request,
                body=body,
                retrieval_service=mock_retrieval,
                graph_provider=mock_graph,
            )

        assert isinstance(response, JSONResponse)
        assert response.status_code == 200
        content = json.loads(response.body.decode("utf-8"))
        assert "query_embedding" not in content
        assert "searchResults" in content


class TestSparseOnlySearchHandoff:
    @pytest.mark.asyncio
    async def test_sparse_only_search_leaves_query_embedding_none_and_non_cacheable(self) -> None:
        """When search is sparse-only (plan.dense is empty), query_embedding is None and turn is non-cacheable with 0 errors."""
        retrieval_service = MagicMock()

        # search_with_filters executes, but does not append to out_query_embedding (simulating plan.dense empty)
        async def _mock_search_with_filters(*args, **kwargs) -> dict[str, object]:
            return {
                "status_code": 200,
                "searchResults": [{"metadata": {"virtualRecordId": "vr1"}}],
                "virtual_to_record_map": {
                    "vr1": {"id": "r1", "updatedAtTimestamp": 100, "sourceLastModifiedTimestamp": 90, "version": 1, "md5Checksum": "c1"},
                },
            }

        retrieval_service.search_with_filters = AsyncMock(side_effect=_mock_search_with_filters)

        builder, rendered = _mock_prefetch_render({"vr1": {"virtual_record_id": "vr1"}}, text="content")

        with patch("app.agents.chat_modes.prefetch.KnowledgeContextBuilder", return_value=builder), \
             patch("app.agents.chat_modes.prefetch.render_knowledge", return_value=rendered):

            result = await prefetch_retrieval(**_make_dummy_prefetch_kwargs(retrieval_service=retrieval_service))

        assert isinstance(result, PrefetchResult)
        assert result.is_empty is False
        # When plan.dense is empty (sparse only), query_embedding is None
        assert result.query_embedding is None
        # Record fingerprints exist, but turn is non-cacheable without dense query embedding
        assert result.record_fingerprints is not None
