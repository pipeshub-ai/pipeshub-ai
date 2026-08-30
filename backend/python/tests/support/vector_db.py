"""Shared test doubles for the vector DB collection layer.

One definition, imported everywhere. Seven copies of "a registry that resolves
to one collection" had drifted across the suite, and each one had to be found
and updated whenever the registry gained a collaborator — which is exactly the
kind of thing a test double should not cost.

The double is deliberately *partly real*: the strategy and the manifest store
are the genuine classes over an in-memory config service, because the locator
and the read path resolve through them. Mocking those out would make the tests
agree with each other and disagree with production.
"""

from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

from app.services.vector_db.collection_manifest import (
    CollectionManifestStore,
    ManagedCollection,
)
from app.services.vector_db.models import CollectionConfig
from app.services.vector_db.strategies.single import SingleCollectionStrategy


def make_config_service() -> MagicMock:
    """An in-memory ConfigurationService: get/set/create-if-absent over a dict."""
    store: dict = {}

    async def get_config(key, default=None):
        return store.get(key, default)

    async def set_config(key, value):
        store[key] = value
        return True

    async def create_config_if_absent(key, value):
        if key in store:
            return False
        store[key] = value
        return True

    svc = MagicMock()
    svc.get_config = AsyncMock(side_effect=get_config)
    svc.set_config = AsyncMock(side_effect=set_config)
    svc.create_config_if_absent = AsyncMock(side_effect=create_config_if_absent)
    svc._store = store
    return svc


def make_manifest_store(
    collection_names=("records",), *, dimension: int = 1024
) -> CollectionManifestStore:
    """A real manifest store pre-seeded as if these collections were created.

    Seeded through the backing config service, not just the in-memory cache:
    delete paths deliberately read with ``fresh=True``, which bypasses the
    cache entirely, and a double that only populated the cache would make them
    resolve no collections at all.
    """
    from app.services.vector_db.collection_manifest import MANIFEST_CONFIG_KEY

    entries = {
        name: ManagedCollection(
            name=name,
            collection_type="records",
            embedding_dimension=dimension,
            strategy_name="single",
        )
        for name in collection_names
    }
    config_service = make_config_service()
    config_service._store[MANIFEST_CONFIG_KEY] = {
        name: asdict(entry) for name, entry in entries.items()
    }

    store = CollectionManifestStore(config_service, MagicMock())
    store._cache = dict(entries)
    # Never expire: a test asserting on collection targets should not depend on
    # how long it took to get there.
    store._cached_at = float("inf")
    return store


def make_collection_registry(collection_name: str = "records") -> MagicMock:
    """A CollectionRegistry double that resolves everything to ``collection_name``.

    Real strategy and real manifest store (see module docstring); the
    vector-DB-touching methods are mocks so a test can assert on them.
    """
    registry = MagicMock()
    registry.strategy = SingleCollectionStrategy()
    registry.manifest_store = make_manifest_store([collection_name])
    registry.strategy_name = "single"
    registry.resolve_write_collection = MagicMock(return_value=collection_name)
    registry.ensure_collection = AsyncMock(return_value=collection_name)
    registry.resolve_for_query = AsyncMock(return_value=[collection_name])
    registry.delete_collection = AsyncMock()
    registry.recreate_all_collections = AsyncMock(return_value=[collection_name])
    registry.list_managed_collections = AsyncMock(
        return_value=[
            ManagedCollection(
                name=collection_name,
                collection_type="records",
                embedding_dimension=1024,
                strategy_name="single",
            )
        ]
    )
    registry.build_collection_config = MagicMock(
        side_effect=lambda size, sparse_idf=False: CollectionConfig(embedding_size=size)
    )
    registry.invalidate = MagicMock()
    return registry
