"""A pin left over by a crash is replaced by a collection's creator; a pin a writer claimed meanwhile never is.

Every call into the configuration store is the real one (``create_config_if_absent``, ``update_config``) over the
in-memory backend. The creator sites are the registry's create branch, the cleanup's recreate, the entity
collection's create, and (on a store with one space per org) the event that replaces an org's empty space.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.services.vector_db.collection_manifest import (
    CollectionManifestStore,
    ManagedCollection,
)
from app.services.vector_db.collection_pin import FP32, INT8, CollectionPinStore, Pin
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.models import CollectionConfig, VectorCollectionInfo
from app.services.vector_db.pgvector.pgvector import PgVectorService
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import RecordContext
from app.utils.precision_witness import PrecisionWitness, set_precision_witness

log = logging.getLogger("stale-pin")
MODEL = "bge-test"
ORG = "a" * 24


@pytest.fixture(autouse=True)
def _witness():  # noqa: ANN202
    witness = PrecisionWitness()
    set_precision_witness(witness)
    yield witness
    set_precision_witness(None)


class _Vdb:
    """Collections by name; ``on_create`` runs while the creation is in flight (another writer's window)."""

    def __init__(self) -> None:
        self.dims: dict[str, int] = {}
        self.on_create = None

    async def get_collection_info(self, name):  # noqa: ANN202
        return VectorCollectionInfo(name=name, exists=name in self.dims, dense_dimension=self.dims.get(name))

    async def collection_exists(self, name):  # noqa: ANN202
        return name in self.dims

    async def create_collection(self, collection_name, config):  # noqa: ANN202
        self.dims[collection_name] = config.embedding_size
        if self.on_create:
            await self.on_create()

    async def delete_collection(self, name):  # noqa: ANN202
        self.dims.pop(name, None)

    create_index = AsyncMock()
    make_searchable = AsyncMock()


def _registry(clock_now=None):  # noqa: ANN202
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    vdb = _Vdb()
    registry = CollectionRegistry(
        vector_db_service=vdb,
        strategy=SingleCollectionStrategy(),
        collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
        manifest_store=CollectionManifestStore(config, MagicMock()),
        logger=MagicMock(),
    )
    return registry, vdb, config, CollectionPinStore(config, log)


async def _stale(pins, collection, precision, *, age_seconds, org=None):  # noqa: ANN202
    """A pin written ``age_seconds`` ago by a process that crashed before removing it."""
    import time

    old = Pin(precision, int((time.time() - age_seconds) * 1000))
    await pins._config.create_config_if_absent(pins.key(collection, org), old.to_value())


def _ctx() -> RecordContext:
    return RecordContext(org_id=ORG, embedding_model=MODEL, embedding_dimension=4)


@pytest.mark.asyncio
async def test_the_create_branch_replaces_a_pin_left_by_a_crash(_witness) -> None:
    registry, _, _, pins = _registry()
    name = registry.resolve_write_collection(_ctx())
    await _stale(pins, name, INT8, age_seconds=3600)
    _witness.record(MODEL, FP32)
    await registry.ensure_collection(_ctx(), 4)
    assert (await pins.get(name, fresh=True)).precision == FP32


@pytest.mark.asyncio
async def test_a_creator_never_replaces_a_pin_claimed_after_it_observed_absence(_witness) -> None:
    """C1 observes the collection absent, W2 claims once it exists, C1 evaluates the rule: W2's pin survives."""
    registry, vdb, _, pins = _registry()
    name = registry.resolve_write_collection(_ctx())
    _witness.record(MODEL, FP32)

    async def writer_claims() -> None:
        await pins.claim(name, INT8)

    vdb.on_create = writer_claims
    await registry.ensure_collection(_ctx(), 4)
    assert (await pins.get(name, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_a_young_pin_over_a_crash_leftover_is_kept_and_documented(_witness) -> None:
    registry, _, _, pins = _registry()
    name = registry.resolve_write_collection(_ctx())
    await _stale(pins, name, INT8, age_seconds=10)  # inside the clock-skew bound: indistinguishable from a winner
    _witness.record(MODEL, FP32)
    await registry.ensure_collection(_ctx(), 4)
    assert (await pins.get(name, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_nothing_is_replaced_without_a_witness_or_under_a_fallback(_witness) -> None:
    registry, _, _, pins = _registry()
    name = registry.resolve_write_collection(_ctx())
    await _stale(pins, name, INT8, age_seconds=3600)
    await registry.ensure_collection(_ctx(), 4)  # no witness of the model yet
    assert (await pins.get(name, fresh=True)).precision == INT8
    registry2, _, _, pins2 = _registry()
    await _stale(pins2, name, INT8, age_seconds=3600)
    _witness.record(MODEL, FP32, fallback=True)
    await registry2.ensure_collection(_ctx(), 4)
    assert (await pins2.get(name, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_the_stale_rule_runs_with_the_real_claim_and_update_calls(_witness) -> None:
    """The replacement is one ``update_config`` of the pin's own key; a later claim meets the real create-if-absent."""
    registry, _, config, pins = _registry()
    name = registry.resolve_write_collection(_ctx())
    await _stale(pins, name, INT8, age_seconds=3600)
    _witness.record(MODEL, FP32)
    calls: list[str] = []
    real_update = config.update_config

    async def spy(key, value):  # noqa: ANN202
        calls.append(key)
        return await real_update(key, value)

    config.update_config = spy
    await registry.ensure_collection(_ctx(), 4)
    assert calls == [pins.key(name)]
    assert (await pins.claim(name, INT8)).precision == FP32  # refused by create-if-absent: the winner's value stands


@pytest.mark.asyncio
async def test_the_cleanup_recreate_replaces_a_stale_pin(_witness, monkeypatch) -> None:
    registry, vdb, _, pins = _registry()
    name = registry.resolve_write_collection(_ctx())
    vdb.dims[name] = 4
    await registry._manifest_store.record(ManagedCollection(
        name=name, collection_type="records", embedding_dimension=4, strategy_name="single", embedding_model=MODEL,
    ))
    _witness.record(MODEL, FP32)

    async def crashed(self, collection):  # noqa: ANN202
        return None  # the crash between the drop and the pin's removal

    monkeypatch.setattr(CollectionPinStore, "release_collection", crashed)
    await _stale(pins, name, INT8, age_seconds=3600)
    await registry.recreate_records_collections(4)
    assert (await pins.get(name, fresh=True)).precision == FP32


@pytest.mark.asyncio
async def test_an_entity_collection_create_replaces_a_stale_pin(_witness) -> None:
    from app.modules.transformers.entity_vectorstore import EntityVectorStore

    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    pins = CollectionPinStore(config, log)
    store = object.__new__(EntityVectorStore)
    store.config_service = config
    store.logger = log
    store._embedding_size = 4
    store._capabilities = SimpleNamespace(supports_sparse_vectors=False)
    store._dense_embeddings = SimpleNamespace(model=MODEL)
    store.vector_db_service = AsyncMock()
    store._ensure_payload_indexes = AsyncMock()
    await _stale(pins, "entities", INT8, age_seconds=3600)
    _witness.record(MODEL, FP32)
    await store._create_collection("entities")
    assert (await pins.get("entities", fresh=True)).precision == FP32


class _Catalog:
    def __init__(self, replaced: bool) -> None:
        self.replaced = replaced
        self.spaces = [SimpleNamespace(role="active", dim=8, model="old", effective_model="old", named_by="x", space_id=1)]

    async def org_spaces(self, org):  # noqa: ANN202
        return list(self.spaces)

    async def replace_empty_space(self, org, model, dim, metric="cosine"):  # noqa: ANN202
        if self.replaced:
            self.spaces = [SimpleNamespace(role="active", dim=dim, model=model, effective_model=model, named_by=model, space_id=2)]
        return self.replaced

    def forget(self, org=None):  # noqa: ANN202
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("replaced", [True, False])
async def test_a_stale_org_pin_is_replaced_only_by_the_replace_empty_space_event(_witness, replaced) -> None:
    registry, vdb, config, pins = _registry()
    name = registry.resolve_write_collection(_ctx())
    vdb.dims[name] = 4
    await registry._manifest_store.record(ManagedCollection(
        name=name, collection_type="records", embedding_dimension=4, strategy_name="single", embedding_model=MODEL,
    ))
    await _stale(pins, name, INT8, age_seconds=3600, org=ORG)
    _witness.record(MODEL, FP32)

    svc = object.__new__(PgVectorService)
    svc._catalog = _Catalog(replaced)
    svc._db = MagicMock()
    svc.add_space_replaced_listener(registry.replace_stale_org_pin)
    # An empty space on its own (reading it, asking for its info) never re-pins.
    await svc._catalog.org_spaces(ORG)
    assert (await pins.get(name, ORG, fresh=True)).precision == INT8
    try:
        await svc._space_for_write(ORG, 4, {MODEL})
    except Exception:
        assert not replaced
    expected = FP32 if replaced else INT8
    assert (await pins.get(name, ORG, fresh=True)).precision == expected


def test_the_registry_listens_to_a_store_with_one_space_per_org() -> None:
    svc = object.__new__(PgVectorService)
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    registry = CollectionRegistry(
        vector_db_service=svc,
        strategy=SingleCollectionStrategy(),
        collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
        manifest_store=CollectionManifestStore(config, MagicMock()),
        logger=MagicMock(),
    )
    assert svc.__dict__["_space_replaced_listeners"] == [registry.replace_stale_org_pin]
