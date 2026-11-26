"""In-memory ``ReconcileLookup``: a provider that holds effects by tag, with an index lag."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from harness_core.effects.reconcile import (
    LookupAbsent,
    LookupFound,
    LookupUndecidable,
    ReconcileEvidence,
    ReconcileTiming,
    effect_tag,
    refuse_write_while_reconciling,
)

if TYPE_CHECKING:
    from datetime import datetime

    from harness_core.clock import Clock
    from harness_core.effects.reconcile import LookupResult, UnknownEffect


@dataclass
class InMemoryReconcileLookup:
    """``write`` records an effect under its tag (refused while reconciling); ``lookup``
    sees it once ``index_lag_s`` passed. ``fail_next`` makes lookups raise;
    ``undecidable`` names argument keys whose presence makes a call undecidable."""

    clock: Clock
    timing: ReconcileTiming = field(
        default_factory=lambda: ReconcileTiming(tool_timeout_s=60.0, read_after_write=True)
    )
    undecidable: frozenset[str] = frozenset()
    fail_next: int = 0
    lookups: list[datetime] = field(default_factory=list["datetime"])
    writes: int = 0
    _held: dict[str, tuple[datetime, ReconcileEvidence]] = field(
        default_factory=dict[str, tuple["datetime", ReconcileEvidence]]
    )

    def write(self, effect_key: str, evidence: ReconcileEvidence) -> None:
        refuse_write_while_reconciling("in_memory.write")
        self.writes += 1
        self._held.setdefault(effect_tag(effect_key), (self.clock.now(), evidence))

    async def lookup(self, effect: UnknownEffect) -> LookupResult:
        self.lookups.append(self.clock.now())
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ConnectionError("provider unavailable")
        if self.undecidable & set(effect.arguments):
            return LookupUndecidable("arguments")
        held = self._held.get(effect.tag)
        if held is None:
            return LookupAbsent()
        written, evidence = held
        if self.clock.now() - written < timedelta(seconds=self.timing.index_lag_s):
            return LookupAbsent()
        return LookupFound(evidence)


__all__ = ["InMemoryReconcileLookup"]
