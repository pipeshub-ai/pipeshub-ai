"""Clock port shared by leases, deadlines, timers and TTLs."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol

_SUSPEND_AWARE_CLOCK_ID: int = getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC)


class Clock(Protocol):
    def now(self) -> datetime:
        """Wall-clock time (UTC). May jump; never use it for durations."""
        ...

    def monotonic(self) -> float:
        """Seconds; stops while the host is suspended."""
        ...

    def suspend_aware(self) -> float:
        """Seconds; keeps counting while the host is suspended.

        Lease self-fencing must use this: a worker that slept through its
        lease has lost it even though ``monotonic()`` barely moved.
        """
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def suspend_aware(self) -> float:
        # Linux CLOCK_MONOTONIC stops during suspend, BOOTTIME does not; on
        # macOS CLOCK_MONOTONIC already includes sleep.
        return time.clock_gettime(_SUSPEND_AWARE_CLOCK_ID)


class StoreTime:
    """The journal's clock (DB time) as one attempt reads it: the store's reading at the claim, carried forward on
    this process's suspend-aware clock (R-8, CH-14).

    A run's deadline and a yield's ``notBefore`` are instants on the store's clock. Read against this node's wall
    clock, a stepped or skewed clock would end a run early, or park its retry for as long as the step. ``store_ms``
    ``None`` (a journal that names no reading) falls back to the wall clock."""

    def __init__(self, clock: Clock, *, store_ms: int | None, at: float) -> None:
        self._clock = clock
        self._store_ms = store_ms
        self._at = at

    def now_s(self) -> float:
        """Epoch seconds on the store's clock."""
        if self._store_ms is None:
            return self._clock.now().timestamp()
        return self._store_ms / 1000 + (self._clock.suspend_aware() - self._at)

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.now_s(), tz=UTC)
