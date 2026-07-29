"""
Tests for hooks/full_record_gate.py.

Pins the behaviours that protect against wrong-triggering:
- Pinpoint query (needs_whole_document=False) → gate is silent, no judge.
- A record whose every block was retrieved is never a candidate.
- Judge receives draft answer; empty verdict and exception both stay silent.
- Judge runs at most once per request.
- Gate is silent when dynamic_fetch_full_record is not in spec tool names.
"""

from __future__ import annotations

import pytest

from uuid import uuid4

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall
from app.agent_loop_lib.core.scope import RunScope, ToolScope, TurnScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ToolOutput
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.full_record_gate import (
    full_record_fetch_tracking,
    full_record_gate,
)
from app.modules.agents.record_escalation.models import (
    FetchCandidate,
    FetchPlan,
    FetchVerdict,
)
from tests.unit.agents.adapter.support.hook_helpers import run_post_model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(**overrides) -> AgentContext:
    defaults: dict = {"org_id": "org-1", "user_id": "user-1", "user_email": "u@example.com"}
    defaults.update(overrides)
    return AgentContext(**defaults)


def _turn_scope(tool_names: list[str], *, spec_name: str = "agent") -> TurnScope:
    spec = AgentSpec(
        name=spec_name, system_prompt="x", tool_names=tool_names,
        model=ModelSpec(provider="scripted", model="m"),
    )
    run_scope = RunScope(
        identity=RunContext(role_name=spec_name, model="m"),
        spec=spec, runtime=AgentRuntime(), goal=Goal(description="g"),
    )
    return TurnScope(run=run_scope, turn_index=0)


def _make_plan(n_candidates: int = 1) -> FetchPlan:
    candidates = tuple(
        FetchCandidate(
            record_id=f"rec-{i}",
            record_name=f"Report {i}",
            topics=("revenue",),
            summary="",
            blocks_held=3,
            blocks_total=20,
        )
        for i in range(n_candidates)
    )
    return FetchPlan(candidates=candidates, excluded=())


def _populated_tool_state() -> dict:
    """Minimal tool_state that build_candidates can compute candidates from."""
    record_id = "rec-1"
    vrid = "vrid-1"
    record = {
        "id": record_id,
        "record_name": "Q3 Report",
        "block_containers": {
            "blocks": [{"index": i, "type": "TEXT", "data": f"b{i}"} for i in range(20)],
            "block_groups": [],
        },
        "semantic_metadata": {"topics": ["revenue"]},
    }
    return {
        "final_results": [
            {"virtual_record_id": vrid, "block_index": i, "content": f"b{i}"}
            for i in range(3)
        ],
        "virtual_record_id_to_result": {vrid: record},
        "full_records_fetched": set(),
        "query": "compare Q3 vs Q4",
        "needs_whole_document": True,
    }


class _StubJudge:
    def __init__(self, verdict: FetchVerdict, raise_exc: Exception | None = None) -> None:
        self._verdict = verdict
        self._raise = raise_exc
        self.calls: list[dict] = []

    async def judge(self, *, query: str, draft_answer: str, plan: FetchPlan) -> FetchVerdict:
        self.calls.append({"query": query, "draft_answer": draft_answer, "plan": plan})
        if self._raise:
            raise self._raise
        return self._verdict


# ---------------------------------------------------------------------------
# Gate tests
# ---------------------------------------------------------------------------


class TestFullRecordGate:
    async def test_silent_when_no_whole_document_need(self) -> None:
        """Pinpoint query: gate must be completely silent."""
        context = _make_context()
        context.needs_whole_document = False
        context.tool_state.update(_populated_tool_state())

        judge = _StubJudge(FetchVerdict(needed=(("rec-1", "x"),), judged=True))
        gate = full_record_gate(context, judge)
        ctx = await run_post_model(
            gate, AssistantMessage(content="The date was March 15."),
            scope=_turn_scope(["dynamic_fetch_full_record"]),
        )
        assert ctx.recovery_message is None
        assert judge.calls == [], "judge must never be called when bit is False"

    async def test_silent_when_fetch_tool_not_in_spec(self) -> None:
        context = _make_context()
        context.needs_whole_document = True
        context.tool_state.update(_populated_tool_state())
        judge = _StubJudge(FetchVerdict(needed=(("rec-1", "x"),), judged=True))
        gate = full_record_gate(context, judge)
        ctx = await run_post_model(
            gate, AssistantMessage(content="The answer."),
            scope=_turn_scope(["retrieval.search_internal_knowledge"]),
        )
        assert ctx.recovery_message is None
        assert judge.calls == []

    async def test_silent_on_tool_calls(self) -> None:
        context = _make_context()
        context.needs_whole_document = True
        context.tool_state.update(_populated_tool_state())
        judge = _StubJudge(FetchVerdict(needed=(("rec-1", "x"),), judged=True))
        gate = full_record_gate(context, judge)
        ctx = await run_post_model(
            gate, AssistantMessage(content=""),
            tool_calls=[ToolCall(id="1", name="dynamic_fetch_full_record", arguments={})],
            scope=_turn_scope(["dynamic_fetch_full_record"]),
        )
        assert ctx.recovery_message is None
        assert judge.calls == []

    async def test_silent_on_truncated_response(self) -> None:
        context = _make_context()
        context.needs_whole_document = True
        context.tool_state.update(_populated_tool_state())
        judge = _StubJudge(FetchVerdict(needed=(("rec-1", "x"),), judged=True))
        gate = full_record_gate(context, judge)
        ctx = await run_post_model(
            gate, AssistantMessage(content="truncated...", truncated=True),
            scope=_turn_scope(["dynamic_fetch_full_record"]),
        )
        assert ctx.recovery_message is None
        assert judge.calls == []

    async def test_empty_verdict_stays_silent(self) -> None:
        """Judge returns empty needed list — gate must stay silent."""
        context = _make_context()
        context.needs_whole_document = True
        context.tool_state.update(_populated_tool_state())
        judge = _StubJudge(FetchVerdict(needed=(), judged=True))
        gate = full_record_gate(context, judge)
        ctx = await run_post_model(
            gate, AssistantMessage(content="The answer has all the data."),
            scope=_turn_scope(["dynamic_fetch_full_record"]),
        )
        assert ctx.recovery_message is None
        assert len(judge.calls) == 1  # judge was called
        assert judge.calls[0]["draft_answer"] == "The answer has all the data."

    async def test_judge_failure_stays_silent(self) -> None:
        """Any exception from the judge must result in silence."""
        context = _make_context()
        context.needs_whole_document = True
        context.tool_state.update(_populated_tool_state())
        judge = _StubJudge(
            FetchVerdict(needed=(), judged=False),
            raise_exc=RuntimeError("transport down"),
        )
        gate = full_record_gate(context, judge)
        ctx = await run_post_model(
            gate, AssistantMessage(content="The answer."),
            scope=_turn_scope(["dynamic_fetch_full_record"]),
        )
        assert ctx.recovery_message is None

    async def test_judge_runs_at_most_once(self) -> None:
        """Gate must not call the judge more than once per request, even across multiple turns."""
        context = _make_context()
        context.needs_whole_document = True
        context.tool_state.update(_populated_tool_state())
        judge = _StubJudge(FetchVerdict(needed=(), judged=True))
        gate = full_record_gate(context, judge)
        scope = _turn_scope(["dynamic_fetch_full_record"])
        for _ in range(3):
            await run_post_model(
                gate, AssistantMessage(content="answer"),
                scope=scope,
            )
        assert len(judge.calls) == 1

    async def test_nudge_names_confirmed_ids(self) -> None:
        """When judge confirms records, recovery_message must name the IDs."""
        context = _make_context()
        context.needs_whole_document = True
        context.tool_state.update(_populated_tool_state())
        judge = _StubJudge(
            FetchVerdict(needed=(("rec-1", "missing Q3 data"),), judged=True)
        )
        gate = full_record_gate(context, judge)
        ctx = await run_post_model(
            gate, AssistantMessage(content="Partial answer."),
            scope=_turn_scope(["dynamic_fetch_full_record"]),
        )
        assert ctx.recovery_message is not None
        assert "rec-1" in ctx.recovery_message.content

    async def test_judge_receives_draft_answer(self) -> None:
        context = _make_context()
        context.needs_whole_document = True
        context.tool_state.update(_populated_tool_state())
        judge = _StubJudge(FetchVerdict(needed=(), judged=True))
        gate = full_record_gate(context, judge)
        await run_post_model(
            gate, AssistantMessage(content="this is the draft answer"),
            scope=_turn_scope(["dynamic_fetch_full_record"]),
        )
        assert judge.calls[0]["draft_answer"] == "this is the draft answer"

    async def test_fully_retrieved_record_never_a_candidate(self) -> None:
        """When every block of a record has been retrieved, it must not be a candidate."""
        context = _make_context()
        context.needs_whole_document = True
        record_id = "rec-full"
        vrid = "vrid-full"
        record = {
            "id": record_id,
            "record_name": "Full Report",
            "block_containers": {
                "blocks": [{"index": i, "type": "TEXT", "data": f"b{i}"} for i in range(3)],
                "block_groups": [],
            },
            "semantic_metadata": {},
        }
        context.tool_state.update({
            "final_results": [
                {"virtual_record_id": vrid, "block_index": i, "content": f"b{i}"}
                for i in range(3)  # all 3 blocks retrieved
            ],
            "virtual_record_id_to_result": {vrid: record},
            "full_records_fetched": set(),
            "query": "summarize the report",
            "needs_whole_document": True,
        })
        judge = _StubJudge(FetchVerdict(needed=(("rec-full", "x"),), judged=True))
        gate = full_record_gate(context, judge)
        ctx = await run_post_model(
            gate, AssistantMessage(content="answer from full record"),
            scope=_turn_scope(["dynamic_fetch_full_record"]),
        )
        # No candidates → judge never called → no nudge
        assert ctx.recovery_message is None
        assert judge.calls == []

    async def test_nudge_budget_capped(self) -> None:
        context = _make_context()
        context.needs_whole_document = True
        context.full_record_gate_nudges = 1  # already at default max
        context.tool_state.update(_populated_tool_state())
        judge = _StubJudge(FetchVerdict(needed=(("rec-1", "x"),), judged=True))
        gate = full_record_gate(context, judge, max_nudges=1)
        ctx = await run_post_model(
            gate, AssistantMessage(content="answer"),
            scope=_turn_scope(["dynamic_fetch_full_record"]),
        )
        assert ctx.recovery_message is None
        assert judge.calls == []


async def _noop_next() -> None:
    return None


def _tool_result_ctx(
    tool_name: str,
    tool_args: dict,
    tool_path: str = "/dynamic/dynamic/fetch_full_record",
) -> ToolResultContext:
    """Build a ToolResultContext whose scope.call carries `tool_name` and `tool_args`."""
    spec = AgentSpec(
        name="agent", system_prompt="x", tool_names=[tool_name],
        model=ModelSpec(provider="scripted", model="m"),
    )
    run_scope = RunScope(
        identity=RunContext(role_name="agent", model="m"),
        spec=spec, runtime=AgentRuntime(tool_registry=ToolRegistry()),
        goal=Goal(description="g"),
    )
    turn_scope = TurnScope(run=run_scope, turn_index=0)
    call = ToolCall(id="tc-1", name=tool_name, arguments=tool_args)
    tool_scope = ToolScope(turn=turn_scope, call=call, tool_path=tool_path)
    return ToolResultContext(
        tool_path=tool_path,
        tool_use_id=uuid4(),
        tool_response=ToolOutput(success=True, data="ok"),
        scope=tool_scope,
    )


class TestFullRecordFetchTracking:
    async def test_records_fetched_ids(self) -> None:
        context = _make_context()
        tracker = full_record_fetch_tracking(context)
        ctx = _tool_result_ctx(
            "dynamic_fetch_full_record",
            {"record_ids": ["rec-a", "rec-b"]},
        )
        await tracker(ctx, _noop_next)
        assert "rec-a" in context.full_records_fetched
        assert "rec-b" in context.full_records_fetched

    async def test_other_tools_not_tracked(self) -> None:
        context = _make_context()
        tracker = full_record_fetch_tracking(context)
        ctx = _tool_result_ctx(
            "some_other_tool",
            {"record_ids": ["rec-x"]},
            tool_path="/tools/some_other_tool",
        )
        await tracker(ctx, _noop_next)
        assert context.full_records_fetched == set()
