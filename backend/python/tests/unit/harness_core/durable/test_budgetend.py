"""BUDGETEND: a run that reaches a limit answers with what it gathered, on one answer-only turn, flagged as cut short.

A run that asked "what's happening in slack?" hit its 1M-token budget after nine calls, and ended with only
"This reply stopped because it reached its spending limit." These cases drive the durable kernel to each limit
(the spending limit on a working call, on a compaction it cannot afford, its working time) and check that the run
completes ``answered`` with ``run.completed.truncatedBy``, that the last request asked for no tools, carried the
cut-short request and drew on the wrap-up reserve, and that a run that cannot pay even for that answers without a
model, listing what it gathered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from harness_core.budget.errors import BudgetExhausted
from harness_core.budget.wrap_up import WrapUpSize
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.failpoints import Arm, Nth, Raise
from harness_core.journal.children import child_result_document
from harness_core.kernel.core.messages import ToolMessage
from harness_core.kernel.core.tokens import count_tokens
from harness_core.kernel.core.turn_limit import cut_short_note
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.ports import ModelFailed
from harness_core.kernel.durable.slots import (
    CUT_SHORT_SLOT,
    FINAL_ANSWER_SLOT,
    WRAP_UP_FIT_SLOT,
)
from harness_core.testing import armed
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.context.kernel_support import WINDOW, compiler, looked_up
from tests.unit.harness_core.durable.support import Crash, World, reply, scripted

if TYPE_CHECKING:
    from harness_core.context.ports import CompactionRequest, CompactionSummary
    from harness_core.kernel.durable.ports import ModelCall, SampledTurn
    from harness_core.testing.durable import ScriptedModelPort

ANSWER = "From the two lookups: the travel cap is 400 USD. I stopped early at this reply's spending limit."


@dataclass
class FakeWrapUp:
    """The run's wrap-up reserve as the ledger would hold it: ``armed`` until the next call draws it."""

    size: WrapUpSize = field(default_factory=lambda: WrapUpSize(input_tokens=2_000, output_tokens=500))
    can_hold: bool = True
    is_held: bool = False
    armed: bool = False
    holds: int = 0
    drawn: int = 0
    released: int = 0

    @property
    def held(self) -> bool:
        return self.is_held

    async def hold(self) -> bool:
        self.holds += 1
        self.is_held = self.is_held or self.can_hold
        return self.is_held

    def arm(self) -> bool:
        self.armed = self.is_held
        return self.armed

    async def release(self) -> None:
        if self.is_held:
            self.released += 1
        self.is_held = self.armed = False


@dataclass
class Ledgered:
    """The run's model port behind its ledger: working calls past ``allow`` are refused for want of spend, the
    call the reserve was armed for draws on it."""

    port: ScriptedModelPort
    wrap: FakeWrapUp
    allow: int = 1_000
    fail_wrap_up: bool = False
    # Which shared budget refused the call (WP-51); None: the run's own spending limit.
    scope: str | None = None
    sent: list[ModelCall] = field(default_factory=list["ModelCall"])

    async def sample(self, call: ModelCall, on_delta: Any) -> SampledTurn:  # noqa: ANN401
        self.sent.append(call)
        if self.wrap.armed:
            self.wrap.armed, self.wrap.is_held = False, False
            self.wrap.drawn += 1
            if self.fail_wrap_up:
                raise ModelFailed("server_error")
            return await self.port.sample(call, on_delta)
        if len([c for c in self.sent if c.tool_choice is None]) > self.allow:
            raise ModelFailed("budget_exhausted", budget_scope=self.scope)
        return await self.port.sample(call, on_delta)

    def restore(self, raw: bytes, *, provider: str, transport: str) -> Any:  # noqa: ANN401
        return self.port.restore(raw, provider=provider, transport=transport)


def _lookup_world(chars: int = 200) -> World:
    world = World()

    async def lookup(args: Any, ctx: Any) -> ToolExecution:  # noqa: ANN401
        return ToolExecution(status="ok", content={"text": f"{args['q']}:" + "x" * chars})

    world.tools.add("lookup", lookup, tags=[("category", "read")])
    return world


def _final(world: World) -> dict[str, Any]:
    return dict(world.events("run.completed")[0].data)


async def _answer(world: World) -> str:
    final = [e for e in world.events("message.completed") if e.data.get("isFinal")]
    return (await world.blob(cast("dict[str, Any]", final[-1].data["textRef"]))).decode()


def _slots(world: World) -> dict[str, Any]:
    saved: dict[str, Any] = {}
    for event in world.events("loop.state_saved"):
        saved.update(cast("dict[str, Any]", event.data["slots"]))
    return saved


async def test_budget_crossing_mid_task_ends_on_an_answer_only_turn_drawn_from_the_reserve() -> None:
    world = _lookup_world()
    world.admit(text="what is the travel cap", max_turns=8)
    model, port = scripted(reply("", ("lookup", {"q": "a"})), reply("", ("lookup", {"q": "b"})), reply(ANSWER))
    wrap = FakeWrapUp()
    ledgered = Ledgered(port, wrap, allow=2)
    done = await world.run(world.kernel(ledgered, wrap_up=wrap))  # type: ignore[arg-type]
    assert done.status == "completed"
    assert _final(world) == {"outcome": "answered", "truncatedBy": "budget"}
    assert await _answer(world) == ANSWER
    assert model.remaining == 0
    assert wrap.holds >= 2 and wrap.drawn == 1, "held before the working calls, drawn by the answer-only turn"
    last = ledgered.sent[-1]
    assert last.tool_choice == "none" and last.max_output_tokens == wrap.size.output_tokens
    newest = last.messages[-1]
    assert isinstance(newest, ToolMessage) and newest.step_footer.endswith(cut_short_note("budget"))
    assert _slots(world)[CUT_SHORT_SLOT] == "budget" and _slots(world)[FINAL_ANSWER_SLOT] == 3


class _Endings:
    """A presenter that keeps how the run ended and journals nothing of its own."""

    def __init__(self) -> None:
        self.endings: list[Any] = []

    def pre_run(self, slots: Any) -> list[Any]:  # noqa: ANN401
        return []

    def response(self, message: Any) -> list[Any]:  # noqa: ANN401
        return []

    async def finalize(self, ending: Any) -> list[Any]:  # noqa: ANN401
        self.endings.append(ending)
        return []


async def test_wp51_a_shared_budget_stop_names_the_budget_that_ran_out() -> None:
    """C9.06 follow-up: the chat's stop line names the budget (the org's, the person's own, the agent's)."""
    for scope in ("pool-a", "p", None):
        world = _lookup_world()
        world.admit(text="what is the travel cap", max_turns=8)
        _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("", ("lookup", {"q": "b"})), reply(ANSWER))
        wrap = FakeWrapUp()
        presenter = _Endings()
        done = await world.run(
            world.kernel(Ledgered(port, wrap, allow=2, scope=scope), wrap_up=wrap, presenter=presenter)  # type: ignore[arg-type]
        )
        assert done.status == "completed"
        assert _final(world) == {"outcome": "answered", "truncatedBy": "budget"}
        ending = presenter.endings[-1]
        assert (ending.truncated_by, ending.budget_scope) == ("budget", scope)


async def test_a_compaction_the_budget_cannot_pay_for_wraps_up_instead_of_failing() -> None:
    """That run's last step: the ladder's compaction was refused for budget, and the run failed with nothing."""
    world = _lookup_world(3_000)
    world.admit(text="what is the travel cap", max_turns=12)
    _, port = scripted(
        looked_up(300, "q0"), looked_up(300, "q1"), looked_up(300, "q2"), looked_up(int(WINDOW * 0.9), "q3"),
        reply(ANSWER),
    )

    class Unaffordable:
        asked = 0

        async def compact(self, request: CompactionRequest) -> CompactionSummary:
            Unaffordable.asked += 1
            raise BudgetExhausted("the run's tree has spent its token budget")

    wrap = FakeWrapUp()
    ledgered = Ledgered(port, wrap)
    done = await world.run(
        world.kernel(ledgered, wrap_up=wrap, compactor=compiler(strategy=Unaffordable()), auto_compact=True)  # type: ignore[arg-type]
    )
    assert done.status == "completed"
    assert Unaffordable.asked == 1, "asked once, never again for the answer-only turn"
    assert world.events("context.compacted") == []
    assert _final(world) == {"outcome": "answered", "truncatedBy": "budget"}
    assert ledgered.sent[-1].tool_choice == "none" and wrap.drawn == 1
    assert await _answer(world) == ANSWER


async def test_the_working_time_running_out_takes_the_answer_only_turn_before_the_deadline() -> None:
    world = _lookup_world()
    deadline = (world.clock.now() + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pinned = cast("dict[str, Any]", dump_contract(minimal_run_spec(max_turns=8)))
    pinned["budgets"]["calendarDeadline"] = deadline
    world.admit(text="what is the travel cap", spec=validate_contract(RunSpec, pinned))
    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply(ANSWER))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert _final(world) == {"outcome": "answered", "truncatedBy": "time"}
    last = port.calls[-1]
    assert last.tool_choice == "none"
    newest = last.messages[-1]
    assert isinstance(newest, ToolMessage) and newest.step_footer.endswith(cut_short_note("time"))


async def test_with_no_reserve_held_the_run_answers_without_a_model_listing_what_it_gathered() -> None:
    world = _lookup_world()
    world.admit(text="what is the travel cap", max_turns=8)
    model, port = scripted(reply("", ("lookup", {"q": "a"})), reply("", ("lookup", {"q": "b"})), reply("never"))
    wrap = FakeWrapUp(can_hold=False)
    ledgered = Ledgered(port, wrap, allow=2)
    done = await world.run(world.kernel(ledgered, wrap_up=wrap))  # type: ignore[arg-type]
    assert done.status == "completed"
    assert model.remaining == 1, "no model call was made for the answer"
    assert _final(world) == {"outcome": "answered", "truncatedBy": "budget"}
    answer = await _answer(world)
    assert answer.startswith("This reply stopped early because it reached its spending limit")
    assert "- Lookup (2 calls)" in answer and '"continue"' in answer


async def test_an_answer_only_turn_that_fails_falls_back_to_the_answer_without_a_model() -> None:
    world = _lookup_world()
    world.admit(text="what is the travel cap", max_turns=8)
    _, port = scripted(reply("Checking the policy first, then the limits.", ("lookup", {"q": "a"})), reply("never"))
    wrap = FakeWrapUp()
    ledgered = Ledgered(port, wrap, allow=1, fail_wrap_up=True)
    done = await world.run(world.kernel(ledgered, wrap_up=wrap))  # type: ignore[arg-type]
    assert done.status == "completed"
    assert _final(world) == {"outcome": "answered", "truncatedBy": "budget"}
    answer = await _answer(world)
    # A budget stop never passes the last narration off as the answer.
    assert answer.startswith("This reply stopped early") and "Checking the policy" not in answer


async def test_the_answer_only_turn_is_cut_to_the_reserve_and_the_cut_is_journaled() -> None:
    world = _lookup_world(chars=30_000)
    world.admit(text="what is the travel cap", max_turns=12)
    _, port = scripted(*(reply("", ("lookup", {"q": f"q{i}"})) for i in range(4)), reply(ANSWER))
    wrap = FakeWrapUp()
    ledgered = Ledgered(port, wrap, allow=4)
    done = await world.run(world.kernel(ledgered, wrap_up=wrap))  # type: ignore[arg-type]
    assert done.status == "completed"
    last = ledgered.sent[-1]
    assert count_tokens(last.messages) <= wrap.size.input_tokens
    assert count_tokens(ledgered.sent[-2].messages) > 4 * wrap.size.input_tokens, "the working requests were larger"
    fit = _slots(world)[WRAP_UP_FIT_SLOT]
    assert fit["messagesBefore"] >= fit["messagesAfter"]
    assert any("travel cap" in str(m.content) for m in last.messages), "the person's request is never cut"


async def test_a_child_cut_short_tells_its_parent_in_its_result() -> None:
    document = child_result_document(outcome="answered", output="partial findings", truncated_by="budget")
    assert document["truncatedBy"] == "budget"
    assert "spending limit" in str(document["stopped_early"])
    assert "truncatedBy" not in child_result_document(outcome="answered", output="all done")


async def test_a_worker_lost_after_the_answer_only_response_completes_it_with_the_same_flag() -> None:
    """The reason rides with the answer-only turn's response (``CUT_SHORT_SLOT``): a new attempt that finds the
    response completes the run with the same flag and takes no second turn."""
    world = _lookup_world()
    world.admit(text="what is the travel cap", max_turns=8)
    model, port = scripted(reply("", ("lookup", {"q": "a"})), reply(ANSWER), reply("never"))
    wrap = FakeWrapUp()
    with armed({"worker.model.after_response_commit": Arm(Raise(Crash), Nth(2))}):
        await world.crash(world.kernel(Ledgered(port, wrap, allow=1), wrap_up=wrap))  # type: ignore[arg-type]
    again = FakeWrapUp()
    done = await world.run(world.kernel(Ledgered(port, again, allow=0), wrap_up=again))  # type: ignore[arg-type]
    assert done.status == "completed" and model.remaining == 1
    assert _final(world) == {"outcome": "answered", "truncatedBy": "budget"}
    assert await _answer(world) == ANSWER


async def test_wp56_stop_2_a_resumed_attempt_still_names_the_shared_budget_that_ran_out() -> None:
    """WP56-STOP-2: which shared budget refused the call is journaled with the cut (``BUDGET_SCOPE_SLOT``), so an
    attempt that resumes after the answer-only response names it as the first attempt would have."""
    world = _lookup_world()
    world.admit(text="what is the travel cap", max_turns=8)
    model, port = scripted(reply("", ("lookup", {"q": "a"})), reply(ANSWER), reply("never"))
    wrap = FakeWrapUp()
    with armed({"worker.model.after_response_commit": Arm(Raise(Crash), Nth(2))}):
        await world.crash(world.kernel(Ledgered(port, wrap, allow=1, scope="pool-b"), wrap_up=wrap))  # type: ignore[arg-type]
    again = FakeWrapUp()
    presenter = _Endings()
    done = await world.run(
        world.kernel(Ledgered(port, again, allow=0), wrap_up=again, presenter=presenter)  # type: ignore[arg-type]
    )
    assert done.status == "completed" and model.remaining == 1
    assert _final(world) == {"outcome": "answered", "truncatedBy": "budget"}
    ending = presenter.endings[-1]
    assert (ending.truncated_by, ending.budget_scope) == ("budget", "pool-b")
