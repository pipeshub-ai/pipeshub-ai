"""The records rebuild ("Delete all embeddings", and an embedding model change)
and the entity index, over one vector DB.

The real registry, entity store and rebuild loop; only the vector DB, the
graph and the embedding clients are stand-ins. The rebuild used to drop the
entities collection whenever the manifest listed it, and nothing filled it
again: taxonomy entities stayed out of search until the model next changed.
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
from app.modules.indexing.entity_index_rebuild import (
    EntityIndexState,
    entity_index_marker,
)
from app.modules.transformers import entity_vectorstore
from app.modules.transformers.entity_vectorstore import (
    EMBEDDING_MODEL_FIELD,
    EntityVectorStore,
)
from app.services.vector_db.collection_manifest import (
    CollectionManifestStore,
    ManagedCollection,
)
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.models import (
    CollectionConfig,
    VectorDBCapabilities,
    VectorPoint,
)
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import RecordContext
from tests.support.embedding_config import (
    config_service,
    embedding_config,
    switch_embedding_model,
)
from tests.support.entity_vector_db import (
    FakeEmbeddingModel,
    FakeEntityVectorDB,
    embedding_models,
)
from tests.unit.modules.indexing.test_entity_index_rebuild import (
    APPS,
    ORGS,
    RECORDS,
    TOPICS,
    Clock,
    FakeGraph,
    _app,
    _org,
    _rebuilder,
    _rec,
    _run_until_settled,
)

LOGGER = logging.getLogger("entity-index-test")
ENTITY_INDEXES = {"metadata.orgId", "metadata.entityType", "metadata.entityId", "metadata.level"}


class _Collection(FakeEntityVectorDB):
    def __init__(self) -> None:
        super().__init__()
        self.indexes: set[str] = set()

    async def delete_collection(self, collection_name: str) -> None:
        await super().delete_collection(collection_name)
        self.indexes.clear()

    async def create_index(self, collection_name: str, field_name: str, field_schema: dict) -> None:
        await super().create_index(collection_name, field_name, field_schema)
        self.indexes.add(field_name)


class FakeVectorDB:
    """One collection per name, each as ``FakeEntityVectorDB`` models it."""

    def __init__(self) -> None:
        self.collections: dict[str, _Collection] = {}

    def get_capabilities(self) -> VectorDBCapabilities:
        return VectorDBCapabilities()

    async def filter_collection(self, **kwargs: Any) -> dict[str, dict[str, Any]]:  # noqa: ANN401
        return await _Collection().filter_collection(**kwargs)

    def __getattr__(self, method: str) -> Any:  # noqa: ANN401
        def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            name = kwargs["collection_name"] if "collection_name" in kwargs else args[0]
            return getattr(self.collections.setdefault(name, _Collection()), method)(*args, **kwargs)

        return call


def _registry(db: FakeVectorDB, config: Any) -> CollectionRegistry:  # noqa: ANN401
    return CollectionRegistry(
        vector_db_service=db,
        strategy=SingleCollectionStrategy(),
        collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
        manifest_store=CollectionManifestStore(config, LOGGER),
        logger=LOGGER,
    )


def _store(db: FakeVectorDB, config: Any) -> EntityVectorStore:  # noqa: ANN401
    return EntityVectorStore(
        logger=LOGGER, config_service=config, vector_db_service=db,
        recreate_on_dimension_mismatch=True,
    )


def _graph() -> FakeGraph:
    graph = FakeGraph()
    graph.docs[APPS]["app-1"] = _app()
    graph.docs[ORGS]["org-1"] = _org()
    graph.sources[(RECORDS, "app-1")] = [_rec("r1", name="Q3 plan", group=None)]
    graph.sources[(TOPICS, "org-1")] = [{"_key": "t1", "name": "Billing"}]
    graph.membership[("topic", "t1")] = {"connectorIds": ["app-1"], "recordGroupIds": []}
    graph.nodes[TOPICS] = {"t1": {"orgId": "org-1"}}
    return graph


class _Deployment:
    """Records and entity points written with the default model (dimension 4)."""

    def __init__(self) -> None:
        self.db, self.config, self.clock = FakeVectorDB(), config_service(), Clock()
        self.registry = _registry(self.db, self.config)
        self.store = _store(self.db, self.config)
        self.graph = _graph()

    async def index(self, *, manifest_lists_entities: bool) -> _Deployment:
        await self.registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=4)
        await self.db.upsert_points(
            "records", [VectorPoint(id="chunk-1", dense_vector=[1.0] * 4, payload={})],
        )
        await self.rebuild()
        assert len(self.entities.points) == 2
        if manifest_lists_entities:
            # As adoption left it on some deployments in an earlier release.
            await self.registry.manifest_store.record(ManagedCollection(
                name="entities", collection_type="entities", embedding_dimension=4, strategy_name="single",
            ))
        return self

    async def rebuild(self) -> list[str]:
        """The rebuild loop, on a fresh leader, until it has nothing left to do."""
        rebuilder = _rebuilder(self.graph, self.store, config_service=self.config, now_ms=self.clock)
        return await _run_until_settled(rebuilder, self.clock)

    @property
    def entities(self) -> _Collection:
        return self.db.collections["entities"]

    @property
    def records(self) -> _Collection:
        return self.db.collections["records"]

    def state(self, collection: str, key: str) -> object:
        return self.graph.docs[collection][key][EntityIndexState.STATE]


def _default_model() -> Any:  # noqa: ANN401
    return patch.object(
        entity_vectorstore, "get_default_embedding_model", return_value=FakeEmbeddingModel(1.0, 4),
    )


class TestDeleteAllEmbeddings:
    async def _cleanup_leaves_the_entity_index(self, *, manifest_lists_entities: bool) -> None:
        with _default_model():
            d = await _Deployment().index(manifest_lists_entities=manifest_lists_entities)
            entities_before = dict(d.entities.points)

            recreated = await d.registry.recreate_records_collections(4)

            assert recreated == ["records"]
            assert d.records.points == {} and d.records.dimension == 4
            assert d.entities.points == entities_before
            assert d.entities.deletions == 0
            assert ENTITY_INDEXES <= d.entities.indexes
            assert await d.rebuild() == ["idle", "idle"]

    async def test_only_the_records_collection_is_emptied(self) -> None:
        await self._cleanup_leaves_the_entity_index(manifest_lists_entities=False)

    async def test_a_manifest_that_lists_the_entity_index_changes_nothing(self) -> None:
        await self._cleanup_leaves_the_entity_index(manifest_lists_entities=True)


class TestEmbeddingModelChange:
    async def test_the_entity_index_moves_to_the_new_model_by_itself(self) -> None:
        """The records rebuild leaves it on the old model; once the new model
        is saved, the entity store recreates it at the new dimension and the
        rebuild embeds every entity again."""
        small = FakeEmbeddingModel(2.0, 6)
        with _default_model(), embedding_models({"text-embedding-3-small": small}):
            d = await _Deployment().index(manifest_lists_entities=True)
            # The guard allows a change only once the records are gone.
            await d.db.delete_collection("records")
            await d.db.create_collection("records", CollectionConfig(embedding_size=4))

            assert await d.registry.recreate_records_collections(6) == ["records"]
            assert d.records.dimension == 6
            assert d.entities.dimension == 4 and len(d.entities.points) == 2

            await switch_embedding_model(d.config, embedding_config("openAI", "text-embedding-3-small"))
            outcomes = await d.rebuild()

        fingerprint = "openAI:text-embedding-3-small:6"
        assert "connector" in outcomes and "taxonomy" in outcomes and "refill" not in outcomes
        assert d.entities.dimension == 6 and d.entities.deletions == 1
        assert len(d.entities.points) == 2
        for point in d.entities.points.values():
            assert point.payload["metadata"][EMBEDDING_MODEL_FIELD] == fingerprint
            assert len(point.dense_vector) == 6
        assert d.state(APPS, "app-1") == entity_index_marker(fingerprint)
        assert d.state(ORGS, "org-1") == entity_index_marker(fingerprint)


class TestADeploymentAlreadyHit:
    async def test_an_index_the_old_cleanup_emptied_is_refilled(self) -> None:
        """What an earlier release's cleanup left: the entities collection
        recreated through the records registry, so empty and without the
        entity payload indexes, with every document still marked done."""
        fingerprint = f"default:{DEFAULT_EMBEDDING_MODEL}:4"
        with _default_model():
            d = await _Deployment().index(manifest_lists_entities=True)
            assert d.state(ORGS, "org-1") == entity_index_marker(fingerprint)
            expected = set(d.entities.points)
            await d.db.delete_collection("entities")
            await d.db.create_collection("entities", CollectionConfig(embedding_size=4))
            assert d.entities.indexes == set()

            outcomes = await d.rebuild()

            assert outcomes[:2] == ["idle", "refill"]
            assert set(d.entities.points) == expected
            assert ENTITY_INDEXES <= d.entities.indexes
            assert d.state(APPS, "app-1") == entity_index_marker(fingerprint, 1)
            assert d.state(ORGS, "org-1") == entity_index_marker(fingerprint, 1)
            assert await d.rebuild() == ["idle", "idle"]
