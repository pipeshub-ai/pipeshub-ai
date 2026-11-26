"""WP-103 SECBLK-1..SECBLK-5: a model call PipesHub blocked as carrying a secret is told as that, with the work kept.

A long research run ended "Stopped early: the coding engine ran into an error" when the egress proxy refused a model call that
carried a public page's example token: the engine reported ``http_403`` and the kernel took it for an engine error.
The proxy's refusal now names ``secret_blocked`` in the engine's error (``[gateway:secret_blocked]``), as the model
gateway names ``forbidden_credential`` (a key in a model call's header): both cut the run short with the typed reason
``secret_blocked``, so the person reads that PipesHub blocked a request that looked like it contained a secret, and
what was written (that run's report.md) is kept and answered from. With nothing gathered the run fails with that code.
"""

from __future__ import annotations

import pytest

from harness_core.engines.durable.kernel import SECRET_BLOCKED_CUT
from harness_core.engines.outcome.faults import SECRET_BLOCKED_CODES, engine_stop_cut
from harness_core.engines.outcome.outcome import Delivered, Partial
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.kernel.core.turn_limit import (
    cut_short_fallback,
    cut_short_note,
    is_cut_short_fallback,
)
from harness_core.sandbox.egress.errors import SECRET_BLOCKED
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

RUN = "run-secblk"


def _world(code: str, wrap_up: FakeWrapUp | None) -> EngineWorld:
    adapter = ScriptedEngineAdapter()
    adapter.fail_with.append(f"{AFTER_TOOLS}{code}")
    return EngineWorld(adapter=adapter, wrap_up=lambda claim: wrap_up)


def _ends(world: EngineWorld) -> list[tuple[str, dict[str, object]]]:
    return [(e.type, dict(e.data)) for e in world.events(RUN) if e.type in ("run.completed", "run.failed")]


def _settled(world: EngineWorld) -> dict[str, object]:
    found = [dict(e.data["outcome"]) for e in world.events(RUN) if e.type == "x.pipeshub.outcome.settled"]
    return found[-1] if found else {}


def test_secblk_0_the_codes_are_the_proxys_and_the_gateways() -> None:
    assert SECRET_BLOCKED_CUT == SECRET_BLOCKED == "secret_blocked"
    assert SECRET_BLOCKED_CODES == frozenset({"secret_blocked", "forbidden_credential"})
    assert engine_stop_cut("http_403") == "engine_error"
    assert engine_stop_cut("model_not_granted") is None, "PipesHub's other stops still fail at once"


@pytest.mark.parametrize("code", sorted(SECRET_BLOCKED_CODES))
async def test_secblk_1_a_blocked_model_call_after_work_answers_with_the_typed_reason(code: str) -> None:
    wrap_up = FakeWrapUp()
    world = _world(code, wrap_up)
    world.admit(RUN, 'tool lookup {"q": "pipeshub personal access tokens"}\nsay done')
    final = await world.attempt(RUN)
    assert final.status == "completed", final
    assert _ends(world) == [("run.completed", {"outcome": "answered", "truncatedBy": SECRET_BLOCKED_CUT})]
    assert _settled(world).get("reason") == SECRET_BLOCKED_CUT
    answer = await _answer_text(world)
    assert answer.startswith(ANSWER) and not is_cut_short_fallback(answer)
    ((_, gathered, reason),) = wrap_up.asked
    assert reason == SECRET_BLOCKED_CUT and [g.tool for g in gathered] == ["lookup"]


@pytest.mark.parametrize("code", sorted(SECRET_BLOCKED_CODES))
async def test_secblk_2_with_nothing_gathered_the_run_fails_with_the_one_code(code: str) -> None:
    world = _world(code, FakeWrapUp())
    world.admit(RUN, "say hello")
    final = await world.attempt(RUN)
    assert (final.status, final.reason) == ("failed", SECRET_BLOCKED)
    assert _ends(world) == [("run.failed", {"errorType": SECRET_BLOCKED})]


def test_secblk_3_the_person_reads_what_happened_and_what_to_do() -> None:
    status = PRESENTER.status(Partial(SECRET_BLOCKED_CUT, Delivered()))
    assert "looked like it contained a secret" in status and "coding engine ran into an error" not in status
    closing = PRESENTER.answer(Partial(SECRET_BLOCKED_CUT, Delivered()), body="Onyx documents its connectors.")
    assert "continue" in closing
    assert "secret" in cut_short_note(SECRET_BLOCKED_CUT)
    assert is_cut_short_fallback(cut_short_fallback(SECRET_BLOCKED_CUT, ("web_search",)))


def test_secblk_4_a_child_cut_by_it_is_told_to_its_parent_plainly() -> None:
    from harness_core.journal.children import STOPPED_EARLY_NOTES

    assert "secret" in STOPPED_EARLY_NOTES[SECRET_BLOCKED_CUT]
