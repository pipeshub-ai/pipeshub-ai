"""WP77-PARENT-1..3: a hand-off whose engine the AI model provider cut short reaches the person as a partial answer.

A recorded child failed on the provider, and the parent relayed a fixed notice without a model call: the 191 files
and 120 cited pages it had gathered were never written up. The child now completes cut short by the provider (its own
wrap-up answers from what it gathered); the parent presents that answer flagged, or, when the engine had no answer,
takes the turn back on its wrap-up reserve, as for a spent budget.
"""

from __future__ import annotations

import pytest

from app.harness.assembly.deep_route import HANDOFF_SLOT, Handoff
from harness_core.engines.outcome.outcome import Delivered, Partial
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.kernel.core.turn_limit import (
    cut_short_fallback,
    cut_short_note,
    is_cut_short_fallback,
)
from harness_core.kernel.durable.ports import TurnResult
from tests.unit.harness.assembly.test_deeproute import _director, _state


def _result(output: str) -> TurnResult:
    content: dict[str, object] = {"success": True, "output": output, "truncatedBy": "provider"}
    return TurnResult(name="code_task", call_id="c1.0", status="ok", content=content)  # type: ignore[arg-type]


@pytest.mark.parametrize("trigger", ["upfront", "escalation"])
def test_wp77_parent_1_an_engine_with_no_answer_gives_the_turn_back_flagged_provider(trigger: str) -> None:
    asked = Handoff(turn=1, trigger=trigger, reason="research")  # type: ignore[arg-type]
    state = _state([(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: asked.slot()})
    plan = _director().before(2, state, [_result(cut_short_fallback("provider", ("fetch_url",)))])
    assert plan.synthetic is None, "the model answers from what was gathered, not a fixed notice"
    assert plan.cut_short == "provider"


def test_wp77_parent_2_an_engine_that_wrapped_up_is_the_answer_flagged_provider() -> None:
    asked = Handoff(turn=1, trigger="upfront", reason="research")
    state = _state([(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: asked.slot()})
    answer = PRESENTER.answer(Partial("provider", Delivered()), body="Onyx documents its connectors.")
    plan = _director().before(2, state, [_result(answer)])
    assert plan.synthetic is not None and plan.synthetic.content[0].text == answer  # type: ignore[union-attr]
    assert plan.cut_short == "provider"


def test_wp77_parent_3_the_provider_reason_is_worded_plainly_everywhere() -> None:
    assert is_cut_short_fallback(cut_short_fallback("provider", ("web_search",)))
    for text in (cut_short_note("provider"), PRESENTER.answer(Partial("provider", Delivered()), body="x")):
        assert "the AI model kept refusing requests" in text and "internal error" not in text, text
