"""UX5 FU-1..FU-4, FU-12: suggested next questions after a durable answer, off the run's critical path.

The step reserves on the run's ledger while the attempt is leased (``prepare``), and calls the model, settles and
delivers after the terminal commit (``run``)."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, override

import pytest

from app.harness.assembly.followups import (
    FOLLOWUPS_KIND,
    OUTPUT_TOKENS,
    FollowupsStep,
    followups_steps,
    parse_followups,
)
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import Cap, RunAccountRef
from harness_core.journal.memory import InMemoryLiveBus
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.ports import RunEnding
from harness_core.logs import HarnessLog
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport, reply_events

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.journal.types import Claim

QUESTION = "What were Q3 sales by region?"
ANSWER = "West led with 42; East had 30."
ITEMS = ["Compare this with Q2", "Which products drove the West?", "Draft an email with these numbers"]
REPLY = json.dumps(ITEMS)
LOG = HarnessLog.named("tests.ux5.followups")
ROOT = RunAccountRef("org-1", "run-1")


class _Sink:
    def __init__(self, *, kept: bool = True) -> None:
        self.kept = kept
        self.items: list[list[str]] = []

    async def deliver(self, items: Sequence[str]) -> bool:
        self.items.append(list(items))
        return self.kept


class _World:
    def __init__(self, text: str = REPLY, inner: RecordedTransport | None = None) -> None:
        self.ledger = InMemoryBudgetLedger(ManualClock())
        self.inner = inner or RecordedTransport([reply_events(text)])
        self.transport = MeteredTransport(
            self.inner, accounts=self.ledger, account=ROOT, pricing_model="gpt-6-luna", ids=SeededIds(0)
        )
        self.bus = InMemoryLiveBus()
        self.sink = _Sink()

    async def open(self) -> _World:
        _ = await self.ledger.open_root(ROOT, Cap(None, 10_000_000))
        return self

    def step(self, **kwargs: Any) -> FollowupsStep:  # noqa: ANN401
        return FollowupsStep(
            self.transport, question=QUESTION, run_id="run-1", fence=2, bus=self.bus, sink=self.sink, log=LOG, **kwargs
        )


def _ending(outcome: str = "answered", **kwargs: Any) -> RunEnding:  # noqa: ANN401
    return RunEnding(outcome, texts=(ANSWER,), **kwargs)  # type: ignore[arg-type]


async def test_fu1_an_answered_run_delivers_its_suggestions_on_the_bus_and_to_node() -> None:
    w = await _World().open()
    step = w.step()
    await step.run(await step.prepare(_ending()))
    [frame] = await w.bus.read("run-1")
    assert (frame.kind, frame.fence) == (FOLLOWUPS_KIND, 2)
    assert frame.data == {"items": [{"text": t} for t in ITEMS]}
    assert w.sink.items == [ITEMS]


async def test_fu2_reserved_while_leased_settled_after_bounded_and_charged() -> None:
    w = await _World().open()
    step = w.step()
    prepared = await step.prepare(_ending())
    assert w.inner.calls == []
    held = await w.ledger.remaining(ROOT)
    assert held.reserved.tokens > 0
    await step.run(prepared)
    [call] = w.inner.calls
    assert call.max_output_tokens == OUTPUT_TOKENS
    after = await w.ledger.remaining(ROOT)
    assert after.reserved.tokens == 0
    assert after.committed.tokens > 0


async def test_fu4_the_model_sees_the_question_and_the_answer_only() -> None:
    w = await _World().open()
    secret_result = "internal-only tool output"
    step = w.step()
    ending = _ending(tool_calls=(ToolCall(id="c1", name="search", arguments={"q": secret_result}),))
    await step.run(await step.prepare(ending))
    [call] = w.inner.calls
    sent = json.dumps([m.model_dump(mode="json") for m in call.messages])
    assert QUESTION in sent
    assert ANSWER in sent
    assert secret_result not in sent
    assert len(call.messages) == 1


@pytest.mark.parametrize("outcome", ["error", "stopped", "needs_input"])
async def test_fu3_only_an_answered_run_prepares(outcome: str) -> None:
    w = await _World().open()
    assert await w.step().prepare(_ending(outcome)) is None
    assert (await w.ledger.remaining(ROOT)).reserved.tokens == 0


async def test_fu3_unusable_output_delivers_nothing() -> None:
    w = await _World("I cannot help with that.").open()
    step = w.step()
    await step.run(await step.prepare(_ending()))
    assert await w.bus.read("run-1") == []
    assert w.sink.items == []


class _Silent(RecordedTransport):
    @override
    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        _ = await asyncio.Event().wait()


async def test_fu3_a_missed_deadline_delivers_nothing_and_releases_the_reservation() -> None:
    w = await _World(inner=_Silent()).open()
    step = w.step(deadline_s=0)
    await step.run(await step.prepare(_ending()))
    assert w.sink.items == []
    assert (await w.ledger.remaining(ROOT)).reserved.tokens == 0


async def test_fu3_a_refused_terminal_commit_releases_the_reservation() -> None:
    w = await _World().open()
    step = w.step()
    await step.discard(await step.prepare(_ending()))
    assert (await w.ledger.remaining(ROOT)).reserved.tokens == 0
    assert w.inner.calls == []


async def test_fu3_a_model_error_raises_for_the_kernel_to_log_and_releases() -> None:
    w = await _World(inner=RecordedTransport(fail=RuntimeError("provider down"))).open()
    step = w.step()
    prepared = await step.prepare(_ending())
    with pytest.raises(RuntimeError):
        await step.run(prepared)
    assert (await w.ledger.remaining(ROOT)).reserved.tokens == 0


async def test_fu3_node_that_refuses_keeps_the_frame_and_logs() -> None:
    w = await _World().open()
    w.sink.kept = False
    step = w.step()
    await step.run(await step.prepare(_ending()))
    assert [f.kind for f in await w.bus.read("run-1")] == [FOLLOWUPS_KIND]


async def test_fu2_without_a_ledger_the_call_is_still_bounded() -> None:
    inner = RecordedTransport([reply_events(REPLY)])
    sink = _Sink()
    step = FollowupsStep(inner, question=QUESTION, run_id="run-1", fence=2, bus=None, sink=sink, log=LOG)
    await step.run(await step.prepare(_ending()))
    assert inner.calls[0].max_output_tokens == OUTPUT_TOKENS
    assert sink.items == [ITEMS]


class _Claim:
    run_id = "run-1"
    fence = 2

    def __init__(self, accepts: bool) -> None:
        self._accepts = accepts

    def journal_accepts(self, feature: str) -> bool:
        return self._accepts


@pytest.mark.parametrize(
    ("enabled", "child", "has_transport", "expected"),
    [(True, False, True, 1), (False, False, True, 0), (True, True, True, 0), (True, False, False, 0)],
)
def test_fu3_the_flag_children_and_test_doubles(enabled: bool, child: bool, has_transport: bool, expected: int) -> None:
    transport = RecordedTransport() if has_transport else None
    claim: Claim = _Claim(True)  # type: ignore[assignment]
    steps = followups_steps(
        transport, claim=claim, question=QUESTION, enabled=enabled, child=child, bus=None, sink=_Sink(), log=LOG
    )
    assert len(steps) == expected


async def test_fu3_an_older_node_is_sent_nothing_but_the_bus_still_carries_them() -> None:
    sink, bus = _Sink(), InMemoryLiveBus()
    claim: Claim = _Claim(False)  # type: ignore[assignment]
    [step] = followups_steps(
        RecordedTransport([reply_events(REPLY)]), claim=claim, question=QUESTION, enabled=True, child=False,
        bus=bus, sink=sink, log=LOG,
    )
    await step.run(await step.prepare(_ending()))
    assert sink.items == []
    assert [f.kind for f in await bus.read("run-1")] == [FOLLOWUPS_KIND]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (REPLY, ITEMS),
        ('Sure:\n["a?", "b?", "c?", "d?"]', ["a?", "b?", "c?"]),
        ("- First idea\n- Second idea\n", ["First idea", "Second idea"]),
        (json.dumps(["Same", "same", QUESTION, "Other"]), ["Same", "Other"]),
        (json.dumps(["only one"]), []),
        (json.dumps(["x" * 200, "ok", "fine"]), ["ok", "fine"]),
        (json.dumps(["Use gh" + "p_abcdefghijklmnopqrstuvwxyz0123456789 here", "Next"]), ["Use •••••• here", "Next"]),
    ],
)
def test_fu1_parsing(text: str, expected: list[str]) -> None:
    assert parse_followups(text, QUESTION) == expected


async def test_fu12_a_slow_suggestions_model_never_delays_run_completed() -> None:
    from harness_core.kernel.durable.driver import _DETACHED
    from tests.unit.harness_core.durable.support import World, reply, scripted

    world = World()
    world.admit()
    _, port = scripted(reply(ANSWER))
    slow = _Silent()
    step = FollowupsStep(slow, question=QUESTION, run_id="run-1", fence=1, bus=None, sink=_Sink(), log=LOG)
    done = await world.run(world.kernel(port, after_run=[step]))
    assert done.status == "completed"
    assert world.events()[-1].type == "run.completed"
    pending = [t for t in _DETACHED if not t.done()]
    assert pending
    for task in pending:
        _ = task.cancel()
    _ = await asyncio.gather(*pending, return_exceptions=True)
