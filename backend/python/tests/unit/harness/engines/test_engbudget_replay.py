"""ENGBUDGET, scripted and durable: a recorded hand-off that ended on "This reply stopped early ... before an answer
could be written." replayed on the real harness.

In a manual test, the parent loop searched Jira for six turns, DEEPROUTE handed the rest to a Codex child, and the child
made 22 Jira calls in 20 model calls, each resending its growing, mostly cached history. The ledger charged each call
its whole prompt (773,807 tokens for USD 0.019), refused the 21st ``BUDGET_INSUFFICIENT`` on the 1M token cap with USD
4.95 of 5 left, the engine ended with nothing written, and the parent passed the no-model fallback through as the whole
answer.

The rig is DEEPROUTE's (``test_deeproute``): the real ``PipesHubRuntimeAssembler``, ``RunDriver`` and durable kernel
over the in-memory journal, the ``code_task`` child on the engine kernel. Here the child's model calls are charged to
an in-memory ledger tree exactly as the model gateway charges them (``reservation_for`` before, ``usage_spend``
after), with the recorded per-call usage, and a refusal stops the engine as the gateway's does.

* the fix as shipped: the child's calls fit, the engine writes its answer, the run ends with it within budget;
* the token cap counting cached reads in full (the recorded ledger): the engine is stopped, and the child's wrap-up
  answers from the Jira results it gathered, on its reserve; the run ends with that answer, flagged as cut short;
* the same with no wrap-up to answer on: the parent takes the turn back and answers from its own searches, flagged.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.spend import OutputAllowance, reservation_for, usage_spend
from harness_core.budget.errors import BudgetRefused
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import Amount, Cap, PinnedBudget, RunAccountRef
from harness_core.budget.wrap_up import WrapUpReserve, WrapUpSize
from harness_core.engines.durable.wrap_up import ModelEngineWrapUp
from harness_core.engines.events import (
    EngineError,
    EngineEventMeta,
    TurnCompleted,
    UsageReported,
)
from harness_core.engines.profiles import profile_json
from harness_core.kernel.core.messages import AssistantMessage, TextPart
from harness_core.kernel.core.responses import TokenUsage
from harness_core.kernel.core.turn_limit import is_cut_short_fallback
from harness_core.kernel.durable.ports import SampledTurn
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.durable import (
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
)
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport, reply_events
from tests.support.longtask_rig.deeproute import (
    ANSWER,
    NARROW,
    PARENT,
    RecordedModel,
    Rig,
    _code_task_result,
    deployment,
)
from tests.unit.harness.assembly import world as w
from tests.unit.harness_core.budget.test_engbudget import RECORDED_CHILD_CALLS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import EngineTask, FollowUp
    from harness_core.journal.types import Claim
    from harness_core.kernel.durable.ports import DeltaKind, ModelCall

MODEL = "gpt-6-luna"
CAP = Cap(5_000_000, 1_000_000)
ROOT = RunAccountRef(w.ORG, PARENT)
# The recorded parent: 169k tokens committed, and BUDGETEND's wrap-up reserve held (6,400 micro-USD, 32,000 tokens).
PARENT_SPEND = Amount(28_000, 169_000)
PARENT_RESERVE = Amount(6_400, 32_000)
# The child's brief: 22 Jira searches (the recorded count), then its answer.
SEARCHES = 22
WRAP_UP_ANSWER = "Pick up PP-7 first: it is the highest-priority open ticket the searches returned."
TAKEN_BACK = "From the PP searches so far: PR-page-1 leads the list."


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    """The deployment the rig's parent runs in."""
    with deployment():
        yield


def _call(step: int) -> tuple[int, int, int]:
    """The recorded call ``step`` (0-based); past its 20th, the prompt keeps growing by a search result a step."""
    if step < len(RECORDED_CHILD_CALLS):
        return RECORDED_CHILD_CALLS[step]
    prompt, cached, output = RECORDED_CHILD_CALLS[-1]
    grow = 2_500 * (step - len(RECORDED_CHILD_CALLS) + 1)
    return prompt + grow, cached + grow - 400, output


def _full_weight(usage: TokenUsage) -> Amount:
    """The recorded ledger: every prompt token, cached or not, at full weight on the token cap."""
    spend = usage_spend(usage, MODEL, charges_usd=True)
    return Amount(spend.usd_micros, usage.input_tokens + usage.output_tokens)


def _as_shipped(usage: TokenUsage) -> Amount:
    return usage_spend(usage, MODEL, charges_usd=True)


@dataclass
class Tree:
    """The reply's ledger tree: the parent's account (recorded spend and reserve) and the code_task child's under it."""

    weigh: Callable[[TokenUsage], Amount]
    ledger: InMemoryBudgetLedger = field(default_factory=lambda: InMemoryBudgetLedger(ManualClock()))
    child: RunAccountRef | None = None
    calls: int = 0
    refused: int = 0
    wrap_up_transport: RecordedTransport = field(default_factory=RecordedTransport)

    async def open(self) -> None:
        await self.ledger.open_root(ROOT, CAP)
        held = await self.ledger.reserve(ROOT, "parent", PARENT_SPEND, minimum=PARENT_SPEND, ttl_s=3_600)
        _ = await self.ledger.commit(held, PARENT_SPEND)
        _ = await self.ledger.reserve(ROOT, "parent-wrapup", PARENT_RESERVE, minimum=PARENT_RESERVE, ttl_s=3_600)

    def adopt(self, claim: Claim) -> RunAccountRef:
        self.child = RunAccountRef(claim.org_id, claim.run_id)
        self.ledger.pin(self.child, PinnedBudget(Cap(None, None), parent=ROOT))
        return self.child

    async def charge(self) -> bool:
        """The engine's next model call, as the gateway charges it; ``False`` when the ledger refuses it."""
        assert self.child is not None
        prompt, cached, output = _call(self.calls)
        requested, minimum = reservation_for(prompt, MODEL, OutputAllowance(), charges_usd=True)
        if self.weigh is _full_weight:
            minimum = Amount(minimum.usd_micros, prompt)
        try:
            held = await self.ledger.reserve(self.child, f"call-{self.calls}", requested, minimum=minimum, ttl_s=60)
        except BudgetRefused:
            self.refused += 1
            return False
        usage = TokenUsage(input_tokens=prompt, output_tokens=output, cache_read_tokens=cached)
        _ = await self.ledger.commit(held, self.weigh(usage))
        self.calls += 1
        return True

    async def committed(self) -> Amount:
        return (await self.ledger.remaining(ROOT)).committed

    def unmetered(self, claim: Claim) -> None:
        """No wrap-up for the child (a run the ledger does not meter): its account is still charged by the gateway."""
        _ = self.adopt(claim)

    def wrap_up(self, claim: Claim) -> ModelEngineWrapUp:
        account = self.adopt(claim)
        self.wrap_up_transport.events = [
            reply_events(WRAP_UP_ANSWER, usage=TokenUsage(input_tokens=6_000, output_tokens=120))
        ]
        reserve = WrapUpReserve(
            accounts=self.ledger, account=account, pricing_model=MODEL, ids=SeededIds(7), size=WrapUpSize(24_000, 8_000)
        )
        metered = MeteredTransport(
            self.wrap_up_transport, accounts=self.ledger, account=account, pricing_model=MODEL, ids=SeededIds(8),
            wrap_up=reserve,
        )
        return ModelEngineWrapUp(reserve=reserve, transport=metered)


class MeteredEngine(ScriptedEngineAdapter):
    """The scripted engine, its model calls charged to ``tree`` as the gateway charges them: a refused call ends the
    turn ``failed`` with ``budget_insufficient`` and nothing written, as Codex's did in the recorded run."""

    def __init__(self, tree: Tree) -> None:
        super().__init__(max_steps=60)
        self.tree = tree

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN201
        return _Metered(await super().open(sandbox, launch, restore, correlation=correlation), self.tree)


class _Metered:
    def __init__(self, inner: object, tree: Tree) -> None:
        self._inner = inner
        self._tree = tree

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        seq = 0
        async for event in self._inner.run_turn(task):  # type: ignore[attr-defined]
            seq = event.meta.seq
            if isinstance(event, UsageReported) and not await self._tree.charge():
                yield EngineError(EngineEventMeta(task.turn_id, seq + 1), "budget_insufficient", "the gateway refused")
                yield TurnCompleted(EngineEventMeta(task.turn_id, seq + 2), "failed")
                return
            yield event


@dataclass
class RecordedJiraModel(RecordedModel):
    """Recorded parent: hands off at turn six with a brief of Jira searches. Given the turn back after an engine that
    wrote nothing, it answers from its own searches."""

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        result = _code_task_result(call)
        if result is not None and is_cut_short_fallback(str(result.get("output") or "")):
            await on_delta("text_delta", TAKEN_BACK)
            usage = TokenUsage(input_tokens=20_000, output_tokens=300)
            return SampledTurn(message=AssistantMessage(content=[TextPart(text=TAKEN_BACK)]), usage=usage)
        sampled = await super().sample(call, on_delta)
        calls = sampled.message.tool_calls or []
        if any(c.name == "code_task" for c in calls):
            searches = "\n".join(f'tool lookup {{"jql": "project = PP page {n}"}}' for n in range(SEARCHES))
            goal = f"Rank the PP tickets to pick up first.\n{searches}\nsay {ANSWER}"
            calls = [c.model_copy(update={"arguments": {"goal": goal}}) if c.name == "code_task" else c for c in calls]
            sampled = dataclasses.replace(sampled, message=sampled.message.model_copy(update={"tool_calls": calls}))
        return sampled


async def _replay(weigh: Callable[[TokenUsage], Amount], *, wrap_up: bool) -> tuple[Rig, Tree, Any]:
    tree = Tree(weigh)
    await tree.open()
    world = EngineWorld(
        engine_authority=profile_json(engine_profile(max_steps=60)),
        adapter=MeteredEngine(tree),
        wrap_up=tree.wrap_up if wrap_up else tree.unmetered,
    )
    rig = Rig(RecordedJiraModel(), world=world)
    done = await rig.drive(NARROW)
    return rig, tree, done


def _answer(rig: Rig) -> str:
    answers = rig.parent("x.pipeshub.answer.finalized")
    assert answers, "the run has an answer"
    return str(answers[-1].data.get("answer"))


def _child_completed(rig: Rig) -> dict[str, Any]:
    (child,) = rig.world.journal.children_of(PARENT)
    (completed,) = [e for e in rig.events(child) if e.type == "run.completed"]
    return dict(completed.data)


async def test_engbudget_the_child_finishes_its_work_within_the_budget() -> None:
    rig, tree, done = await _replay(_as_shipped, wrap_up=True)
    assert done.status == "completed", done
    assert tree.refused == 0 and tree.calls >= len(RECORDED_CHILD_CALLS), (tree.calls, tree.refused)
    assert _child_completed(rig) == {"outcome": "answered"}
    assert ANSWER in _answer(rig)
    assert [e.data.get("truncatedBy") for e in rig.parent("run.completed")] == [None]
    spent = await tree.committed()
    assert spent.tokens < CAP.tokens // 2 and spent.usd_micros < CAP.usd_micros, spent  # type: ignore[operator]


async def test_engbudget_counted_in_full_the_childs_wrap_up_answers_from_its_results() -> None:
    rig, tree, done = await _replay(_full_weight, wrap_up=True)
    assert done.status == "completed", done
    # Recorded refusal, reproduced: counted in full, the token cap refuses the child's 21st call (its 20th now, with the
    # child's own wrap-up reserve held back too).
    assert tree.refused == 1 and len(RECORDED_CHILD_CALLS) - 1 <= tree.calls <= len(RECORDED_CHILD_CALLS), (tree.calls, tree.refused)
    assert _child_completed(rig) == {"outcome": "answered", "truncatedBy": "budget"}
    answer = _answer(rig)
    assert WRAP_UP_ANSWER in answer and not is_cut_short_fallback(answer)
    # The wrap-up read the Jira results the engine gathered, and was paid from the reserve held for it.
    (sent,) = tree.wrap_up_transport.calls
    assert "lookup" in "\n".join(str(m.content) for m in sent.messages)
    # The parent passes the engine's answer through, flagged as cut short.
    assert [e.data.get("truncatedBy") for e in rig.parent("run.completed")] == ["budget"]
    spent = await tree.committed()
    assert spent.usd_micros < CAP.usd_micros  # type: ignore[operator]
    assert (await tree.ledger.remaining(tree.child)).reserved == Amount(0, 0)  # type: ignore[arg-type]


async def test_engbudget_an_empty_cut_short_child_hands_the_turn_back_to_the_parent() -> None:
    rig, tree, done = await _replay(_full_weight, wrap_up=False)
    assert done.status == "completed", done
    assert tree.refused == 1
    completed = _child_completed(rig)
    assert completed == {"outcome": "answered", "truncatedBy": "budget"}
    answer = _answer(rig)
    # Before: the parent passed "This reply stopped early ... before an answer could be written." through.
    assert TAKEN_BACK in answer and not is_cut_short_fallback(answer)
    assert [e.data.get("truncatedBy") for e in rig.parent("run.completed")] == ["budget"]
