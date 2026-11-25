"""Counter deltas for v3: what was counted since the last batch was cut."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping


class DeltaTracker:
    """Holds each counter series' cumulative value at the last cut.

    The registry keeps counting upwards for the life of the process; v3 wants
    what was counted since the previous push. ``cut`` hands out that difference
    and remembers the new value. The caller cuts only when it has no batch
    waiting for an acknowledgement, so a count is in exactly one batch.
    """

    def __init__(self) -> None:
        self._baseline: dict[Hashable, float] = {}

    def cut(self, current: Mapping[Hashable, float], limit: int | None = None) -> dict[Hashable, float]:
        """The positive differences since the last cut, in key order, at most ``limit`` of them.

        A value below its baseline means the registry was reset, so it counts
        from zero. A series left out by ``limit`` keeps its baseline and is in
        the next cut. A series that is gone from ``current`` is forgotten.
        """
        deltas: dict[Hashable, float] = {}
        for key in sorted(current):  # type: ignore[type-var]
            value = current[key]
            base = self._baseline.get(key, 0.0)
            if value < base:
                base = 0.0
                self._baseline[key] = 0.0
            delta = value - base
            if delta <= 0:
                continue
            if limit is not None and len(deltas) >= limit:
                continue
            deltas[key] = delta
            self._baseline[key] = value
        for key in [k for k in self._baseline if k not in current]:
            del self._baseline[key]
        return deltas

    def rebase(self, current: Mapping[Hashable, float]) -> None:
        """Take ``current`` as already delivered (it went out some other way)."""
        self._baseline = dict(current)

    def baseline(self, key: Hashable) -> float:
        return self._baseline.get(key, 0.0)
