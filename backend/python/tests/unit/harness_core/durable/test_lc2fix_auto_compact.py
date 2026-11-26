"""LC2FIX-2: the durable loop compacts on its own when the provider counts a request above 80% of the window,
on the shaper path too (``HARNESS_CONTEXT_COMPILER`` off), so a long chat never runs past its window. Below
that the shaped requests are unchanged (no clearing rung, which only the compiled layout can apply), and the
operator's ``HARNESS_AUTO_COMPACT=false`` (``auto_compact=False``) turns it off."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness_core.context.user_compact import CONTEXT_COMPACTED_EVENT
from harness_core.kernel.core.messages import ToolMessage, UserMessage
from tests.unit.harness_core.context.kernel_support import (
    WINDOW,
    answered,
    compiler,
    looked_up,
)
from tests.unit.harness_core.durable.support import World, scripted
from tests.unit.harness_core.durable.test_slash2_c2_compact import (
    Recorder,
    Summaries,
    _world,
)

if TYPE_CHECKING:
    from harness_core.kernel.durable.ports import ModelCall

SUMMARY = "a summary of the earlier conversation"


def _texts(call: ModelCall) -> list[str]:
    return [str(m.content) for m in call.messages if isinstance(m, UserMessage)]


async def _run(world: World, measured: int, **kernel: Any) -> tuple[Recorder, Summaries]:  # noqa: ANN401
    """Four lookups, the last counted at ``measured`` tokens by the provider, then an answer."""
    world.admit(text="what is the travel cap", max_turns=12)
    _, port = scripted(
        looked_up(300, "q0"), looked_up(300, "q1"), looked_up(300, "q2"), looked_up(measured, "q3"), answered()
    )
    recorder, summaries = Recorder(port), Summaries()
    done = await world.run(world.kernel(recorder, compactor=compiler(strategy=summaries), **kernel))  # type: ignore[arg-type]
    assert done.status == "completed"
    return recorder, summaries


async def test_above_80_percent_the_shaped_loop_compacts_before_its_next_request() -> None:
    world = _world()
    recorder, summaries = await _run(world, int(WINDOW * 0.9), auto_compact=True)
    (compaction,) = world.events("context.compacted")
    assert len(summaries.asked) == 1
    measured = next(e for e in world.events("model.response") if e.data["usage"]["inputTokens"] == int(WINDOW * 0.9))
    assert measured.seq < compaction.seq < world.events("model.response")[-1].seq
    last = _texts(recorder.sent[-1])
    assert any(SUMMARY in text for text in last), last
    assert "what is the travel cap" in last, "the run's own input follows the summary, as the ladder keeps it"
    results = [m.tool_call_id for m in recorder.sent[-1].messages if isinstance(m, ToolMessage)]
    # The recent turns kept are at most a quarter of the window (BUDGETEND): two of these 1k-token turns fit it.
    assert results == ["call-q2", "call-q3"], "the oldest turns were summarised away, the recent ones kept"
    assert world.events("context.cleared") == []


async def test_lc2fix_3_the_chat_is_told_with_the_providers_count() -> None:
    world = _world()
    await _run(world, int(WINDOW * 0.9), auto_compact=True)
    (row,) = world.events(CONTEXT_COMPACTED_EVENT)
    data = row.data
    assert (data["trigger"], data["executor"], data["outcome"], data["beforeTokens"]) == (
        "auto", "loop", "compacted", int(WINDOW * 0.9),
    )
    assert 0 < data["afterTokens"] < data["beforeTokens"]
    assert row.seq > world.events("context.compacted")[0].seq


async def test_below_80_percent_nothing_is_cleared_or_compacted() -> None:
    world = _world()
    _, summaries = await _run(world, int(WINDOW * 0.7), auto_compact=True)
    assert summaries.asked == []
    assert world.events("context.compacted") == [] and world.events("context.cleared") == []


async def test_the_operator_switch_off_leaves_the_requests_as_they_were() -> None:
    world = _world()
    _, summaries = await _run(world, int(WINDOW * 0.9), auto_compact=False)
    assert summaries.asked == []
    assert world.events("context.compacted") == []
