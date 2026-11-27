"""PERFA-4..PERFA-6 (WRK-35, WRK-36; QA22-F1): a model turn with more parallel reads than one commit
holds is journaled as consecutive commits, and a crash after any of them recovers exactly.

The in-memory journal refuses a commit over the limit as Node does, so a run that completes here made
no commit over it.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import pytest

from harness_core.failpoints import Arm, Nth, Raise
from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS
from harness_core.journal.types import NewEvent
from harness_core.kernel.durable import ToolExecution
from harness_core.testing import armed

from .support import RUN, Crash, World, assert_paired, render_pairs, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.core.messages import AssistantMessage
    from harness_core.kernel.durable import Disposition, ToolContext
    from harness_core.kernel.durable.ports import RunEnding
    from harness_core.testing import Reply

CRASH = Raise(Crash)
AFTER_COMMIT = "worker.commit.after_ack"
PROBE_CASES = [50, 51, 100, 101, 500]


def wide(count: int, *extra: tuple[str, dict[str, JsonValue]]) -> Reply:
    return reply("", *[("lookup", {"q": f"q{i}"}) for i in range(count)], *extra)


class Reads:
    """A world whose reads are counted, and that can end the batch with an answer, a question or slots."""

    def __init__(self, limit: int = MAX_COMMIT_EVENTS) -> None:
        self.world = World(max_commit_events=limit)
        self.ran: Counter[str] = Counter()
        self.world.on_read = lambda _world: None
        self.world.tools.add("lookup", self._lookup, tags=[("category", "read")])
        self.world.tools.add("noted", self._noted, tags=[("category", "read")])
        self.world.tools.add("final", self._final, tags=[("effect", "read")])
        self.world.tools.add("ask", self._ask, tags=[("effect", "read")])
        self.world.admit()

    async def _lookup(self, args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        self.ran[str(args["q"])] += 1
        return ToolExecution(status="ok", content={"found": args["q"]})

    async def _noted(self, args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        self.ran[str(args["q"])] += 1
        return ToolExecution(status="ok", content={"noted": args["q"]}, slots={f"seen.{args['q']}": True})

    async def _final(self, args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"ok": True}, answer=str(args["answer"]))

    async def _ask(self, args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"asked": True}, needs_input=True)

    def per_invocation(self, event_type: str) -> Counter[str]:
        return Counter(str(e.data["invocationId"]) for e in self.world.events(event_type))

    def commits(self, kind: str) -> list[list[str]]:
        return [types for command, types in self.world.journal.commits(RUN) if f":{kind}:" in command]

    def assert_each_read_journaled_once(self, calls: int) -> None:
        for event_type in ("tool.intent", "tool.outcome"):
            counts = self.per_invocation(event_type)
            assert len(counts) == calls, event_type
            assert set(counts.values()) == {1}, f"{event_type} written twice or lost"

    def assert_reads_commit_whole(self) -> None:
        """A read's intent and outcome are in the same commit: no commit boundary falls inside a read."""
        for command, events in self.world.journal.commit_events(RUN):
            intents = {str(e.data["invocationId"]) for e in events if e.type == "tool.intent"}
            outcomes = {str(e.data["invocationId"]) for e in events if e.type == "tool.outcome"}
            assert intents == outcomes, command


async def crash_then_finish(reads: Reads, replies: tuple[Reply, ...], nth: int) -> Disposition:
    _, port = scripted(*replies)
    with armed({AFTER_COMMIT: Arm(CRASH, Nth(nth))}):
        await reads.world.crash(reads.world.kernel(port))
    return await reads.world.run(reads.world.kernel(port))


@pytest.mark.parametrize("calls", PROBE_CASES)
async def test_perfa_4_a_turn_of_parallel_reads_completes_whatever_its_width(calls: int) -> None:
    reads = Reads()
    model, port = scripted(wide(calls), reply("done"))
    done = await reads.world.run(reads.world.kernel(port))
    assert (done.status, model.remaining) == ("completed", 0)
    reads.assert_each_read_journaled_once(calls)
    reads.assert_reads_commit_whole()
    assert set(reads.ran.values()) == {1}
    committed = reads.commits("reads")
    assert len(committed) == -(-2 * calls // MAX_COMMIT_EVENTS), "as few commits as the limit allows"
    assert all(len(types) <= MAX_COMMIT_EVENTS for types in committed)
    assert_paired(list(done.transcript))


async def test_perfa_4_the_parts_are_consecutive_commits_of_one_attempt_under_one_fence() -> None:
    reads = Reads()
    _, port = scripted(wide(120), reply("done"))
    _ = await reads.world.run(reads.world.kernel(port))
    commands = [command for command, _ in reads.world.journal.commits(RUN)]
    assert len(set(commands)) == len(commands)
    numbers = [int(command.rsplit(":", 1)[1]) for command in commands]
    assert numbers == list(range(1, len(commands) + 1)), "one counter orders every commit of the attempt"
    outcomes = reads.world.events("tool.outcome")
    assert {e.fence for e in outcomes} == {1} and len({e.attempt_id for e in outcomes}) == 1
    assert [e.seq for e in outcomes] == sorted(e.seq for e in outcomes)
    assert [str(e.data["invocationId"]) for e in outcomes] == [f"t1.{i}" for i in range(120)], "call order kept"


@pytest.mark.parametrize(("limit", "calls"), [(MAX_COMMIT_EVENTS, 120), (MAX_COMMIT_EVENTS, 500), (16, 30), (6, 11)])
async def test_perfa_5_a_crash_after_any_part_loses_and_repeats_no_read(limit: int, calls: int) -> None:
    replies = (wide(calls), reply("done"))
    baseline = Reads(limit)
    _, port = scripted(*replies)
    expected = await baseline.world.run(baseline.world.kernel(port))
    parts = len(baseline.commits("reads"))
    assert parts == -(-2 * calls // limit) and parts > 1
    per_part = limit // 2
    # The first commit is the model's response; a crash after it is the unsplit case (K-2).
    for part in range(1, parts + 1):
        reads = Reads(limit)
        done = await crash_then_finish(reads, replies, nth=1 + part)
        assert done.status == "completed", part
        reads.assert_each_read_journaled_once(calls)
        reads.assert_reads_commit_whole()
        journaled_before_crash = min(calls, part * per_part)
        assert [reads.ran[f"q{i}"] for i in range(calls)] == [1] * journaled_before_crash + [2] * (
            calls - journaled_before_crash
        ), f"part {part}: a journaled read never runs again, an unjournaled one runs once more"
        assert render_pairs(list(done.transcript)) == render_pairs(list(expected.transcript)), part


async def test_perfa_5_reads_of_uneven_size_are_never_cut_at_a_part_boundary() -> None:
    """A read that also sets a slot journals three events: boundaries that would fall inside one move before it."""
    limit, calls = 8, 14
    mixed = reply("", *[("noted" if i % 3 == 0 else "lookup", {"q": f"q{i}"}) for i in range(calls)])
    replies = (mixed, reply("done"))
    baseline = Reads(limit)
    _, port = scripted(*replies)
    expected = await baseline.world.run(baseline.world.kernel(port))
    baseline.assert_reads_commit_whole()
    parts = len(baseline.commits("reads"))
    assert parts > 2
    for part in range(1, parts + 1):
        reads = Reads(limit)
        done = await crash_then_finish(reads, replies, nth=1 + part)
        assert done.status == "completed", part
        reads.assert_each_read_journaled_once(calls)
        reads.assert_reads_commit_whole()
        assert render_pairs(list(done.transcript)) == render_pairs(list(expected.transcript)), part


async def test_perfa_6_slots_set_by_split_reads_survive_a_crash_at_every_part() -> None:
    limit, calls = 9, 12
    replies = (reply("", *[("noted", {"q": f"q{i}"}) for i in range(calls)]), reply("done"))
    baseline = Reads(limit)
    _, port = scripted(*replies)
    _ = await baseline.world.run(baseline.world.kernel(port))
    parts = len(baseline.commits("reads"))
    want = {f"seen.q{i}": True for i in range(calls)}
    assert {k: v for k, v in port.calls[-1].slots.items() if k.startswith("seen.")} == want
    for part in range(1, parts + 1):
        reads = Reads(limit)
        _, port = scripted(*replies)
        with armed({AFTER_COMMIT: Arm(CRASH, Nth(1 + part))}):
            await reads.world.crash(reads.world.kernel(port))
        saved: dict[str, JsonValue] = {}
        for event in reads.world.events("loop.state_saved"):
            saved.update(event.data["slots"])  # type: ignore[arg-type]
        journaled = {str(e.data["invocationId"]) for e in reads.world.events("tool.outcome")}
        assert {f"seen.q{i}" for i in range(calls) if f"t1.{i}" in journaled} <= set(saved), (
            f"part {part}: a journaled read's slot is in the same commit, never a later one"
        )
        done = await reads.world.run(reads.world.kernel(port))
        assert done.status == "completed"
        assert {k: v for k, v in port.calls[-1].slots.items() if k.startswith("seen.")} == want, part


@pytest.mark.parametrize("position", [0, 7, 59])
async def test_perfa_6_a_terminal_answer_among_split_reads_still_ends_the_run_after_a_crash(position: int) -> None:
    calls = 60
    lookups = [("lookup", {"q": f"q{i}"}) for i in range(calls)]
    lookups.insert(position, ("final", {"answer": "Twenty-five days."}))
    replies = (reply("thinking", *lookups), reply("never"))
    baseline = Reads()
    model, port = scripted(*replies)
    expected = await baseline.world.run(baseline.world.kernel(port))
    assert (expected.status, model.remaining) == ("completed", 1)
    (complete,) = baseline.commits("complete")
    assert "tool.outcome" in complete and "run.completed" in complete, "the answer's outcome commits with the completion"
    parts = len(baseline.commits("reads"))
    assert parts >= 1
    for part in range(1, parts + 1):
        reads = Reads()
        model, port = scripted(*replies)
        with armed({AFTER_COMMIT: Arm(CRASH, Nth(1 + part))}):
            await reads.world.crash(reads.world.kernel(port))
        done = await reads.world.run(reads.world.kernel(port))
        assert (done.status, model.remaining) == ("completed", 1), "the model is not asked again"
        finals = [e for e in reads.world.events("message.completed") if e.data.get("isFinal")]
        assert len(finals) == 1
        assert (await reads.world.blob(finals[0].data["textRef"])).decode() == "Twenty-five days."  # type: ignore[arg-type]
        reads.assert_each_read_journaled_once(calls + 1)


@pytest.mark.parametrize("position", [0, 7, 59])
async def test_perfa_6_a_question_among_split_reads_still_ends_the_run_asking_after_a_crash(position: int) -> None:
    calls = 60
    lookups = [("lookup", {"q": f"q{i}"}) for i in range(calls)]
    lookups.insert(position, ("ask", {}))
    replies = (reply("", *lookups), reply("never"))
    baseline = Reads()
    model, port = scripted(*replies)
    expected = await baseline.world.run(baseline.world.kernel(port))
    assert (expected.status, expected.reason, model.remaining) == ("completed", "needs_input", 1)
    parts = len(baseline.commits("reads"))
    for part in range(1, parts + 1):
        reads = Reads()
        model, port = scripted(*replies)
        with armed({AFTER_COMMIT: Arm(CRASH, Nth(1 + part))}):
            await reads.world.crash(reads.world.kernel(port))
        done = await reads.world.run(reads.world.kernel(port))
        assert (done.status, done.reason, model.remaining) == ("completed", "needs_input", 1), part
        reads.assert_each_read_journaled_once(calls + 1)


class _PresentsMuch:
    """A presenter whose ending is larger than the room a batch of ending reads leaves it."""

    def pre_run(self, slots: Mapping[str, JsonValue]) -> list[NewEvent]:
        return []

    def response(self, message: AssistantMessage) -> list[NewEvent]:
        return []

    async def finalize(self, ending: RunEnding) -> list[NewEvent]:
        return [NewEvent("x.test.answer", {"outcome": ending.outcome, "n": n}, critical=False) for n in range(12)]


async def test_perfa_6_a_completion_node_refuses_for_queued_input_commits_no_outcome_twice() -> None:
    """The outcomes do not fit one commit with the run's ending, so they land first; Node then refuses the
    completion because the user wrote meanwhile, and the turn that takes that input repeats no outcome."""
    calls = 44
    reads = Reads()
    reads.world.journal._extension_writers["x.test.answer"] = frozenset({"worker"})  # pyright: ignore[reportPrivateUsage]

    async def final_while_the_user_writes(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        _ = reads.world.journal.queue_input(RUN, "one more thing")
        return ToolExecution(status="ok", content={"ok": True}, answer="Twenty-five days.")

    reads.world.tools.add("final", final_while_the_user_writes, tags=[("effect", "read")])
    lookups = [("lookup", {"q": f"q{i}"}) for i in range(calls)]
    model, port = scripted(reply("", *lookups, ("final", {"answer": "x"})), reply("with the new input"))
    done = await reads.world.run(reads.world.kernel(port, presenter=_PresentsMuch()))
    assert (done.status, model.remaining) == ("completed", 0), "the queued input got its own turn"
    reads.assert_each_read_journaled_once(calls + 1)
    assert len(reads.world.events("input.consumed")) == 1
    assert_paired(list(done.transcript))
