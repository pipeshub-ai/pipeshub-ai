"""Synthetic turns: the turns a loop strategy took without the model (K-L).

A ``LoopDirector`` may answer a boundary with the turn's response itself
(legacy deep mode's programmatic dispatch). The kernel journals it as a
``model.response`` under these machine names, so the fold, the turn budget and
a product's shaper can tell it from a model's: it costs no turn, carries no
step footer and is never an assistant text an answer is taken from, unless the
director ends the run with a text-only turn of its own (``/run``): that text is
the run's answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness_core.kernel.fold import ResponseRecord, RunState

SYNTHETIC_PROVIDER = "director"
SYNTHETIC_TRANSPORT = "synthetic"


def is_synthetic(response: ResponseRecord) -> bool:
    return response.provider == SYNTHETIC_PROVIDER and response.transport == SYNTHETIC_TRANSPORT


def synthetic_turns(state: RunState, before: int | None = None) -> int:
    """How many of the run's turns (before ``before``, when given) were synthetic."""
    return sum(
        1
        for response in state.responses
        if is_synthetic(response) and (before is None or response.turn < before)
    )


def model_turn_index(state: RunState, turn: int) -> int:
    """``turn``'s zero-based index among the turns the model took (legacy's ``turn_index``)."""
    return turn - 1 - synthetic_turns(state, before=turn)


__all__ = [
    "SYNTHETIC_PROVIDER",
    "SYNTHETIC_TRANSPORT",
    "is_synthetic",
    "model_turn_index",
    "synthetic_turns",
]
