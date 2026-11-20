"""Entity writes: the witness stays with its vector, and an unpinned index is read before an int8 claim."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.messaging.precision_flip import PrecisionFlip
from app.services.vector_db.collection_pin import INT8
from tests.unit.modules.transformers.test_entity_vectorstore_precision import (
    _Bundled,
    _store,
)
from tests.unit.services.vector_db.test_collection_pin import _FakeDb
from tests.unit.services.vector_db.test_precision_scope import _guard


def _entity(entity_id: str = "e1", org: str = "o1") -> MagicMock:
    entity = MagicMock()
    entity.org_id = org
    entity.entity_id = entity_id
    entity.entity_type.value = "record"
    entity.name = "n"
    entity.embedding_text = "alpha"
    entity.connector_ids = []
    entity.record_group_ids = []
    entity.to_vector_payload.return_value = {"orgId": org}
    return entity


class _Embedder(_Bundled):
    def __init__(self, precision: str) -> None:
        super().__init__("int8-w")
        self.precision = precision

    def embed_documents_witnessed(self, texts):  # noqa: ANN202
        return [[0.1] * 3 for _ in texts], self.precision, False


@pytest.mark.asyncio
async def test_each_vector_carries_the_witness_of_the_batch_that_embedded_it() -> None:
    entity = _entity()
    store = _store(_Embedder("fp32"))
    store._embed_sparse = AsyncMock(side_effect=lambda texts: [None] * len(texts))
    first = await store._embed_entities([entity])
    store._dense_embeddings = _Embedder("int8-w")  # a second batch of the same entity, embedded meanwhile
    second = await store._embed_entities([entity])
    pid = store._point_id("o1", "record", "e1")
    assert first[pid][2] == ("fp32", False) and second[pid][2] == ("int8-w", False)


def _write_store(db: _FakeDb, serving: str):  # noqa: ANN202
    store = _store(_Embedder(serving))
    guard, pins = _guard(db)
    store._precision_guard = guard
    store.logger = MagicMock()
    store.collection_name = "entities"
    store._ensure_initialized = AsyncMock()
    store._ensure_collection = AsyncMock()
    store._generation = 1
    store._config_hash = "h"
    store._fingerprint = lambda: "fp"
    store._fetch_existing_states = AsyncMock(return_value={})
    store._embed_sparse = AsyncMock(side_effect=lambda texts: [None] * len(texts))
    store._raise_if_config_moved = AsyncMock()
    store.vector_db_service = MagicMock(upsert_points=AsyncMock())
    return store, pins


async def _upsert(store: EntityVectorStore) -> None:
    from app.modules.transformers.entity_vectorstore import EntityWriteOutcome

    store._entity_lock = lambda key: _AsyncNull()
    await store._upsert_batch("entities", [_entity()], EntityWriteOutcome(), set(), merge_membership=False)


class _AsyncNull:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc) -> None:
        return None


@pytest.mark.asyncio
async def test_an_unpinned_index_holding_untagged_points_is_not_claimed_by_an_int8_batch() -> None:
    db = _FakeDb()
    db.add(None, org="o1", n=3)  # written before the pins existed: fp32
    store, pins = _write_store(db, "int8-w")
    with pytest.raises(PrecisionFlip):
        await _upsert(store)
    store.vector_db_service.upsert_points.assert_not_awaited()
    assert await pins.keys() == []


@pytest.mark.asyncio
async def test_an_empty_unpinned_index_is_claimed_by_the_first_int8_batch() -> None:
    store, pins = _write_store(_FakeDb(), "int8-w")
    await _upsert(store)
    store.vector_db_service.upsert_points.assert_awaited_once()
    assert (await pins.get("entities", fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_on_a_store_with_a_space_per_org_the_batch_org_names_the_pin() -> None:
    db = _FakeDb()
    db.get_capabilities = lambda: type("C", (), {"spaces_per_org": True})()
    store, pins = _write_store(db, "fp32")
    await _upsert(store)
    assert await pins.keys() == [pins.key("entities", "o1")]
