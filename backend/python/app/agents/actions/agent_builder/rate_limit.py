"""Per-user daily cap on agent drafts, counted in Redis."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

DAILY_DRAFT_LIMIT = 20
_KEY_TTL_SECONDS = 90_000
_REDIS_TIMEOUT_SECONDS = 2.0

_fail_open_count = 0


def fail_open_count() -> int:
    return _fail_open_count


def draft_key(org_id: str, user_id: str, now: datetime | None = None) -> str:
    day = (now or datetime.now(UTC)).strftime("%Y%m%d")
    return f"agentdraft:{org_id}:{user_id}:{day}"


async def _redis_client(config_service: ConfigurationService) -> Any:  # noqa: ANN401
    from app.services.redis.config import RedisConnectionConfig
    from app.services.redis.connection_provider_factory import (
        get_prepared_redis_provider,
    )

    cfg = await config_service.get_redis_config()
    provider = await get_prepared_redis_provider(
        RedisConnectionConfig.from_host_port(
            host=cfg.host, port=cfg.port, password=cfg.password, db=cfg.db, tls=cfg.tls,
        )
    )
    return provider.get_client()


async def _count(config_service: ConfigurationService, key: str) -> int:
    client = await _redis_client(config_service)
    count = int(await client.incr(key))
    await client.expire(key, _KEY_TTL_SECONDS)
    return count


async def allow_draft(config_service: ConfigurationService, org_id: str, user_id: str) -> bool:
    """False once the user is over the daily cap. Fails open when Redis is unreachable:
    a draft has no side effects, so a Redis outage must not take the tool down."""
    global _fail_open_count  # noqa: PLW0603
    key = draft_key(org_id, user_id)
    try:
        count = await asyncio.wait_for(_count(config_service, key), _REDIS_TIMEOUT_SECONDS)
    except Exception as exc:
        _fail_open_count += 1
        logger.warning("agent draft rate limit unavailable, allowing draft (fail-open #%d): %s", _fail_open_count, exc)
        return True
    return count <= DAILY_DRAFT_LIMIT
