"""Keyword (lexical) scoring must stay on for every collection, new or old.

The sparse leg on Qdrant is fastembed's ``Qdrant/bm25``, which emits
term-frequency weights and relies on the collection for IDF; created without
it, a common word scores like a rare name and hybrid search ranked *below*
dense-only on PipesHub's own index. OpenSearch indexed ``page_content`` with
the standard analyzer only, so "running" never matched "run".

These tests pin the fixes: the config every collection is created from, the
query each provider issues, and the in-place migration of collections created
before the fix. Redis stems and applies IDF natively, but intersected every
query word, so its lexical leg matched almost nothing; its query shape is
pinned too. Behaviour against real servers is covered by
``tests/integration/vector_db/test_lexical_scoring_contract.py``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("qdrant_client", reason="qdrant_client not installed")
pytest.importorskip("opensearchpy", reason="opensearch-py not installed")

from opensearchpy.exceptions import NotFoundError
from qdrant_client.http.models import Modifier, SparseIndexParams, SparseVectorParams

from app.services.vector_db.collection_manifest import CollectionManifestStore
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    CollectionConfig,
    HybridSearchRequest,
    VectorCollectionInfo,
    VectorDBCapabilities,
)
from app.services.vector_db.opensearch.config import OpenSearchConfig
from app.services.vector_db.opensearch.opensearch import OpenSearchService
from app.services.vector_db.opensearch.utils import (
    PAGE_CONTENT_MAPPING,
    STEMMED_PAGE_CONTENT_FIELD,
    OpenSearchUtils,
)
from app.services.vector_db.qdrant.config import QdrantConfig
from app.services.vector_db.qdrant.qdrant import QdrantService
from app.services.vector_db.redis.utils import redisearch_any_term_query
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import RecordContext
from tests.support.vector_db import make_config_service, make_manifest_store


@pytest.mark.asyncio
async def test_interface_default_changes_nothing() -> None:
    """Providers whose native text search already stems and applies IDF
    (Redis) inherit this rather than stubbing it."""
    assert await IVectorDBService.reconcile_lexical_scoring(MagicMock(), "records") is None


# ---------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------


@pytest.fixture
def qdrant() -> QdrantService:
    service = QdrantService(QdrantConfig(host="localhost", port=6333))
    service.client = AsyncMock()
    return service


def _qdrant_info(sparse_vectors: object) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(params=SimpleNamespace(sparse_vectors=sparse_vectors))
    )


class TestQdrantCreatesWithIdf:
    @pytest.mark.asyncio
    async def test_default_config_sets_idf_modifier(self, qdrant) -> None:
        await qdrant.create_collection("records", CollectionConfig())

        sparse = qdrant.client.create_collection.call_args.kwargs["sparse_vectors_config"]
        assert sparse["sparse"].modifier == Modifier.IDF

    @pytest.mark.asyncio
    async def test_dense_only_collection_has_no_sparse_config(self, qdrant) -> None:
        await qdrant.create_collection("records", CollectionConfig(enable_sparse=False))

        assert qdrant.client.create_collection.call_args.kwargs["sparse_vectors_config"] is None


class TestQdrantReconcileLexicalScoring:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("legacy_modifier", [None, Modifier.NONE])
    async def test_enables_idf_on_legacy_collection(self, qdrant, legacy_modifier) -> None:
        qdrant.client.get_collection = AsyncMock(return_value=_qdrant_info(
            {"sparse": SparseVectorParams(
                index=SparseIndexParams(on_disk=True), modifier=legacy_modifier,
            )}
        ))

        changed = await qdrant.reconcile_lexical_scoring("records", CollectionConfig())

        assert changed == "sparse.modifier"
        sent = qdrant.client.update_collection.call_args.kwargs
        assert sent["collection_name"] == "records"
        params = sent["sparse_vectors_config"]["sparse"]
        assert params.modifier == Modifier.IDF
        # Only the scoring changes; the index's storage settings are left alone.
        assert params.index is None

    @pytest.mark.asyncio
    async def test_no_op_when_idf_already_on(self, qdrant) -> None:
        qdrant.client.get_collection = AsyncMock(return_value=_qdrant_info(
            {"sparse": SparseVectorParams(modifier=Modifier.IDF)}
        ))

        assert await qdrant.reconcile_lexical_scoring("records", CollectionConfig()) is None
        qdrant.client.update_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_op_on_collection_without_sparse_vectors(self, qdrant) -> None:
        qdrant.client.get_collection = AsyncMock(return_value=_qdrant_info(None))

        assert await qdrant.reconcile_lexical_scoring("records", CollectionConfig()) is None
        qdrant.client.update_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_op_when_config_has_no_sparse_leg(self, qdrant) -> None:
        qdrant.client.get_collection = AsyncMock(return_value=_qdrant_info(
            {"sparse": SparseVectorParams(modifier=None)}
        ))

        config = CollectionConfig(enable_sparse=False)
        assert await qdrant.reconcile_lexical_scoring("records", config) is None
        qdrant.client.update_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_requires_connection(self) -> None:
        service = QdrantService(QdrantConfig(host="localhost", port=6333))
        with pytest.raises(RuntimeError, match="not connected"):
            await service.reconcile_lexical_scoring("records", CollectionConfig())


# ---------------------------------------------------------------------------
# OpenSearch
# ---------------------------------------------------------------------------

_LEGACY_MAPPINGS = {"properties": {"page_content": {"type": "text"}}}
_CURRENT_MAPPINGS = {"properties": {"page_content": PAGE_CONTENT_MAPPING}}


def _mapping_response(mappings: dict, index: str = "records") -> dict:
    return {index: {"mappings": mappings}}


@pytest.fixture
def opensearch() -> OpenSearchService:
    service = OpenSearchService(OpenSearchConfig(host="localhost", port=9200))
    client = AsyncMock()
    client.indices = AsyncMock()
    client.indices.exists = AsyncMock(return_value=False)
    client.indices.get_mapping = AsyncMock(return_value=_mapping_response(_CURRENT_MAPPINGS))
    client.transport = AsyncMock()
    client.tasks = AsyncMock()
    service.client = client
    return service


def _meta_written(opensearch: OpenSearchService) -> dict:
    """The ``_meta`` body of the last put_mapping call."""
    return opensearch.client.indices.put_mapping.call_args.kwargs["body"]["_meta"]


class TestOpenSearchIndexesStemmedText:
    @pytest.mark.asyncio
    async def test_new_index_maps_exact_and_stemmed_page_content(self, opensearch) -> None:
        await opensearch.create_collection("records", CollectionConfig(embedding_size=8))

        body = opensearch.client.indices.create.call_args.kwargs["body"]
        page_content = body["mappings"]["properties"]["page_content"]
        # The parent keeps the standard analyzer so IDs match as written.
        assert page_content["type"] == "text"
        assert "analyzer" not in page_content
        assert page_content["fields"]["stemmed"] == {"type": "text", "analyzer": "english"}

    def test_lexical_leg_queries_exact_and_stemmed_fields(self) -> None:
        body = OpenSearchUtils.build_hybrid_query(
            HybridSearchRequest(text_query="running migrations", limit=5)
        )

        assert body["query"] == {
            "multi_match": {
                "query": "running migrations",
                "fields": ["page_content", STEMMED_PAGE_CONTENT_FIELD],
                "type": "most_fields",
            }
        }

    def test_hybrid_query_keeps_filter_around_lexical_leg(self) -> None:
        from app.services.vector_db.models import FieldCondition, FilterExpression

        body = OpenSearchUtils.build_hybrid_query(HybridSearchRequest(
            text_query="error 0x80070005",
            dense_query=[0.1] * 8,
            filter=FilterExpression(must=[FieldCondition(key="metadata.orgId", value="o1")]),
            limit=5,
        ))

        lexical = body["query"]["hybrid"]["queries"][0]
        assert lexical["bool"]["must"][0]["multi_match"]["fields"] == [
            "page_content", STEMMED_PAGE_CONTENT_FIELD,
        ]
        assert lexical["bool"]["filter"]


class TestOpenSearchReconcileLexicalScoring:
    @pytest.mark.asyncio
    async def test_adds_stemmed_subfield_to_legacy_index(self, opensearch) -> None:
        opensearch.client.indices.get_mapping = AsyncMock(
            return_value=_mapping_response(_LEGACY_MAPPINGS)
        )

        changed = await opensearch.reconcile_lexical_scoring("records")

        assert changed == STEMMED_PAGE_CONTENT_FIELD
        opensearch.client.indices.put_mapping.assert_awaited_once_with(
            index="records",
            body={"properties": {"page_content": PAGE_CONTENT_MAPPING}},
        )

    @pytest.mark.asyncio
    async def test_no_op_when_subfield_present(self, opensearch) -> None:
        assert await opensearch.reconcile_lexical_scoring("records") is None
        opensearch.client.indices.put_mapping.assert_not_awaited()


class TestOpenSearchStemmedBackfill:
    """The backfill rewrites documents, so it lives in the opt-in
    ``reconcile_storage_layout`` and advances one step per call."""

    def _with_meta(self, opensearch: OpenSearchService, meta: dict) -> None:
        opensearch.client.indices.get_mapping = AsyncMock(return_value=_mapping_response(
            {**_CURRENT_MAPPINGS, "_meta": meta}
        ))

    @pytest.mark.asyncio
    async def test_waits_for_the_subfield_mapping(self, opensearch) -> None:
        opensearch.client.indices.get_mapping = AsyncMock(
            return_value=_mapping_response(_LEGACY_MAPPINGS)
        )

        assert await opensearch.reconcile_storage_layout("records") is None
        opensearch.client.update_by_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_starts_throttled_backfill_of_documents_missing_the_field(self, opensearch) -> None:
        self._with_meta(opensearch, {"owner": "kept"})
        opensearch.client.update_by_query = AsyncMock(return_value={"task": "node:1"})

        changed = await opensearch.reconcile_storage_layout("records")

        assert changed == f"{STEMMED_PAGE_CONTENT_FIELD}.backfill"
        sent = opensearch.client.update_by_query.call_args.kwargs
        assert sent["body"]["query"] == {
            "bool": {"must_not": {"exists": {"field": STEMMED_PAGE_CONTENT_FIELD}}}
        }
        assert sent["params"]["conflicts"] == "proceed"
        assert sent["params"]["wait_for_completion"] == "false"
        assert sent["params"]["requests_per_second"] > 0
        assert _meta_written(opensearch) == {
            "owner": "kept",
            "pipeshub_stemmed_backfill": {"task": "node:1"},
        }

    @pytest.mark.asyncio
    async def test_leaves_a_running_task_alone(self, opensearch) -> None:
        self._with_meta(opensearch, {"pipeshub_stemmed_backfill": {"task": "node:1"}})
        opensearch.client.tasks.get = AsyncMock(return_value={"completed": False})

        assert await opensearch.reconcile_storage_layout("records") is None
        opensearch.client.update_by_query.assert_not_awaited()
        opensearch.client.indices.put_mapping.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_records_success(self, opensearch) -> None:
        self._with_meta(opensearch, {"pipeshub_stemmed_backfill": {"task": "node:1"}})
        opensearch.client.tasks.get = AsyncMock(
            return_value={"completed": True, "response": {"failures": []}}
        )

        assert await opensearch.reconcile_storage_layout("records") is None
        assert _meta_written(opensearch) == {"pipeshub_stemmed_backfill": {"done": True}}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("task", [
        {"completed": True, "response": {"failures": [{"cause": "x"}]}},
        {"completed": True, "error": {"type": "task_cancelled_exception"}},
    ])
    async def test_forgets_a_failed_task_so_it_is_retried(self, opensearch, task) -> None:
        self._with_meta(opensearch, {"pipeshub_stemmed_backfill": {"task": "node:1"}})
        opensearch.client.tasks.get = AsyncMock(return_value=task)

        assert await opensearch.reconcile_storage_layout("records") is None
        assert _meta_written(opensearch) == {"pipeshub_stemmed_backfill": {}}

    @pytest.mark.asyncio
    async def test_forgets_a_lost_task_so_it_is_retried(self, opensearch) -> None:
        self._with_meta(opensearch, {"pipeshub_stemmed_backfill": {"task": "node:1"}})
        opensearch.client.tasks.get = AsyncMock(side_effect=NotFoundError(404, "missing", {}))

        assert await opensearch.reconcile_storage_layout("records") is None
        assert _meta_written(opensearch) == {"pipeshub_stemmed_backfill": {}}

    @pytest.mark.asyncio
    async def test_no_op_once_done(self, opensearch) -> None:
        self._with_meta(opensearch, {"pipeshub_stemmed_backfill": {"done": True}})

        assert await opensearch.reconcile_storage_layout("records") is None
        opensearch.client.update_by_query.assert_not_awaited()
        opensearch.client.tasks.get.assert_not_awaited()


# ---------------------------------------------------------------------------
# Redis (native stemming and IDF; the query shape is what can break)
# ---------------------------------------------------------------------------


class TestRedisKeywordQuery:
    def test_words_are_unioned_not_intersected(self) -> None:
        assert redisearch_any_term_query("Bella La Rosa born") == "(Bella | La | Rosa | born)"

    def test_single_word_needs_no_grouping(self) -> None:
        assert redisearch_any_term_query("ERR_CONN_REFUSED") == "ERR_CONN_REFUSED"

    def test_operators_inside_words_are_escaped(self) -> None:
        assert redisearch_any_term_query("user@example.com x-ray") == (
            r"(user\@example\.com | x\-ray)"
        )

    @pytest.mark.parametrize("query", ["", "   "])
    def test_blank_query_is_empty(self, query) -> None:
        assert redisearch_any_term_query(query) == ""

    def test_filter_narrows_the_whole_union(self) -> None:
        from app.services.vector_db.redis.redis_vector import _combine_text_and_filter

        combined = _combine_text_and_filter(
            redisearch_any_term_query("run migration"), "@metadata_orgId:{o1}"
        )
        assert combined == "(run | migration) @metadata_orgId:{o1}"


# ---------------------------------------------------------------------------
# CollectionRegistry
# ---------------------------------------------------------------------------


def _registry(
    vdb: AsyncMock, manifest_store: CollectionManifestStore | None = None
) -> CollectionRegistry:
    return CollectionRegistry(
        vector_db_service=vdb,
        strategy=SingleCollectionStrategy(),
        collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
        manifest_store=manifest_store or CollectionManifestStore(make_config_service(), MagicMock()),
        logger=MagicMock(),
    )


def _existing_vdb(dimension: int = 1024) -> AsyncMock:
    vdb = AsyncMock()
    vdb.get_collection_info = AsyncMock(
        return_value=VectorCollectionInfo(name="records", exists=True, dense_dimension=dimension)
    )
    vdb.reconcile_lexical_scoring = AsyncMock(return_value=None)
    return vdb


class TestRegistryReconcilesLexicalScoring:
    @pytest.mark.asyncio
    async def test_write_path_reconciles_an_existing_collection_once(self) -> None:
        vdb = _existing_vdb(dimension=1024)
        registry = _registry(vdb)

        await registry.ensure_collection(RecordContext(org_id="o1"), embedding_size=1024)
        registry.invalidate("records")
        await registry.ensure_collection(RecordContext(org_id="o1"), embedding_size=1024)

        vdb.reconcile_lexical_scoring.assert_awaited_once()
        sent = vdb.reconcile_lexical_scoring.call_args.kwargs
        assert sent["collection_name"] == "records"
        assert sent["config"].embedding_size == 1024
        assert sent["config"].sparse_idf is True

    @pytest.mark.asyncio
    async def test_write_path_survives_a_reconcile_failure(self) -> None:
        vdb = _existing_vdb()
        vdb.reconcile_lexical_scoring = AsyncMock(side_effect=RuntimeError("boom"))
        registry = _registry(vdb)

        assert await registry.ensure_collection(
            RecordContext(org_id="o1"), embedding_size=1024
        ) == "records"

    @pytest.mark.asyncio
    async def test_startup_reconciles_every_managed_collection(self) -> None:
        vdb = AsyncMock()
        vdb.reconcile_lexical_scoring = AsyncMock(
            side_effect=lambda collection_name, config: (
                None if collection_name == "current" else "sparse.modifier"
            )
        )
        registry = _registry(vdb, make_manifest_store(("legacy", "current")))

        assert await registry.reconcile_lexical_scoring() == ["legacy"]
        assert vdb.reconcile_lexical_scoring.await_count == 2

    @pytest.mark.asyncio
    async def test_startup_skips_a_failing_collection_and_retries_it_later(self) -> None:
        vdb = AsyncMock()
        vdb.reconcile_lexical_scoring = AsyncMock(
            side_effect=[RuntimeError("unreachable"), "sparse.modifier", "sparse.modifier"]
        )
        registry = _registry(vdb, make_manifest_store(("a", "b")))

        assert await registry.reconcile_lexical_scoring() == ["b"]
        # "a" was never marked done, so it is tried again; "b" is not.
        assert await registry.reconcile_lexical_scoring() == ["a"]
