"""The Redis handle the fleet breaker and the pause budget share.

The indexing service binds it once at start-up, from the client and key namespace of its retry
tracker (``RetryManager``). A process that never binds it (a service that does not run the stage
runtime, a unit test) keeps the per-process breaker the gateway has always had. Once it is bound,
the breaker lives in Redis and **an unreadable Redis pauses paid calls** instead of falling back to a
per-process breaker, so a replica can never decide an outage is over on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

RedisFactory = Callable[[], Any]


class FleetRedis:
    """A Redis client factory (the client of the calling event loop) and the install's key namespace."""

    def __init__(
        self, client: RedisFactory, namespace: str = "", *, clock_ms: Callable[[], int] | None = None
    ) -> None:
        self._client = client
        self._namespace = namespace
        # A test seam (a simulated clock for the breaker scripts); production reads Redis ``TIME``.
        self.clock_ms = clock_ms

    def client(self) -> Any:  # noqa: ANN401 - redis.asyncio / cluster clients
        return self._client()

    @property
    def namespace(self) -> str:
        return self._namespace

    def key(self, name: str) -> str:
        return f"{self._namespace}:{name}" if self._namespace else name


_bound: FleetRedis | None = None


def bind_fleet_redis(handle: FleetRedis | None) -> None:
    """Bind (or with ``None`` unbind) the process's fleet Redis."""
    global _bound  # noqa: PLW0603 - one handle per process, bound at start-up
    _bound = handle


def get_fleet_redis() -> FleetRedis | None:
    return _bound


async def open_fleet_redis(logger: Any, redis_config: Any) -> tuple[FleetRedis, Any]:  # noqa: ANN401
    """Open the fleet's own Redis client registry from the service's Redis config, and return the handle with the
    registry (the caller closes it with ``aclose``).

    It does not go through the retry tracker: trackers wrap Redis (``FailSafeRetryTracker``) or PostgreSQL
    (``PgRetryTracker``) and only ``RetryManager`` has a client. The registry goes through the Redis connection provider
    of the configured ``REDIS_MODE``, so every mode (standalone, cluster, in-process, PostgreSQL-backed) gets a client.
    """
    from app.services.messaging.config import messaging_env
    from app.services.messaging.redis_client import RedisClientRegistry

    registry = RedisClientRegistry(
        logger,
        redis_config,
        max_connections=messaging_env.concurrency_redis_max_connections,
        socket_timeout_seconds=messaging_env.concurrency_redis_timeout_seconds,
    )
    await registry.client().ping()
    return FleetRedis(registry.client, registry.provider.key_namespace), registry


async def fleet_redis_config(config_service: Any) -> Any:  # noqa: ANN401 - ConfigurationService -> RedisConfig
    """The Redis every service's fleet breaker and pause budget live in: the configured one, whatever the message broker.
    It must be the same server in every process, so it is never a default chosen by broker type (a PostgreSQL broker
    does not mean Redis is on localhost); with nothing stored, the configuration falls back to the Redis environment."""
    return await config_service.get_redis_config()


async def bind_service_fleet(config_service: Any, logger: Any) -> Any:  # noqa: ANN401
    """Bind the fleet Redis of a service that makes paid model calls but does not run the stage runtime (the Extraction and
    parsing services): the same breaker state as the indexing service, so an outage seen by one is seen by all.

    Returns the client registry for the caller to ``aclose`` at shutdown."""
    from app.services.llm_gateway.fleet_breaker import load_install_secret

    await load_install_secret(config_service)
    fleet, registry = await open_fleet_redis(logger, await fleet_redis_config(config_service))
    bind_fleet_redis(fleet)
    logger.info("Fleet breaker bound to Redis (namespace %r)", fleet.namespace)
    return registry
