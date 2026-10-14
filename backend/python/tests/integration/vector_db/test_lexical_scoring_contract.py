"""Keyword search behaves like BM25 on every vector DB provider.

Guards against three regressions that shipped once already: Qdrant's sparse
leg created without IDF (a common word scored like a rare name, so hybrid
search ranked below dense-only), OpenSearch indexing ``page_content`` without
stemming ("running" never matched "run"), and Redis intersecting every query
word (a multi-keyword query matched almost nothing). Queries here are lexical-only so the
assertions are about keyword scoring alone.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_lexical_scoring_contract.py -m integration --timeout=180
"""

import pytest

from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    CollectionConfig,
    HybridSearchRequest,
    VectorPoint,
)
from app.services.vector_db.sparse_embeddings import SparseEmbedder
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import DIM, make_dense, point_id, wait_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

# OpenSearch indexes refresh every 30s.
VISIBILITY_TIMEOUT = 45.0

RARE_NAME_DOC = "Bella La Rosa was crowned Miss Venezuela in 1970 in Caracas."
# Short and repetitive on the query's common words: with term frequency alone
# these outscore the one document that names the person, which is exactly the
# failure IDF exists to prevent.
COMMON_WORD_DOCS = [
    f"Born on this date. Birth date and born date noted, entry {i}." for i in range(30)
]
RARE_NAME_QUERY = "Bella La Rosa born birth date"

STEMMED_DOC = "The platform engineers were running database migrations overnight."
IDENTIFIER_DOC = "Deploy failed with ERR_CONN_REFUSED when the worker started."
OTHER_DOCS = [
    "Quarterly revenue grew in the enterprise segment.",
    "The connection was refused because the error budget ran out.",
    "Hiring plan for the platform team next quarter.",
]


@pytest.fixture(scope="module")
async def sparse_embedder() -> SparseEmbedder:
    embedder = SparseEmbedder()
    try:
        await embedder._ensure_initialized()
    except Exception as exc:
        pytest.skip(f"fastembed Qdrant/bm25 model unavailable: {exc}")
    return embedder


async def _points(svc, embedder, texts: list[str]) -> list[VectorPoint]:
    """Identical dense vectors, so nothing but the keyword leg can order them."""
    sparse = (
        await embedder.embed_documents(texts)
        if svc.get_capabilities().supports_sparse_vectors
        else [None] * len(texts)
    )
    return [
        VectorPoint(
            id=point_id(text),
            dense_vector=make_dense([1.0]),
            sparse_vector=sparse_vector,
            payload={"page_content": text, "metadata": {"orgId": "org-lexical"}},
        )
        for text, sparse_vector in zip(texts, sparse)
    ]


async def _keyword_search(svc, embedder, col: str, query: str, limit: int = 5) -> list[str]:
    """Lexical-only search through the provider's own lexical leg, as retrieval does."""
    caps = svc.get_capabilities()
    request = HybridSearchRequest(
        sparse_query=(await embedder.embed_query(query)) if caps.supports_sparse_vectors else None,
        text_query=query if caps.supports_server_side_text_search else None,
        limit=limit,
    )
    results = (await svc.query_nearest_points(col, [request]))[0]
    return [r.payload.get("page_content", "") for r in results]


async def _searchable(svc, embedder, col: str, query: str) -> list[str]:
    return await wait_for(
        lambda: _keyword_search(svc, embedder, col, query), timeout=VISIBILITY_TIMEOUT
    )


class _LexicalScoringContract:
    """Subclasses inject ``vector_service`` from a module-scoped fixture."""

    async def test_rare_name_outranks_repeated_common_words(self, vector_service, sparse_embedder) -> None:
        col = make_collection(f"{vector_service.get_service_name()}_idf")
        try:
            await vector_service.create_collection(col, CollectionConfig(embedding_size=DIM))
            await vector_service.upsert_points(
                col, await _points(vector_service, sparse_embedder, [RARE_NAME_DOC, *COMMON_WORD_DOCS])
            )

            ranked = await _searchable(vector_service, sparse_embedder, col, RARE_NAME_QUERY)

            assert ranked[0] == RARE_NAME_DOC
        finally:
            await vector_service.delete_collection(col)

    async def test_inflected_forms_match(self, vector_service, sparse_embedder) -> None:
        col = make_collection(f"{vector_service.get_service_name()}_stem")
        try:
            await vector_service.create_collection(col, CollectionConfig(embedding_size=DIM))
            await vector_service.upsert_points(
                col, await _points(vector_service, sparse_embedder, [STEMMED_DOC, *OTHER_DOCS])
            )

            ranked = await _searchable(vector_service, sparse_embedder, col, "run migration")

            assert ranked[0] == STEMMED_DOC
        finally:
            await vector_service.delete_collection(col)

    async def test_identifiers_still_match_exactly(self, vector_service, sparse_embedder) -> None:
        col = make_collection(f"{vector_service.get_service_name()}_ident")
        try:
            await vector_service.create_collection(col, CollectionConfig(embedding_size=DIM))
            await vector_service.upsert_points(
                col, await _points(vector_service, sparse_embedder, [IDENTIFIER_DOC, *OTHER_DOCS])
            )

            ranked = await _searchable(vector_service, sparse_embedder, col, "ERR_CONN_REFUSED")

            assert ranked[0] == IDENTIFIER_DOC
        finally:
            await vector_service.delete_collection(col)

    async def test_reconcile_on_a_current_collection_is_a_no_op(self, vector_service) -> None:
        col = make_collection(f"{vector_service.get_service_name()}_noop")
        try:
            config = CollectionConfig(embedding_size=DIM)
            await vector_service.create_collection(col, config)

            assert await vector_service.reconcile_lexical_scoring(col, config) is None
        finally:
            await vector_service.delete_collection(col)


class TestQdrantLexicalScoring(_LexicalScoringContract):
    @pytest.fixture
    async def vector_service(self, qdrant_service: IVectorDBService) -> IVectorDBService:
        return qdrant_service

    async def test_legacy_collection_gains_idf_in_place(self, qdrant_service, sparse_embedder) -> None:
        """A collection created before the fix is repaired without re-indexing."""
        col = make_collection("qdrant_idf_legacy")
        try:
            await qdrant_service.create_collection(
                col, CollectionConfig(embedding_size=DIM, sparse_idf=False)
            )
            await qdrant_service.upsert_points(
                col, await _points(qdrant_service, sparse_embedder, [RARE_NAME_DOC, *COMMON_WORD_DOCS])
            )
            before = await _keyword_search(qdrant_service, sparse_embedder, col, RARE_NAME_QUERY)
            # The corpus has to expose the bug, or the assertion below proves nothing.
            assert before[0] != RARE_NAME_DOC

            changed = await qdrant_service.reconcile_lexical_scoring(
                col, CollectionConfig(embedding_size=DIM)
            )

            assert changed == "sparse.modifier"
            after = await _keyword_search(qdrant_service, sparse_embedder, col, RARE_NAME_QUERY)
            assert after[0] == RARE_NAME_DOC
        finally:
            await qdrant_service.delete_collection(col)


class TestOpenSearchLexicalScoring(_LexicalScoringContract):
    @pytest.fixture
    async def vector_service(self, opensearch_service: IVectorDBService) -> IVectorDBService:
        return opensearch_service

    async def test_legacy_index_gains_stemming_after_backfill(self, opensearch_service, sparse_embedder) -> None:
        """An index created with the old mapping gets the stemmed field and,
        once the opt-in backfill runs, its existing documents match stems."""
        col = make_collection("opensearch_stem_legacy")
        client = opensearch_service.client
        try:
            await client.indices.create(index=col, body={
                "mappings": {"properties": {"page_content": {"type": "text"}}},
            })
            await opensearch_service.upsert_points(
                col, await _points(opensearch_service, sparse_embedder, [STEMMED_DOC, *OTHER_DOCS])
            )
            await client.indices.refresh(index=col)
            assert STEMMED_DOC not in await _keyword_search(
                opensearch_service, sparse_embedder, col, "run migration"
            )

            config = CollectionConfig(embedding_size=DIM)
            assert await opensearch_service.reconcile_lexical_scoring(col, config)
            assert await opensearch_service.reconcile_storage_layout(col, config)

            async def backfill_recorded() -> bool:
                await opensearch_service.reconcile_storage_layout(col, config)
                mapping = await client.indices.get_mapping(index=col)
                meta = mapping[col]["mappings"].get("_meta") or {}
                return bool((meta.get("pipeshub_stemmed_backfill") or {}).get("done"))

            await wait_for(backfill_recorded, timeout=60.0, poll=1.0)
            await client.indices.refresh(index=col)

            ranked = await _keyword_search(opensearch_service, sparse_embedder, col, "run migration")
            assert ranked[0] == STEMMED_DOC
        finally:
            await opensearch_service.delete_collection(col)


class TestRedisLexicalScoring(_LexicalScoringContract):
    @pytest.fixture
    async def vector_service(self, redis_service: IVectorDBService) -> IVectorDBService:
        return redis_service
