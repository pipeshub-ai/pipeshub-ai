"""K-1..K-5, K-11: crash windows, commit rules and effect recovery (REPORT 6.1.4, 6.2.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.failpoints import Raise
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ThinkingPart,
    ToolMessage,
)
from harness_core.testing import armed

from .support import RUN, Crash, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    pass

CRASH = Raise(Crash)


def _world() -> World:
    world = World()
    world.admit()
    return world


async def test_k1_crash_before_response_commit_resamples_under_a_new_message() -> None:
    world = _world()
    model, port = scripted(reply("first try"), reply("second try"))
    with armed({"worker.model.before_response_commit": CRASH}):
        await world.crash(world.kernel(port))
    assert world.events("model.response") == []
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert model.remaining == 0
    (response,) = world.events("model.response")
    fences = {f.fence for f in world.live.frames}
    assert fences == {1, 2}
    old = [f for f in world.live.frames if f.fence == 1]
    new = [f for f in world.live.frames if f.fence == 2]
    assert {f.frame_id for f in old}.isdisjoint(f.frame_id for f in new)
    assert "".join(str(f.data["text"]) for f in new) == "second try"
    assert response.fence == 2


async def test_k2_crash_after_response_commit_never_resamples_that_turn() -> None:
    world = _world()
    model, port = scripted(reply("the answer", reasoning="private chain"))
    with armed({"worker.model.after_response_commit": CRASH}):
        await world.crash(world.kernel(port))
    assert model.remaining == 0
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert model.remaining == 0 and len(port.calls) == 1
    assistant = done.transcript[-1]
    assert isinstance(assistant, AssistantMessage) and assistant.text == "the answer"
    assert any(isinstance(p, ThinkingPart) for p in assistant.content), (
        "raw provider state restored"
    )
    assert port.restored >= 1


async def test_k2_resume_continues_from_a_journaled_tool_turn_without_resampling() -> (
    None
):
    world = _world()
    model, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    with armed({"worker.model.after_response_commit": CRASH}):
        await world.crash(world.kernel(port))
    assert model.remaining == 1
    done = await world.run(world.kernel(port))
    assert done.status == "completed" and len(port.calls) == 2
    assert_paired(port.calls[-1].messages)


@pytest.mark.parametrize(
    "idempotent", [True, False], ids=["idempotent", "non-idempotent"]
)
async def test_k3_crash_after_intent_executes_the_effect_once(idempotent: bool) -> None:
    world = _world()
    tool = "create_issue" if idempotent else "post_message"
    model, port = scripted(reply("", (tool, {"title": "bug"})), reply("filed"))
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(world.kernel(port))
    assert world.saas.effects == []
    assert [e.type for e in world.events("tool.intent")] == ["tool.intent"]
    first = await world.run(world.kernel(port))
    if not idempotent:
        # The kernel cannot know the effect did not happen: a human decides.
        assert first.status == "suspended" and first.reason == "ix.unknown.t1.0"
        assert world.saas.effects == []
        world.journal.resolve_interaction(RUN, "ix.unknown.t1.0", "approved")
        first = await world.run(world.kernel(port))
    assert first.status == "completed"
    assert len(world.saas.effects) == 1
    assert model.remaining == 0


async def test_k4_idempotent_lost_ack_retries_with_the_same_key_and_the_provider_dedupes() -> (
    None
):
    world = _world()
    world.lose_ack_calls = 1
    _, port = scripted(reply("", ("create_issue", {"title": "bug"})), reply("filed"))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    effect = world.saas.assert_exactly_once(f"org1:{RUN}:t1.0")
    (outcome,) = world.events("tool.outcome")
    assert outcome.data["status"] == "ok" and outcome.data["receipt"] == {
        "id": effect.receipt_id
    }


async def test_k4_non_idempotent_lost_ack_suspends_as_unknown_and_never_blind_retries() -> (
    None
):
    world = _world()
    world.lose_ack_calls = 1
    model, port = scripted(
        reply("", ("post_message", {"text": "hi"})), reply("could not confirm")
    )
    suspended = await world.run(world.kernel(port))
    assert suspended.status == "suspended"
    assert [e.type for e in world.events()][-3:] == [
        "tool.unknown",
        "interaction.requested",
        "run.suspended",
    ]
    assert len(world.saas.effects) == 1
    world.journal.resolve_interaction(RUN, "ix.unknown.t1.0", "denied")
    done = await world.run(world.kernel(port))
    assert done.status == "completed" and len(world.saas.effects) == 1
    (outcome,) = world.events("tool.outcome")
    assert outcome.data["status"] == "error"
    result = port.calls[-1].messages[-1]
    assert isinstance(result, ToolMessage) and "unknown" in result.text
    assert model.remaining == 0


async def test_k4_unclassified_exception_from_a_write_is_treated_as_unknown() -> None:
    world = _world()
    _, port = scripted(reply("", ("flaky_write", {})), reply("ok"))
    assert (await world.run(world.kernel(port))).status == "suspended"
    assert world.events("tool.unknown")


async def test_k5_read_crash_before_outcome_reexecutes_and_records_once() -> None:
    world = _world()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("found"))
    with armed({"worker.tool.before_outcome_commit": CRASH}):
        await world.crash(world.kernel(port))
    assert world.events("tool.outcome") == []
    assert (await world.run(world.kernel(port))).status == "completed"
    assert [name for name, _ in world.tools.executed] == ["lookup", "lookup"]
    assert len(world.events("tool.outcome")) == 1
    assert len(world.events("tool.intent")) == 1


async def test_k11_pre_run_steps_run_once_across_a_crash() -> None:
    world = _world()
    runs: list[str] = []

    class Rewrite:
        key = "goal"

        async def run(self, input_text: str) -> str:
            runs.append(input_text)
            return input_text.upper()

    _, port = scripted(reply("one"), reply("two"))
    with armed({"worker.prerun.after_commit": CRASH}):
        await world.crash(world.kernel(port, pre_run=[Rewrite()]))
    done = await world.run(world.kernel(port, pre_run=[Rewrite()]))
    assert done.status == "completed"
    assert runs == ["please help"]
    assert port.calls[-1].slots["pre_run.goal"] == "PLEASE HELP"
    assert len(world.events("loop.state_saved")) == 1
