"""QAFIX4 B2 on the kernel: the shaped loop (``HARNESS_CONTEXT_COMPILER`` off) no longer has the legacy sliding window
to fit a request into the window, so a request the provider refuses as too large is reduced the journaled way, as on
the compiler path (P4.9): the older turns compacted with ``context.compacted`` and the chat's "Context compacted" row,
the run's own input kept after the summary, and the request sent again."""

from __future__ import annotations

from harness_core.context.user_compact import CONTEXT_COMPACTED_EVENT
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.durable.ports import REQUEST_TOO_LARGE
from tests.unit.harness_core.context.kernel_support import answered, compiler, looked_up
from tests.unit.harness_core.context.test_request_too_large import Window
from tests.unit.harness_core.durable.support import scripted
from tests.unit.harness_core.durable.test_slash2_c2_compact import Summaries, _world

QUESTION = "what is the travel cap"


async def _no_wait(seconds: float) -> None:
    del seconds


async def test_a_shaped_request_refused_as_too_large_is_compacted_journaled_and_sent_again() -> None:
    world = _world()
    world.admit(text=QUESTION, max_turns=12)
    _, port = scripted(*(looked_up(300, f"q{i}") for i in range(6)), answered())
    window = Window(port, limit=14_000)
    summaries = Summaries()
    kernel = world.kernel(window, compactor=compiler(strategy=summaries), auto_compact=True, sleep=_no_wait)  # type: ignore[arg-type]
    done = await world.run(kernel)
    assert done.status == "completed"
    assert window.refused >= 1 and summaries.asked
    compactions = world.events("context.compacted")
    rows = world.events(CONTEXT_COMPACTED_EVENT)
    assert len(rows) == len(compactions) >= 1, "each compaction is shown in the chat"
    assert all(r.data["trigger"] == "auto" for r in rows)
    for call in window.sent:
        texts = [str(m.content) for m in call.messages if isinstance(m, UserMessage)]
        assert QUESTION in texts, "the run's own input is in every request it sent"


async def test_with_the_operator_switch_off_the_refusal_fails_the_run_as_before() -> None:
    world = _world()
    world.admit(text=QUESTION, max_turns=12)
    _, port = scripted(*(looked_up(300, f"q{i}") for i in range(6)), answered())
    window = Window(port, limit=14_000)
    kernel = world.kernel(window, compactor=compiler(strategy=Summaries()), auto_compact=False, sleep=_no_wait)  # type: ignore[arg-type]
    done = await world.run(kernel)
    assert done.status == "failed"
    assert world.events("run.failed")[0].data["errorType"] == REQUEST_TOO_LARGE
    assert world.events("context.compacted") == []
