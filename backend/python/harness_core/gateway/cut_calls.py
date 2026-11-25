"""What a model call the gateway cut short is charged, and what its budget keeps held (WP-66).

A cut call (the engine went away, the run was stopped, the stream failed) has no usage from its provider, and the
gateway does not drain the provider's stream to learn it (WP-65: draining pays for the rest of the answer). So it
settles on two numbers:

* **the bound** (R13-EST): the most the provider can bill, output at the limit the call ran under, input without
  cache credit. The budget keeps it held, so no cap is ever passed on an estimate's account.
* **the best estimate**: what streamed, plus what the model makes at its observed output rate in the time between
  the last streamed token and the cut, never above the bound. The person and the cost ledger are charged this,
  marked as an estimate.

The hold goes when the provider's own usage replaces the estimate (a provider that keeps the response, looked up
once) or after ``hold_ttl_s``, when the estimate stands.
"""

from __future__ import annotations

import math
import os
from collections import OrderedDict
from dataclasses import dataclass, field

ENV_HOLD_TTL_S = "MODEL_GATEWAY_CUT_HOLD_TTL_S"
ENV_OUTPUT_TPS = "MODEL_GATEWAY_CUT_OUTPUT_TPS"
# How long a cut call's bound stays held when its provider cannot say what it billed (Node holds a reservation an
# hour at most).
DEFAULT_HOLD_TTL_S = 600
MAX_HOLD_TTL_S = 3_600
# A model's output rate before this process has seen one of its calls finish: about what current frontier models
# stream (published figures for GPT-5 and Claude Sonnet 4 are 50 to 90 tokens a second).
DEFAULT_OUTPUT_TPS = 60.0
# How long after the cut the provider is asked for its usage (it settles a response a moment after it stops), and how
# long the gateway waits for its answer.
RECONCILE_DELAY_S = 5.0
LOOKUP_TIMEOUT_S = 10.0

# A call this short says nothing about a model's rate (a cached or tiny answer).
_MIN_OBSERVED_S = 0.5
_RATE_WEIGHT = 0.2
_RATES_KEPT = 256
_MIN_TPS, _MAX_TPS = 1.0, 2_000.0


@dataclass(frozen=True, slots=True)
class CutCalls:
    hold_ttl_s: int = DEFAULT_HOLD_TTL_S
    default_tps: float = DEFAULT_OUTPUT_TPS
    reconcile_delay_s: float = RECONCILE_DELAY_S
    lookup_timeout_s: float = LOOKUP_TIMEOUT_S

    @classmethod
    def from_env(cls) -> CutCalls:
        """``MODEL_GATEWAY_CUT_HOLD_TTL_S`` (whole seconds, 1 to 3600) and ``MODEL_GATEWAY_CUT_OUTPUT_TPS`` (a
        positive number); unset or malformed: the default."""
        ttl, tps = os.getenv(ENV_HOLD_TTL_S, "").strip(), os.getenv(ENV_OUTPUT_TPS, "").strip()
        hold = int(ttl) if ttl.isdigit() and 1 <= int(ttl) <= MAX_HOLD_TTL_S else DEFAULT_HOLD_TTL_S
        try:
            rate = float(tps) if tps else DEFAULT_OUTPUT_TPS
        except ValueError:
            rate = DEFAULT_OUTPUT_TPS
        return cls(hold_ttl_s=hold, default_tps=rate if math.isfinite(rate) and rate > 0 else DEFAULT_OUTPUT_TPS)


@dataclass(slots=True)
class OutputRates:
    """Each model's output tokens a second, a moving average over the calls this process saw finish (bounded)."""

    default_tps: float = DEFAULT_OUTPUT_TPS
    _rates: OrderedDict[str, float] = field(default_factory=OrderedDict[str, float])

    def observe(self, model: str, output_tokens: int, seconds: float) -> None:
        if output_tokens <= 0 or seconds < _MIN_OBSERVED_S:
            return
        seen = min(_MAX_TPS, max(_MIN_TPS, output_tokens / seconds))
        known = self._rates.pop(model, None)
        self._rates[model] = seen if known is None else known + _RATE_WEIGHT * (seen - known)
        if len(self._rates) > _RATES_KEPT:
            _ = self._rates.popitem(last=False)

    def rate(self, model: str) -> float:
        return self._rates.get(model, self.default_tps)


def best_output(produced: int, bound: int, rate_tps: float, unseen_s: float) -> int:
    """What streamed plus what the model made unseen at its rate, at most the bound; never less than what streamed
    (a transport that ignored the limit streamed it and is billed it)."""
    unseen = math.ceil(rate_tps * max(0.0, unseen_s))
    return max(produced, min(bound, produced + unseen))


__all__ = [
    "DEFAULT_HOLD_TTL_S",
    "DEFAULT_OUTPUT_TPS",
    "ENV_HOLD_TTL_S",
    "ENV_OUTPUT_TPS",
    "CutCalls",
    "OutputRates",
    "best_output",
]
