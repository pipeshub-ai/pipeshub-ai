"""PERFA-10 (WRK-39; QA22-F1): the other commits whose size follows the model's parallel calls.

A response is presented with one event a call, every write that needs approval is asked in one commit,
and every delegation a batch refuses gets its outcome in one commit. Each is cut at the journal's limit
without cutting what belongs to one call.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harness_core.failpoints import Arm, Nth, Raise
from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS
from harness_core.journal.types import InteractionResolved, NewEvent
from harness_core.kernel.durable.delegation import (
    DelegatedOutcome,
    Refuse,
    delegate_effect,
    goal_tool,
)
from harness_core.kernel.durable.slots import CAPABILITY_SLOT
from harness_core.testing import armed
from harness_core.testing.durable_ports import StaticCapabilityCheck
from harness_core.testing.run_spec import minimal_run_spec

from .support import RUN, Crash, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ToolEffect
    from harness_core.journal.types import CommitReceipt, CommitRequest, JournalEvent
    from harness_core.kernel.core.messages import AssistantMessage, ToolCall
    from harness_core.kernel.core.tool_schema import ToolSchema
    from harness_core.kernel.durable.delegation import (
        Decision,
        Delegated,
        DelegationView,
    )
    from harness_core.kernel.durable.ports import RunEnding

CRASH = Raise(Crash)
AFTER_COMMIT = "worker.commit.after_ack"
CALL_PRESENTED = {"x.test.call": frozenset({"worker"})}


def commits_of(world: World) -> list[tuple[str, list[JournalEvent]]]:
    return [(command, list(events)) for command, events in world.journal.commit_events(RUN)]


# ---- a response with more calls than a commit holds ----


class _PresentsCalls:
    """One event a call, as the product's presenter journals a response."""

    def pre_run(self, slots: Mapping[str, JsonValue]) -> list[NewEvent]:
        return []

    def response(self, message: AssistantMessage) -> list[NewEvent]:
        return [NewEvent("x.test.call", {"callId": c.id}, critical=False) for c in message.tool_calls or []]

    async def finalize(self, ending: RunEnding) -> list[NewEvent]:
        return []


async def test_perfa_10_a_response_with_more_calls_than_a_commit_holds_is_journaled_whole_first() -> None:
    calls = MAX_COMMIT_EVENTS + 50
    world = World(extension_writers=CALL_PRESENTED)  # type: ignore[arg-type]
    world.admit()
    model, port = scripted(reply("", *[("lookup", {"q": f"q{i}"}) for i in range(calls)]), reply("done"))
    done = await world.run(world.kernel(port, presenter=_PresentsCalls()))
    assert (done.status, model.remaining) == ("completed", 0)
    first, second = [events for command, events in commits_of(world) if ":model:" in command][:2]
    head = [e.type for e in first if e.type != "x.test.call"]
    assert head == ["message.completed", "turn.started", "model.request", "model.response"]
    assert len(first) == MAX_COMMIT_EVENTS and {e.type for e in second} == {"x.test.call"}
    presented = [str(e.data["callId"]) for e in world.events("x.test.call")]
    assert len(presented) == len(set(presented)) == calls
    assert_paired(list(done.transcript))


async def test_perfa_10_a_crash_between_the_parts_of_a_response_never_samples_the_turn_again() -> None:
    calls = MAX_COMMIT_EVENTS + 50
    world = World(extension_writers=CALL_PRESENTED)  # type: ignore[arg-type]
    world.admit()
    model, port = scripted(reply("", *[("lookup", {"q": f"q{i}"}) for i in range(calls)]), reply("done"))
    kernel = world.kernel(port, presenter=_PresentsCalls())
    with armed({AFTER_COMMIT: Arm(CRASH, Nth(1))}):
        await world.crash(kernel)
    assert len(world.events("model.response")) == 1
    done = await world.run(kernel)
    assert (done.status, model.remaining, len(port.calls)) == ("completed", 0, 2)
    outcomes = Counter(str(e.data["invocationId"]) for e in world.events("tool.outcome"))
    assert len(outcomes) == calls and set(outcomes.values()) == {1}
    presented = [str(e.data["callId"]) for e in world.events("x.test.call")]
    response = world.events("model.response")[0]
    assert presented == [c["callId"] for c in response.data["toolCalls"]], (  # type: ignore[index,union-attr]
        "every call is presented once, in the response's order"
    )


async def test_perfa_10_a_resumed_turn_whose_presentation_fits_its_commit_presents_nothing_again() -> None:
    world = World(extension_writers=CALL_PRESENTED)  # type: ignore[arg-type]
    world.admit()
    model, port = scripted(reply("", *[("lookup", {"q": f"q{i}"}) for i in range(5)]), reply("done"))
    kernel = world.kernel(port, presenter=_PresentsCalls())
    with armed({AFTER_COMMIT: Arm(CRASH, Nth(1))}):
        await world.crash(kernel)
    done = await world.run(kernel)
    assert (done.status, model.remaining) == ("completed", 0)
    assert len(world.events("x.test.call")) == 5
    assert not any(":present:" in command for command, _ in commits_of(world))


# ---- more writes asking for approval than a commit holds ----


class _AsksWhy:
    """Asks about every write, and says why (``interaction.explained`` beside each request)."""

    def __call__(self, call: ToolCall, effect: ToolEffect) -> str:
        return "ask" if effect.effect == "write" else "allow"

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue]:
        return {
            "rule": "flow_external_write",
            "toolId": effect.tool_id,
            "sensitivity": "private",
            "sources": [],
            "moreSources": 0,
            "conversationGrant": False,
        }


def _writes(count: int):  # noqa: ANN202
    return reply("", *[("post_message", {"text": f"m{i}"}) for i in range(count)])


def _assert_each_asked_once_with_its_reason(world: World, calls: int) -> None:
    asked = Counter(str(e.data["interactionId"]) for e in world.events("interaction.requested"))
    assert len(asked) == calls and set(asked.values()) == {1}
    assert world.journal.open_interactions(RUN).keys() == asked.keys()
    for command, events in commits_of(world):
        explained = {str(e.data["interactionId"]) for e in events if e.type == "interaction.explained"}
        requested = {str(e.data["interactionId"]) for e in events if e.type == "interaction.requested"}
        assert explained == requested, f"{command}: a request is never in a later commit than its reason"


@pytest.mark.parametrize(("limit", "calls"), [(MAX_COMMIT_EVENTS, MAX_COMMIT_EVENTS // 2 + 10), (7, 10)])
async def test_perfa_10_more_approvals_than_a_commit_holds_suspend_the_run_with_each_asked_once(
    limit: int, calls: int
) -> None:
    world = World(max_commit_events=limit)
    world.admit()
    _, port = scripted(_writes(calls))
    done = await world.run(world.kernel(port, approval=_AsksWhy()))
    assert done.status == "suspended" and world.journal.status(RUN) == "suspended"
    _assert_each_asked_once_with_its_reason(world, calls)
    assert world.saas.effects == [], "nothing is sent before a person decides"


async def test_perfa_10_a_crash_between_the_parts_of_the_asks_asks_no_call_twice() -> None:
    limit, calls = 7, 10
    baseline = World(max_commit_events=limit)
    baseline.admit()
    _, port = scripted(_writes(calls))
    _ = await baseline.run(baseline.kernel(port, approval=_AsksWhy()))
    parts = sum(1 for command, _ in commits_of(baseline) if ":suspend:" in command)
    assert parts > 2
    for part in range(1, parts):
        world = World(max_commit_events=limit)
        world.admit()
        _, port = scripted(_writes(calls))
        with armed({AFTER_COMMIT: Arm(CRASH, Nth(1 + part))}):
            await world.crash(world.kernel(port, approval=_AsksWhy()))
        done = await world.run(world.kernel(port, approval=_AsksWhy()))
        assert done.status == "suspended", part
        _assert_each_asked_once_with_its_reason(world, calls)


# ---- more refused delegations than a commit holds ----


class _RefusesAll:
    def delegates(self, call: ToolCall) -> bool:
        return call.name == "delegate"

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        return Refuse("refused: not a task for a sub-agent")

    async def outcome(self, call: ToolCall, document: JsonValue) -> DelegatedOutcome:
        return DelegatedOutcome("", error=True)

    def effect(self, name: str) -> ToolEffect:
        return delegate_effect(name)

    def schemas(self) -> list[ToolSchema]:
        return [goal_tool("delegate", "Hand one task to a sub-agent")]


async def test_perfa_10_more_refused_delegations_than_a_commit_holds_each_get_one_outcome() -> None:
    # An odd limit: a boundary would fall inside a refusal's two events if they were not one unit.
    limit, calls = 5, 8
    delegations = reply("", *[("delegate", {"goal": f"g{i}", "task_id": f"t{i}"}) for i in range(calls)])

    def world_and_kernel():  # noqa: ANN202
        world = World(max_commit_events=limit)
        world.admit(spec=minimal_run_spec(max_children=4))
        model, port = scripted(delegations, reply("done"))
        return world, model, world.kernel(port, children=world.journal, delegation=_RefusesAll())

    world, model, kernel = world_and_kernel()
    done = await world.run(kernel)
    assert (done.status, model.remaining) == ("completed", 0)
    parts = sum(1 for command, _ in commits_of(world) if ":refusal:" in command)
    assert parts == calls // (limit // 2)
    for part in range(1, parts + 1):
        world, model, kernel = world_and_kernel()
        with armed({AFTER_COMMIT: Arm(CRASH, Nth(1 + part))}):
            await world.crash(kernel)
        done = await world.run(kernel)
        assert (done.status, model.remaining) == ("completed", 0), part
        for event_type in ("tool.intent", "tool.outcome"):
            counts = Counter(str(e.data["invocationId"]) for e in world.events(event_type))
            assert len(counts) == calls and set(counts.values()) == {1}, (part, event_type)
        for command, events in commits_of(world):
            intents = {str(e.data["invocationId"]) for e in events if e.type == "tool.intent"}
            outcomes = {str(e.data["invocationId"]) for e in events if e.type == "tool.outcome"}
            assert intents == outcomes, command
        assert_paired(list(done.transcript))


# ---- what rides with a commit is journaled once ----


class _ResolvedOnSecondCommit:
    """A journal that refuses the attempt's second commit as Node refuses a suspension it has nothing to wait on."""

    def __init__(self, world: World) -> None:
        self._journal = world.journal
        self.claim = world.journal.claim
        self.read = world.journal.read
        self.last_seq = world.journal.last_seq
        self.renew = world.journal.renew
        self.release = world.journal.release
        self.commits = 0

    async def commit(self, request: CommitRequest) -> CommitReceipt:
        self.commits += 1
        if self.commits == 2:
            raise InteractionResolved("INTERACTION_RESOLVED")
        return await self._journal.commit(request)


async def test_perfa_10_what_rode_with_a_part_that_landed_is_not_journaled_again_when_a_later_part_is_refused() -> None:
    calls = MAX_COMMIT_EVENTS + 50
    world = World(extension_writers=CALL_PRESENTED)  # type: ignore[arg-type]
    world.admit()
    _, port = scripted(reply("", *[("lookup", {"q": f"q{i}"}) for i in range(calls)]))
    kernel = world.kernel(
        port, presenter=_PresentsCalls(), capability_check=StaticCapabilityCheck({"tool:lookup": "sha256:1"})
    )
    done = await world.run(replace(kernel, journal=_ResolvedOnSecondCommit(world)))  # type: ignore[arg-type]
    assert done.status == "failed"
    recorded = [e for e in world.events("loop.state_saved") if CAPABILITY_SLOT in e.data["slots"]]  # type: ignore[operator]
    assert len(recorded) == 1
