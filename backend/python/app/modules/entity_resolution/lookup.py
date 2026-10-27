"""Entity-resolution lookups that never answer "no match" for "could not look".

A vector or graph lookup that raises is retried once, then raised as
``EntityLookupUnavailableError``. Callers let it propagate: the record's
resolution is retried by redelivery (taxonomy) or by the named-entity persist
retry, and no node is created for a name whose lookup did not run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from app.exceptions.indexing_exceptions import EntityLookupUnavailableError
from app.telemetry.modules import entity_resolution_metrics as metrics
from app.utils.retry import retry_async

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

T = TypeVar("T")

# One quick retry absorbs a blip. Under a timeout storm each attempt can last
# the store's whole timeout, so more attempts here would only multiply the load;
# a longer outage is left to the redelivery backoff.
LOOKUP_ATTEMPTS = 2
LOOKUP_RETRY_DELAY_SECONDS = 0.5


async def run_lookup(
    call: Callable[[], Awaitable[T]],
    *,
    lookup: str,
    logger: logging.Logger,
    on_failure: Callable[[], None] | None = None,
) -> T:
    """``call()``, retried; raises ``EntityLookupUnavailableError`` when every
    attempt failed. ``on_failure`` runs once per failed attempt. Logs only the
    error type: a graph or vector error can echo the names it was asked for."""

    async def attempt() -> T:
        try:
            return await call()
        except Exception as exc:
            metrics.record_lookup_error(lookup, "failed")
            if on_failure is not None:
                on_failure()
            logger.warning("entity lookup %s failed: %s", lookup, type(exc).__name__)
            raise

    try:
        return await retry_async(
            attempt,
            max_attempts=LOOKUP_ATTEMPTS,
            base_delay_seconds=LOOKUP_RETRY_DELAY_SECONDS,
        )
    except Exception as exc:
        metrics.record_lookup_error(lookup, "deferred")
        raise EntityLookupUnavailableError(
            f"Entity lookup '{lookup}' could not run; resolution is deferred",
            details={"lookup": lookup, "error": type(exc).__name__},
        ) from exc


__all__ = ["LOOKUP_ATTEMPTS", "LOOKUP_RETRY_DELAY_SECONDS", "EntityLookupUnavailableError", "run_lookup"]
