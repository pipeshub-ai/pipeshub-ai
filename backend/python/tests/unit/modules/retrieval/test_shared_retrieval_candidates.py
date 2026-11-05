from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval.retrieval_service import (
    RetrievalService,
    _QueryPlan,
)
from app.services.cache.interface import (
    RetrievalCandidate,
    SemanticQueryEntry,
    SemanticQueryMatch,
    SemanticQueryScope,
)
from app.services.vector_db.models import VectorPoint


class CandidateCache:
    enabled = True

    def __init__(self, candidates=None) -> None:
        self.candidates = candidates
        self.writes: list[tuple[str, list[RetrievalCandidate]]] = []
        self.candidate_pools: dict[str, list[RetrievalCandidate]] = {}
        self.similar_entries: list[SemanticQueryMatch] = []
        self.semantic_writes: list[SemanticQueryEntry] = []
        self.read_keys: list[str] = []

    async def get_candidates(self, key: str):
        self.read_keys.append(key)
        return self.candidate_pools.get(key, self.candidates)

    async def set_candidates(self, key: str, candidates: list[RetrievalCandidate]):
        self.writes.append((key, candidates))
        self.candidate_pools[key] = candidates
        self.candidates = candidates

    async def get_similar_entries(self, scope, embedding, limit=1):
        return self.similar_entries[:limit]

    async def set_semantic_entry(self, entry):
        self.semantic_writes.append(entry)


def _service(cache: CandidateCache) -> RetrievalService:
    service = RetrievalService.__new__(RetrievalService)
    service.retrieval_candidates_cache = cache
    service.logger = MagicMock()
    service.graph_provider = MagicMock()
    service.vector_db_service = MagicMock()
    service.vector_db_service.filter_collection = AsyncMock(return_value="org-filter")
    service._resolve_search_collections = AsyncMock(return_value=["records"])
    service._shared_candidate_cache_key = AsyncMock(return_value="cache-key")
    service._capabilities = SimpleNamespace(supports_sparse_vectors=False)
    service._cached_embedding_is_local = True
    service._cached_embedding_config_hash = "model-v1"
    embedding_model = SimpleNamespace(aembed_query=AsyncMock(return_value=[1.0, 0.0]))
    service.get_embedding_model_instance = AsyncMock(return_value=embedding_model)
    service._ensure_sparse_embedder = AsyncMock(return_value=None)
    return service


def _semantic_cache_key(query: str) -> str:
    return json.dumps(
        {
            "version": 1,
            "org_id": "org-1",
            "query": query,
            "collections": ["records"],
            "embedding_config": "model-v1",
            "vector_provider": "qdrant",
            "candidate_pool_size": 300,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _semantic_entry(query: str, candidate_cache_key: str) -> SemanticQueryEntry:
    return SemanticQueryEntry(
        scope=SemanticQueryScope(
            org_id="org-1",
            collection_name="records",
            embedding_config_hash="model-v1",
            vector_provider="qdrant",
        ),
        normalized_query=query,
        embedding=[1.0, 0.0],
        candidate_cache_key=candidate_cache_key,
    )


def _candidate(point_id: str, virtual_record_id: str, score: float = 0.9):
    return RetrievalCandidate(
        collection_name="records",
        point_id=point_id,
        virtual_record_id=virtual_record_id,
        score=score,
    )


@pytest.mark.asyncio
async def test_cache_hit_checks_current_user_before_fetching_candidate_content():
    cache = CandidateCache(
        [
            _candidate("denied-point", "denied-vrid"),
            _candidate("allowed-point", "allowed-vrid"),
        ]
    )
    service = _service(cache)
    service.graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
        return_value={"allowed-vrid": "record-1"}
    )
    service.vector_db_service.retrieve_points = AsyncMock(
        return_value=[
            VectorPoint(
                id="allowed-point",
                payload={
                    "page_content": "only Bob can read this",
                    "metadata": {
                        "virtualRecordId": "allowed-vrid",
                        "orgId": "org-1",
                    },
                },
            )
        ]
    )
    containers = SimpleNamespace(
        app_ids_trusted=frozenset(),
        record_group_ids_trusted=frozenset(),
    )

    result = await service._search_shared_candidates(
        query="policy",
        org_id="org-1",
        user_id="bob",
        limit=1,
        containers=containers,
        accessible_virtual_id_to_record_id={},
        scope_connector_ids=None,
    )

    assert result is not None
    search_results, accessible, degraded = result
    assert [item["metadata"]["virtualRecordId"] for item in search_results] == [
        "allowed-vrid"
    ]
    assert search_results[0]["content"] == "only Bob can read this"
    assert accessible == {"allowed-vrid": "record-1"}
    assert degraded is False
    service.graph_provider.filter_accessible_virtual_record_ids.assert_awaited_once()
    service.vector_db_service.retrieve_points.assert_awaited_once_with(
        "records", ["allowed-point"]
    )


@pytest.mark.asyncio
async def test_cache_hit_permission_outage_returns_degraded_without_fetching_content():
    cache = CandidateCache([_candidate("point-1", "vr-1")])
    service = _service(cache)
    service.graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
        side_effect=PermissionVerificationUnavailableError("graph unavailable")
    )
    service.vector_db_service.retrieve_points = AsyncMock()

    result = await service._search_shared_candidates(
        query="policy",
        org_id="org-1",
        user_id="bob",
        limit=1,
        containers=SimpleNamespace(
            app_ids_trusted=frozenset(),
            record_group_ids_trusted=frozenset(),
        ),
        accessible_virtual_id_to_record_id={},
        scope_connector_ids=None,
    )

    assert result == ([], {}, True)
    service.vector_db_service.retrieve_points.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_miss_searches_org_pool_and_caches_references():
    cache = CandidateCache()
    service = _service(cache)
    call_order: list[str] = []

    async def verify_permissions(*args, **kwargs):
        call_order.append("permissions")
        return {"allowed-vrid": "record-1"}

    async def retrieve_content(*args, **kwargs):
        call_order.append("content")
        return [
            VectorPoint(
                id="allowed-point",
                payload={
                    "page_content": "allowed text",
                    "metadata": {
                        "virtualRecordId": "allowed-vrid",
                        "orgId": "org-1",
                    },
                },
            )
        ]

    service.graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
        side_effect=verify_permissions
    )
    service.vector_db_service.retrieve_points = AsyncMock(side_effect=retrieve_content)
    plan_candidates = [
        _candidate("denied-point", "denied-vrid"),
        _candidate("allowed-point", "allowed-vrid"),
    ]
    service._execute_parallel_searches = AsyncMock()

    async def execute(*args, **kwargs):
        plan: _QueryPlan = kwargs["plan"]
        plan.candidates = plan_candidates
        return [
            {
                "score": 0.95,
                "metadata": {"virtualRecordId": "denied-vrid"},
            },
            {
                "score": 0.9,
                "metadata": {"virtualRecordId": "allowed-vrid"},
            },
        ]

    service._execute_parallel_searches.side_effect = execute

    result = await service._search_shared_candidates(
        query="policy",
        org_id="org-1",
        user_id="bob",
        limit=1,
        containers=SimpleNamespace(
            app_ids_trusted=frozenset(),
            record_group_ids_trusted=frozenset(),
        ),
        accessible_virtual_id_to_record_id={},
        scope_connector_ids=None,
    )

    assert result is not None
    assert result[0][0]["metadata"]["virtualRecordId"] == "allowed-vrid"
    service.vector_db_service.filter_collection.assert_awaited_once_with(
        must={"orgId": "org-1"}
    )
    assert service._execute_parallel_searches.await_args.args[0] == ["policy"]
    assert service._execute_parallel_searches.await_args.args[1] == "org-filter"
    assert service._execute_parallel_searches.await_args.args[2] == 300
    assert service._execute_parallel_searches.await_args.kwargs["with_payload"] == [
        "metadata.virtualRecordId",
        "metadata.orgId",
    ]
    assert service._execute_parallel_searches.await_args.kwargs["plan"].candidates == (
        plan_candidates
    )
    assert cache.writes == [("cache-key", plan_candidates)]
    service.vector_db_service.retrieve_points.assert_awaited_once_with(
        "records", ["allowed-point"]
    )
    assert call_order == ["permissions", "content"]


@pytest.mark.asyncio
async def test_multiple_collections_bypass_shared_candidate_cache():
    cache = CandidateCache([_candidate("point-1", "vr-1")])
    service = _service(cache)
    service._resolve_search_collections = AsyncMock(
        return_value=["records-a", "records-b"]
    )

    result = await service._search_shared_candidates(
        query="policy",
        org_id="org-1",
        user_id="bob",
        limit=1,
        containers=None,
        accessible_virtual_id_to_record_id={"vr-1": "record-1"},
        scope_connector_ids=None,
    )

    assert result is None
    assert cache.writes == []


@pytest.mark.asyncio
async def test_insufficient_allowed_candidates_falls_back_to_regular_search():
    cache = CandidateCache([_candidate("point-1", "denied-vrid")])
    service = _service(cache)
    service.graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
        return_value={}
    )
    service.vector_db_service.retrieve_points = AsyncMock()

    result = await service._search_shared_candidates(
        query="policy",
        org_id="org-1",
        user_id="bob",
        limit=1,
        containers=SimpleNamespace(
            app_ids_trusted=frozenset(),
            record_group_ids_trusted=frozenset(),
        ),
        accessible_virtual_id_to_record_id={},
        scope_connector_ids=None,
    )

    assert result is None
    service.vector_db_service.retrieve_points.assert_not_awaited()


@pytest.mark.asyncio
async def test_semantic_hit_checks_access_then_reranks_query():
    cache = CandidateCache()
    current_key = _semantic_cache_key("leave policy for contractors")
    source_key = _semantic_cache_key("contractor leave rules")
    cache.candidate_pools[source_key] = [
        _candidate("allowed-point", "allowed-vrid", score=0.1),
        _candidate("denied-point", "denied-vrid", score=0.99),
    ]
    cache.similar_entries = [
        SemanticQueryMatch(
            entry=_semantic_entry("contractor leave rules", source_key),
            similarity=0.96,
        )
    ]
    service = _service(cache)
    service._shared_candidate_cache_key = AsyncMock(return_value=current_key)
    call_order: list[str] = []

    async def verify(*args, **kwargs):
        call_order.append("permission")
        return {"allowed-vrid": "record-1"}

    async def rerank(*args, **kwargs):
        call_order.append("rerank")
        plan: _QueryPlan = kwargs["plan"]
        assert plan.dense == [[1.0, 0.0]]
        return [
            {
                "score": 0.88,
                "metadata": {
                    "virtualRecordId": "allowed-vrid",
                    "orgId": "org-1",
                },
                "content": "fresh ranking for current query",
            }
        ]

    service.graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
        side_effect=verify
    )
    service._execute_parallel_searches = AsyncMock(side_effect=rerank)

    result = await service._search_shared_candidates(
        query="leave policy for contractors",
        org_id="org-1",
        user_id="bob",
        limit=1,
        containers=SimpleNamespace(
            app_ids_trusted=frozenset(),
            record_group_ids_trusted=frozenset(),
        ),
        accessible_virtual_id_to_record_id={},
        scope_connector_ids=None,
    )

    assert result is not None
    assert result[0][0]["content"] == "fresh ranking for current query"
    assert result[1] == {"allowed-vrid": "record-1"}
    assert call_order == ["permission", "rerank"]
    service.vector_db_service.filter_collection.assert_awaited_once_with(
        must={
            "orgId": "org-1",
            "virtualRecordId": ["allowed-vrid"],
        }
    )
    assert service._execute_parallel_searches.await_args.args[0] == [
        "leave policy for contractors"
    ]
    assert service._execute_parallel_searches.await_args.args[2] == 1
    assert cache.semantic_writes[0].normalized_query == "leave policy for contractors"
    assert cache.semantic_writes[0].candidate_cache_key == source_key


@pytest.mark.asyncio
async def test_semantic_match_with_opposite_negation_uses_fresh_query_candidates():
    query = "employees not eligible for leave"
    current_key = _semantic_cache_key(query)
    source_key = _semantic_cache_key("employees eligible for leave")
    cache = CandidateCache()
    cache.candidate_pools[source_key] = [_candidate("source-point", "source-vrid")]
    cache.similar_entries = [
        SemanticQueryMatch(
            entry=_semantic_entry("employees eligible for leave", source_key),
            similarity=0.99,
        )
    ]
    service = _service(cache)
    service._shared_candidate_cache_key = AsyncMock(return_value=current_key)
    service._execute_parallel_searches = AsyncMock(return_value=[])

    result = await service._search_shared_candidates(
        query=query,
        org_id="org-1",
        user_id="bob",
        limit=1,
        containers=SimpleNamespace(
            app_ids_trusted=frozenset(),
            record_group_ids_trusted=frozenset(),
        ),
        accessible_virtual_id_to_record_id={},
        scope_connector_ids=None,
    )

    assert result is None
    assert cache.read_keys == [current_key]
    service._execute_parallel_searches.assert_awaited_once()
    assert service._execute_parallel_searches.await_args.args[0] == [query]
    assert service._execute_parallel_searches.await_args.args[2] == 300


@pytest.mark.asyncio
async def test_semantic_match_below_threshold_does_not_read_candidate_pool():
    query = "employee leave policy"
    current_key = _semantic_cache_key(query)
    source_key = _semantic_cache_key("leave eligibility policy")
    cache = CandidateCache()
    cache.candidate_pools[source_key] = [_candidate("source-point", "source-vrid")]
    cache.similar_entries = [
        SemanticQueryMatch(
            entry=_semantic_entry("leave eligibility policy", source_key),
            similarity=0.91,
        )
    ]
    service = _service(cache)
    service._shared_candidate_cache_key = AsyncMock(return_value=current_key)
    service._execute_parallel_searches = AsyncMock(return_value=[])

    result = await service._search_shared_candidates(
        query=query,
        org_id="org-1",
        user_id="bob",
        limit=1,
        containers=SimpleNamespace(
            app_ids_trusted=frozenset(),
            record_group_ids_trusted=frozenset(),
        ),
        accessible_virtual_id_to_record_id={},
        scope_connector_ids=None,
    )

    assert result is None
    assert cache.read_keys == [current_key]
    service._execute_parallel_searches.assert_awaited_once()
    assert service._execute_parallel_searches.await_args.args[2] == 300


@pytest.mark.asyncio
async def test_semantic_pool_with_too_few_authorized_candidates_falls_back():
    query = "policy for managers"
    current_key = _semantic_cache_key(query)
    source_key = _semantic_cache_key("manager policy")
    cache = CandidateCache()
    cache.candidate_pools[source_key] = [
        _candidate("allowed-point", "allowed-vrid"),
        _candidate("denied-point", "denied-vrid"),
    ]
    cache.similar_entries = [
        SemanticQueryMatch(
            entry=_semantic_entry("manager policy", source_key),
            similarity=0.97,
        )
    ]
    service = _service(cache)
    service._shared_candidate_cache_key = AsyncMock(return_value=current_key)
    service.graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
        return_value={"allowed-vrid": "record-1"}
    )
    service._execute_parallel_searches = AsyncMock()

    result = await service._search_shared_candidates(
        query=query,
        org_id="org-1",
        user_id="bob",
        limit=2,
        containers=SimpleNamespace(
            app_ids_trusted=frozenset(),
            record_group_ids_trusted=frozenset(),
        ),
        accessible_virtual_id_to_record_id={},
        scope_connector_ids=None,
    )

    assert result is None
    service.graph_provider.filter_accessible_virtual_record_ids.assert_awaited_once()
    service._execute_parallel_searches.assert_not_awaited()


def test_shared_candidate_search_only_accepts_plain_single_query():
    eligible = RetrievalService._shared_candidate_search_is_eligible

    assert eligible(["same  query"], {}, None, False, None)
    assert not eligible(["first", "second"], {}, None, False, None)
    assert not eligible(["query"], {"kb": ["kb-1"]}, None, False, None)
    assert not eligible(["query"], {}, ["vr-1"], False, None)
    assert not eligible(["query"], {}, None, True, None)
    assert not eligible(["query"], {}, None, False, {"start": 1})


def test_semantic_query_guard_rejects_changed_negation_and_numeric_qualifiers():
    signature = RetrievalService._semantic_query_guard_signature

    assert signature("employees eligible for leave") != signature(
        "employees not eligible for leave"
    )
    assert signature("leave policy for 2024") != signature("leave policy for 2025")
