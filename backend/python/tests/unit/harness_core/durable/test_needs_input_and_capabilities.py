"""Questions end the run with needs_input; observed capabilities are pinned by the first attempt."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.failpoints import Raise, registry
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.capability import CAPABILITY_RECHECK_S

from .support import RUN, Crash, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import ToolContext


def _asking_world() -> World:
    world = World()

    async def ask(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"asked": True}, needs_input=True)

    world.tools.add("ask_user", ask, tags=[("category", "read")])
    world.admit()
    return world


async def test_a_question_completes_the_run_with_needs_input_without_another_sample() -> None:
    world = _asking_world()
    model, port = scripted(
        reply("", ("lookup", {"q": "x"}), ("ask_user", {"question": "which?"})),
        reply("never sampled"),
    )
    disposition = await world.run(world.kernel(port))
    assert (disposition.status, disposition.reason) == ("completed", "needs_input")
    assert len(port.calls) == 1
    assert world.journal.status(RUN) == "completed"
    tail = world.types()[-4:]
    assert tail == ["tool.intent", "tool.outcome", "turn.completed", "run.completed"]
    (completed,) = world.events("run.completed")
    assert completed.data == {"outcome": "needs_input"}
    # Outcomes and the completion are one commit: no window re-samples the model.
    assert world.journal.commits(RUN)[-1][1][-2:] == ["turn.completed", "run.completed"]
    assert model is not None


class _Observed:
    def __init__(self, value: dict[str, JsonValue]) -> None:
        self.value = value
        self.calls = 0

    async def observe(self) -> Mapping[str, JsonValue]:
        self.calls += 1
        return dict(self.value)


async def test_first_attempt_journals_observed_capabilities_and_a_changed_one_is_rechecked() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    observed = _Observed({"mcp:inst-1": "sha256:" + "a" * 64})

    # Crash after the first model response: the next attempt re-checks.
    registry.arm("worker.model.after_response_commit", Raise(Crash))
    await world.crash(world.kernel(port, capability_check=observed))
    slots = [e.data for e in world.events("loop.state_saved")]
    assert slots and slots[0] == {
        "slots": {"capabilities.observed": {"mcp:inst-1": "sha256:" + "a" * 64}}
    }

    observed.value = {"mcp:inst-1": "sha256:" + "b" * 64}
    lost = await world.run(world.kernel(port, capability_check=observed))
    assert (lost.status, lost.reason) == ("yielded", "capability_wait")
    (waited,) = world.events("run.yielded")
    assert waited.data["reason"] == "capability_wait"
    assert world.journal.status(RUN) == "queued"
    assert world.events("run.suspended") == []


async def test_pa7_a_capability_still_lost_after_the_bounded_rechecks_fails_the_run() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    observed = _Observed({"mcp:inst-1": "sha256:" + "a" * 64})
    registry.arm("worker.model.after_response_commit", Raise(Crash))
    await world.crash(world.kernel(port, capability_check=observed))
    registry.reset()
    observed.value = {"mcp:inst-1": "sha256:" + "b" * 64}

    outcomes = []
    for delay in CAPABILITY_RECHECK_S:
        outcome = await world.run(world.kernel(port, capability_check=observed))
        outcomes.append((outcome.status, outcome.reason))
        _ = world.clock.advance(delay)
    final = await world.run(world.kernel(port, capability_check=observed))
    assert outcomes == [("yielded", "capability_wait")] * len(CAPABILITY_RECHECK_S)
    assert (final.status, final.reason) == ("failed", "capability_lost")
    (failed,) = world.events("run.failed")
    assert failed.data == {"errorType": "capability_lost"}
    assert world.journal.status(RUN) == "failed"


async def test_pa7_a_capability_back_within_the_bound_resumes_on_the_same_pin() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    observed = _Observed({"mcp:inst-1": "sha256:" + "a" * 64})
    registry.arm("worker.model.after_response_commit", Raise(Crash))
    await world.crash(world.kernel(port, capability_check=observed))
    registry.reset()
    observed.value = {"mcp:inst-1": "sha256:" + "b" * 64}
    waited = await world.run(world.kernel(port, capability_check=observed))
    assert waited.status == "yielded"
    _ = world.clock.advance(CAPABILITY_RECHECK_S[0])
    observed.value = {"mcp:inst-1": "sha256:" + "a" * 64}
    done = await world.run(world.kernel(port, capability_check=observed))
    assert done.status == "completed"
    assert len(world.events("loop.state_saved")) == 1


async def test_an_unchanged_observation_runs_and_is_journaled_once() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("hello"))
    observed = _Observed({"mcp:inst-1": "sha256:" + "a" * 64})
    done = await world.run(world.kernel(port, capability_check=observed))
    assert done.status == "completed"
    assert len(world.events("loop.state_saved")) == 1


async def test_td3_a_terminal_answer_completes_the_run_with_its_outcome() -> None:
    """A terminal tool (legacy ``final_answer``): its outcome and the completion
    are one commit, so a crash never leaves the answer journaled but unused."""
    world = World()

    async def final(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"ok": True}, answer=str(args["answer"]))

    world.tools.add("final", final, tags=[("effect", "read")])
    world.admit()
    model, port = scripted(reply("thinking", ("final", {"answer": "Twenty-five days."})), reply("never"))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert model.remaining == 1, "the model is not called after a terminal answer"
    [(complete, types)] = [c for c in world.journal.commits(RUN) if ":complete:" in c[0]]
    assert types[:2] == ["tool.intent", "tool.outcome"] and "message.completed" in types
    assert not any(":reads:" in c for c, _ in world.journal.commits(RUN))
    final_text = [e for e in world.events("message.completed") if e.data["isFinal"]]
    assert len(final_text) == 1


async def test_td2_slot_updates_from_a_batch_are_journaled_last_finisher_wins() -> None:
    world = World()
    order: list[str] = []

    async def grow(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        # The first call finishes last: its snapshot includes the second's.
        if args["n"] == 1:
            await asyncio.sleep(0.01)
        order.append(str(args["n"]))
        return ToolExecution(status="ok", content={}, slots={"disclosure.visible": sorted(order)})

    world.tools.add("grow", grow, tags=[("effect", "read")])
    world.admit()
    _, port = scripted(reply("", ("grow", {"n": 1}), ("grow", {"n": 2})), reply("done"))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    saved = [e.data["slots"] for e in world.events("loop.state_saved") if "disclosure.visible" in e.data["slots"]]
    assert saved == [{"disclosure.visible": ["1", "2"]}]
    assert port.calls[-1].slots["disclosure.visible"] == ["1", "2"]
