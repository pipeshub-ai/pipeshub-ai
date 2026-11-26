"""Asks indexing to slow down while code sandbox runs wait for memory.

``LiveLimit`` calls ``on_memory_wait`` on the event loop while a run waits
for memory that indexing shares (``LiveLimit.waits_for_memory``). After the
run has waited ``YIELD_MIN_WAIT_S``, this worker refreshes the shared yield
signal at most once per ``YIELD_REFRESH_S``. Nothing ever clears it: another
worker may still be waiting, so the signal ends by its TTL once the last
waiter stops refreshing. The worker that started an episode logs its end,
seen from the sampler.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.sandbox.capacity import HoldReason
    from app.agent_loop_lib.sandbox.live_limit import LiveLimit
    from app.agent_loop_lib.sandbox.live_settings import LiveSettings
    from app.services.resource_governor.yield_signal import YieldSignal

__all__ = ["YIELD_MIN_WAIT_S", "YIELD_REFRESH_S", "IndexingYield"]

logger = logging.getLogger(__name__)

# A momentary wait must not touch indexing.
YIELD_MIN_WAIT_S = 3.0
YIELD_REFRESH_S = 5.0
GiB = 2**30


def _gib(value: float | None) -> str:
    return "?" if value is None else f"{value / GiB:.1f}GiB"


class IndexingYield:
    """This worker's writer of the indexing back-off signal."""

    def __init__(
        self, live: LiveLimit, signal: YieldSignal | None, settings: LiveSettings, *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._live = live
        self._signal = signal
        self._settings = settings
        self._clock = clock
        self._refreshed_at: float | None = None
        # The episode this worker started, until it sees it end.
        self._episode: float | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.yield_indexing and self._signal is not None

    def on_memory_wait(self, reason: HoldReason, waited_s: float) -> None:
        """Refresh the signal. One non-blocking lock try: a busy lock is
        tried again on the run's next poll."""
        signal = self._signal
        if signal is None or not self._settings.yield_indexing or waited_s < YIELD_MIN_WAIT_S:
            return
        now = self._clock()
        if self._refreshed_at is not None and 0 <= now - self._refreshed_at < YIELD_REFRESH_S:
            return
        refreshed = signal.refresh(reason=reason.value, ttl_s=self._settings.yield_ttl_s, now=now)
        if refreshed is None:
            return
        self._refreshed_at = now
        if refreshed.started:
            self._episode = refreshed.since
            view = self._live.view()
            logger.info(
                "sandbox runs waiting for host memory (host_avail=%s floor=%s reason=%s); "
                "asking indexing to slow down",
                _gib(view.mem_available_bytes), _gib(self._live.hard_floor_bytes()), reason.value,
            )

    def check_lapsed(self) -> None:
        """Log the end of the episode this worker started. Reads a file: run
        it off the event loop."""
        signal, since = self._signal, self._episode
        if signal is None or since is None:
            return
        now = self._clock()
        record = signal.read()
        if record is not None and record.since == since and record.active(now):
            return
        self._episode = None
        ended = record.expires_at if record is not None and record.since == since else now
        logger.info(
            "sandbox runs no longer waiting for memory; indexing back-off released after %.0fs",
            max(0.0, min(ended, now) - since),
        )
