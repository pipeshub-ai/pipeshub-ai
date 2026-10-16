"""Exact-text search through each vector store's own lexical index.

Runs the product path end to end on a real store: ``RetrievalService.search_lexical``
(the same scope and permission filter as semantic search) under
``run_index_pattern_match``. Collections are created the way the product
creates them, and the legacy layouts already deployed are covered too: no
index is added for this, so an existing collection must answer as it is.

Requires: docker compose -f tests/integration/compose/vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_exact_text_real_backends.py -m integration --timeout=300
"""

import logging
import random
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.graph_db.interface.graph_db_provider import AccessibleContainers
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import CollectionConfig, VectorPoint
from app.services.vector_db.sparse_embeddings import SparseEmbedder
from app.utils.exact_text_index import plan_command, run_index_pattern_match
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import DIM, make_dense, point_id

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ORG, OTHER_ORG = "org-exact", "org-exact-other"
CONNECTOR, PRIVATE_CONNECTOR = "conn-wiki", "conn-private"
NEEDLE = "Rollback plan for ERR_GATEWAY_7731 in the edge proxy, approved by the on-call lead."
FILLER_CHUNKS = 1500
LOG = logging.getLogger("test.exact_text")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def sparse_embedder() -> SparseEmbedder:
    embedder = SparseEmbedder()
    try:
        await embedder._ensure_initialized()
    except Exception as exc:
        pytest.skip(f"fastembed Qdrant/bm25 model unavailable: {exc}")
    return embedder


def _filler(n: int) -> list[str]:
    rng = random.Random(11)
    words = ["status", "update", "meeting", "notes", "deploy", "review", "budget", "roadmap",
             "latency", "gateway", "proxy", "incident", "customer", "ticket", "release", "plan"]
    return [" ".join(rng.choices(words, k=40)) + f" item {i}" for i in range(n)]


async def _points(svc: IVectorDBService, embedder: SparseEmbedder, rows: list[dict]) -> list[VectorPoint]:
    texts = [r["text"] for r in rows]
    sparse = (
        await embedder.embed_documents(texts)
        if svc.get_capabilities().supports_sparse_vectors
        else [None] * len(texts)
    )
    return [
        VectorPoint(
            id=point_id(f"{r['org']}/{r['vrid']}/{r.get('idx', 0)}"),
            dense_vector=make_dense([1.0]),
            sparse_vector=vector,
            payload={
                "page_content": r["text"],
                "metadata": {"orgId": r["org"], "virtualRecordId": r["vrid"], "blockIndex": r.get("idx", 0)},
                "connectorIds": [r.get("connector", CONNECTOR)],
                "recordGroupIds": [],
            },
        )
        for r, vector in zip(rows, sparse)
    ]


def _corpus() -> list[dict]:
    rows = [{"org": ORG, "vrid": f"vr-fill-{i}", "text": t} for i, t in enumerate(_filler(FILLER_CHUNKS))]
    rows += [
        {"org": ORG, "vrid": "vr-needle", "text": NEEDLE},
        # Same text in another tenant, under the same connector id.
        {"org": OTHER_ORG, "vrid": "vr-other-org", "text": NEEDLE},
        # Same org, a connector this user cannot reach.
        {"org": ORG, "vrid": "vr-private", "text": NEEDLE, "connector": PRIVATE_CONNECTOR},
        # Reachable connector, but the record itself is denied.
        {"org": ORG, "vrid": "vr-denied", "text": NEEDLE},
        # An AND across chunks of one record.
        {"org": ORG, "vrid": "vr-and", "idx": 0, "text": "quokkaflux migration notes for the gateway"},
        {"org": ORG, "vrid": "vr-and", "idx": 1, "text": "sign-off recorded in marmotgrid by the release team"},
        {"org": ORG, "vrid": "vr-a-only", "text": "quokkaflux appears here alone"},
        {"org": ORG, "vrid": "vr-b-only", "text": "marmotgrid appears here alone"},
    ]
    return rows


_READABLE = {
    **{f"vr-fill-{i}": f"r-fill-{i}" for i in range(FILLER_CHUNKS)},
    "vr-needle": "r-needle", "vr-and": "r-and", "vr-a-only": "r-a", "vr-b-only": "r-b",
    # Claimed readable on purpose: only the vector filter's orgId clause stands
    # between this user and the other tenant's chunk.
    "vr-other-org": "r-other-org",
}


def _retrieval(svc: IVectorDBService, collection: str, *, containers_on: bool) -> RetrievalService:
    graph = MagicMock()
    graph.get_accessible_virtual_record_ids = AsyncMock(return_value=dict(_READABLE))
    graph.get_accessible_containers = AsyncMock(return_value=AccessibleContainers(
        app_ids=frozenset({CONNECTOR}), app_ids_trusted=frozenset(),
    ))

    async def _adjudicate(vrids, user_id, org_id, **_kwargs: object) -> dict[str, str]:
        return {v: _READABLE[v] for v in vrids if v in _READABLE}

    graph.filter_accessible_virtual_record_ids = AsyncMock(side_effect=_adjudicate)
    registry = MagicMock()
    registry.strategy_name = "single"
    registry.resolve_for_query = AsyncMock(return_value=[collection])
    retrieval = RetrievalService(LOG, MagicMock(), registry, svc, graph, MagicMock())
    retrieval._container_filter_enabled = AsyncMock(return_value=containers_on)
    retrieval._excluded_demo_apps = AsyncMock(return_value=frozenset())
    retrieval._get_user_cached = AsyncMock(return_value={"_key": "u1"})
    retrieval._accessible_connector_names = AsyncMock(return_value=[])
    return retrieval


async def _refresh(svc: IVectorDBService, collection: str) -> None:
    """OpenSearch makes writes searchable on its 30 s refresh; force it."""
    client = svc.get_service_client() if svc.get_service_name() == "opensearch" else None
    if client is not None:
        await client.indices.refresh(index=collection)


async def _exact(retrieval: RetrievalService, command: str) -> tuple[list[str], bool]:
    outcome = await run_index_pattern_match(
        [plan_command(command)], retrieval, user_id="u1", org_id=ORG, filters=None, logger_instance=LOG,
    )
    return [h["virtual_record_id"] for h in outcome.hits[0]], outcome.capped


class _ExactTextContract:
    """Subclasses provide ``vector_service`` and ``filled_collection``."""

    @pytest.mark.parametrize("containers_on", [False, True])
    async def test_the_needle_is_found_and_nothing_the_user_cannot_read(
        self, filled_collection, vector_service, containers_on,
    ) -> None:
        retrieval = _retrieval(vector_service, filled_collection, containers_on=containers_on)

        hits, capped = await _exact(retrieval, 'grep -rci "ERR_GATEWAY_7731" .')

        assert hits == ["vr-needle"]
        assert capped is False

    async def test_an_and_across_chunks_of_one_record_is_found(self, filled_collection, vector_service) -> None:
        retrieval = _retrieval(vector_service, filled_collection, containers_on=True)

        hits, _ = await _exact(retrieval, 'grep -rliZ "quokkaflux" . | xargs -0 grep -Hci "marmotgrid"')

        assert hits == ["vr-and"]

    async def test_the_store_size_counts_its_chunks(self, filled_collection, vector_service) -> None:
        retrieval = _retrieval(vector_service, filled_collection, containers_on=False)

        assert await retrieval.lexical_store_chunks(ORG, "u1") >= len(_corpus())

    async def test_a_phrase_is_verified_exactly(self, filled_collection, vector_service) -> None:
        retrieval = _retrieval(vector_service, filled_collection, containers_on=False)

        hits, _ = await _exact(retrieval, 'grep -rci "edge proxy, approved" .')
        scattered, _ = await _exact(retrieval, 'grep -rci "proxy gateway approved" .')

        assert hits == ["vr-needle"]
        assert scattered == []


async def _fill(svc: IVectorDBService, embedder: SparseEmbedder, collection: str) -> None:
    rows = _corpus()
    for start in range(0, len(rows), 500):
        await svc.upsert_points(collection, await _points(svc, embedder, rows[start:start + 500]))
    await _refresh(svc, collection)


class TestQdrantExactText(_ExactTextContract):
    @pytest_asyncio.fixture(scope="class", loop_scope="module")
    async def vector_service(self, qdrant_service: IVectorDBService) -> IVectorDBService:
        return qdrant_service

    @pytest_asyncio.fixture(scope="class", loop_scope="module")
    async def filled_collection(self, qdrant_service, sparse_embedder) -> AsyncIterator[str]:
        col = make_collection("qdrant_exact")
        await qdrant_service.create_collection(col, CollectionConfig(embedding_size=DIM))
        await _fill(qdrant_service, sparse_embedder, col)
        yield col
        await qdrant_service.delete_collection(col)

    async def test_a_collection_from_before_idf_needs_no_new_index(self, qdrant_service, sparse_embedder) -> None:
        col = make_collection("qdrant_exact_legacy")
        try:
            await qdrant_service.create_collection(col, CollectionConfig(embedding_size=DIM, sparse_idf=False))
            await _fill(qdrant_service, sparse_embedder, col)

            hits, _ = await _exact(_retrieval(qdrant_service, col, containers_on=True), 'grep -rci "ERR_GATEWAY_7731" .')

            assert hits == ["vr-needle"]
        finally:
            await qdrant_service.delete_collection(col)


class TestOpenSearchExactText(_ExactTextContract):
    @pytest_asyncio.fixture(scope="class", loop_scope="module")
    async def vector_service(self, opensearch_service: IVectorDBService) -> IVectorDBService:
        return opensearch_service

    @pytest_asyncio.fixture(scope="class", loop_scope="module")
    async def filled_collection(self, opensearch_service, sparse_embedder) -> AsyncIterator[str]:
        col = make_collection("opensearch_exact")
        await opensearch_service.create_collection(col, CollectionConfig(embedding_size=DIM))
        await _fill(opensearch_service, sparse_embedder, col)
        yield col
        await opensearch_service.delete_collection(col)

    async def test_an_index_with_the_old_mapping_needs_no_new_index(self, opensearch_service, sparse_embedder) -> None:
        """Created before the stemmed sub-field existed, and never migrated."""
        col = make_collection("opensearch_exact_legacy")
        client = opensearch_service.get_service_client()
        try:
            await client.indices.create(index=col, body={"mappings": {"properties": {
                "page_content": {"type": "text"},
                "point_id": {"type": "keyword"},
                "connectorIds": {"type": "keyword"},
                "recordGroupIds": {"type": "keyword"},
                "metadata": {"properties": {
                    "orgId": {"type": "keyword"}, "virtualRecordId": {"type": "keyword"},
                }},
            }}})
            await _fill(opensearch_service, sparse_embedder, col)

            hits, _ = await _exact(_retrieval(opensearch_service, col, containers_on=True), 'grep -rci "ERR_GATEWAY_7731" .')

            assert hits == ["vr-needle"]
        finally:
            await opensearch_service.delete_collection(col)


class TestRedisExactText(_ExactTextContract):
    @pytest_asyncio.fixture(scope="class", loop_scope="module")
    async def vector_service(self, redis_service: IVectorDBService) -> IVectorDBService:
        return redis_service

    @pytest_asyncio.fixture(scope="class", loop_scope="module")
    async def filled_collection(self, redis_service, sparse_embedder) -> AsyncIterator[str]:
        col = make_collection("redis_exact")
        await redis_service.create_collection(col, CollectionConfig(embedding_size=DIM))
        await _fill(redis_service, sparse_embedder, col)
        yield col
        await redis_service.delete_collection(col)
