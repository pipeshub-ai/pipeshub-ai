"""A saved Ollama dimension must not resize an existing embedding space."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.collection_manifest import CollectionManifestStore
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.models import CollectionConfig, VectorCollectionInfo
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import RecordContext
from tests.support.vector_db import make_config_service


@pytest.mark.parametrize("path", ["indexing", "retrieval", "entities"])
async def test_saved_dimensions_keep_existing_collection_usable(path, monkeypatch) -> None:
    config = {"provider": "ollama", "isDefault": True, "configuration": {
        "model": "embeddinggemma", "dimensions": 512,
    }}
    vdb = AsyncMock()
    vdb.get_collection_info.side_effect = lambda name: VectorCollectionInfo(
        name=name, exists=True, dense_dimension=768,
    )
    registry = CollectionRegistry(
        vector_db_service=vdb,
        strategy=SingleCollectionStrategy(),
        collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
        manifest_store=CollectionManifestStore(make_config_service(), MagicMock()),
        logger=MagicMock(),
    )
    requests = []

    def build_model(provider, effective) -> MagicMock:
        assert provider == "ollama"
        dimension = effective["configuration"].get("dimensions", 768)
        requests.append(dimension)
        model = MagicMock(model="embeddinggemma")
        model.aembed_query = AsyncMock(return_value=[0.1] * dimension)
        return model

    if path == "indexing":
        from app.modules.transformers import vectorstore as module

        store = module.VectorStore.__new__(module.VectorStore)
        store.logger = MagicMock()
        store.collection_registry = registry
        store._ensure_sparse_embeddings = AsyncMock()
        monkeypatch.setattr(module, "get_embedding_model", build_model)
        monkeypatch.setattr(module, "_model_window", AsyncMock(return_value=2048))
        await store._build_embedding_model([config], "saved-config")
        model = store.dense_embeddings
    elif path == "retrieval":
        from app.modules.retrieval import retrieval_service as module

        store = module.RetrievalService.__new__(module.RetrievalService)
        store.logger = MagicMock()
        store.collection_registry = registry
        store.config_service = AsyncMock()
        store.config_service.get_config.return_value = {"embedding": [config]}
        store._cached_dense_embeddings = None
        store._embedding_model_lock = asyncio.Lock()
        monkeypatch.setattr(module, "get_embedding_model", build_model)
        model = await store.get_embedding_model_instance()
    else:
        from app.modules.transformers import entity_vectorstore as module

        store = module.EntityVectorStore.__new__(module.EntityVectorStore)
        store.vector_db_service = vdb
        store.collection_name = "entities"
        monkeypatch.setattr(module, "get_embedding_model", build_model)
        model = await store._build_dense_embeddings(config)

    vector = await model.aembed_query("existing document or search query")
    # Exercise the real dimension guard, rather than just checking kwargs.
    assert await registry.ensure_collection(RecordContext(org_id="org"), len(vector)) == "records"
    assert requests == [768]
    assert config["configuration"]["dimensions"] == 512
    vdb.delete_collection.assert_not_called()
    vdb.create_collection.assert_not_called()
