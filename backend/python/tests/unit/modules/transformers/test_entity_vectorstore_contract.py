"""The real-backend ``EntityVectorStore`` suite, run on the in-memory vector
service (F3).

The same cases run against Qdrant, Redis and OpenSearch in
``tests/integration/vector_db/test_entity_vectorstore_real_backends.py``.
Running them here too keeps ``InMemoryVectorDBService`` to the contract the
real providers share, so unit tests that use it test something real, and
gives the store's behaviour a fast check that needs no server.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING
import pytest

from app.modules.transformers.entity_vectorstore import EntityVectorStore
from tests.support.embedding_config import config_service as embedding_config_service
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.integration.vector_db import test_entity_vectorstore_real_backends as suite

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger("entity-store-contract")


@pytest.fixture
async def store() -> AsyncIterator[EntityVectorStore]:
    service = InMemoryVectorDBService()
    entity_store = EntityVectorStore(
        logger=logger, config_service=embedding_config_service(), vector_db_service=service,
        collection_name=f"entities_{uuid.uuid4().hex[:8]}",
    )

    async def _stub_embeddings(embedding_configs: list | None = None) -> None:
        entity_store._dense_embeddings = suite._StubEmbeddings()
        entity_store._embedding_size = suite.DIM
        entity_store._model_id = "stub:hash"

    entity_store._init_embeddings = _stub_embeddings  # type: ignore[method-assign]
    entity_store.backend = "memory"  # type: ignore[attr-defined]
    await entity_store._ensure_initialized()
    yield entity_store


TestMembershipReads = suite.TestMembershipReads
TestMatchesAndSearch = suite.TestMatchesAndSearch
TestConnectorDeletion = suite.TestConnectorDeletion
TestDeletesWithoutEmbeddings = suite.TestDeletesWithoutEmbeddings
TestReplaceMode = suite.TestReplaceMode
TestFinalSweep = suite.TestFinalSweep
TestRebuildSupport = suite.TestRebuildSupport
TestWriteOutcomeAndLocks = suite.TestWriteOutcomeAndLocks
TestSearchPasses = suite.TestSearchPasses


class TestFakeKeepsTheProviderGuards:
    """Guards every real provider enforces; a fake without them would pass a
    unit test for a write that fails in production."""

    async def test_a_point_of_the_wrong_dimension_is_refused(self) -> None:
        # Qdrant and OpenSearch reject the write; Redis stores it but leaves
        # it out of the vector index, so search never finds it.
        from app.services.vector_db.models import CollectionConfig, VectorPoint

        service = InMemoryVectorDBService()
        await service.create_collection("c", CollectionConfig(embedding_size=3))
        good = VectorPoint(id="good", dense_vector=[1.0, 0.0, 0.0], payload={})
        bad = VectorPoint(id="bad", dense_vector=[1.0, 0.0], payload={})
        with pytest.raises(ValueError, match="dimension"):
            await service.upsert_points("c", [good, bad])
        assert (await service.scroll("c", await service.filter_collection(), limit=10)).points == []

    async def test_a_delete_bounding_only_a_length_is_refused(self) -> None:
        service = InMemoryVectorDBService()
        await service.create_collection("c")
        flt = await service.filter_collection(max_values={"connectorIds": 1})
        with pytest.raises(ValueError):
            await service.delete_points("c", flt)

    async def test_an_overwrite_with_no_filter_is_refused(self) -> None:
        from app.services.vector_db.models import FilterExpression

        service = InMemoryVectorDBService()
        await service.create_collection("c")
        with pytest.raises(ValueError):
            await service.overwrite_payload("c", {"x": 1}, FilterExpression())

    async def test_a_single_value_counts_as_one_under_a_length_bound(self) -> None:
        from app.services.vector_db.models import VectorPoint

        service = InMemoryVectorDBService()
        await service.create_collection("c")
        await service.upsert_points("c", [VectorPoint(id="p", payload={"connectorIds": "x"})])
        flt = await service.filter_collection(must={"connectorIds": "x"}, max_values={"connectorIds": 1})
        page = await service.scroll("c", flt, limit=10)
        assert [p.id for p in page.points] == ["p"]
