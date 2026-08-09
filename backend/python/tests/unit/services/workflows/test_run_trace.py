"""`get_run_trace` is the whole of the product's debugging story ("debugging
and observability handled entirely through execution traces"), so what it
must guarantee is: steps in execution order, one normalised shape regardless
of whether the run was code or an agent, and no cross-tenant read.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.tasks.domain.errors import RunNotFoundError
from app.services.workflows.application.workflow_service import WorkflowService
from app.services.workflows.domain.models import (
    ErrorRecord,
    JournalEntry,
    ResultRef,
    StepOutcome,
)


def _entry(seq: int, step_key: str, *, kind: str = "tool", outcome: str = "succeeded",
           error: str | None = None) -> JournalEntry:
    return JournalEntry(
        run_id="run-1",
        seq=seq,
        step_key=step_key,
        entry_kind=kind,
        idempotency_key=step_key,
        outcome=StepOutcome(outcome),
        result_ref=ResultRef(inline=None),
        error=ErrorRecord(code="tool_error", message=error) if error else None,
    )


class _FakeJournal:
    def __init__(self, entries: list[JournalEntry]) -> None:
        self._entries = entries
        self.loaded: list[str] = []

    async def load(self, run_id: str) -> list[JournalEntry]:
        self.loaded.append(run_id)
        return list(self._entries)


def _service(run: SimpleNamespace, journal: _FakeJournal | None = None) -> tuple[WorkflowService, MagicMock]:
    engine = MagicMock()
    engine.get_run = AsyncMock(return_value=run)
    return WorkflowService(task_engine=engine, journal=journal), engine


def _code_run() -> SimpleNamespace:
    return SimpleNamespace(run_id="run-1", agent_run_id=None, status="succeeded")


class TestCodeRunTrace:
    @pytest.mark.asyncio
    async def test_returns_steps_in_seq_order_regardless_of_storage_order(self) -> None:
        journal = _FakeJournal([
            _entry(3, "ctx.emit:text#0", kind="emit"),
            _entry(1, "ctx.tool:jira__search_issues#0"),
            _entry(2, "ctx.tool:slack__post_message#0"),
        ])
        service, _ = _service(_code_run(), journal)

        _, trace = await service.get_run_trace(
            workflow_id="wf-1", run_id="run-1", org_id="org-1",
        )

        assert [e.seq for e in trace] == [1, 2, 3]
        assert [e.label for e in trace] == [
            "ctx.tool:jira__search_issues#0",
            "ctx.tool:slack__post_message#0",
            "ctx.emit:text#0",
        ]

    @pytest.mark.asyncio
    async def test_exposes_the_tool_target_so_a_graph_node_can_be_matched_to_a_row(self) -> None:
        journal = _FakeJournal([_entry(1, "ctx.tool:jira__create_issue#0")])
        service, _ = _service(_code_run(), journal)

        _, trace = await service.get_run_trace(
            workflow_id="wf-1", run_id="run-1", org_id="org-1",
        )

        assert trace[0].target == "jira__create_issue"
        assert trace[0].kind == "tool"

    @pytest.mark.asyncio
    async def test_surfaces_failed_and_skipped_outcomes(self) -> None:
        journal = _FakeJournal([
            _entry(1, "ctx.tool:a#0", outcome="failed", error="401 Unauthorized"),
            _entry(2, "ctx.state.set:k#0", kind="state", outcome="skipped"),
        ])
        service, _ = _service(_code_run(), journal)

        _, trace = await service.get_run_trace(
            workflow_id="wf-1", run_id="run-1", org_id="org-1",
        )

        assert [(e.outcome, e.error) for e in trace] == [
            ("failed", "401 Unauthorized"),
            ("skipped", None),
        ]

    @pytest.mark.asyncio
    async def test_a_run_with_no_journal_yields_an_empty_trace_not_an_error(self) -> None:
        """An agent-era run, or one that died before its first step, still has
        to render a run page."""
        service, _ = _service(_code_run(), journal=None)

        run, trace = await service.get_run_trace(
            workflow_id="wf-1", run_id="run-1", org_id="org-1",
        )

        assert run.run_id == "run-1"
        assert trace == []

    @pytest.mark.asyncio
    async def test_an_unreadable_journal_degrades_to_empty_rather_than_500(self) -> None:
        journal = _FakeJournal([])
        journal.load = AsyncMock(side_effect=ConnectionError("redis down"))  # type: ignore[method-assign]
        service, _ = _service(_code_run(), journal)

        _, trace = await service.get_run_trace(
            workflow_id="wf-1", run_id="run-1", org_id="org-1",
        )

        assert trace == []


class TestOrgIsolation:
    @pytest.mark.asyncio
    async def test_ownership_is_checked_before_any_journal_read(self) -> None:
        """The journal is keyed by run id alone, so the engine's org/workflow
        check is the only thing standing between a guessed run id and another
        tenant's trace -- it must run first, and a denial must read nothing."""
        journal = _FakeJournal([_entry(1, "ctx.tool:a#0")])
        service, engine = _service(_code_run(), journal)
        engine.get_run.side_effect = RunNotFoundError("run-1")

        with pytest.raises(RunNotFoundError):
            await service.get_run_trace(
                workflow_id="wf-1", run_id="run-1", org_id="org-INTRUDER",
            )

        assert journal.loaded == []

    @pytest.mark.asyncio
    async def test_the_engine_is_asked_with_the_callers_org(self) -> None:
        service, engine = _service(_code_run(), _FakeJournal([]))

        await service.get_run_trace(workflow_id="wf-1", run_id="run-1", org_id="org-1")

        engine.get_run.assert_awaited_once_with("run-1", "wf-1", "org-1")


class TestAgentRunTrace:
    @pytest.mark.asyncio
    async def test_agent_runs_read_the_timeline_not_the_journal(self) -> None:
        journal = _FakeJournal([_entry(1, "ctx.tool:a#0")])
        run = SimpleNamespace(run_id="run-1", agent_run_id="agent-9", status="succeeded")
        service, _ = _service(run, journal)

        _, trace = await service.get_run_trace(
            workflow_id="wf-1", run_id="run-1", org_id="org-1",
        )

        # No graph provider is configured, so the timeline is unavailable --
        # the point is that it did not silently fall back to the code journal,
        # which belongs to a different execution model.
        assert journal.loaded == []
        assert trace == []
