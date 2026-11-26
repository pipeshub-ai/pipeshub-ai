"""EL-1 (S1b): the request's effect log tells what a task's calls returned (what its engine read)."""

from __future__ import annotations

from harness_core.effects import RequestEffectLog, ToolExecution
from tests.unit.harness_core.effects.test_runner import make_call, read_effect


async def test_el_1_outcomes_are_the_completed_executions_latest_per_key_in_call_order() -> None:
    log = RequestEffectLog()
    first, second, pending, lost = (
        make_call(read_effect(), f"inv-{i}", {"q": q}) for i, q in enumerate(("a", "b", "c", "d"))
    )
    again = make_call(read_effect(), "inv-9", {"q": "a"})
    for call in (first, second, pending, lost):
        await log.intent(call)
    await log.outcome(second, ToolExecution(status="ok", content="b1"))
    await log.outcome(first, ToolExecution(status="ok", content="a1"))
    await log.unknown(lost)
    await log.intent(again)
    await log.outcome(again, ToolExecution(status="error", content="a2"))
    assert [e.content for e in log.outcomes()] == ["a2", "b1"]
    assert RequestEffectLog().outcomes() == ()
