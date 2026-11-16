"""The one retry layer of a paid call: how often a failed send is sent again, how long to wait, and when to stop.

The SDK clients of every indexing call site retry nothing (``sdk_retries=0``); the gateways (``LLMGateway.call``,
``EmbeddingGateway.call``) are the only place a failed paid request is repeated, so a logical call makes at most
``MAX_ATTEMPTS`` requests whatever the provider or its wrapper does.

* Waits are full jitter: uniform in ``[0, base * 2**attempt]``, base 1 s for a throttle (429), 0.25 s for a
  transient failure (5xx, a dropped connection). A provider's Retry-After is honoured instead when it asks for more.
* One call sleeps at most ``MAX_TOTAL_SLEEP_S`` in all. A Retry-After beyond what is left of that is not waited out:
  the caller gets ``RateLimited`` at once and defers the work.
* A process-wide ``RetryBudget`` (500 tokens; a throttle retry costs 5, a transient one 14, a success pays 1 back)
  bounds how many retries a struggling provider can cost the process, whatever the number of calls.

Everything here is pure or thread-safe state; nothing sleeps or sends.
"""

from __future__ import annotations

import enum
import math
import random
import threading
from dataclasses import dataclass

MAX_ATTEMPTS = 3
MAX_TOTAL_SLEEP_S = 60.0
THROTTLE_BASE_S = 1.0
TRANSIENT_BASE_S = 0.25
# A provider's Retry-After is untrusted input: whatever it asks for, no sleep, hold or requeue is longer than this.
MAX_RETRY_AFTER_S = 3600.0
RETRY_BUDGET_CAPACITY = 500
THROTTLE_COST = 5
TRANSIENT_COST = 14
SUCCESS_REFUND = 1


def clamp_retry_after(value: object) -> float | None:
    """``value`` as seconds to wait: finite and not negative, at most ``MAX_RETRY_AFTER_S``; None for anything else
    (a NaN, an infinity, a negative number, a bool, a string), which is ignored as if it had not been sent."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    if math.isnan(seconds) or math.isinf(seconds) or seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_S)


class RetryKind(enum.Enum):
    THROTTLE = "throttle"
    TRANSIENT = "transient"


COST = {RetryKind.THROTTLE: THROTTLE_COST, RetryKind.TRANSIENT: TRANSIENT_COST}
BASE_S = {RetryKind.THROTTLE: THROTTLE_BASE_S, RetryKind.TRANSIENT: TRANSIENT_BASE_S}


class RetryBudget:
    """Tokens a process spends on retries. Thread-safe: every event loop of the process shares one."""

    def __init__(self, capacity: int = RETRY_BUDGET_CAPACITY) -> None:
        self._capacity = capacity
        self._tokens = capacity
        self._lock = threading.Lock()

    @property
    def tokens(self) -> int:
        with self._lock:
            return self._tokens

    def try_spend(self, kind: RetryKind) -> bool:
        """Take the cost of one retry; False (nothing taken) when the budget cannot pay it."""
        cost = COST[kind]
        with self._lock:
            if self._tokens < cost:
                return False
            self._tokens -= cost
            return True

    def refund(self, amount: int = SUCCESS_REFUND) -> None:
        with self._lock:
            self._tokens = min(self._capacity, self._tokens + amount)


_process_budget = RetryBudget()


def process_retry_budget() -> RetryBudget:
    """The budget shared by this process's gateways (LLM and embeddings)."""
    return _process_budget


# Why a failed attempt is not retried.
NOT_RETRYABLE = "not_retryable"
ATTEMPTS_EXHAUSTED = "attempts_exhausted"
BUDGET_EXHAUSTED = "budget_exhausted"
RETRY_AFTER_BEYOND_BUDGET = "retry_after_beyond_budget"
RETRY = "retry"


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay_s: float
    reason: str
    kind: RetryKind | None = None


def decide_retry(
    *,
    attempt: int,
    kind: RetryKind | None,
    budget: RetryBudget,
    retry_after_s: float | None = None,
    slept_s: float = 0.0,
    max_attempts: int = MAX_ATTEMPTS,
    rng: random.Random | None = None,
) -> RetryDecision:
    """Whether to send again after the failed attempt number ``attempt`` (0 for the first), and how long to wait first.

    ``kind`` is None for a failure that is not worth repeating (the request's own fault, a hang, a refusal).
    ``slept_s`` is what this call has already waited. Spends a retry-budget token only when it decides to retry.
    """
    if kind is None:
        return RetryDecision(False, 0.0, NOT_RETRYABLE)
    remaining = max(0.0, MAX_TOTAL_SLEEP_S - slept_s)
    # Checked before the attempt limit, so the last attempt reports a wait it may not spend the same way every other does.
    if retry_after_s is not None and retry_after_s > remaining:
        return RetryDecision(False, 0.0, RETRY_AFTER_BEYOND_BUDGET)
    if attempt + 1 >= max_attempts:
        return RetryDecision(False, 0.0, ATTEMPTS_EXHAUSTED)
    if not budget.try_spend(kind):
        return RetryDecision(False, 0.0, BUDGET_EXHAUSTED)
    jitter = (rng or random).uniform(0.0, BASE_S[kind] * 2**attempt)
    delay = max(jitter, retry_after_s) if retry_after_s is not None else jitter
    return RetryDecision(True, min(delay, remaining), RETRY, kind)
