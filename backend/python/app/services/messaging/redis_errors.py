"""Translate a failed Redis coordination call into downstream feedback.

Shared by the lease renewer and the consumers' lease/retry helpers, which sit
at different layers and must not import each other.
"""
from __future__ import annotations

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.services.resource_governor.feedback import get_default_downstream_feedback

_REDIS_POOL_EXHAUSTED_MARKER = "No connection available"


def report_redis_error(error: BaseException) -> None:
    """Tell the governor what a failed Redis call means: an exhausted client
    pool, a timed-out command, or a Redis it cannot reach."""
    feedback = get_default_downstream_feedback()
    if isinstance(error, RedisConnectionError) and _REDIS_POOL_EXHAUSTED_MARKER in str(error):
        feedback.report_pool_exhausted("redis")
    elif isinstance(error, RedisTimeoutError):
        feedback.report_timeout("redis")
    elif isinstance(error, (RedisConnectionError, OSError)):
        feedback.report_unavailable("redis")
