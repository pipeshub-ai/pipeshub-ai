"""``RetrievalService.search_lexical``: keyword lookups under the same scope
and permission filter as ``search_with_filters``."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.retrieval_service as mod
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval.lexical_search import LexicalSearchUnavailableError
from app.services.graph_db.interface.graph_db_provider import AccessibleContainers
from app.services.vector_db.const.const import CONNECTOR_IDS_FIELD
from app.services.vector_db.models import (
    SearchResult,
    SparseVector,
    VectorDBCapabilities,
)


@pytest.fixture(autouse=True)
def _clear_user_cache() -> Iterator[None]:
    mod._user_cache.clear()
    yield
    mod._user_cache.clear()


def _point(vrid: str, text: str, org: str = "o1") -> SearchResult:
    return SearchResult(
        id=f"{vrid}-{abs(hash(text))}", score=1.0,
        payload={"page_content": text, "metadata": {"virtualRecordId": vrid, "orgId": org}},
    )


def _vector_db(*, sparse: bool, text: bool, batches: list[list[SearchResult]]) -> MagicMock:
    db = AsyncMock()
    db.get_capabilities = MagicMock(return_value=VectorDBCapabilities(
        supports_sparse_vectors=sparse, supports_server_side_text_search=text,
    ))
    db.get_service_name = MagicMock(return_value="fake")
    db.filter_collection = AsyncMock(side_effect=lambda **kwargs: kwargs)
    db.query_nearest_points = AsyncMock(return_value=batches)
    return db


def _service(vector_db, graph, *, containers_on: bool) -> mod.RetrievalService:
    registry = MagicMock()
    registry.strategy_name = "single"
    registry.resolve_for_query = AsyncMock(return_value=["records"])
    svc = mod.RetrievalService(
        logger=MagicMock(), config_service=AsyncMock(), collection_registry=registry,
        vector_db_service=vector_db, graph_provider=graph, blob_store=MagicMock(),
    )
    svc._container_filter_enabled = AsyncMock(return_value=containers_on)
    svc._excluded_demo_apps = AsyncMock(return_value=frozenset())
    svc._get_user_cached = AsyncMock(return_value={"_key": "u1"})
    svc._accessible_connector_names = AsyncMock(return_value=[])
    return svc


def _graph(**overrides) -> AsyncMock:
    graph = AsyncMock()
    graph.get_accessible_virtual_record_ids = AsyncMock(return_value={"vr-mine": "r-mine"})
    graph.get_accessible_containers = AsyncMock(return_value=AccessibleContainers(
        app_ids=frozenset({"wiki"}), app_ids_trusted=frozenset(),
    ))
    graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={"vr-mine": "r-mine"})
    for name, value in overrides.items():
        setattr(graph, name, value)
    return graph


class TestRequests:
    @pytest.mark.asyncio
    async def test_a_text_search_store_gets_text_only_requests(self) -> None:
        db = _vector_db(sparse=False, text=True, batches=[[_point("vr-mine", "p99 latency")]])
        svc = _service(db, _graph(), containers_on=False)

        result = await svc.search_lexical(["p99 latency"], "u1", "o1")

        (request,) = db.query_nearest_points.await_args.kwargs["requests"]
        assert request.dense_query is None and request.sparse_query is None
        assert request.text_query == "p99 latency"
        assert [h.virtual_record_id for h in result.hits[0]] == ["vr-mine"]

    @pytest.mark.asyncio
    async def test_a_sparse_store_gets_sparse_only_requests(self) -> None:
        db = _vector_db(sparse=True, text=False, batches=[[_point("vr-mine", "p99")]])
        svc = _service(db, _graph(), containers_on=False)
        embedder = AsyncMock()
        embedder.embed_query = AsyncMock(return_value=SparseVector(indices=[7], values=[1.0]))
        svc._ensure_sparse_embedder = AsyncMock(return_value=embedder)

        await svc.search_lexical(["p99"], "u1", "o1")

        (request,) = db.query_nearest_points.await_args.kwargs["requests"]
        assert request.dense_query is None and request.text_query is None
        assert request.sparse_query.indices == [7]

    @pytest.mark.asyncio
    async def test_a_query_with_no_indexed_term_is_not_sent(self) -> None:
        # A request with no leg would be a filter-only query: arbitrary chunks.
        db = _vector_db(sparse=True, text=False, batches=[[_point("vr-mine", "p99")]])
        svc = _service(db, _graph(), containers_on=False)
        embedder = AsyncMock()
        embedder.embed_query = AsyncMock(side_effect=[
            SparseVector(indices=[], values=[]), SparseVector(indices=[7], values=[1.0]),
        ])
        svc._ensure_sparse_embedder = AsyncMock(return_value=embedder)

        result = await svc.search_lexical(["the of", "p99"], "u1", "o1")

        assert len(db.query_nearest_points.await_args.kwargs["requests"]) == 1
        assert result.empty_query == [True, False]
        assert result.hits[0] == [] and len(result.hits[1]) == 1

    @pytest.mark.asyncio
    async def test_a_store_without_a_lexical_index_says_so(self) -> None:
        db = _vector_db(sparse=False, text=False, batches=[])
        svc = _service(db, _graph(), containers_on=False)

        assert await svc.lexical_search_available() is False
        with pytest.raises(LexicalSearchUnavailableError):
            await svc.search_lexical(["p99"], "u1", "o1")
        db.query_nearest_points.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_full_batch_is_reported_as_capped(self) -> None:
        db = _vector_db(sparse=False, text=True, batches=[[_point("vr-mine", f"p99 {i}") for i in range(3)]])
        svc = _service(db, _graph(), containers_on=False)

        result = await svc.search_lexical(["p99"], "u1", "o1", limit=3)

        assert result.capped == [True]


class TestPermissions:
    @pytest.mark.asyncio
    async def test_record_id_path_filters_by_org_and_accessible_records(self) -> None:
        db = _vector_db(sparse=False, text=True, batches=[[_point("vr-mine", "p99")]])
        svc = _service(db, _graph(), containers_on=False)

        await svc.search_lexical(["p99"], "u1", "o1", filter_groups={"apps": ["wiki"]})

        assert db.filter_collection.await_args.kwargs == {
            "must": {"orgId": "o1", "virtualRecordId": ["vr-mine"]},
        }
        graph_call = svc.graph_provider.get_accessible_virtual_record_ids.await_args.kwargs
        assert graph_call["user_id"] == "u1" and graph_call["org_id"] == "o1"
        assert graph_call["filters"] == {"apps": ["wiki"]}

    @pytest.mark.asyncio
    async def test_container_hits_are_adjudicated_per_record(self) -> None:
        db = _vector_db(sparse=False, text=True, batches=[[
            _point("vr-mine", "zebrafinch here"), _point("vr-not-mine", "zebrafinch there"),
        ]])
        svc = _service(db, _graph(), containers_on=True)

        result = await svc.search_lexical(["zebrafinch"], "u1", "o1")

        kwargs = db.filter_collection.await_args.kwargs
        assert kwargs["must"] == {"orgId": "o1"}
        assert kwargs["should"][CONNECTOR_IDS_FIELD] == ["wiki"]
        assert [h.virtual_record_id for h in result.hits[0]] == ["vr-mine"]
        svc.graph_provider.filter_accessible_virtual_record_ids.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_narrowing_never_reaches_past_the_accessible_records(self) -> None:
        db = _vector_db(sparse=False, text=True, batches=[[]])
        svc = _service(db, _graph(), containers_on=False)

        await svc.search_lexical(["p99"], "u1", "o1", virtual_record_ids=["vr-mine", "vr-not-mine"])

        assert db.filter_collection.await_args.kwargs["must"]["virtualRecordId"] == ["vr-mine"]

    @pytest.mark.asyncio
    async def test_a_user_who_reaches_nothing_gets_nothing_without_a_query(self) -> None:
        db = _vector_db(sparse=False, text=True, batches=[[_point("vr-x", "p99")]])
        graph = _graph(get_accessible_virtual_record_ids=AsyncMock(return_value={}))
        svc = _service(db, graph, containers_on=False)

        result = await svc.search_lexical(["p99"], "u1", "o1")

        assert result.hits == [[]]
        db.query_nearest_points.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unreadable_permission_graph_raises(self) -> None:
        db = _vector_db(sparse=False, text=True, batches=[[_point("vr-mine", "p99")]])
        graph = _graph(filter_accessible_virtual_record_ids=AsyncMock(
            side_effect=PermissionVerificationUnavailableError("graph down"),
        ))
        svc = _service(db, graph, containers_on=True)

        with pytest.raises(PermissionVerificationUnavailableError):
            await svc.search_lexical(["p99"], "u1", "o1")


class TestSearchWithFiltersUnchanged:
    @pytest.mark.asyncio
    async def test_the_shared_permission_filter_still_narrows_semantic_search(self) -> None:
        db = _vector_db(sparse=False, text=False, batches=[[]])
        svc = _service(db, _graph(), containers_on=False)
        with patch.object(svc, "_execute_parallel_searches", AsyncMock(return_value=[])):
            await svc.search_with_filters(
                queries=["q"], user_id="u1", org_id="o1",
                virtual_record_ids_from_tool=["vr-mine", "vr-not-mine"],
            )

        assert db.filter_collection.await_args.kwargs == {
            "must": {"orgId": "o1", "virtualRecordId": ["vr-mine"]},
        }


class TestSeveralCollections:
    def _svc(self, db) -> mod.RetrievalService:
        svc = _service(db, _graph(get_accessible_virtual_record_ids=AsyncMock(
            return_value={"vr-a": "r-a", "vr-b": "r-b"},
        )), containers_on=False)
        svc.collection_registry.resolve_for_query = AsyncMock(return_value=["col-1", "col-2"])
        return svc

    @pytest.mark.asyncio
    async def test_a_full_collection_is_capped_even_when_merging_shortens_the_list(self) -> None:
        # col-1 returns its whole limit as two points of one block; merging
        # with col-2 dedupes them to a list shorter than the limit.
        full = [_point("vr-a", "p99 one"), _point("vr-a", "p99 one again")]
        for point in full:
            point.payload["metadata"]["blockId"] = "b1"
        db = _vector_db(sparse=False, text=True, batches=[])
        db.query_nearest_points = AsyncMock(side_effect=[[full], [full[:1]]])

        result = await self._svc(db).search_lexical(["p99"], "u1", "o1", limit=2)

        assert len(result.hits[0]) < 2
        assert result.capped == [True]

    @pytest.mark.asyncio
    async def test_a_collection_that_failed_is_counted_and_the_rest_kept(self) -> None:
        db = _vector_db(sparse=False, text=True, batches=[])
        db.query_nearest_points = AsyncMock(side_effect=[RuntimeError("shard down"), [[_point("vr-b", "p99")]]])

        result = await self._svc(db).search_lexical(["p99"], "u1", "o1")

        assert [h.virtual_record_id for h in result.hits[0]] == ["vr-b"]
        assert (result.failed_collections, result.searched_collections) == (1, 2)

    @pytest.mark.asyncio
    async def test_every_collection_failing_raises(self) -> None:
        db = _vector_db(sparse=False, text=True, batches=[])
        db.query_nearest_points = AsyncMock(side_effect=RuntimeError("store down"))

        with pytest.raises(RuntimeError):
            await self._svc(db).search_lexical(["p99"], "u1", "o1")


class TestStoreSize:
    @pytest.mark.asyncio
    async def test_counts_every_collection_the_search_reads(self) -> None:
        from app.services.vector_db.models import VectorCollectionInfo

        db = _vector_db(sparse=False, text=True, batches=[])
        db.get_collection_info = AsyncMock(side_effect=lambda name: VectorCollectionInfo(
            name=name, exists=True, points_count={"col-1": 120, "col-2": 30}[name],
        ))
        svc = _service(db, _graph(), containers_on=False)
        svc.collection_registry.resolve_for_query = AsyncMock(return_value=["col-1", "col-2"])

        assert await svc.lexical_store_chunks("o1", "u1") == 150

    @pytest.mark.asyncio
    async def test_a_collection_with_no_count_makes_the_size_unknown(self) -> None:
        db = _vector_db(sparse=False, text=True, batches=[])
        db.get_collection_info = AsyncMock(side_effect=[MagicMock(points_count=120), MagicMock(points_count=None)])
        svc = _service(db, _graph(), containers_on=False)
        svc.collection_registry.resolve_for_query = AsyncMock(return_value=["col-1", "col-2"])

        assert await svc.lexical_store_chunks("o1", "u1") is None
