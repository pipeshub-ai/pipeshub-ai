"""Stall and runaway-loop detection shared by every loop (BU-06, C8.08 as amended in ADR-OWD-05).

A run is stalled when its tool rounds stop making progress: ``STALL_STALE_ROUNDS`` rounds in a row whose results were
all errors or empty, or ``STALL_REPEAT_ROUNDS`` rounds in a row that made the same calls with the same arguments and
got the same results back (a runaway loop). Either ends the run's work the way a limit does: one answer-only turn
from what it gathered, flagged ``truncatedBy: stall``, and the person continues in the next run.

The model already sees ``stale_rounds`` on every result's footer and is told to change course; this is the backstop
for a model that does not. Both loops feed it the same values (a round's calls by name and argument digest, and its
results as the model reads them), so the durable loop ends on the same turn as legacy.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from harness_core.kernel.core.messages import TextPart
from harness_core.kernel.core.tool_content import tool_message_content
from harness_core.kernel.core.turn_limit import next_stale_rounds

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

STALL_STALE_ROUNDS = 6
STALL_REPEAT_ROUNDS = 4
# A web allowance refusal (``app.web.errors.BudgetExhausted``'s ``error_type``) is the harness's own limit, not a step
# that failed: its result tells the model what is left and to finish from what it gathered. A round of such refusals
# is no evidence of a stall, and none of progress either (WP-62: an engine was stopped by six of them, all from one
# parallel batch its model never got to read). A model that ignores it and repeats itself still meets the repeat rule.
ALLOWANCE_EXHAUSTED: Final = "budget_exhausted"
# Also when the payload arrives JSON-encoded inside another result (escaped quotes).
_ALLOWANCE_REFUSAL: Final = re.compile(r'\\?"error_type\\?"\s*:\s*\\?"' + ALLOWANCE_EXHAUSTED + r'\\?"')


@dataclass(frozen=True)
class RoundCall:
    """One call of a tool round, as both loops see it: its name, its arguments' digest and its result."""

    name: str
    args_digest: str
    is_error: bool
    content: Any


@dataclass(frozen=True)
class StallState:
    stale: int = 0
    repeats: int = 0
    signature: str = ""

    def to_slot(self) -> dict[str, Any]:
        return {"stale": self.stale, "repeats": self.repeats, "signature": self.signature}

    @classmethod
    def from_slot(cls, value: object) -> StallState:
        if not isinstance(value, dict):
            return cls()
        data: Mapping[str, Any] = value  # pyright: ignore[reportUnknownVariableType]
        stale, repeats, signature = data.get("stale"), data.get("repeats"), data.get("signature")
        return cls(
            stale if isinstance(stale, int) else 0,
            repeats if isinstance(repeats, int) else 0,
            signature if isinstance(signature, str) else "",
        )


def _signature(calls: Iterable[RoundCall]) -> str:
    parts = sorted(
        f"{call.name}\x00{call.args_digest}\x00{int(call.is_error)}\x00{tool_message_content(call.content)}"
        for call in calls
    )
    return hashlib.sha256("\x01".join(parts).encode("utf-8")).hexdigest() if parts else ""


def _text_of(content: Any) -> str:
    """A result's text: itself, or its text parts when it carries images too (a list of parts)."""
    normalized = tool_message_content(content)
    if isinstance(normalized, str):
        return normalized
    return "\n".join(part.text for part in normalized if isinstance(part, TextPart))


def is_allowance_refusal(call: RoundCall) -> bool:
    """Whether the call was refused only because the run's web allowance is spent."""
    return bool(_ALLOWANCE_REFUSAL.search(_text_of(call.content)))


def next_stall(state: StallState, calls: Iterable[RoundCall]) -> StallState:
    """The state after one more tool round: a model turn's calls, however many it made at once."""
    round_calls = list(calls)
    weighed = [call for call in round_calls if not is_allowance_refusal(call)]
    stale = (
        next_stale_rounds(state.stale, [(call.is_error, call.content) for call in weighed])
        if weighed or not round_calls
        else state.stale
    )
    signature = _signature(round_calls)
    repeats = state.repeats + 1 if signature and signature == state.signature else (1 if signature else 0)
    return StallState(stale, repeats, signature)


def stalled(state: StallState) -> bool:
    """Whether the rounds so far show no progress: the run answers from what it has instead of another round."""
    return state.stale >= STALL_STALE_ROUNDS or state.repeats >= STALL_REPEAT_ROUNDS


__all__ = [
    "ALLOWANCE_EXHAUSTED",
    "STALL_REPEAT_ROUNDS",
    "STALL_STALE_ROUNDS",
    "RoundCall",
    "StallState",
    "is_allowance_refusal",
    "next_stall",
    "stalled",
]
