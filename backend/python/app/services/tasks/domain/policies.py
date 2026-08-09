"""Policy value objects for the task engine. Pure, zero I/O.

Kept separate from `models.py` because these three are genuinely
independent, reusable policy objects (a `RetryPolicy` shape is not specific
to tasks), whereas `models.py` holds the task/trigger/run entities that
reference them.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class MisfirePolicy(str, Enum):
    """What to do when a trigger's `next_run_at` is discovered late (the
    scheduler was down, or the tick loop fell behind). Distinct from
    `RetryPolicy`, which governs a FAILED run, not a late-discovered due
    trigger."""

    SKIP = "skip"
    RUN_ONCE = "run_once"
    RUN_ALL = "run_all"


class RetryPolicy(BaseModel):
    """How a failed run should be retried."""

    max_attempts: int = 3
    backoff_seconds: int = 60
    backoff_multiplier: float = 2.0
    max_backoff_seconds: int = 3600

    def delay_for_attempt(self, attempt: int) -> int:
        """`attempt` is 1-indexed (first retry = 1)."""
        if attempt <= 0:
            return 0
        delay = self.backoff_seconds * (self.backoff_multiplier ** (attempt - 1))
        return min(int(delay), self.max_backoff_seconds)


class BudgetPolicy(BaseModel):
    """Resource governance for a task's recurring execution."""

    max_turns_per_run: int = 15
    max_tokens_per_run: int | None = None
    max_cost_usd_per_run: float | None = None
    max_consecutive_failures: int = 5
    """Task auto-disables (status -> DISABLED) after this many consecutive
    run failures, so a broken recurring task doesn't burn budget forever."""
