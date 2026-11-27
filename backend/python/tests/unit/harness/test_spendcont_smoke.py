"""SPENDCONT: the human-test smoke's cut-short check (``deployment/e2e/smoke_human.py`` ``cut_short_partial``).

It passes only when the turn finished, the engine child was cut short for budget and saved the requested file, and
the answer on screen is labelled partial and names that file. The stack is a fake here.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from harness_core.access.spend import OutputAllowance, reservation_for, usage_spend
from harness_core.budget.tree import usd_micros
from harness_core.budget.types import Amount
from harness_core.budget.wrap_up import WrapUpSize
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.kernel.core.responses import TokenUsage

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "deployment" / "e2e"))

smoke = importlib.import_module("smoke_human")
from lane import Frame  # noqa: E402

NAME = "spendcont-abc123.md"
CUT = "engine.kernel.cut_short runId=c1 reason=budget partial=True saved=True"
SAVED = "engine.handoff.saved runId=c1 deliverable=True reason=budget revision=2"


def frame(kind: str, **data: Any) -> Frame:  # noqa: ANN401 - a frame's JSON
    return Frame(None, kind, {"type": kind, **data})


def frames(answer: str, end: str = "RUN_FINISHED") -> list[Frame]:
    return [
        frame("RUN_STARTED", runId="r1"),
        frame("TEXT_MESSAGE_CONTENT", messageId="m1", delta=answer),
        frame(end),
    ]


PARTIAL = f"- onboarding: read me\n\n---\n**This result is partial.** …\nSaved so far: **{NAME}**, marked partial."


def test_a_cut_short_child_whose_partial_and_file_reach_the_person_passes() -> None:
    ok, detail = smoke.cut_short_shown(frames(PARTIAL), [CUT, SAVED], NAME)
    assert ok, detail


def test_a_typed_partial_outcome_naming_the_file_is_labelled_without_the_english_words() -> None:
    """WP-89: the answer carries only the prose and the final snapshot the typed outcome the chat words."""
    outcome = {"kind": "partial", "reason": "budget", "action": "continue",
               "delivered": {"file": {"path": f"out/{NAME}"}}}
    typed = [*frames("- onboarding: read me")[:-1], frame("STATE_SNAPSHOT", snapshot={"final": True, "answer": "", "runOutcome": outcome}),
             frame("RUN_FINISHED")]
    ok, detail = smoke.cut_short_shown(typed, [CUT, SAVED], NAME)
    assert ok, detail
    done = {**outcome, "kind": "completed"}
    plain = [*frames("- onboarding: read me")[:-1], frame("STATE_SNAPSHOT", snapshot={"final": True, "answer": "", "runOutcome": done}),
             frame("RUN_FINISHED")]
    assert not smoke.cut_short_shown(plain, [CUT, SAVED], NAME)[0]


def test_a_not_ready_answer_fails() -> None:
    ok, _ = smoke.cut_short_shown(frames("the requested file isn't ready yet."), [CUT], NAME)
    assert not ok


def test_no_saved_file_or_an_error_fails() -> None:
    assert not smoke.cut_short_shown(frames(PARTIAL), [CUT], NAME)[0]
    assert not smoke.cut_short_shown(frames(PARTIAL, "RUN_ERROR"), [CUT, SAVED], NAME)[0]


# The smoke's turn: a coder agent's loop (one hand-off call) hands the script to its engine child, which makes
# one call per scripted line. Every call reports the script's `usage`, at e2e-llm's price (the table does not know it:
# Sonnet-class defaults), against the release's budgets, which the child's tree shares with its parent.
SMOKE_MODEL = "e2e-llm"
CHILD_CALLS = 4  # three lookup_note calls, then the answer
CALL_OUTPUT_TOKENS = 50


def _call_spend() -> Amount:
    usage = TokenUsage(input_tokens=smoke.CUT_SHORT_USAGE, output_tokens=CALL_OUTPUT_TOKENS)
    return usage_spend(usage, SMOKE_MODEL, charges_usd=True)


def _reached(spent: Amount, cap: Amount) -> bool:
    return spent.usd_micros >= cap.usd_micros or spent.tokens >= cap.tokens


def test_the_cut_short_usage_lets_the_parent_hand_off_and_cuts_its_child_mid_script() -> None:
    """At 6M tokens a call the loop's own hand-off call cost USD 18 of its 5, so Node refused the child
    (CHILD_BUDGET_EXCEEDS_REMAINING) and no engine was ever cut short. The usage must leave the parent room to start
    its child, and stop that child for budget after it has done some of its work but before it finishes."""
    budgets = RELEASE_DEFAULTS.budgets
    cap = Amount(usd_micros(budgets.max_cost_usd), budgets.max_tokens)
    wrap_up = WrapUpSize.for_window(None).amount(SMOKE_MODEL, charges_usd=True)
    call = _call_spend()
    held = wrap_up + wrap_up  # the parent's wrap-up reserve and its child's
    minimum = reservation_for(20_000, SMOKE_MODEL, OutputAllowance(), charges_usd=True)[1]

    spent = call  # the parent's hand-off call
    assert not _reached(spent, cap), f"the parent's own hand-off call spends its budget: {spent} of {cap}"
    assert cap.usd_micros - spent.usd_micros - held.usd_micros >= minimum.usd_micros, "no room for the child's call"

    done = 0
    while done < CHILD_CALLS and not _reached(spent, cap):
        spent = spent + call
        done += 1
    assert 1 <= done < CHILD_CALLS, f"the child made {done} of {CHILD_CALLS} calls before its budget stopped it"
    assert spent.usd_micros >= cap.usd_micros, "USD decides; the token cap is a backstop"
