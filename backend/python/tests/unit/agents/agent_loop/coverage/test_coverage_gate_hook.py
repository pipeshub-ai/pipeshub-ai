"""The POST_MODEL coverage gate, with the model and the searches stubbed."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

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
    render_opening_request,
)
from tests.unit.agents.adapter.support.hook_helpers import run_post_model

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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


def _shows(
    *records: tuple[str, str, str, str], text: str = "Record: northwind-eu ... May 4",
) -> "Callable[..., Awaitable[list[TargetedResult]]]":
    """A gate search that shows ``(vrid, record id, name, passage)`` records, as
    the search tool records them in the request's tool state."""

    async def _search(context, queries, **kwargs) -> list[TargetedResult]:
        state = context.tool_state
        records_map = state.setdefault("virtual_record_id_to_result", {})
        results = state.setdefault("final_results", [])
        for vrid, rid, name, passage in records:
            records_map[vrid] = {"id": rid, "record_name": name}
            results.append({"virtual_record_id": vrid, "content": passage, "block_index": 0})
        return [TargetedResult(query, text) for query in queries]

    return _search


_NEW_EU_RECORD = ("vr-eu", "rec-eu", "northwind-eu", "Northwind EU go-live is May 4.")


@pytest.fixture
def stubs(monkeypatch) -> AsyncMock:
    monkeypatch.setenv("COVERAGE_LOOP", "true")
    monkeypatch.setattr(gate_module, "coverage_llm", AsyncMock(return_value=None))
    searches = AsyncMock(side_effect=_shows(_NEW_EU_RECORD))
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
        stubs.side_effect = None
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

    async def test_gate_searches_run_under_the_short_timeout(self, stubs, monkeypatch) -> None:
        monkeypatch.setattr(gate_module, "assess_draft", _decision(Gap("facet", "EU region", "q")))
        await run_post_model(coverage_gate(_context()), AssistantMessage(content="May 4."), scope=_scope())
        assert stubs.await_args.kwargs["timeout"] == 20.0

    async def test_the_round_budget_follows_its_switch(self, stubs, monkeypatch) -> None:
        monkeypatch.setenv("COVERAGE_LOOP_GATE_ROUNDS", "0")
        assess = _decision(Gap("facet", "EU region", "q"))
        monkeypatch.setattr(gate_module, "assess_draft", assess)
        await run_post_model(coverage_gate(_context()), AssistantMessage(content="May 4."), scope=_scope())
        assess.assert_not_called()


_ARCHIVE_QUESTION = "For the Orchard Lane clinic pilot, can you put together a signed audit archive for June?"
_LEAD = "I can't export or sign the archive myself, and the ticket doesn't show it was sent."
_STATUS_LED_DRAFT = (
    f"The Orchard Lane ticket (HD-5521) describes this archive. {_LEAD}\n\n"
    "- A notarised index for the June window [1](ref1).\n"
)


def _archive_context() -> AgentContext:
    context = AgentContext(org_id="o", user_id="u", user_email="u@example.com", query=_ARCHIVE_QUESTION)
    coverage_state(context.tool_state).plan = _PLAN
    return context


class TestAnswerOpening:
    @pytest.fixture
    def opening_on(self, stubs, monkeypatch) -> AsyncMock:
        monkeypatch.setenv("ANSWER_OPENING_CHECK", "true")
        return stubs

    async def test_a_limitation_led_draft_is_sent_back_once_to_rewrite(self, opening_on, monkeypatch, caplog) -> None:
        monkeypatch.setenv("COVERAGE_LOOP_GATE_ROUNDS", "3")
        monkeypatch.setattr(gate_module, "assess_draft", AsyncMock(return_value=GateDecision()))
        context = _archive_context()
        gate = coverage_gate(context)

        with caplog.at_level(logging.INFO, logger=gate_module.__name__):
            first = await run_post_model(gate, AssistantMessage(content=_STATUS_LED_DRAFT), scope=_scope())
        second = await run_post_model(gate, AssistantMessage(content=_STATUS_LED_DRAFT), scope=_scope())

        assert isinstance(first.recovery_message, UserMessage) and first.recovery_message.injected
        assert first.recovery_message.content == render_opening_request(_LEAD)
        assert "No new search is needed" in first.recovery_message.content
        assert first.recovery_message.content.endswith(RESTATE_THE_ANSWER)
        opening_on.assert_not_called()
        assert second.recovery_message is None
        state = coverage_state(context.tool_state)
        assert state.gate_rounds == 1 and state.opening_rewrite_sent
        assert state.counters["gate_opening_rewrites"] == 1 and state.counters["gate_rounds_sent"] == 1
        assert any("coverage_loop: answer opening" in record.getMessage() for record in caplog.records)

    async def test_switched_off_the_draft_stands(self, stubs, monkeypatch) -> None:
        monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
        monkeypatch.setattr(gate_module, "assess_draft", AsyncMock(return_value=GateDecision()))
        context = _archive_context()
        ctx = await run_post_model(coverage_gate(context), AssistantMessage(content=_STATUS_LED_DRAFT), scope=_scope())
        assert ctx.recovery_message is None
        state = coverage_state(context.tool_state)
        assert state.gate_rounds == 0 and "gate_opening_rewrites" not in state.counters

    async def test_a_substance_led_draft_is_final(self, opening_on, monkeypatch) -> None:
        monkeypatch.setattr(gate_module, "assess_draft", AsyncMock(return_value=GateDecision()))
        draft = f"The archive is a notarised index [1](ref1). {_LEAD}"
        ctx = await run_post_model(coverage_gate(_archive_context()), AssistantMessage(content=draft), scope=_scope())
        assert ctx.recovery_message is None

    async def test_it_rides_along_with_a_gap_request(self, opening_on, monkeypatch) -> None:
        monkeypatch.setattr(gate_module, "assess_draft", _decision(Gap("facet", "EU region", "q")))
        context = _archive_context()
        ctx = await run_post_model(coverage_gate(context), AssistantMessage(content=_STATUS_LED_DRAFT), scope=_scope())
        content = ctx.recovery_message.content
        assert "- Your draft may lack: EU region" in content
        assert f'- Your draft opens with "{_LEAD}": lead with the substance the records give' in content
        state = coverage_state(context.tool_state)
        assert state.gate_rounds == 1 and state.counters["gate_opening_rewrites"] == 1

    async def test_searches_with_nothing_new_still_send_the_rewrite(self, opening_on, monkeypatch) -> None:
        opening_on.side_effect = None
        opening_on.return_value = [TargetedResult("q", None)]
        monkeypatch.setattr(gate_module, "assess_draft", _decision(Gap("facet", "EU region", "q")))
        context = _archive_context()
        ctx = await run_post_model(coverage_gate(context), AssistantMessage(content=_STATUS_LED_DRAFT), scope=_scope())
        assert ctx.recovery_message.content == render_opening_request(_LEAD)
        state = coverage_state(context.tool_state)
        assert state.gate_rounds == 1 and state.counters["gate_nothing_found"] == 1


def test_the_request_fills_gaps_and_keeps_cited_values_unless_revised() -> None:
    text = render_gap_request(GateDecision(gaps=[Gap("reference", "ADR-021", "ADR-021")]), "results")
    assert "- Named but not read: ADR-021" in text
    assert "Use a record here to fill a point your draft lacks" in text
    assert "Keep every value your draft already cites" in text
    assert "unless that record explicitly revises it" in text
    assert "give both, each with its source, and say which is which" in text
    assert "Where a record here covers a point, use it" not in text
    assert text.endswith("results")


# A draft that cites a table row for its value, and a gate search that turns up
# a lookalike record giving a different value for the same part.
_ROOM_QUESTION = "For the Larkspur cold-room outage, what defrost interval and restock window should the night crew use?"
_ROOM_PLAN = FacetPlan(anchor="Larkspur cold-room", facets=[
    Facet(id="f1", kind="entity", text="Larkspur cold-room outage", terms=["Larkspur", "cold-room"],
          query="Larkspur cold-room outage"),
    Facet(id="f2", kind="sub_question", text="night crew defrost interval and restock window",
          terms=["night crew", "defrost", "restock window"], query="night crew defrost and restock timings"),
])
_CITED_ROW = "Night crew | defrost every 6 hours | restock window 40 min"
_ROOM_DRAFT = (
    "The night crew defrosts every 6 hours and restocks within a 40 minute window [S1](ref2). "
    "The outage log doesn't state a cause [S2](ref1)."
)
_LOOKALIKE = ("vr-pilot", "rec-pilot", "cold-room pilot schedule",
              "Approved: night crew defrost every 4 hours, restock window 25 min at the pilot site.")


def _room_context(*, cited_row_index: int = 72) -> AgentContext:
    context = AgentContext(org_id="o", user_id="u", user_email="u@example.com", query=_ROOM_QUESTION)
    coverage_state(context.tool_state).plan = _ROOM_PLAN
    filler = [
        f"Night crew rota: check the door seals every {n} minutes; defrost timing is logged per shift."
        for n in range(5, 60, 5)
    ]
    context.tool_state.update({
        "virtual_record_id_to_result": {
            "vr-ticket": {"id": "rec-ticket", "record_name": "OPS-17 compressor alarms during the outage"},
            "vr-matrix": {"id": "rec-matrix", "record_name": "cold-room handling matrix"},
        },
        "final_results": [
            {"virtual_record_id": "vr-ticket", "block_index": 0,
             "content": "Larkspur cold-room outage: compressor alarms; cause field empty."},
            *[{"virtual_record_id": "vr-matrix", "block_index": i, "content": text} for i, text in enumerate(filler)],
            {"virtual_record_id": "vr-matrix", "block_index": cited_row_index, "content": _CITED_ROW},
        ],
        "citation_ref_mapper": SimpleNamespace(ref_to_url={
            "ref1": "https://app/record/rec-ticket/preview#blockIndex=0",
            "ref2": f"https://app/record/rec-matrix/preview#blockIndex={cited_row_index}",
        }),
    })
    return context


def _checker(*replies: dict) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=[AIMessage(content=json.dumps(reply)) for reply in replies])
    return llm


_UNSUPPORTED_CITED_PART = {"facets": [
    {"id": "f1", "supported": True},
    {"id": "f2", "supported": False, "query": "cold-room pilot schedule defrost interval"},
]}


class TestCitedValuesStand:
    @pytest.fixture
    def searches(self, monkeypatch) -> AsyncMock:
        monkeypatch.setenv("COVERAGE_LOOP", "true")
        searches = AsyncMock(side_effect=_shows(_LOOKALIKE, text="Record: cold-room pilot schedule ..."))
        monkeypatch.setattr(gate_module, "run_targeted_searches", searches)
        return searches

    async def test_a_part_the_cited_row_states_is_not_reopened(self, searches, monkeypatch) -> None:
        llm = _checker(_UNSUPPORTED_CITED_PART)
        monkeypatch.setattr(gate_module, "coverage_llm", AsyncMock(return_value=llm))
        context = _room_context()

        ctx = await run_post_model(coverage_gate(context), AssistantMessage(content=_ROOM_DRAFT), scope=_scope())

        assert ctx.recovery_message is None
        searches.assert_not_called()
        # The checker saw the cited row itself, not only focus-ranked excerpts.
        assert _CITED_ROW in llm.ainvoke.await_args_list[0].args[0][1].content

    async def test_an_admission_send_back_keeps_the_cited_value(self, searches, monkeypatch) -> None:
        reply = {**_UNSUPPORTED_CITED_PART, "admissions": [
            {"quote": "The outage log doesn't state a cause", "query": "Larkspur cold-room outage cause"},
        ]}
        monkeypatch.setattr(gate_module, "coverage_llm", AsyncMock(return_value=_checker(reply, {"conflicts": []})))

        ctx = await run_post_model(coverage_gate(_room_context()), AssistantMessage(content=_ROOM_DRAFT), scope=_scope())

        assert searches.await_args.args[1] == ["Larkspur cold-room outage cause"]
        content = ctx.recovery_message.content
        assert "Your draft may lack" not in content and "night crew defrost interval" not in content
        assert "Keep every value your draft already cites" in content

    async def test_records_the_gate_finds_are_adjudicated(self, searches, monkeypatch, caplog) -> None:
        reply = {"facets": [], "admissions": [
            {"quote": "The outage log doesn't state a cause", "query": "Larkspur cold-room outage cause"},
        ]}
        conflicts = {"conflicts": [{
            "facet": "f2", "draft_leads_with": ["R2"],
            "values": [
                {"record": "R2", "value": "6 h / 40 min", "same_scope_as_question": True},
                {"record": "R3", "value": "4 h / 25 min", "scope": "the pilot site",
                 "same_scope_as_question": False},
            ],
        }]}
        llm = _checker(reply, conflicts)
        monkeypatch.setattr(gate_module, "coverage_llm", AsyncMock(return_value=llm))
        context = _room_context()

        with caplog.at_level(logging.INFO, logger=gate_module.__name__):
            ctx = await run_post_model(coverage_gate(context), AssistantMessage(content=_ROOM_DRAFT), scope=_scope())

        conflict_prompt = llm.ainvoke.await_args_list[1].args[0][1].content
        assert "[R3] cold-room pilot schedule (blocks from search, new from a search run for you)" in (
            conflict_prompt
        )
        ruling = (
            "For night crew defrost interval and restock window: lead with the value from cold-room "
            "handling matrix (record rec-matrix)."
        )
        assert ruling in ctx.recovery_message.content
        assert "cold-room pilot schedule (record rec-pilot) applies to another scope (the pilot site)" in (
            ctx.recovery_message.content
        )
        assert coverage_state(context.tool_state).counters["gate_found_rulings"] == 1
        assert any(ruling[:80] in record.getMessage() for record in caplog.records)

    async def test_a_named_reference_found_unread_is_still_sent_back(self, searches, monkeypatch) -> None:
        searches.side_effect = _shows(
            ("vr-spec", "rec-spec", "SPEC-40 compressor relay", "Relay swap procedure."),
            text="Record: SPEC-40 compressor relay ...",
        )
        monkeypatch.setattr(gate_module, "coverage_llm", AsyncMock(return_value=None))
        monkeypatch.setattr(gate_module, "assess_draft", _decision(
            Gap("reference", "SPEC-40", "SPEC-40 compressor relay swap"),
        ))

        ctx = await run_post_model(coverage_gate(_room_context()), AssistantMessage(content=_ROOM_DRAFT), scope=_scope())

        content = ctx.recovery_message.content
        assert "- Named but not read: SPEC-40" in content
        assert content.endswith('Search "SPEC-40 compressor relay swap":\nRecord: SPEC-40 compressor relay ...')

    async def test_searches_that_add_no_new_record_send_nothing_back(self, searches, monkeypatch) -> None:
        searches.side_effect = _shows(text="Record: cold-room handling matrix ... 40 min")
        monkeypatch.setattr(gate_module, "assess_draft", _decision(Gap("facet", "EU region", "q")))
        context = _room_context()

        ctx = await run_post_model(coverage_gate(context), AssistantMessage(content=_ROOM_DRAFT), scope=_scope())

        assert ctx.recovery_message is None
        assert coverage_state(context.tool_state).counters["gate_nothing_new"] == 1

    async def test_a_caveat_with_named_unread_documents_still_brings_them_in(self, searches, monkeypatch) -> None:
        searches.side_effect = _shows(
            ("vr-guide", "rec-guide", "compressor restart guide", "Restart order per bank."),
            text="Record: compressor restart guide ...",
        )
        reply = {**_UNSUPPORTED_CITED_PART, "unread_references": [
            {"name": "compressor restart guide", "query": "compressor restart guide bank order"},
        ]}
        monkeypatch.setattr(gate_module, "coverage_llm", AsyncMock(return_value=_checker(reply, {"conflicts": []})))

        ctx = await run_post_model(coverage_gate(_room_context()), AssistantMessage(content=_ROOM_DRAFT), scope=_scope())

        assert searches.await_args.args[1] == ["compressor restart guide bank order"]
        assert "- Named but not read: compressor restart guide" in ctx.recovery_message.content
