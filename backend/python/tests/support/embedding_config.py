"""The embedding model config as the Python services see it: a real
ConfigurationService over an in-memory key-value store.

Its cache and invalidation are the production ones, so a test that switches
model sees what a running service sees: a cached read until the change
notification arrives, then the new value.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.config.providers.in_memory_store import InMemoryKeyValueStore, KeyData

if TYPE_CHECKING:
    from app.modules.transformers.entity_vectorstore import EntityVectorStore

AI_MODELS = config_node_constants.AI_MODELS.value


def embedding_config(provider: str, model: str, **configuration: object) -> dict[str, Any]:
    return {"provider": provider, "isDefault": True, "configuration": {"model": model, **configuration}}


def config_service(*embedding: dict[str, Any]) -> ConfigurationService:
    """A ConfigurationService whose AI models config names ``embedding`` (no
    key at all when empty, which reads as the default model)."""
    store: InMemoryKeyValueStore = InMemoryKeyValueStore(logging.getLogger("test-kv"))
    # The watch thread subscribes for notifications; the in-memory store has
    # none, and ``switch_embedding_model`` delivers them itself.
    with patch.object(ConfigurationService, "_start_watch"), \
            patch.dict("os.environ", {"SECRET_KEY": "test-secret-key"}):
        service = ConfigurationService(logger=logging.getLogger("test-config"), key_value_store=store)
    if embedding:
        store.store[AI_MODELS] = KeyData({"embedding": list(embedding)})
    return service


def another_process(service: ConfigurationService) -> ConfigurationService:
    """A ConfigurationService over the same key-value store as ``service``,
    with its own cache: what a second replica sees."""
    with patch.object(ConfigurationService, "_start_watch"), \
            patch.dict("os.environ", {"SECRET_KEY": "test-secret-key"}):
        return ConfigurationService(logger=logging.getLogger("test-config"), key_value_store=service.store)


async def switch_embedding_model(
    service: ConfigurationService, *embedding: dict[str, Any], notify: bool = True,
) -> None:
    """Change the embedding model as the Node.js admin API does: write the
    key, then publish the change (``notify``), which clears the key from every
    process's cache."""
    await service.store.create_key(AI_MODELS, {"embedding": list(embedding)})
    if notify:
        service._invalidation_callback(AI_MODELS)



def skip_bootstrap(store: EntityVectorStore) -> None:
    """Mark an ``EntityVectorStore`` built over ``config_service()`` as
    initialised for the model its config names, without the embedding probe
    or the collection setup. The test sets the embeddings itself."""
    from app.utils.aimodels import embedding_config_hash

    data = store.config_service.store.store.get(AI_MODELS)
    store._config_hash = embedding_config_hash((data.value if data else {}).get("embedding"))
    store._initialized = True
