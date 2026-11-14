"""Switching the weight precision ends: after the cleanup, records and entity writes both succeed at the new precision.

The real handler, collection registry, entity store, guard and pin store run over an in-memory vector store that
keeps points by collection. The entity index is as much an index of embedding vectors as the records collection,
so the cleanup resets it with its pin, and what a precision switch leaves of it is nothing.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.services.vector_store_rebuild import PHASE_FAILED, PHASE_READY
from app.models.entities import EntityRecord
from app.modules.transformers import entity_vectorstore as entity_module
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.messaging.kafka.handlers import record as record_module
from app.services.messaging.kafka.handlers.record import RecordEventHandler
from app.services.messaging.precision_flip import PrecisionFlip
from app.services.vector_db.collection_manifest import (
    CollectionManifestStore,
    ManagedCollection,
)
from app.services.vector_db.collection_pin import (
    FP32,
    INT8,
    STAMP_TAG,
    CollectionPinStore,
)
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.models import CollectionConfig, VectorCollectionInfo
from app.services.vector_db.precision_guard import PrecisionGuard
from app.services.vector_db.strategies.single import SingleCollectionStrategy

log = logging.getLogger("switch-converges")
ORG = "o1"
RECORDS = "records"
ENTITIES = "entities"


class _World:
    """A vector store that keeps points by collection; the guard's counts and the deletes work on them."""

    def __init__(self) -> None:
        self.points: dict[str, list[dict]] = {}
        self.dims: dict[str, int] = {}

    def get_capabilities(self):  # noqa: ANN202
        return SimpleNamespace(spaces_per_org=False, supports_sparse_vectors=False, stamps_in_catalog=False)

    def add(self, collection: str, tag: str, *, n: int = 1, **meta) -> None:
        self.dims.setdefault(collection, 4)
        self.points.setdefault(collection, []).extend({"orgId": ORG, "embedPrecision": tag, **meta} for _ in range(n))

    async def get_collection_info(self, name):  # noqa: ANN202
        return VectorCollectionInfo(
            name=name, exists=name in self.dims, dense_dimension=self.dims.get(name), points_count=len(self.points.get(name, [])),
        )

    async def collection_exists(self, name):  # noqa: ANN202
        return name in self.dims

    async def create_collection(self, collection_name, config):  # noqa: ANN202
        self.dims[collection_name] = config.embedding_size
        self.points[collection_name] = []

    async def delete_collection(self, name):  # noqa: ANN202
        self.dims.pop(name, None)
        self.points.pop(name, None)

    async def create_index(self, *a, **k):  # noqa: ANN202
        return None

    async def make_searchable(self, name):  # noqa: ANN202
        return None

    async def filter_collection(self, must=None, **_k):  # noqa: ANN202
        return dict(must or {})

    @staticmethod
    def _matches(point, expression) -> bool:
        for key, wanted in expression.items():
            value = point.get(key.removeprefix("metadata."))
            if value not in (wanted if isinstance(wanted, list) else [wanted]):
                return False
        return True

    async def count_points(self, collection, expression):  # noqa: ANN202
        return sum(self._matches(p, expression) for p in self.points.get(collection, []))

    async def delete_points(self, collection, expression, refresh=False):  # noqa: ANN202
        self.points[collection] = [p for p in self.points.get(collection, []) if not self._matches(p, expression)]

    async def upsert_points(self, collection_name, points):  # noqa: ANN202
        assert collection_name in self.dims, "the write went to a collection that does not exist"
        for point in points:
            meta = point.payload["metadata"]
            self.points[collection_name] = [p for p in self.points[collection_name] if p.get("id") != point.id]
            self.points[collection_name].append({"id": point.id, **meta})


class _Lock:
    def __init__(self, *_a, **_k) -> None:
        pass

    async def try_acquire(self) -> bool:
        return True

    async def release(self) -> None:
        return None


@pytest.fixture
def world(monkeypatch):  # noqa: ANN201
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    vdb = _World()
    pins = CollectionPinStore(config, log)
    registry = CollectionRegistry(
        vector_db_service=vdb,
        strategy=SingleCollectionStrategy(),
        collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
        manifest_store=CollectionManifestStore(config, MagicMock()),
        logger=log,
    )
    monkeypatch.setattr(entity_module, "RebuildJobLock", _Lock)
    monkeypatch.setattr(entity_module, "redis_from_config_service", AsyncMock(return_value=MagicMock(aclose=AsyncMock())))

    served = {"precision": FP32}
    store = object.__new__(EntityVectorStore)
    store.config_service = config
    store.logger = log
    store.vector_db_service = vdb
    store.collection_name = ENTITIES
    store._shared_collection = ENTITIES
    store._ready_collections = set()
    store._embedding_size = 4
    store._generation = 0
    store._config_hash = "h"
    store._capabilities = vdb.get_capabilities()
    store._dense_embeddings = SimpleNamespace(model="m")
    store._ensure_initialized = AsyncMock()
    store._ensure_collection = AsyncMock(return_value=True)
    store._ensure_payload_indexes = AsyncMock()
    store._fetch_existing_states = AsyncMock(return_value={})
    store._raise_if_config_moved = AsyncMock()
    store._fingerprint = lambda: "m:4"
    store._entity_lock = lambda key: _NullLock()

    async def embed(entities):  # noqa: ANN202
        return {
            store._point_id(e.org_id, e.entity_type.value, e.entity_id): ([0.1] * 4, None, (served["precision"], False))
            for e in entities
        }

    store._embed_entities = embed
    store._embed_changed = lambda entities, states, fingerprint: embed(entities)
    return SimpleNamespace(config=config, vdb=vdb, pins=pins, registry=registry, store=store, served=served)


class _NullLock:
    async def __aenter__(self):  # noqa: ANN204
        return self

    async def __aexit__(self, *_a):  # noqa: ANN204
        return False


async def _seed_int8_index(w) -> None:
    """An index written at int8: records, entities and the entity stamp, each with its pin."""
    w.vdb.add(RECORDS, INT8, n=3, virtualRecordId="v1")
    w.vdb.add(ENTITIES, INT8, n=2, entityType="record", entityId="r1")
    w.vdb.add(ENTITIES, STAMP_TAG)
    await w.registry._manifest_store.record(ManagedCollection(
        name=RECORDS, collection_type="records", embedding_dimension=4, strategy_name="single", embedding_model="m",
    ))
    await w.pins.claim(RECORDS, INT8)
    await w.pins.claim(ENTITIES, INT8)


def _handler(w, monkeypatch, *, entity_store=True):  # noqa: ANN202
    phases: list[str] = []

    async def mark(_config, phase, **_k):  # noqa: ANN202
        phases.append(phase)

    monkeypatch.setattr(record_module, "mark_cleanup_phase", mark)
    handler = object.__new__(RecordEventHandler)
    handler.config_service = w.config
    handler.logger = log
    handler.event_processor = SimpleNamespace(
        sink_orchestrator=SimpleNamespace(
            vector_store=SimpleNamespace(get_embedding_model_instance=AsyncMock(), embedding_size=4),
            entity_vector_store=w.store if entity_store else None,
        ),
        processor=SimpleNamespace(indexing_pipeline=SimpleNamespace(collection_registry=w.registry)),
    )
    handler.phases = phases
    return handler


def _entity() -> EntityRecord:
    return EntityRecord.for_record("r1", "Q3 plan", ORG, "c1", "g1")


async def _entity_write(w) -> None:
    await w.store._upsert_batch(ENTITIES, [_entity()], SimpleNamespace(written=0, unchanged=0, membership_only=0), set(), merge_membership=False)


@pytest.mark.asyncio
async def test_after_the_cleanup_records_and_entity_writes_both_succeed_at_the_new_precision(world, monkeypatch) -> None:
    await _seed_int8_index(world)
    guard = PrecisionGuard(world.pins, world.vdb, log)

    # the server now serves fp32: both indexes refuse it, as they must until the cleanup has run
    with pytest.raises(PrecisionFlip):
        await guard.verify_batch(RECORDS, ORG, FP32, fallback=False, intent=INT8)
    with pytest.raises(PrecisionFlip):
        await _entity_write(world)

    handler = _handler(world, monkeypatch)
    assert [e async for e in handler._delete_vector_collection()]
    assert handler.phases == [PHASE_READY]

    # nothing of the old precision is left, in either index, and no pin either
    assert await world.pins.keys() == []
    assert world.vdb.points[RECORDS] == []
    assert world.vdb.points[ENTITIES] == []  # the stamp point went too: the rebuild reads a collection it has not filled

    # the reindex after the cleanup: a records batch and an entity batch, both at the new precision
    await guard.verify_batch(RECORDS, ORG, FP32, fallback=False, intent=INT8)
    world.vdb.add(RECORDS, FP32, virtualRecordId="v1")
    await _entity_write(world)
    assert {p["embedPrecision"] for p in world.vdb.points[RECORDS]} == {FP32}
    assert {p["embedPrecision"] for p in world.vdb.points[ENTITIES]} == {FP32}
    assert (await world.pins.get(RECORDS, fresh=True)).precision == FP32
    assert (await world.pins.get(ENTITIES, fresh=True)).precision == FP32


@pytest.mark.asyncio
async def test_the_pin_of_a_collection_that_is_still_there_is_kept_and_an_orphans_is_released(world, monkeypatch) -> None:
    await _seed_int8_index(world)
    world.vdb.add("other", FP32, n=2)  # a live collection the cleanup does not manage
    await world.pins.claim("other", FP32)
    await world.pins.claim("gone", INT8)  # a collection that no longer exists
    handler = _handler(world, monkeypatch)
    assert [e async for e in handler._delete_vector_collection()]
    assert await world.pins.keys() == [world.pins.key("other")]
    assert (await world.pins.get("other", fresh=True)).precision == FP32
    assert len(world.vdb.points["other"]) == 2  # nothing of it is touched


@pytest.mark.asyncio
async def test_without_an_entity_store_the_entity_pin_stays_with_its_vectors(world, monkeypatch) -> None:
    await _seed_int8_index(world)
    handler = _handler(world, monkeypatch, entity_store=False)
    assert [e async for e in handler._delete_vector_collection()]
    assert await world.pins.keys() == [world.pins.key(ENTITIES)]  # never a pin released over vectors that remain
    assert len(world.vdb.points[ENTITIES]) == 3


@pytest.mark.asyncio
async def test_a_pin_that_cannot_be_released_publishes_the_failed_phase(world, monkeypatch) -> None:
    await _seed_int8_index(world)
    await world.pins.claim("gone", INT8)  # an orphan, whose release the store refuses
    real_delete = world.config.delete_config

    async def delete(key):  # noqa: ANN202
        if key.endswith("/gone"):
            raise ConnectionError("kv down")
        return await real_delete(key)

    world.config.delete_config = delete
    handler = _handler(world, monkeypatch)
    with pytest.raises(ConnectionError):
        [e async for e in handler._delete_vector_collection()]
    assert handler.phases == [PHASE_FAILED]  # the job reads a phase, not its own timeout


@pytest.mark.asyncio
async def test_an_entity_batch_written_while_the_pin_moved_is_removed_and_handed_back(world) -> None:
    """The pin is int8 when the batch is checked; a cleanup and an fp32 writer replace it before the write lands."""
    await world.pins.claim(ENTITIES, INT8)
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    world.served["precision"] = INT8
    real = world.vdb.upsert_points

    async def pin_moves_then_write(collection_name, points):  # noqa: ANN202
        await world.pins.release(ENTITIES)
        await world.pins.claim(ENTITIES, FP32)
        await real(collection_name, points)

    world.vdb.upsert_points = pin_moves_then_write
    with pytest.raises(PrecisionFlip):
        await _entity_write(world)
    assert all(p.get("entityId") != "r1" for p in world.vdb.points[ENTITIES])  # what it wrote is gone
    assert (await world.pins.get(ENTITIES, fresh=True)).precision == FP32  # and the other writer's pin stands
