"""``ManualWakeup``: an ``InteractionWakeup`` on a ``ManualClock``.

An un-woken wait advances the clock by its timeout instead of sleeping, so a
two-minute hold runs in no time; ``on_wait(n)`` runs before the ``n``-th wait,
where a test plays Node (resolves an interaction, cancels, lets the lease lapse).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.testing.clock import ManualClock


@dataclass
class ManualWakeup:
    clock: ManualClock
    on_wait: Callable[[int], None] | None = None
    waits: int = 0
    woken: int = 0
    waited_s: float = 0.0
    _pending: set[str] = field(default_factory=set[str])

    def wake(self, run_id: str) -> None:
        self._pending.add(run_id)

    async def wait(self, run_id: str, timeout_s: float) -> bool:
        self.waits += 1
        if self.on_wait is not None:
            self.on_wait(self.waits)
        if run_id in self._pending:
            self._pending.discard(run_id)
            self.woken += 1
            return True
        _ = self.clock.advance(timeout_s)
        self.waited_s += timeout_s
        return False


__all__ = ["ManualWakeup"]
