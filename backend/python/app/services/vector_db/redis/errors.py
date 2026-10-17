"""Which redis-py errors a vector search may retry."""

from __future__ import annotations

from app.services.vector_db.models import SearchFailureKind
from redis.exceptions import (
    AuthenticationError,
    AuthorizationError,
)
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


def classify_redis_search_error(error: BaseException) -> SearchFailureKind | None:
    if isinstance(error, RedisTimeoutError):
        return SearchFailureKind.TIMEOUT
    # redis-py derives both from ConnectionError; a wrong password is not transient.
    if isinstance(error, (AuthenticationError, AuthorizationError)):
        return None
    if isinstance(error, RedisConnectionError):
        return SearchFailureKind.UNAVAILABLE
    return None
