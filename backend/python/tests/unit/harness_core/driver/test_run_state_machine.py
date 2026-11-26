"""QA13-1 (WRK-31): ``RunStateMachine``, the run's states under every operation a deployment applies.

The real ``DurableKernel`` works on ``InMemoryJournal`` (the durable test world, which enforces what
Node enforces) while Hypothesis interleaves what happens around it: a worker crashing at any failpoint,
the lease lapsing, the sweeper, a person resolving or cancelling, steering, a stale attempt's late
commit and renew, a retried commit, a claim of a run that is not queued.

After every step: the journal is a valid history (H1..H6); an ended run never changes again; the claims'
fences rise one by one; a stale token writes nothing; no effect reaches the SaaS twice; and the fold of
the journal agrees with the journal's own state (status, fence, open interactions).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections import Counter
from typing import TYPE_CHECKING, override

import pytest
from hypothesis import HealthCheck, seed, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    get_state_machine_test,  # pyright: ignore[reportUnknownVariableType]  # a seedable run_state_machine_as_test
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,  # pyright: ignore[reportUnknownVariableType]  # its signature is partly untyped
)

from harness_core.failpoints import Arm, Nth, Raise
from harness_core.journal import (
    AttemptToken,
    CommitRequest,
    InteractionNotOpen,
    OwnershipLost,
    RunNotClaimable,
)
from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.kernel.fold import fold_run
from harness_core.testing import (
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
    armed,
)
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.history import assert_journal_history
from tests.unit.harness_core.durable.support import RUN, SESSION, Crash, World

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.contracts.generated.enums import InteractionDecision
    from harness_core.journal import CommitReceipt
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable import ToolEffect
    from harness_core.testing import ModelRequest

CRASH_POINTS = [
    "worker.model.before_response_commit",
    "worker.model.after_response_commit",
    "worker.tool.after_intent",
    "worker.tool.after_effect",
    "worker.tool.before_outcome_commit",
    "worker.batch.before_suspend",
    "worker.commit.after_ack",
]
TOOLS = ["lookup", "create_issue", "post_message", "flaky_write"]
MAX_ATTEMPTS = 3
# The journal's own status for each status the fold can say. A lapsed lease re-queues without an event.
JOURNAL_STATUSES = {
    "queued": {"queued"},
    "running": {"running", "queued"},
    "suspended": {"suspended"},
}


def _ask_to_post(call: ToolCall, effect: ToolEffect) -> str:
    return "ask" if call.name == "post_message" else "allow"


class _Recorder:
    """The journal's commits as the worker sent them, for a retried dispatch."""

    def __init__(self, world: World) -> None:
        self.requests: list[CommitRequest] = []
        commit = world.journal.commit

        async def recording(request: CommitRequest) -> CommitReceipt:
            receipt = await commit(request)
            self.requests.append(request)
            return receipt

        world.journal.commit = recording  # type: ignore[method-assign]


class RunStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.world = World()
        self.world.admit(max_turns=6)
        self.loop = asyncio.new_event_loop()
        self.plan: list[Reply] = []
        self.recorder = _Recorder(self.world)
        self.model = ScriptedModelPort(
            ScriptedModel(ModelScript(id="wrk31", turns=[Turn(from_request=self._reply) for _ in range(400)]))
        )
        self.ended: tuple[str, int] | None = None

    # ---- the model's plan ----

    def _reply(self, request: ModelRequest) -> Reply:
        turn = sum(1 for m in request.messages if m.role == "assistant")
        if turn < len(self.plan):
            return self.plan[turn]
        return Reply(text="done", chunks=2)

    @initialize(turns=st.lists(st.lists(st.sampled_from(TOOLS), min_size=1, max_size=3), max_size=3))
    def plan_turns(self, turns: list[list[str]]) -> None:
        for calls in turns:
            self.model_turn(calls)

    @rule(calls=st.lists(st.sampled_from(TOOLS), min_size=0, max_size=3))
    def model_turn(self, calls: list[str]) -> None:
        """What the model answers on its next unplanned turn: tool calls (stable ids), or text."""
        turn = len(self.plan) + 1
        specs = [ToolCallSpec(id=f"call_t{turn}_{i}", name=name, args={"n": i}) for i, name in enumerate(calls)]
        self.plan.append(Reply(text="" if specs else "answer", tool_calls=specs, chunks=None if specs else 2))

    # ---- the worker ----

    @property
    def status(self) -> str:
        return self.world.journal.status(RUN)

    def _events(self) -> int:
        return len(self.world.journal.events(SESSION))

    def _tokens(self) -> list[AttemptToken]:
        return [
            AttemptToken(RUN, str(e.attempt_id), int(str(e.data["fence"])))
            for e in self.world.journal.events(SESSION)
            if e.type == "run.claimed"
        ]

    @precondition(lambda self: self.status == "queued")
    @rule(point=st.sampled_from([*CRASH_POINTS, None]), nth=st.integers(1, 3))
    def drive(self, point: str | None, nth: int) -> None:
        kernel = self.world.kernel(self.model, approval=_ask_to_post, journal_retry_s=0, max_attempts=MAX_ATTEMPTS)  # type: ignore[arg-type]
        arms = {} if point is None else {point: Arm(Raise(Crash), Nth(nth))}
        with armed(arms), contextlib.suppress(Crash):
            _ = self.loop.run_until_complete(self.world.run(kernel))

    @precondition(lambda self: self.status == "queued")
    @rule(point=st.sampled_from(CRASH_POINTS))
    def crash_loop(self, point: str) -> None:
        """Workers dying at the same point until the attempts cap quarantines the run (or it ends)."""
        for _ in range(MAX_ATTEMPTS + 1):
            if self.status != "queued":
                return
            self.drive(point, 1)
            self.lease_expire()
            self.sweep()

    @rule()
    def lease_expire(self) -> None:
        _ = self.world.db_clock.advance(31)

    @rule()
    def sweep(self) -> None:
        _ = self.world.journal.sweep()

    # ---- people ----

    @precondition(lambda self: bool(self.world.journal.open_interactions(RUN)))
    @rule(approve=st.booleans())
    def resolve(self, approve: bool) -> None:
        """A person in the conversation. On an unknown effect they may say it happened or deny it: approving its
        re-call is the operator console's decision, which Node's session route refuses (``OPERATOR_REQUIRED``)."""
        interaction = sorted(self.world.journal.open_interactions(RUN))[0]
        if interaction.startswith("ix.unknown."):
            self._decide(interaction, "answered" if approve else "denied")
        else:
            self._decide(interaction, "approved" if approve else "denied")

    @precondition(lambda self: any(ix.startswith("ix.unknown.") for ix in self.world.journal.open_interactions(RUN)))
    @rule(abandon=st.booleans())
    def operator_resolves(self, abandon: bool) -> None:
        """The operator console (P2.9) on an unknown effect, answering from what the SaaS holds: applied
        (``answered``), not applied (``approved``: one re-call), or abandon (``cancelled``). An operator who says
        "not applied" of an effect the SaaS holds asks for the second send (WRK-31's counterexample)."""
        interaction = sorted(ix for ix in self.world.journal.open_interactions(RUN) if ix.startswith("ix.unknown."))[0]
        invocation = interaction.removeprefix("ix.unknown.")
        applied = any(str(e.payload.get("invocation")) == invocation for e in self.world.saas.effects)
        self._decide(interaction, "cancelled" if abandon else "answered" if applied else "approved")

    def _decide(self, interaction: str, decision: InteractionDecision) -> None:
        try:
            self.world.journal.resolve_interaction(RUN, interaction, decision)
        except InteractionNotOpen:
            assert self.world.journal.status(RUN) in TERMINAL_STATUSES or self._cancel_requested()

    @rule()
    def resolve_what_is_not_open(self) -> None:
        before = self._events()
        with pytest.raises(InteractionNotOpen):
            self.world.journal.resolve_interaction(RUN, "ix.approve.t9.9", "approved")
        assert self._events() == before

    @precondition(lambda self: len(self._tokens()) >= 1)
    @rule()
    def cancel(self) -> None:
        before = self._events()
        ended = self.status in TERMINAL_STATUSES
        self.world.journal.request_cancel(RUN)
        if ended:
            assert self._events() == before

    @rule(text=st.sampled_from(["also this", "and that"]))
    def steer(self, text: str) -> None:
        before = self._events()
        if self.status in TERMINAL_STATUSES or self._cancel_requested():
            with pytest.raises(RunNotClaimable):
                _ = self.world.journal.queue_input(RUN, text)
            assert self._events() == before
        else:
            _ = self.world.journal.queue_input(RUN, text)

    def _cancel_requested(self) -> bool:
        return any(e.type == "run.cancel_requested" for e in self.world.journal.events(SESSION))

    # ---- what a deployment must refuse ----

    @precondition(lambda self: len(self._tokens()) >= 1)
    @rule(data=st.data())
    def stale_commit(self, data: st.DataObject) -> None:
        tokens = self._tokens()
        current = tokens[-1]
        stale = data.draw(st.sampled_from(tokens[:-1])) if len(tokens) > 1 else current
        owner = self.status == "running" and stale == current
        if owner:
            return
        before = self._events()
        request = CommitRequest(f"stale-{stale.fence}-{before}", stale)
        with pytest.raises(OwnershipLost):
            _ = self.loop.run_until_complete(self.world.journal.commit(request))
        with pytest.raises(OwnershipLost):
            _ = self.loop.run_until_complete(self.world.journal.renew(stale))
        assert self._events() == before

    @precondition(lambda self: bool(self.recorder.requests))
    @rule(data=st.data())
    def duplicate_dispatch(self, data: st.DataObject) -> None:
        """A commit the worker retries (a lost ack) replays its receipt and writes nothing."""
        request = data.draw(st.sampled_from(self.recorder.requests))
        before = self._events()
        receipt = self.loop.run_until_complete(self.world.journal.commit(request))
        assert receipt.replayed
        assert self._events() == before

    @precondition(lambda self: self.status != "queued")
    @rule()
    def claim_what_is_not_queued(self) -> None:
        before = self._events()
        with pytest.raises(RunNotClaimable):
            _ = self.loop.run_until_complete(self.world.journal.claim(RUN, worker_id="w2"))
        assert self._events() == before

    # ---- invariants ----

    @invariant()
    def the_journal_is_a_valid_history(self) -> None:
        assert_journal_history(self.world.journal.events(SESSION))

    @invariant()
    def an_ended_run_never_changes(self) -> None:
        if self.ended is not None:
            assert (self.status, self._events()) == self.ended
        elif self.status in TERMINAL_STATUSES:
            self.ended = (self.status, self._events())

    @invariant()
    def fences_rise_one_claim_at_a_time(self) -> None:
        fences = [t.fence for t in self._tokens()]
        assert fences == list(range(1, len(fences) + 1))

    @invariant()
    def no_effect_reaches_the_saas_twice(self) -> None:
        keys = Counter(e.idempotency_key for e in self.world.saas.effects if e.idempotency_key)
        assert all(n == 1 for n in keys.values()), keys
        posts = Counter(str(e.payload["invocation"]) for e in self.world.saas.effects_for("post_message"))
        assert all(n == 1 for n in posts.values()), posts

    @invariant()
    def the_fold_agrees_with_the_journal(self) -> None:
        state = fold_run(self.world.journal.events(SESSION), run_id=RUN)
        tokens = self._tokens()
        assert state.fence == (tokens[-1].fence if tokens else 0)
        journal = self.status
        if state.status in TERMINAL_STATUSES:
            assert journal == state.status
            return
        assert journal in JOURNAL_STATUSES[state.status], (state.status, journal)
        open_in_fold = {ix for ix, record in state.interactions.items() if record.decision is None}
        assert open_in_fold == set(self.world.journal.open_interactions(RUN))

    @override
    def teardown(self) -> None:
        self.loop.close()


_HEALTH = [HealthCheck.too_slow, HealthCheck.filter_too_much]


@pytest.mark.timeout(600)
def test_wrk31_the_run_state_machine_keeps_its_invariants() -> None:
    run_state_machine_as_test(RunStateMachine, settings=settings(stateful_step_count=25, suppress_health_check=_HEALTH))


# The CI profile is derandomized, so it replays one set of examples; these seeds search at random within a fixed
# budget, the way REL1 found WRK-31. ``WRK31_SEARCH_EXAMPLES`` widens it for a local soak.
SEARCH_SEEDS = (1, 2, 3, 5, 8, 13)
SEARCH_EXAMPLES = int(os.environ.get("WRK31_SEARCH_EXAMPLES", "100"))


@pytest.mark.timeout(900)
@pytest.mark.parametrize("search_seed", SEARCH_SEEDS)
def test_wrk31_a_seeded_random_search_finds_no_counterexample(search_seed: int) -> None:
    search = get_state_machine_test(
        RunStateMachine,
        settings=settings(
            derandomize=False,
            database=None,
            deadline=None,
            max_examples=SEARCH_EXAMPLES,
            stateful_step_count=25,
            suppress_health_check=_HEALTH,
        ),
    )
    seed(search_seed)(search)()


def _invariants_hold(machine: RunStateMachine) -> None:
    for check in (
        machine.the_journal_is_a_valid_history,
        machine.an_ended_run_never_changes,
        machine.fences_rise_one_claim_at_a_time,
        machine.no_effect_reaches_the_saas_twice,
        machine.the_fold_agrees_with_the_journal,
    ):
        check()


@pytest.mark.parametrize(
    ("lost_at", "decide"),
    [
        # REL1-F1's counterexample: the person's "approve" on the unknown effect is its "it happened".
        ("worker.tool.after_effect", lambda m: m.resolve(approve=True)),
        ("worker.tool.after_effect", lambda m: m.operator_resolves(abandon=False)),
        # Not sent before the crash: the operator's "not applied" re-calls it, once.
        ("worker.tool.after_intent", lambda m: m.operator_resolves(abandon=False)),
    ],
    ids=["person", "operator-applied", "operator-not-applied"],
)
def test_wrk31_a_post_whose_outcome_was_lost_reaches_the_saas_once(
    lost_at: str, decide: Callable[[RunStateMachine], None]
) -> None:
    machine = RunStateMachine()
    try:
        machine.plan_turns(turns=[["post_message"]])
        for step in (
            lambda: machine.crash_loop(point="worker.model.after_response_commit"),
            lambda: machine.resolve(approve=True),
            lambda: machine.crash_loop(point=lost_at),
            lambda: decide(machine),
            lambda: machine.crash_loop(point="worker.model.before_response_commit"),
        ):
            step()
            _invariants_hold(machine)
        assert len(machine.world.saas.effects_for("post_message")) == 1
    finally:
        machine.teardown()
