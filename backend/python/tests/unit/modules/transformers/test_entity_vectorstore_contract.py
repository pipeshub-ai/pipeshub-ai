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
from unittest.mock import MagicMock

import pytest

from app.modules.transformers.entity_vectorstore import EntityVectorStore
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.integration.vector_db import test_entity_vectorstore_real_backends as suite

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger("entity-store-contract")


@pytest.fixture
async def store() -> AsyncIterator[EntityVectorStore]:
    service = InMemoryVectorDBService()
    entity_store = EntityVectorStore(
        logger=logger, config_service=MagicMock(), vector_db_service=service,
        collection_name=f"entities_{uuid.uuid4().hex[:8]}",
    )

    async def _stub_embeddings() -> None:
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
