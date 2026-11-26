"""Bridge ``StateSlot(persist=True)`` values on a ``RunScope`` to journaled slots."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from harness_core.kernel.core.scope import known_persisted_slots

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.core.scope import RunScope
    from harness_core.kernel.durable.ports import TurnHook
    from harness_core.kernel.fold import RunState

PRE_RUN_SLOT_PREFIX = "pre_run."
# A post-run step's state ("started", then "done" or "failed"): kernel bookkeeping, never a scope slot.
POST_RUN_SLOT_PREFIX = "post_run."
CAPABILITY_SLOT = "capabilities.observed"
# A terminal tool's answer, journaled with its outcome: a batch that still has
# effects to run completes with it after them, on whichever attempt runs them.
TERMINAL_SLOT = "terminal.answer"
# The turn past ``maxTurns`` taken as the answer-only turn (J-15, legacy ``_finish_after_max_turns``): journaled
# before that turn's request, so a resumed attempt takes it again and never a second one.
FINAL_ANSWER_SLOT = "turns.final_answer"
# Which limit the answer-only turn is for ("turns", "budget", "time"; BUDGETEND), journaled with
# ``FINAL_ANSWER_SLOT``: absent on a run journaled before it, which took that turn for its turn limit.
CUT_SHORT_SLOT = "turns.cut_short"
# Which shared budget refused the call that cut the run short (C9.06; its scope, opaque here), journaled with
# ``CUT_SHORT_SLOT`` "budget": an attempt that resumes after it names the same budget (WP56-STOP-2). Absent: the
# run's own spending limit.
BUDGET_SCOPE_SLOT = "turns.budget_scope"
# How the answer-only turn after the spending limit cut its history to the wrap-up reserve (BUDGETEND): journaled
# with that turn's response, as every cut in history is; absent when nothing was cut.
WRAP_UP_FIT_SLOT = "turns.wrap_up_fit"


def scope_turn_hook(scope: RunScope) -> TurnHook:
    """Journal the scope's persisted slots with each model response."""

    def hook(state: RunState) -> Mapping[str, JsonValue] | None:
        return cast("dict[str, JsonValue]", scope.snapshot_extensions()) or None

    return hook


def restore_persisted_slots(scope: RunScope, state: RunState) -> None:
    """Rehydrate every declared persisted slot from the folded journal."""
    values = {
        k: v
        for k, v in state.slots.items()
        if not k.startswith((PRE_RUN_SLOT_PREFIX, POST_RUN_SLOT_PREFIX))
        and k not in (CAPABILITY_SLOT, TERMINAL_SLOT)
    }
    scope.restore_extensions(values, known_persisted_slots())


__all__ = [
    "BUDGET_SCOPE_SLOT",
    "CAPABILITY_SLOT",
    "CUT_SHORT_SLOT",
    "FINAL_ANSWER_SLOT",
    "POST_RUN_SLOT_PREFIX",
    "PRE_RUN_SLOT_PREFIX",
    "TERMINAL_SLOT",
    "WRAP_UP_FIT_SLOT",
    "restore_persisted_slots",
    "scope_turn_hook",
]
