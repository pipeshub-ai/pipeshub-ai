"""One bounded retry for a vector search that hit a transient store error.

Lives behind ``IVectorDBService`` so every provider gets it; which errors count
as transient is each provider's call (``transient_search_error``), plus the
builtin timeout and connection errors every client can surface.

The retry is bounded by time, not just by count. A search that failed fast (a
dropped connection) gets a full second attempt; one that failed after a long
timeout gets only what is left of the budget, and none at all when too little
is left, so a retry never takes a search much past ``budget_seconds``.

The budget bounds the whole search. The store clients' own timeouts default
to 300 s, which would use up any budget before a retry could start, so the
first attempt is cut off early enough to leave room for one
(``_first_attempt_seconds``). A healthy search answers in well under a second;
one still running near the budget is the store failing, not a slow answer.

A request with a deadline of its own (``app/utils/request_deadline``) cuts the
budget further, so a search fails here early enough to answer its caller: with
results from the collections that did answer, or a clear error, rather than
nothing at all once the caller has stopped waiting.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING

from app.services.vector_db.models import SearchFailureKind
from app.telemetry.modules.vector_search_metrics import record_search_retry
from app.utils.request_deadline import seconds_left
from app.utils.retry import retry_async
from app.utils.stage_timer import span

if TYPE_CHECKING:
    import logging

    from app.services.vector_db.interface.vector_db import IVectorDBService
    from app.services.vector_db.models import HybridSearchRequest, SearchResult


def _budget_from_env() -> float:
    raw = os.getenv("VECTOR_SEARCH_RETRY_BUDGET_SECONDS", "60")
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 60.0


# Both attempts together. Sized to the latency a chat turn already tolerates
# when a search waits out a store timeout; raise it for a store that is slow
# when healthy.
SEARCH_RETRY_BUDGET_SECONDS = _budget_from_env()
# Below this a second attempt against a store that just timed out is not worth
# the load it adds.
MIN_RETRY_SECONDS = 5.0
_RETRY_DELAY_SECONDS = 0.25


def _reserve_from_env() -> float:
    raw = os.getenv("SEARCH_DEADLINE_RESERVE_SECONDS", "10")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 10.0


# Under a request deadline (``app/utils/request_deadline``), what the vector
# search leaves for the rest of the search: checking access to the hits,
# fetching their records and content, and the reply.
DEADLINE_RESERVE_SECONDS = _reserve_from_env()
# A vector search with less than this left would only add load to a store that
# is already slow; it fails at once instead, as a timeout.
MIN_DEADLINE_BUDGET_SECONDS = 1.0


def _first_attempt_seconds(budget: float) -> float:
    """How long the first attempt may run: the budget less room for a retry,
    unless the budget is too small to hold both."""
    reserve = MIN_RETRY_SECONDS + _RETRY_DELAY_SECONDS
    return budget - reserve if budget >= 2 * reserve else budget


class VectorSearchUnavailableError(Exception):
    """The vector store could not answer a search, after the retry if one ran."""

    def __init__(self, kind: SearchFailureKind, collection_name: str) -> None:
        super().__init__(f"vector search on '{collection_name}' failed: {kind.value}")
        self.kind = kind
        self.collection_name = collection_name


def classify_search_error(
    service: "IVectorDBService", error: BaseException,
) -> SearchFailureKind | None:
    if isinstance(error, VectorSearchUnavailableError):
        return error.kind
    if isinstance(error, TimeoutError):
        return SearchFailureKind.TIMEOUT
    if isinstance(error, ConnectionError):
        return SearchFailureKind.UNAVAILABLE
    kind = service.transient_search_error(error)
    # A mocked service answers with a mock; only a real verdict counts.
    return kind if isinstance(kind, SearchFailureKind) else None


async def query_nearest_points_with_retry(
    service: "IVectorDBService",
    collection_name: str,
    requests: list["HybridSearchRequest"],
    *,
    logger: "logging.Logger",
    budget_seconds: float | None = None,
) -> list[list["SearchResult"]]:
    """``service.query_nearest_points``, retried once on a transient error.

    Raises ``VectorSearchUnavailableError`` when the store still cannot answer
    (or there was no time to ask again); any other error propagates unchanged.

    Under a request deadline the budget is cut to what the deadline leaves after
    ``DEADLINE_RESERVE_SECONDS``, so the search can still answer its caller.
    With too little left, it fails at once as a timeout.
    """
    budget = SEARCH_RETRY_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    left = seconds_left()
    if left is not None:
        fitted = min(budget, left - DEADLINE_RESERVE_SECONDS)
        if fitted < MIN_DEADLINE_BUDGET_SECONDS:
            logger.warning(
                "Vector search on '%s' not started: %.1fs left before the request's deadline",
                collection_name, left,
            )
            raise VectorSearchUnavailableError(SearchFailureKind.TIMEOUT, collection_name)
        budget = fitted
    with span("vector"):
        return await _query_with_retry(service, collection_name, requests, logger=logger, budget=budget)


async def _query_with_retry(
    service: "IVectorDBService",
    collection_name: str,
    requests: list["HybridSearchRequest"],
    *,
    logger: "logging.Logger",
    budget: float,
) -> list[list["SearchResult"]]:
    started = time.monotonic()
    deadline = started + budget
    attempts = 0

    async def _attempt() -> list[list["SearchResult"]]:
        nonlocal attempts
        attempts += 1
        call = service.query_nearest_points(collection_name=collection_name, requests=requests)
        until = started + _first_attempt_seconds(budget) if attempts == 1 else deadline
        return await asyncio.wait_for(call, timeout=max(0.0, until - time.monotonic()))

    def _should_retry(error: Exception) -> bool:
        if classify_search_error(service, error) is None:
            return False
        if deadline - time.monotonic() < MIN_RETRY_SECONDS:
            record_search_retry(_provider_name(service), "skipped_budget")
            logger.warning(
                "Vector search on '%s' failed after %.1fs; no time left in the %.0fs budget to retry",
                collection_name, budget - (deadline - time.monotonic()), budget,
            )
            return False
        return True

    try:
        results = await retry_async(
            _attempt,
            max_attempts=2,
            base_delay_seconds=_RETRY_DELAY_SECONDS,
            logger=logger,
            description=f"vector search on '{collection_name}'",
            should_retry=_should_retry,
        )
    except Exception as error:
        kind = classify_search_error(service, error)
        if attempts > 1:
            record_search_retry(_provider_name(service), "failed")
        if kind is None:
            raise
        raise VectorSearchUnavailableError(kind, collection_name) from error
    if attempts > 1:
        record_search_retry(_provider_name(service), "recovered")
        logger.warning("Vector search on '%s' recovered on retry", collection_name)
    return results


def _provider_name(service: "IVectorDBService") -> str:
    name = service.get_service_name()
    return name if isinstance(name, str) else "unknown"
