"""Which redis-py errors a vector search may retry."""

from __future__ import annotations

from app.exceptions.non_retryable import NonRetryableError
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


class FilterFieldNotIndexed(NonRetryableError):
    """A query names a field the index schema does not hold.

    RediSearch's default query dialect (1) drops a clause on such a field, so
    the query matches every document; a delete built on it would remove the
    whole collection. The provider refuses the query instead, and never
    narrows it to the fields it can honour.
    """

    def __init__(self, index: str, fields: "frozenset[str] | set[str]") -> None:
        self.index = index
        self.fields = frozenset(fields)
        super().__init__(
            f"Redis index '{index}' has no field(s) {sorted(self.fields)} in its schema; "
            "a filter on them cannot be honoured. Add the field with create_index() first."
        )


class IndexBackfilling(RuntimeError):
    """The index is still reading documents that were already in Redis (after ``FT.CREATE`` or ``FT.ALTER``, or while loading a dump).

    A search over it sees only some of them, so a delete or a payload write built on one would report success and leave the rest.
    Retry once ``FT.INFO`` reports ``indexing`` 0; nothing was changed.
    """

    def __init__(self, index: str, percent_indexed: float | None) -> None:
        self.index = index
        self.percent_indexed = percent_indexed
        done = "unknown" if percent_indexed is None else f"{percent_indexed:.1%}"
        super().__init__(f"Redis index '{index}' is still indexing existing documents ({done} done); retry later")
