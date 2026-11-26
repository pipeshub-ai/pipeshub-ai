"""How long a read the service rate limited may wait before it is tried again (C3.34, TG-08).

Only a read is tried again: it leaves nothing behind, so a second try can never double an effect. The waits come
from the call's own budget: at most ``MAX_RETRIES`` tries more, ``WAIT_BUDGET_S`` of waiting in all, and never a
wait that would leave the call less than ``MIN_CALL_S`` of its time bound. A wait the budget cannot pay is not
waited at all: the call answers ``rate_limited`` at once, with the service's retry-after, so the engine decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MAX_RETRIES: Final = 3
WAIT_BUDGET_S: Final = 30.0
# What a try needs of its time bound to be worth making after a wait.
MIN_CALL_S: Final = 1.0
# The first wait when the service names none; doubled at each try.
FIRST_BACKOFF_S: Final = 1.0


@dataclass(frozen=True, slots=True)
class RateLimitBudget:
    max_retries: int = MAX_RETRIES
    wait_budget_s: float = WAIT_BUDGET_S
    min_call_s: float = MIN_CALL_S
    first_backoff_s: float = FIRST_BACKOFF_S

    def wait_s(self, retry_after_s: float | None, retries: int) -> float:
        return retry_after_s if retry_after_s is not None else self.first_backoff_s * 2**retries

    def affords(self, wait_s: float, *, retries: int, waited_s: float, left_s: float | None) -> bool:
        """``left_s``: the call's remaining time bound, ``None`` when it has none."""
        if retries >= self.max_retries or waited_s + wait_s > self.wait_budget_s:
            return False
        return left_s is None or wait_s + self.min_call_s <= left_s


__all__ = ["FIRST_BACKOFF_S", "MAX_RETRIES", "MIN_CALL_S", "WAIT_BUDGET_S", "RateLimitBudget"]
