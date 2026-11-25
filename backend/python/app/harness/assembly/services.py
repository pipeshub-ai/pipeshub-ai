"""The trusted services a durable run is assembled from.

``HarnessServices`` is the harness worker's own DI container: logger, KV and
configuration at start, everything else a lazy resource built on the first
run that needs it. A worker is not a query service: it wires no routes and,
while idle, imports no retrieval, graph, vector or LLM stack (P0.9 measured
667 MiB PSS for a worker built on ``QueryAppContainer``; the budget is 350 MB).

``ContainerServices`` gives the assembler the same view of either container,
so the inline profile (the query service's own ``QueryAppContainer``) and a
dispatched worker assemble runs through one code path.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Protocol

from dependency_injector import containers, providers

from app.utils.logger import create_logger

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService


def _key_value_store(logger: Logger) -> object:
    from app.config.providers.encrypted_store import EncryptedKeyValueStore

    return EncryptedKeyValueStore(logger=logger)


def _config_service(logger: Logger, key_value_store: object) -> ConfigurationService:
    from app.config.configuration_service import ConfigurationService

    return ConfigurationService(logger=logger, key_value_store=key_value_store)


def _utils() -> Any:  # noqa: ANN401 - ContainerUtils, imported on first use
    # Its module imports every parser and the indexing pipeline; a worker pays
    # for that on its first run, never while idle.
    from app.containers.utils.utils import ContainerUtils

    return ContainerUtils()


async def _accessible_records_cache(logger: Logger, config_service: object) -> object:
    return await _utils().create_accessible_records_cache(
        logger=logger, config_service=config_service
    )


async def _graph_provider(
    logger: Logger, config_service: object, accessible_records_cache: object
) -> object:
    return await _utils().create_graph_provider(
        logger=logger,
        config_service=config_service,
        accessible_records_cache=accessible_records_cache,
    )


async def _vector_db_service(config_service: object) -> object:
    return await _utils().get_vector_db_service(config_service=config_service)


async def _blob_store(logger: Logger, config_service: object, graph_provider: object) -> object:
    return await _utils().create_blob_storage(
        logger=logger, config_service=config_service, graph_provider=graph_provider
    )


async def _collection_registry(
    logger: Logger, config_service: object, vector_db_service: object
) -> object:
    return await _utils().create_collection_registry(
        logger=logger, config_service=config_service, vector_db_service=vector_db_service
    )


async def _entity_vector_store(logger: Logger, config_service: object, vector_db_service: object) -> object:
    from app.services.vector_db.const.const import VECTOR_DB_ENTITIES_COLLECTION_NAME

    return await _utils().create_entity_vector_store(
        logger=logger,
        config_service=config_service,
        vector_db_service=vector_db_service,
        collection_name=VECTOR_DB_ENTITIES_COLLECTION_NAME,
    )


async def _retrieval_service(
    config_service: object,
    logger: Logger,
    vector_db_service: object,
    graph_provider: object,
    blob_store: object,
    collection_registry: object,
) -> object:
    return await _utils().create_retrieval_service(
        config_service=config_service,
        logger=logger,
        vector_db_service=vector_db_service,
        graph_provider=graph_provider,
        blob_store=blob_store,
        collection_registry=collection_registry,
    )


class HarnessServices(containers.DeclarativeContainer):
    logger = providers.Singleton(create_logger, "agent_harness")
    key_value_store = providers.Singleton(_key_value_store, logger=logger)
    config_service = providers.Singleton(
        _config_service, logger=logger, key_value_store=key_value_store
    )
    accessible_records_cache = providers.Resource(
        _accessible_records_cache, logger=logger, config_service=config_service
    )
    graph_provider = providers.Resource(
        _graph_provider,
        logger=logger,
        config_service=config_service,
        accessible_records_cache=accessible_records_cache,
    )
    vector_db_service = providers.Resource(_vector_db_service, config_service=config_service)
    blob_store = providers.Resource(
        _blob_store, logger=logger, config_service=config_service, graph_provider=graph_provider
    )
    collection_registry = providers.Resource(
        _collection_registry,
        logger=logger,
        config_service=config_service,
        vector_db_service=vector_db_service,
    )
    # The query service's entity store (``QueryAppContainer.entity_vector_store``): the knowledge-graph entity
    # tools are offered to a durable run exactly when they are to a legacy request (DUR3-B2).
    entity_vector_store = providers.Resource(
        _entity_vector_store,
        logger=logger,
        config_service=config_service,
        vector_db_service=vector_db_service,
    )
    retrieval_service = providers.Resource(
        _retrieval_service,
        config_service=config_service,
        logger=logger,
        vector_db_service=vector_db_service,
        graph_provider=graph_provider,
        blob_store=blob_store,
        collection_registry=collection_registry,
    )


class RunServices(Protocol):
    """What the assembler needs from a process's container."""

    def logger(self) -> Logger: ...

    def config_service(self) -> ConfigurationService: ...

    async def graph_provider(self) -> Any: ...  # noqa: ANN401 - IGraphDBProvider

    async def retrieval_service(self) -> Any: ...  # noqa: ANN401 - RetrievalService

    async def blob_store(self) -> Any: ...  # noqa: ANN401 - BlobStorage

    async def entity_vector_store(self, org_id: str | None = None) -> Any: ...  # noqa: ANN401 - EntityVectorStore, or None


class ContainerServices:
    """``RunServices`` over ``HarnessServices`` or the query service's container."""

    def __init__(self, container: Any) -> None:  # noqa: ANN401 - a DI container
        self._container = container

    @property
    def container(self) -> Any:  # noqa: ANN401 - a DI container
        """The process's container, for services that take it whole (the model-call identity's group lookup)."""
        return self._container

    def logger(self) -> Logger:
        return self._container.logger()

    def config_service(self) -> ConfigurationService:
        return self._container.config_service()

    async def graph_provider(self) -> Any:  # noqa: ANN401
        return await self._resolve(self._container.graph_provider)

    async def retrieval_service(self) -> Any:  # noqa: ANN401
        return await self._resolve(self._container.retrieval_service)

    async def blob_store(self) -> Any:  # noqa: ANN401
        return await self._resolve(self._container.blob_store)

    async def entity_vector_store(self, org_id: str | None = None) -> Any:  # noqa: ANN401
        from app.utils.entity_vector_store import load_entity_vector_store

        # The entities collection is per org (main #4207), as the query service's routes read it.
        return await load_entity_vector_store(self._container, self.logger(), org_id)

    @staticmethod
    async def _resolve(provider: Any) -> Any:  # noqa: ANN401
        # An async resource hands back an awaitable until it is initialized.
        value = provider()
        return await value if inspect.isawaitable(value) else value


__all__ = ["ContainerServices", "HarnessServices", "RunServices"]
