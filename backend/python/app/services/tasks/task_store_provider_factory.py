"""Factories for constructing task-engine ports, following the house
convention set by `GraphDBProviderFactory`/`VectorDBProviderFactory`
(static-method factory, `create_*` classmethods, no DI-container coupling).

Split in two because `ITaskStore` and `ITriggerStore`/`ITaskRunStore` have
different backends and different connection lifecycles (a shared
`IGraphDBProvider` the caller already owns, vs. a Redis client this factory
constructs and owns) -- see Part B5 of the task engine plan.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from redis.asyncio import Redis

from app.services.tasks.adapters.graph.task_store import GraphTaskStore
from app.services.tasks.adapters.redis.run_store import RedisRunStore
from app.services.tasks.adapters.redis.trigger_store import RedisTriggerStore

if TYPE_CHECKING:
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.messaging.config import RedisConfig
    from app.services.tasks.interface.run_store import ITaskRunStore
    from app.services.tasks.interface.task_store import ITaskStore
    from app.services.tasks.interface.trigger_store import ITriggerStore


class TaskStoreProviderFactory:
    """Builds `ITaskStore` over an already-connected `IGraphDBProvider` --
    the caller (DI container) owns the provider's lifecycle; this factory
    never connects/disconnects it."""

    @staticmethod
    def create_provider(graph_provider: "IGraphDBProvider") -> ITaskStore:
        return GraphTaskStore(graph_provider)


class TaskScheduleStoreFactory:
    """Builds `ITriggerStore`/`ITaskRunStore` over a dedicated Redis
    connection. Field-by-field client construction (not `from_url`),
    matching `DistributedConcurrencyManager`'s convention for
    scheduling-critical Redis clients."""

    @staticmethod
    async def create_redis_client(redis_config: RedisConfig, *, operation_timeout_seconds: float = 5.0) -> Redis:
        client = Redis(
            host=redis_config.host,
            port=redis_config.port,
            password=redis_config.password,
            db=redis_config.db,
            decode_responses=True,
            socket_timeout=operation_timeout_seconds,
            socket_connect_timeout=operation_timeout_seconds,
        )
        await client.ping()
        return client

    @staticmethod
    async def create_trigger_store(
        logger: Logger, redis_config: RedisConfig, *, redis_client: Redis | None = None,
    ) -> ITriggerStore:
        client = redis_client or await TaskScheduleStoreFactory.create_redis_client(redis_config)
        logger.info("TaskScheduleStoreFactory: RedisTriggerStore connected")
        return RedisTriggerStore(client)

    @staticmethod
    async def create_run_store(
        logger: Logger,
        redis_config: RedisConfig,
        *,
        redis_client: Redis | None = None,
        graph_provider: "IGraphDBProvider | None" = None,
    ) -> ITaskRunStore:
        """Redis-backed, wrapped in `ArchivingRunStore` when a graph provider
        is available so finished runs survive their Redis TTL.

        Without a provider the store still works and still expires runs -- it
        just has nowhere to put the history, so nothing is expired either (see
        `ArchivingRunStore._archive_terminal`).
        """
        client = redis_client or await TaskScheduleStoreFactory.create_redis_client(redis_config)
        store: ITaskRunStore = RedisRunStore(client)
        if graph_provider is None:
            logger.warning(
                "TaskScheduleStoreFactory: RedisRunStore connected without a run archive -- "
                "run history is limited to what Redis holds",
            )
            return store

        from app.services.tasks.adapters.archiving_run_store import ArchivingRunStore
        from app.services.tasks.adapters.graph.run_archive import GraphRunArchive

        logger.info("TaskScheduleStoreFactory: RedisRunStore connected (archiving to graph)")
        return ArchivingRunStore(store, GraphRunArchive(graph_provider))
