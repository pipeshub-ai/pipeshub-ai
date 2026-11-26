"""A per-run cap on LiveBus frames (SEC-DOS-02, C2.31): a run flooding the bus loses frames, the bus does not.

The LiveBus is best effort (the journal is the record): a frame over a run's rate is dropped where it is
published, before it costs the bus a write, and counted. Every run has its own bucket, so one run's flood
never takes another's frames; the buckets of runs not heard from lately are forgotten, so the table stays
bounded however many runs a worker serves.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable

LOGGER = "harness_core.journal.live_rate"
MAX_RUNS = 4096


@dataclass(frozen=True, slots=True)
class LiveRate:
    """About ten times what a chat streams (25 text frames a second) and a burst of the whole activity buffer."""

    per_second: float = 250.0
    burst: int = 4000


@dataclass
class _Bucket:
    tokens: float
    last: float
    dropped: int = 0


class LiveRateCap:
    def __init__(
        self, rate: LiveRate | None = None, *, clock: Callable[[], float] = time.monotonic, max_runs: int = MAX_RUNS
    ) -> None:
        self._rate = rate or LiveRate()
        self._clock = clock
        self._max_runs = max_runs
        self._runs: OrderedDict[str, _Bucket] = OrderedDict()
        self._log = HarnessLog.named(LOGGER)

    def admit(self, run_id: str) -> bool:
        """Whether a frame of ``run_id`` may be published now; a refused one is counted, the first one logged."""
        now = self._clock()
        bucket = self._runs.get(run_id)
        if bucket is None:
            bucket = self._runs[run_id] = _Bucket(float(self._rate.burst), now)
            if len(self._runs) > self._max_runs:
                _ = self._runs.popitem(last=False)
        else:
            self._runs.move_to_end(run_id)
            bucket.tokens = min(float(self._rate.burst), bucket.tokens + max(0.0, now - bucket.last) * self._rate.per_second)
            bucket.last = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return True
        bucket.dropped += 1
        if bucket.dropped == 1:
            self._log.warning("live.frames_dropped", runId=run_id, reasonCode="live_rate", perSecond=self._rate.per_second)
        return False

    def dropped(self, run_id: str) -> int:
        bucket = self._runs.get(run_id)
        return 0 if bucket is None else bucket.dropped


__all__ = ["LiveRate", "LiveRateCap"]
