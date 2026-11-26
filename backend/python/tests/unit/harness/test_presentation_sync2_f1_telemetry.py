"""SYNC2-F1: a durable run's ``retrieval_context`` frames are journaled, so a resumed attempt reports only what is
new, numbered after what earlier attempts reported, and a run that did not opt in journals nothing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.agents.agent_loop.protocol.formatter import AGUIFormatter
from app.agents.agent_loop.retrieval_ledger import (
    RetrievalContextLedger,
    emit_retrieval_context,
)
from app.harness.citations import TELEMETRY_EVENT, CitationJournal
from app.harness.presentation import PipesHubPresenter
from harness_core.journal import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.core.types import ToolResult
from harness_core.tools.registry import ToolRegistry


def _context(*, opted_in: bool = True) -> Any:  # noqa: ANN401 - the legacy AgentContext shape the hooks see
    return SimpleNamespace(
        tool_state={}, root_agent_spec=None, conversation_id=None, run_id="run-1",
        include_retrieval_context=opted_in, retrieval_ledger=RetrievalContextLedger(), formatter=AGUIFormatter(),
        completion_gate_nudges=0,
    )


def _found(context: Any, vrid: str) -> None:  # noqa: ANN401
    """What a search leaves in the request's state: the block and its record."""
    context.tool_state.setdefault("final_results", []).append({"virtual_record_id": vrid, "block_index": 0})
    context.tool_state.setdefault("virtual_record_id_to_result", {})[vrid] = {"id": f"rec-{vrid}", "record_name": vrid}


async def _call(context: Any, presenter: PipesHubPresenter, call_id: str, vrid: str) -> list[Any]:  # noqa: ANN401
    _found(context, vrid)
    # The POST_TOOL_USE hook's write (`retrieval_context_emission`), into the presenter's sink.
    await emit_retrieval_context(context, source="tool", tool_name="knowledgegraph__search", tool_call_id=call_id)
    events = await presenter.result(ToolCall(id=call_id, name="knowledgegraph__search", arguments={}), ToolResult(
        tool_call_id=call_id, name="knowledgegraph__search", content="ok"
    ))
    return list(events)


def _journaled(events: list[Any]) -> list[JournalEvent]:
    return [
        JournalEvent(
            org_id="org-1", session_id="s-1", stream_id="s-1", seq=seq, run_id="run-1", attempt_id="run-1.a1",
            fence=1, type=e.type, schema_version=1, critical=False, ts="2026-10-17T12:00:00Z", writer="worker",
            visibility="owner", data=dict(e.data),
        )
        for seq, e in enumerate(events, start=1)
    ]


def _frames(events: list[Any]) -> list[dict[str, Any]]:
    return [e.data["value"] for e in events if e.type == TELEMETRY_EVENT]


async def test_sync2_f1_a_resumed_attempt_reports_only_what_is_new() -> None:
    first = _context()
    presenter = PipesHubPresenter(first, ToolRegistry(), citations=CitationJournal(first))
    journaled = await _call(first, presenter, "c1", "vr-a")
    [frame] = _frames(journaled)
    assert (frame["seq"], [r["virtualRecordId"] for r in frame["records"]]) == (1, ["vr-a"])

    # Another worker: the citation state comes back from the journal, and with it what was already reported.
    second = _context()
    citations = CitationJournal(second)
    resumed = PipesHubPresenter(second, ToolRegistry(), citations=citations)
    await citations.restore(_journaled(journaled))
    [frame] = _frames(await _call(second, resumed, "c2", "vr-b"))
    assert frame["seq"] == 2
    assert [r["virtualRecordId"] for r in frame["records"]] == ["vr-b"]
    assert (frame["cumulativeRecords"], frame["cumulativeBlocks"]) == (2, 2)


async def test_sync3_run_usage_on_a_resumed_run_counts_every_attempts_auxiliary_calls() -> None:
    """A tool's own model call is in the request's usage ledger, which lives in one process: journaled with the
    call's citation delta, so the resumed attempt's ``run_usage`` counts the calls earlier attempts made too."""
    from app.agents.agent_loop.protocol.run_usage import UsageLedger
    from harness_core.kernel.core.responses import TokenUsage
    from harness_core.kernel.durable.ports import RunEnding

    first = _context()
    first.usage_ledger = UsageLedger()
    presenter = PipesHubPresenter(first, ToolRegistry(), citations=CitationJournal(first))
    first.usage_ledger.add("grep_generation", TokenUsage(input_tokens=100, output_tokens=10, cache_read_tokens=40))
    journaled = await _call(first, presenter, "c1", "vr-a")

    second = _context()
    second.usage_ledger = UsageLedger()
    citations = CitationJournal(second)
    resumed = PipesHubPresenter(second, ToolRegistry(), citations=citations)
    await citations.restore(_journaled(journaled))
    second.usage_ledger.add("plan_critic", TokenUsage(input_tokens=7, output_tokens=3))
    events = journaled + await _call(second, resumed, "c2", "vr-b")

    usage = resumed._run_usage(RunEnding(outcome="completed", events=tuple(_journaled(events))))  # pyright: ignore[reportPrivateUsage]
    assert usage is not None
    value = usage.data["value"]
    assert value["callSources"] == ["grep_generation", "plan_critic"]
    assert (value["auxiliaryLlmCalls"], value["inputTokens"], value["outputTokens"], value["cacheReadTokens"]) == (
        2, 107, 13, 40,
    )


async def test_sync2_f1_a_run_that_did_not_opt_in_journals_no_telemetry() -> None:
    context = _context(opted_in=False)
    presenter = PipesHubPresenter(context, ToolRegistry(), citations=CitationJournal(context))
    assert _frames(await _call(context, presenter, "c1", "vr-a")) == []
