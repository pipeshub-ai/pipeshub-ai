"""Batched compaction: a layer compacts in batches, down to a low-water mark.

A layer fires once the context is over its trigger. If it compacted only what
had just aged past its window, the next call would be over the trigger again
and compact one more message, near the end of the request: every call would
rewrite the sent list and miss the provider's prompt cache. At small windows
that is the steady state, because the turns a layer must keep (its
keep-last-N, or a tail ratio) already sit above the trigger on their own.

So compaction comes in batches. A pass runs only when it can free at least the
band between the trigger and a low-water mark a fixed fraction of the budget
below it; a smaller pass waits until enough has aged past the layer's floor.
A pass that runs compacts everything the floor allows, which takes the context
to or below the low-water mark whenever the floor permits. (Stopping right at
the mark kept more verbatim but, at large windows, brought the next batch
sooner than compacting everything did.) The sliding window, which can evict
anything unprotected, evicts down to the mark exactly. Between batches the
calls only append, and the memo (``compaction_memo.py``) replays every earlier
batch identically, whether or not the context is over the trigger on that call.

Each layer keeps its own floor (keep-last-N, tail, protected messages); a
batch never compacts past it, it only waits longer before compacting what
crossed it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent_loop_lib.context.base import ContextBudget

#: The band between a layer's trigger and its low-water mark, as a fraction of
#: the budget: where one tool result is a tenth to a quarter of the budget
#: (8K-32K windows), 0.2 still let every other call compact, and a wider band
#: only makes the sliding window evict more than it must.
LOW_WATER_FRACTION = 0.3


@dataclass(frozen=True, slots=True)
class Watermarks:
    """One layer's trigger and the low-water mark below it."""

    trigger: int
    low_water: int

    @property
    def band(self) -> int:
        return self.trigger - self.low_water

    def worth_compacting(self, total: int, savings: int) -> bool:
        """Whether a pass that could free ``savings`` tokens (everything the
        layer may compact) should run now on a context of ``total`` tokens:
        over the trigger, and able to free at least the band. A smaller pass
        is the one-message-per-call drip that breaks the prompt cache; its
        candidates wait until there are enough of them. A layer set to fire
        at any size (no band below its trigger) compacts whatever it may on
        every call.
        """
        if total <= self.trigger:
            return False
        return self.band <= 0 or savings >= self.band


def watermarks(budget: ContextBudget, trigger: int) -> Watermarks:
    """The marks of a layer that fires above ``trigger`` tokens."""
    low_water = trigger - int(budget.effective_max_tokens * LOW_WATER_FRACTION)
    return Watermarks(trigger=trigger, low_water=max(low_water, 0))


__all__ = ["LOW_WATER_FRACTION", "Watermarks", "watermarks"]
