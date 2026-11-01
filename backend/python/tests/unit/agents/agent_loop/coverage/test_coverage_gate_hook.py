"""The POST_MODEL coverage gate, with the model and the searches stubbed."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.coverage.facets import Facet, FacetPlan
from app.agents.agent_loop.coverage.gate import Gap, GateDecision
from app.agents.agent_loop.coverage.state import coverage_state
from app.agents.agent_loop.coverage.targeted import TargetedResult
from app.agents.agent_loop.hooks import coverage_gate as gate_module
from app.agents.agent_loop.hooks.coverage_gate import (
    RESTATE_THE_ANSWER,
    coverage_gate,
    render_gap_request,
)
from tests.unit.agents.adapter.support.hook_helpers import run_post_model

_PLAN = FacetPlan(anchor="Northwind", facets=[
    Facet(id="f1", kind="entity", text="Northwind", terms=["Northwind"], query="Northwind rollout"),
    Facet(id="f2", kind="condition", text="EU region", terms=["EU region"], query="Northwind EU region rollout"),
    Facet(id="f3", kind="sub_question", text="who approved", terms=["approved"], query="Northwind approval"),
])


def _scope(*, depth: int = 0, max_turns: int = 15) -> TurnScope:
    spec = AgentSpec(
        name="a", system_prompt="x", tool_names=["retrieval__search_internal_knowledge"],
        model=ModelSpec(provider="scripted", model="m"), max_turns=max_turns,
    )
    run = RunScope(
        identity=RunContext(role_name="a", model="m", spawn_depth=depth),
        spec=spec, runtime=AgentRuntime(), goal=Goal(description="g"),
    )
    return TurnScope(run=run, turn_index=1)


def _context() -> AgentContext:
    context = AgentContext(org_id="o", user_id="u", user_email="u@example.com", query="When does Northwind ship?")
    coverage_state(context.tool_state).plan = _PLAN
    return context


@pytest.fixture
def stubs(monkeypatch) -> AsyncMock:
    monkeypatch.setenv("COVERAGE_LOOP", "true")
    monkeypatch.setattr(gate_module, "coverage_llm", AsyncMock(return_value=None))
    searches = AsyncMock(return_value=[TargetedResult("Northwind EU go-live", "Record: northwind-eu ... May 4")])
    monkeypatch.setattr(gate_module, "run_targeted_searches", searches)
    return searches


def _decision(*gaps: Gap, rulings: list[str] | None = None) -> AsyncMock:
    return AsyncMock(return_value=GateDecision(gaps=list(gaps), rulings=rulings or [], checked=True))


class TestCoverageGate:
    async def test_a_covered_draft_is_final(self, stubs, monkeypatch) -> None:
        monkeypatch.setattr(gate_module, "assess_draft", AsyncMock(return_value=GateDecision()))
        ctx = await run_post_model(coverage_gate(_context()), AssistantMessage(content="May 4."), scope=_scope())
        assert ctx.recovery_message is None
        stubs.assert_not_called()

    async def test_a_gap_is_searched_and_the_draft_sent_back_once(self, stubs, monkeypatch) -> None:
        monkeypatch.setattr(gate_module, "assess_draft", _decision(
            Gap("admission", "The closest match is APAC.", "Northwind EU go-live"),
        ))
        context = _context()
        gate = coverage_gate(context)

        first = await run_post_model(gate, AssistantMessage(content="The closest match is APAC."), scope=_scope())
        second = await run_post_model(gate, AssistantMessage(content="Still APAC."), scope=_scope())

        assert stubs.await_args.args[1] == ["Northwind EU go-live"]
        assert isinstance(first.recovery_message, UserMessage) and first.recovery_message.injected
        content = first.recovery_message.content
        assert 'Your draft says: "The closest match is APAC."' in content
        assert 'Search "Northwind EU go-live":' in content and RESTATE_THE_ANSWER in content
        # One round by default.
        assert second.recovery_message is None
        state = coverage_state(context.tool_state)
        assert state.gate_rounds == 1 and state.was_searched("northwind eu go-live")

    async def test_nothing_found_and_no_ruling_leaves_the_draft_final(self, stubs, monkeypatch) -> None:
        stubs.return_value = [TargetedResult("q", None)]
        monkeypatch.setattr(gate_module, "assess_draft", _decision(Gap("facet", "EU region", "q")))
        context = _context()
        ctx = await run_post_model(coverage_gate(context), AssistantMessage(content="May 4."), scope=_scope())
        assert ctx.recovery_message is None
        assert coverage_state(context.tool_state).counters["gate_nothing_found"] == 1

    async def test_a_ruling_alone_is_sent_back_without_searching(self, stubs, monkeypatch) -> None:
        monkeypatch.setattr(gate_module, "assess_draft", _decision(rulings=["For EU region: lead with R1."]))
        ctx = await run_post_model(coverage_gate(_context()), AssistantMessage(content="June 1."), scope=_scope())
        assert "- For EU region: lead with R1." in ctx.recovery_message.content
        stubs.assert_not_called()

    @pytest.mark.parametrize("scope", [_scope(depth=1), _scope(max_turns=3)])
    async def test_sub_agents_and_spent_turn_budgets_are_not_gated(self, stubs, monkeypatch, scope) -> None:
        assess = _decision(Gap("facet", "EU region", "q"))
        monkeypatch.setattr(gate_module, "assess_draft", assess)
        ctx = await run_post_model(coverage_gate(_context()), AssistantMessage(content="May 4."), scope=scope)
        assert ctx.recovery_message is None
        assess.assert_not_called()

    async def test_a_failed_check_leaves_the_draft_final(self, stubs, monkeypatch) -> None:
        monkeypatch.setattr(gate_module, "assess_draft", AsyncMock(side_effect=RuntimeError("checker down")))
        context = _context()
        ctx = await run_post_model(coverage_gate(context), AssistantMessage(content="May 4."), scope=_scope())
        assert ctx.recovery_message is None
        assert coverage_state(context.tool_state).gate_rounds == 0

    async def test_a_reply_another_hook_sent_back_is_left_alone(self, stubs, monkeypatch) -> None:
        assess = _decision(Gap("facet", "EU region", "q"))
        monkeypatch.setattr(gate_module, "assess_draft", assess)
        gate = coverage_gate(_context())

        async def _inner_sends_back(ctx, _next) -> None:
            async def _next_fn() -> None:
                ctx.recovery_message = UserMessage(content="ask with the card", injected=True)
            await gate(ctx, _next_fn)

        ctx = await run_post_model(_inner_sends_back, AssistantMessage(content="May 4."), scope=_scope())
        assert ctx.recovery_message.content == "ask with the card"
        assess.assert_not_called()

    async def test_the_round_budget_follows_its_switch(self, stubs, monkeypatch) -> None:
        monkeypatch.setenv("COVERAGE_LOOP_GATE_ROUNDS", "0")
        assess = _decision(Gap("facet", "EU region", "q"))
        monkeypatch.setattr(gate_module, "assess_draft", assess)
        await run_post_model(coverage_gate(_context()), AssistantMessage(content="May 4."), scope=_scope())
        assess.assert_not_called()


def test_the_request_keeps_supported_parts_and_asks_for_the_whole_answer() -> None:
    text = render_gap_request(GateDecision(gaps=[Gap("reference", "ADR-021", "ADR-021")]), "results")
    assert "- Named but not read: ADR-021" in text
    assert "keep what your draft already supported" in text
    assert text.endswith("results")

