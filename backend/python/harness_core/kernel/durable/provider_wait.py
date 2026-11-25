"""How long a run waits out an AI model provider that refuses or fails its calls (P4.9, ADR-RUN-01 FIX-429).

One policy for both durable kernels. A provider that answers 429 (too many requests) or fails in a way waiting can
fix gives the run's slot back (a ``run.yielded`` with ``llm_backoff``) and the run is re-claimed after the wait. The
waits are bounded: at most ``len(PROVIDER_RECHECK_S)`` of them and ``PROVIDER_WAIT_BUDGET_S`` in all, then the run
ends cut short by the provider (``truncatedBy: provider``) and answers from what it gathered. Unbounded, a provider
that kept refusing re-claimed one chat's run every 20 s for hours while the person watched a spinner.

The engine kernel counts its waits from the journal's yields; the loop kernel keeps them in a journaled slot
(``PROVIDER_WAIT_SLOT``), because its yields share the reason with the budget ledger's, which is not the provider's
to bound, and because a call the provider answers starts the count again: the bound is for one outage, not a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from pydantic import JsonValue

# Each wait at least this long, so a provider asking for 1 s does not cost a claim a second.
PROVIDER_RECHECK_S: Final[tuple[float, ...]] = (15.0, 30.0, 60.0, 120.0)
# The provider's own Retry-After is honoured above the schedule, so the time is bounded on its own too.
PROVIDER_WAIT_BUDGET_S: Final = sum(PROVIDER_RECHECK_S)
PROVIDER_WAIT_SLOT: Final = "provider.waits"


@dataclass(frozen=True)
class ProviderWaits:
    """The provider waits a run spent since its model last answered."""

    count: int = 0
    waited_s: float = 0.0

    @classmethod
    def of_count(cls, count: int) -> ProviderWaits:
        """Waits that each took the schedule's delay (the engine kernel's, which never asks the provider)."""
        return cls(count, sum(PROVIDER_RECHECK_S[:count]))

    @classmethod
    def of_slot(cls, value: object) -> ProviderWaits:
        """The journaled slot's value; none for a run that never waited (or an unreadable value)."""
        if not isinstance(value, dict):
            return cls()
        data = cast("dict[str, object]", value)
        count, waited = data.get("count"), data.get("waitedS")
        if not isinstance(count, int) or isinstance(count, bool) or not isinstance(waited, int | float):
            return cls()
        return cls(max(count, 0), max(float(waited), 0.0))

    def slot(self) -> JsonValue:
        return {"count": self.count, "waitedS": round(self.waited_s, 3)}

    def next_delay(self, asked_s: float = 0.0) -> float | None:
        """Seconds until the next try, the provider's ``asked_s`` honoured; ``None`` once the bound is spent."""
        if self.count >= len(PROVIDER_RECHECK_S) or self.waited_s >= PROVIDER_WAIT_BUDGET_S:
            return None
        return max(PROVIDER_RECHECK_S[self.count], asked_s)

    def after(self, delay_s: float) -> ProviderWaits:
        return ProviderWaits(self.count + 1, self.waited_s + delay_s)


__all__ = [
    "PROVIDER_RECHECK_S",
    "PROVIDER_WAIT_BUDGET_S",
    "PROVIDER_WAIT_SLOT",
    "ProviderWaits",
]
