"""Contract suites for the durable kernel's turn and delegation ports (WRK-G4).

Case ids continue ``durable_ports``' numbering (``DP<port>.<n>``):

* DP10 ``TurnShaperContract`` (``shapers``: ``TurnHarness``)
* DP11 ``ResponseReviewContract`` (``reviews``: ``ReviewHarness``)
* DP13 ``ChildResultShaperContract`` (``child_result``)
* DP14 ``LoopDirectorContract`` (``directors``: ``DirectorHarness``)
* DP15 ``ContextHostContract`` (``hosts``: ``TurnHarness``)
* DP16 ``DelegationToolsContract`` (``delegations``: ``DelegationHarness``)
* DP17 ``ChildBudgetContract`` (``child_budget``)
* DP18 ``RoutePortsContract`` (``route_ports``: ``RoutePortsCase``)

Every one of them is asked again by a re-claimed attempt, a new process that has only the
journal: the promise they share is that the journal alone decides their answer.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

import pytest

from harness_core.journal.children import ChildRequest, child_result_document
from harness_core.kernel.core.messages import AssistantMessage, TextPart, ToolCall
from harness_core.kernel.durable.ports import TurnResult
from harness_core.models.errors import ModelRouteNotPinned
from harness_core.testing.contracts.durable_ports import OUTCOMES, journaled

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.kernel.durable.context_compiler import ContextHost
    from harness_core.kernel.durable.delegation import (
        ChildBudget,
        Delegated,
        DelegationTools,
        DelegationView,
    )
    from harness_core.kernel.durable.ports import (
        ChildResultShaper,
        LoopDirector,
        ModelCall,
        ResponseReview,
        RunEndingOutcome,
        TurnShaper,
    )
    from harness_core.kernel.durable.routing import RoutePorts
    from harness_core.kernel.fold import RunState
    from harness_core.models.policy import PolicyRoute

P = TypeVar("P", covariant=True)


class TurnHarness(Protocol, Generic[P]):
    def fresh(self) -> P:
        """A new attempt's instance (a re-claim builds the runtime again)."""
        ...

    def state(self) -> RunState:
        """The journaled state of a run its pre-run steps settled."""
        ...

    def call(self) -> ModelCall:
        """The request the kernel assembled for a turn of that run."""
        ...


# ---- DP10 TurnShaper ----


class TurnShaperContract:
    @pytest.fixture
    def shapers(self) -> TurnHarness[TurnShaper]:
        raise NotImplementedError("provide a shapers fixture")

    async def test_dp10_1_the_same_journal_shapes_the_same_request(self, shapers: TurnHarness[TurnShaper]) -> None:
        shaper, state, call = shapers.fresh(), shapers.state(), shapers.call()
        assert await shaper.shape(call, 1, state) == await shaper.shape(call, 1, state)

    async def test_dp10_2_a_resumed_attempt_sends_what_the_first_one_sent(
        self, shapers: TurnHarness[TurnShaper]
    ) -> None:
        state, call = shapers.state(), shapers.call()
        first = shapers.fresh()
        _ = await first.shape(call, 1, state)
        sent = await first.shape(call, 2, state)
        assert await shapers.fresh().shape(call, 2, state) == sent

    async def test_dp10_3_a_resume_at_dispatch_opens_the_turn_it_did_not_shape(
        self, shapers: TurnHarness[TurnShaper]
    ) -> None:
        state, call = shapers.state(), shapers.call()
        sent = await shapers.fresh().shape(call, 2, state)
        resumed = shapers.fresh()
        await resumed.enter(2, state)
        assert await resumed.shape(call, 2, state) == sent


# ---- DP11 ResponseReview ----


class ReviewHarness(Protocol):
    def fresh(self) -> ResponseReview: ...

    def state(self) -> RunState: ...


ANSWER = AssistantMessage(content=[TextPart(text="The deploy finished at 10:42.")])
EMPTY = AssistantMessage(content=[])
CUT = AssistantMessage(content=[TextPart(text="The deploy fin")], truncated=True)


class ResponseReviewContract:
    @pytest.fixture
    def reviews(self) -> ReviewHarness:
        raise NotImplementedError("provide a reviews fixture")

    async def test_dp11_1_a_complete_answer_is_the_answer(self, reviews: ReviewHarness) -> None:
        assert await reviews.fresh().review(ANSWER, 1, reviews.state()) is None

    @pytest.mark.parametrize("message", [EMPTY, CUT], ids=["empty", "truncated"])
    async def test_dp11_2_a_re_claimed_attempt_reviews_a_response_as_the_first_did(
        self, reviews: ReviewHarness, message: AssistantMessage
    ) -> None:
        state = reviews.state()
        assert await reviews.fresh().review(message, 1, state) == await reviews.fresh().review(message, 1, state)

    @pytest.mark.parametrize("message", [EMPTY, CUT], ids=["empty", "truncated"])
    async def test_dp11_3_a_recovery_is_a_message_the_model_can_read(
        self, reviews: ReviewHarness, message: AssistantMessage
    ) -> None:
        recovery = await reviews.fresh().review(message, 1, reviews.state())
        assert recovery is None or recovery.strip()


# ---- DP13 ChildResultShaper ----


def _child_document(outcome: RunEndingOutcome) -> dict[str, JsonValue]:
    if outcome in ("answered", "needs_input"):
        return child_result_document(outcome=outcome, output="Found 3 open tickets.")  # type: ignore[arg-type]
    return {"output": "", "success": False, "error": "model_error" if outcome == "error" else "stopped"}


class ChildResultShaperContract:
    @pytest.fixture
    def child_result(self) -> ChildResultShaper:
        raise NotImplementedError("provide a child_result fixture")

    @pytest.mark.parametrize("outcome", OUTCOMES)
    async def test_dp13_1_the_parent_reads_the_childs_success_from_the_journal(
        self, child_result: ChildResultShaper, outcome: RunEndingOutcome
    ) -> None:
        document = _child_document(outcome)
        shaped = await child_result.shape(document, outcome)
        assert shaped.get("success") == document["success"]
        assert journaled(shaped) == shaped

    async def test_dp13_2_the_kernels_document_is_left_as_it_was(self, child_result: ChildResultShaper) -> None:
        document = _child_document("answered")
        kept = journaled(document)
        _ = await child_result.shape(document, "answered")
        assert document == kept

    @pytest.mark.parametrize("outcome", OUTCOMES)
    async def test_dp13_3_a_child_completed_again_completes_the_same(
        self, child_result: ChildResultShaper, outcome: RunEndingOutcome
    ) -> None:
        document = _child_document(outcome)
        assert await child_result.shape(document, outcome) == await child_result.shape(document, outcome)


# ---- DP14 LoopDirector ----


class DirectorHarness(Protocol):
    def fresh(self) -> LoopDirector: ...

    def state(self) -> RunState:
        """A run its strategy drives (its pre-run steps chose the strategy)."""
        ...


def _with_slots(state: RunState, slots: dict[str, JsonValue]) -> RunState:
    return state.model_copy(update={"slots": {**state.slots, **journaled(slots)}})  # type: ignore[dict-item]


PREVIOUS = (TurnResult(name="lookup", call_id="c1", status="ok", content="3 open tickets"),)


class LoopDirectorContract:
    @pytest.fixture
    def directors(self) -> DirectorHarness:
        raise NotImplementedError("provide a directors fixture")

    def test_dp14_1_the_same_boundary_gets_the_same_plan(self, directors: DirectorHarness) -> None:
        state = directors.state()
        assert directors.fresh().before(1, state, ()) == directors.fresh().before(1, state, ())

    def test_dp14_2_a_re_claimed_attempt_plans_a_later_boundary_as_the_first(
        self, directors: DirectorHarness
    ) -> None:
        first = directors.fresh()
        state = _with_slots(directors.state(), dict(first.before(1, directors.state(), ()).slots))
        planned = first.before(2, state, PREVIOUS)
        assert directors.fresh().before(2, state, PREVIOUS) == planned

    def test_dp14_3_the_plan_is_journaled_as_planned(self, directors: DirectorHarness) -> None:
        director, state = directors.fresh(), directors.state()
        for turn, previous in ((1, ()), (2, PREVIOUS)):
            plan = director.before(turn, state, previous)
            assert journaled(dict(plan.slots)) == dict(plan.slots)
            calls = (plan.synthetic.tool_calls or []) if plan.synthetic is not None else []
            assert len({c.id for c in calls}) == len(calls)
            state = _with_slots(state, dict(plan.slots))


# ---- DP15 ContextHost ----


class ContextHostContract:
    @pytest.fixture
    def hosts(self) -> TurnHarness[ContextHost]:
        raise NotImplementedError("provide a hosts fixture")

    async def test_dp15_1_every_attempt_compiles_the_same_bands(self, hosts: TurnHarness[ContextHost]) -> None:
        state, call = hosts.state(), hosts.call()
        assert await hosts.fresh().bands(call, 1, state) == await hosts.fresh().bands(call, 1, state)

    async def test_dp15_2_every_tool_has_its_own_id(self, hosts: TurnHarness[ContextHost]) -> None:
        bands = await hosts.fresh().bands(hosts.call(), 1, hosts.state())
        ids = [t.tool_id for t in bands.tools]
        assert all(ids) and len(set(ids)) == len(ids)


# ---- DP16 DelegationTools ----


class DelegationHarness(Protocol):
    def fresh(self) -> DelegationTools: ...

    def ready(self) -> tuple[Delegated, DelegationView]:
        """A pending delegating call in a view where it may spawn its child."""
        ...


class DelegationToolsContract:
    @pytest.fixture
    def delegations(self) -> DelegationHarness:
        raise NotImplementedError("provide a delegations fixture")

    def test_dp16_1_it_delegates_the_tools_it_shows_and_nothing_else(self, delegations: DelegationHarness) -> None:
        delegation = delegations.fresh()
        names = [s.name for s in delegation.schemas()]
        assert names
        assert all(delegation.delegates(ToolCall(id=f"c{i}", name=n, arguments={})) for i, n in enumerate(names))
        assert not delegation.delegates(ToolCall(id="c9", name="lookup", arguments={"q": "x"}))

    def test_dp16_2_a_re_claimed_attempt_decides_a_call_as_the_first(self, delegations: DelegationHarness) -> None:
        pending, view = delegations.ready()
        decided = delegations.fresh().decide(pending, view)
        assert isinstance(decided, ChildRequest)
        assert decided.invocation_id == pending.invocation_id
        assert delegations.fresh().decide(pending, view) == decided

    def test_dp16_3_spawning_is_idempotent_per_invocation(self, delegations: DelegationHarness) -> None:
        delegation = delegations.fresh()
        for name in (s.name for s in delegation.schemas()):
            effect = delegation.effect(name)
            assert effect.idempotent
            assert delegation.effect(name) == effect

    async def test_dp16_4_a_joined_childs_failure_reads_as_an_error(self, delegations: DelegationHarness) -> None:
        pending, _ = delegations.ready()
        delegation = delegations.fresh()
        answered = await delegation.outcome(pending.call, child_result_document(outcome="answered", output="3 tickets"))
        failed = await delegation.outcome(pending.call, {"output": "", "success": False, "error": "model_error"})
        assert (answered.error, failed.error) == (False, True)
        for outcome in (answered, failed):
            assert journaled(outcome.content) == outcome.content


# ---- DP17 ChildBudget ----

PARENTS = ((15, 1_000_000), (3, 10_000), (1, 1))
DEFINITIONS = (1, 8, 50)
BATCHES = (1, 3, 16)


class ChildBudgetContract:
    @pytest.fixture
    def child_budget(self) -> ChildBudget:
        raise NotImplementedError("provide a child_budget fixture")

    def test_dp17_1_a_child_never_gets_more_than_its_parent_or_its_definition(
        self, child_budget: ChildBudget
    ) -> None:
        for (turns, tokens), definition, batch in itertools.product(PARENTS, DEFINITIONS, BATCHES):
            share = child_budget.share(
                parent_max_turns=turns, parent_max_tokens=tokens, definition_max_turns=definition, batch=batch
            )
            assert 1 <= share.max_turns <= min(turns, definition), (turns, definition)
            assert 1 <= share.max_tokens <= tokens, (tokens, batch)

    def test_dp17_2_a_bigger_batch_never_gives_a_child_more(self, child_budget: ChildBudget) -> None:
        shares = [
            child_budget.share(parent_max_turns=15, parent_max_tokens=100_000, definition_max_turns=8, batch=b).max_tokens
            for b in BATCHES
        ]
        assert shares == sorted(shares, reverse=True)

    def test_dp17_3_the_same_question_gets_the_same_share(self, child_budget: ChildBudget) -> None:
        ask = {"parent_max_turns": 15, "parent_max_tokens": 100_000, "definition_max_turns": 8, "batch": 3}
        assert child_budget.share(**ask) == child_budget.share(**ask)

    def test_dp17_4_a_batch_of_children_leaves_the_parent_its_own_share(self, child_budget: ChildBudget) -> None:
        for (turns, tokens), batch in itertools.product(PARENTS[:2], BATCHES):
            share = child_budget.share(
                parent_max_turns=turns, parent_max_tokens=tokens, definition_max_turns=8, batch=batch
            )
            assert share.max_tokens * batch < tokens, (tokens, batch)


# ---- DP18 RoutePorts ----


@dataclass(frozen=True)
class RoutePortsCase:
    """``reachable``: a pinned route the host can reach; ``unreachable``: one it cannot."""

    route_ports: RoutePorts
    reachable: PolicyRoute
    unreachable: PolicyRoute


class RoutePortsContract:
    @pytest.fixture
    def route_ports(self) -> RoutePortsCase:
        raise NotImplementedError("provide a route_ports fixture")

    async def test_dp18_1_a_reachable_route_gets_a_model_port(self, route_ports: RoutePortsCase) -> None:
        port = await route_ports.route_ports.port(route_ports.reachable)
        assert callable(getattr(port, "sample", None)) and callable(getattr(port, "restore", None))

    async def test_dp18_2_a_route_it_cannot_reach_is_refused_as_not_pinned(self, route_ports: RoutePortsCase) -> None:
        with pytest.raises(ModelRouteNotPinned):
            _ = await route_ports.route_ports.port(route_ports.unreachable)


__all__ = [
    "ANSWER",
    "CUT",
    "EMPTY",
    "PREVIOUS",
    "ChildBudgetContract",
    "ChildResultShaperContract",
    "ContextHostContract",
    "DelegationHarness",
    "DelegationToolsContract",
    "DirectorHarness",
    "LoopDirectorContract",
    "ResponseReviewContract",
    "ReviewHarness",
    "RoutePortsCase",
    "RoutePortsContract",
    "TurnHarness",
    "TurnShaperContract",
]
