"""WP84-ENGERR: an engine run whose engine ended its turn on an error of its own still answers from what it gathered.

WP-77 (D3) made a provider that stays down end the run cut short (``truncatedBy: provider``): its wrap-up answers from
the tool results and the engine's notes, the workspace merges, and the parent presents that answer flagged partial.
Every other engine error still failed the run, so a child that had gathered for twenty minutes and then hit an engine
error ended in the fixed notice, with no answer turn (WP-77 open item). The same path now takes those: the run
completes ``truncatedBy: engine_error`` when the wrap-up answers. A refusal by PipesHub's own gateways or rules (an
egress cap, a model the run was not granted) is no engine error and still fails with its code,
as does an error with nothing gathered to answer from.
"""

from __future__ import annotations

import inspect

import pytest

from harness_core.engines.durable.kernel import ENGINE_ERROR_CUT
from harness_core.engines.outcome.faults import PIPESHUB_STOP_CODES
from harness_core.engines.outcome.outcome import Delivered, Partial
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.gateway import errors as gateway_errors
from harness_core.kernel.core.turn_limit import (
    cut_short_fallback,
    cut_short_note,
    is_cut_short_fallback,
)
from harness_core.testing.engines.durable import (
    AFTER_TOOLS,
    EngineWorld,
    ScriptedEngineAdapter,
)
from tests.unit.harness_core.engines.durable.test_engbudget_wrap_up import (
    ANSWER,
    FakeWrapUp,
    _answer_text,
)

RUN = "run-engerr"


def _world(code: str, wrap_up: FakeWrapUp | None) -> EngineWorld:
    adapter = ScriptedEngineAdapter()
    adapter.fail_with.append(f"{AFTER_TOOLS}{code}")
    return EngineWorld(adapter=adapter, wrap_up=lambda claim: wrap_up)


def _ends(world: EngineWorld) -> list[tuple[str, dict[str, object]]]:
    return [(e.type, dict(e.data)) for e in world.events(RUN) if e.type in ("run.completed", "run.failed")]


@pytest.mark.parametrize("code", ["engine_error", "engine_failed", "http_500", "unknown_stop"])
async def test_wp84_engerr_1_an_engine_error_after_work_answers_from_what_was_gathered(code: str) -> None:
    wrap_up = FakeWrapUp()
    world = _world(code, wrap_up)
    world.admit(RUN, 'tool lookup {"q": "onyx connectors"}\nsay done')
    final = await world.attempt(RUN)
    assert final.status == "completed", final
    assert _ends(world) == [("run.completed", {"outcome": "answered", "truncatedBy": ENGINE_ERROR_CUT})]
    answer = await _answer_text(world)
    assert answer.startswith(ANSWER) and not is_cut_short_fallback(answer)
    ((_, gathered, reason),) = wrap_up.asked
    assert reason == ENGINE_ERROR_CUT and [g.tool for g in gathered] == ["lookup"]
    assert "run.quarantined" not in [e.type for e in world.events(RUN)]


async def test_wp84_engerr_2_with_nothing_gathered_the_run_still_fails_with_its_code() -> None:
    wrap_up = FakeWrapUp()
    world = _world("engine_error", wrap_up)
    world.admit(RUN, "say hello")
    final = await world.attempt(RUN)
    assert (final.status, final.reason) == ("failed", "engine_error")
    assert wrap_up.asked == []
    assert _ends(world) == [("run.failed", {"errorType": "engine_error"})]


@pytest.mark.parametrize("answer_text", [None, ""])
async def test_wp84_engerr_3_a_wrap_up_that_cannot_answer_leaves_the_failure(answer_text: str | None) -> None:
    world = _world("engine_error", FakeWrapUp(answer_text=answer_text))
    world.admit(RUN, 'tool lookup {"q": "x"}\nsay done')
    final = await world.attempt(RUN)
    assert (final.status, final.reason) == ("failed", "engine_error")


async def test_wp84_engerr_4_an_unmetered_run_without_a_wrap_up_fails_as_before() -> None:
    world = _world("engine_error", None)
    world.admit(RUN, 'tool lookup {"q": "x"}\nsay done')
    final = await world.attempt(RUN)
    assert (final.status, final.reason) == ("failed", "engine_error")


# A forbidden credential is no longer here: it cuts the run short as ``secret_blocked`` (WP-103, test_wp103_secret_blocked).
@pytest.mark.parametrize("code", ["egress_volume_exceeded", "model_not_granted", "org_policy_refused"])
async def test_wp84_engerr_5_a_pipeshub_refusal_is_no_engine_error_and_fails_at_once(code: str) -> None:
    wrap_up = FakeWrapUp()
    world = _world(code, wrap_up)
    world.admit(RUN, 'tool lookup {"q": "x"}\nsay done')
    final = await world.attempt(RUN)
    assert (final.status, final.reason) == ("failed", code)
    assert wrap_up.asked == []


def test_wp84_engerr_6_every_gateway_refusal_code_is_a_pipeshub_stop() -> None:
    """A refusal added to the gateways later stays a failure without anyone remembering this list."""
    codes = {
        cls.reason_code
        for _, cls in inspect.getmembers(gateway_errors, inspect.isclass)
        if issubclass(cls, gateway_errors.GatewayRefusal) and isinstance(cls.__dict__.get("reason_code"), str)
    }
    assert codes and codes <= PIPESHUB_STOP_CODES, sorted(codes - PIPESHUB_STOP_CODES)


def test_wp84_engerr_7_the_reason_is_worded_plainly_everywhere() -> None:
    assert is_cut_short_fallback(cut_short_fallback(ENGINE_ERROR_CUT, ("web_search",)))
    for text in (cut_short_note(ENGINE_ERROR_CUT), PRESENTER.status(Partial(ENGINE_ERROR_CUT, Delivered()))):
        assert "coding engine" in text and "internal error" not in text, text


@pytest.mark.parametrize("trigger", ["upfront", "escalation"])
def test_wp84_engerr_8_the_parent_presents_the_childs_answer_or_takes_the_turn_back_flagged(trigger: str) -> None:
    """The parent of a hand-off treats an engine-error cut like a provider cut (WP77-PARENT): the child's wrap-up
    answer is the turn's, flagged; a child with no answer gives the turn back on the parent's reserve."""
    from app.harness.assembly.deep_route import HANDOFF_SLOT, Handoff
    from harness_core.kernel.durable.ports import TurnResult
    from tests.unit.harness.assembly.test_deeproute import _director, _state

    def result(output: str) -> TurnResult:
        content = {"success": True, "output": output, "truncatedBy": ENGINE_ERROR_CUT}
        return TurnResult(name="code_task", call_id="c1.0", status="ok", content=content)  # type: ignore[arg-type]

    asked = Handoff(turn=1, trigger=trigger, reason="research")  # type: ignore[arg-type]
    state = _state([(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: asked.slot()})
    answer = PRESENTER.answer(Partial(ENGINE_ERROR_CUT, Delivered()), body="Onyx documents its connectors.")
    plan = _director().before(2, state, [result(answer)])
    assert plan.synthetic is not None and plan.synthetic.content[0].text == answer  # type: ignore[union-attr]
    assert plan.cut_short == ENGINE_ERROR_CUT
    back = _director().before(2, state, [result(cut_short_fallback(ENGINE_ERROR_CUT, ("fetch_url",)))])
    assert back.synthetic is None and back.cut_short == ENGINE_ERROR_CUT
