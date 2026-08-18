"""Cross-cutting backpressure signal shared between downstream service HTTP
clients (parsing, docling, embedding) and the indexing message consumers.

When a downstream service returns HTTP 429 with a ``Retry-After`` header,
the client that hit it already backs off on its own bounded retry schedule
(see ``BaseServiceClient._request_with_retry``) — but that alone doesn't
stop the consumer from claiming *more* messages off the event bus that would
just queue up behind the same saturated service, growing
``MAX_PENDING_INDEXING_TASKS`` worth of stuck records instead of leaving
them on the broker where they belong. ``BackpressureCoordinator`` closes
that gap: any client signals it on a 429, and the consumer's read loop
checks it before every poll, pausing new reads for the signalled duration
instead of admitting more work it can't yet process.

Single-event-loop use only (like ``CircuitBreaker`` in ``base_client.py``):
one coordinator per indexing worker process/loop, shared by every service
client and the consumer that loop drives.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class BackpressureCoordinator:
    """Tracks the furthest-future pause deadline signalled by any service.

    A later signal from a *different* service while already paused extends
    (never shortens) the shared pause deadline — the consumer must wait for
    whichever downstream service recovers last, since resuming early would
    just immediately re-admit work for the still-saturated one.
    """

    def __init__(self, *, clock: "Callable[[], float]" = time.monotonic) -> None:
        self._clock = clock
        self._pause_until: dict[str, float] = {}

    def signal(self, service_name: str, retry_after: float) -> None:
        """Record that *service_name* asked us to back off for
        *retry_after* seconds. A no-op for a non-positive duration."""
        if retry_after <= 0:
            return
        until = self._clock() + retry_after
        previous = self._pause_until.get(service_name, 0.0)
        if until > previous:
            self._pause_until[service_name] = until
            logger.info(
                "Backpressure signalled by %s: pausing consumption for %.1fs",
                service_name, retry_after,
            )

    def is_paused(self) -> bool:
        return self.pause_remaining() > 0.0

    def pause_remaining(self) -> float:
        """Seconds until every signalled service's pause has cleared (0 if
        none are currently paused). Expired entries are pruned as a side
        effect so ``paused_services`` never reports a stale name."""
        now = self._clock()
        expired = [name for name, until in self._pause_until.items() if until <= now]
        for name in expired:
            del self._pause_until[name]
        if not self._pause_until:
            return 0.0
        return max(0.0, max(self._pause_until.values()) - now)

    @property
    def paused_services(self) -> frozenset[str]:
        """Which services currently have an unexpired pause signalled."""
        self.pause_remaining()  # prune as a side effect
        return frozenset(self._pause_until)


# Process-wide default instance. Service clients (ParsingClient,
# DoclingClient, EmbeddingServerEmbeddings) are constructed from many places
# — DI container factories, EventProcessor, per-tenant AI-model helpers — and
# threading an explicit coordinator through every one of those call chains
# would touch far more of the codebase than the feature warrants. Call sites
# that already hold a specific coordinator (e.g. a test, or a consumer's own
# wiring) should still pass it explicitly; this is only the fallback so the
# indexing service's consumer and its downstream clients agree on one
# instance without every constructor needing a new parameter threaded in.
_default_coordinator: BackpressureCoordinator | None = None


def get_default_backpressure_coordinator() -> BackpressureCoordinator:
    """Return the process-wide :class:`BackpressureCoordinator`, creating it
    on first use."""
    global _default_coordinator
    if _default_coordinator is None:
        _default_coordinator = BackpressureCoordinator()
    return _default_coordinator


def set_default_backpressure_coordinator(coordinator: BackpressureCoordinator | None) -> None:
    """Override the process-wide default. Pass ``None`` to reset it back to
    lazy creation (mainly for test isolation)."""
    global _default_coordinator
    _default_coordinator = coordinator
