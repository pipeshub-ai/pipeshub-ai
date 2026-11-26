"""The deployment's Redis for harness counters and request-scoped state (spend, request grants, task leases)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.services.redis.connection_provider import RedisClient

_LOG = HarnessLog.named("app.harness.redis")


async def harness_redis(config_service: ConfigurationService) -> tuple[RedisClient, str] | None:
    """A client and the deployment's key namespace; ``None`` (logged once) when Redis is unreachable."""
    from app.services.redis.config import ClientOptions, RedisConnectionConfig
    from app.services.redis.connection_provider_factory import get_redis_provider

    try:
        provider = get_redis_provider(RedisConnectionConfig.from_redis_config(await config_service.get_redis_config()))
        client = provider.create_client(ClientOptions(decode_responses=True))
        _ = await client.ping()  # pyright: ignore[reportUnknownMemberType]
    except Exception as exc:
        _LOG.warning("harness.redis_unreachable", errorType=type(exc).__name__)
        return None
    return client, provider.key_namespace or ""


__all__ = ["harness_redis"]
