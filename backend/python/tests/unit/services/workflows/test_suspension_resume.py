"""A code workflow that parks on `ctx.request_approval`/`ctx.wait_for_event`
resumes by re-running from the top and replaying its journal. Nothing in the
run store makes that happen: the answer only unblocks the workflow because
`CodeWorkflowRunner` writes it into the journal under the exact step key the
run suspended at. Without that entry the replay reaches the same call, finds
nothing, and parks again -- forever.
"""
from __future__ import annotations

import hashlib

import pytest

from app.services.tasks.domain.models import (
    TaskDefinition,
    TaskPrincipal,
    TaskRun,
    TaskStatus,
)
from app.services.workflows.domain.models import (
    ArtifactRef,
    JournalEntry,
    ResultRef,
    StepOutcome,
    WorkflowVersion,
)
from app.services.workflows.interface.broker import BrokerResult
from app.services.workflows.runtime.code_runner import CodeWorkflowRunner

_VERSION_ID = "ver-1"
# `sdk` is injected into the exec namespace; the in-process path bans
# `__import__`, so workflow sources reach the decorator through it.
_APPROVAL_SOURCE = """
@sdk.workflow(name="deploy")
async def main(ctx):
    approved = await ctx.request_approval("deploy")
    return {"approved": approved}
"""

_EVENT_SOURCE = """
@sdk.workflow(name="wait")
async def main(ctx):
    event = await ctx.wait_for_event("pr_merged")
    return {"event": event}
"""


class _FakeJournal:
    def __init__(self) -> None:
        self.entries: dict[str, JournalEntry] = {}
        self.appended: list[JournalEntry] = []
        self.touched = False

    async def lookup(self, run_id: str, step_key: str) -> JournalEntry | None:  # noqa: ARG002
        return self.entries.get(step_key)

    async def append(self, entry: JournalEntry) -> None:
        self.appended.append(entry)
        self.entries[entry.step_key] = entry

    async def touch(self, run_id: str) -> str | None:  # noqa: ARG002
        self.touched = True
        return "2099-01-01T00:00:00+00:00"


class _NullBroker:
    async def dispatch(self, call, principal) -> BrokerResult:  # noqa: ANN001, ARG002
        return BrokerResult(success=True, data=None)


class _FakeVersionStore:
    def __init__(self, version: WorkflowVersion) -> None:
        self._version = version

    async def get(self, *, version_id: str, org_id: str) -> WorkflowVersion | None:  # noqa: ARG002
        return self._version


class _FakeCodeStore:
    def __init__(self, source: bytes) -> None:
        self._source = source

    async def get(self, ref: ArtifactRef) -> bytes:  # noqa: ARG002
        return self._source


def _task() -> TaskDefinition:
    task = TaskDefinition(
        task_id="wf-1",
        org_id="org-1",
        created_by_user_id="u-1",
        principal=TaskPrincipal(org_id="org-1", user_id="u-1", user_email="u@example.com"),
        title="Deploy",
        description="deploy on approval",
        instructions="deploy on approval",
        status=TaskStatus.ACTIVE,
    )
    return task.model_copy(update={"workflow_version_id": _VERSION_ID})


def _run(**overrides) -> TaskRun:
    run = TaskRun(
        run_id="run-1",
        task_id="wf-1",
        org_id="org-1",
        idempotency_key="wf-1:2026-01-01T00:00:00Z",
        scheduled_for="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
    )
    return run.model_copy(update=overrides)


def _runner(source: str, journal: _FakeJournal) -> CodeWorkflowRunner:
    source_bytes = source.encode("utf-8")
    version = WorkflowVersion(
        version_id=_VERSION_ID,
        workflow_id="wf-1",
        org_id="org-1",
        bundle_ref=ArtifactRef(artifact_id="blob-1"),
        content_hash=hashlib.sha256(source_bytes).hexdigest(),
    )
    return CodeWorkflowRunner(
        journal=journal,
        broker=_NullBroker(),
        version_store=_FakeVersionStore(version),
        code_store=_FakeCodeStore(source_bytes),
    )


class TestApprovalSuspension:
    @pytest.mark.asyncio
    async def test_first_attempt_parks_and_reports_the_step_key(self) -> None:
        journal = _FakeJournal()

        result = await _runner(_APPROVAL_SOURCE, journal).run(task=_task(), run=_run())

        assert result["status"] == "awaiting_input"
        assert result["suspension_kind"] == "approval"
        # The executor persists this onto the run; resumption is keyed off it.
        assert result["step_key"]
        # Suspending restarts the journal's retention clock, and the deadline
        # it returns is what stops a late answer replaying completed steps.
        assert journal.touched is True
        assert result["resume_deadline_at"] == "2099-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_an_answer_is_journaled_at_the_parked_step_and_the_run_completes(self) -> None:
        journal = _FakeJournal()
        runner = _runner(_APPROVAL_SOURCE, journal)
        parked = await runner.run(task=_task(), run=_run())
        step_key = parked["step_key"]

        result = await runner.run(
            task=_task(),
            run=_run(suspended_step_key=step_key, suspension_kind="approval", pending_answer="yes"),
        )

        assert result == {"status": "succeeded", "output": {"approved": True}}
        assert journal.entries[step_key].entry_kind == "approval"
        assert journal.entries[step_key].result_ref.inline is True

    @pytest.mark.asyncio
    async def test_a_rejection_resolves_to_false_rather_than_parking_again(self) -> None:
        journal = _FakeJournal()
        runner = _runner(_APPROVAL_SOURCE, journal)
        step_key = (await runner.run(task=_task(), run=_run()))["step_key"]

        result = await runner.run(
            task=_task(),
            run=_run(suspended_step_key=step_key, suspension_kind="approval", pending_answer="no"),
        )

        assert result == {"status": "succeeded", "output": {"approved": False}}

    @pytest.mark.asyncio
    async def test_a_redispatch_without_an_answer_parks_again_without_journaling(self) -> None:
        # e.g. the lease was reaped: there is no answer to record, and inventing
        # one would silently approve something nobody approved.
        journal = _FakeJournal()
        runner = _runner(_APPROVAL_SOURCE, journal)
        step_key = (await runner.run(task=_task(), run=_run()))["step_key"]

        result = await runner.run(
            task=_task(),
            run=_run(suspended_step_key=step_key, suspension_kind="approval"),
        )

        assert result["status"] == "awaiting_input"
        assert journal.appended == []

    @pytest.mark.asyncio
    async def test_an_already_answered_step_is_not_journaled_twice(self) -> None:
        journal = _FakeJournal()
        runner = _runner(_APPROVAL_SOURCE, journal)
        step_key = (await runner.run(task=_task(), run=_run()))["step_key"]
        journal.entries[step_key] = JournalEntry(
            run_id="run-1", seq=1, step_key=step_key, entry_kind="approval",
            idempotency_key=step_key, outcome=StepOutcome.SUCCEEDED,
            result_ref=ResultRef(inline=True),
        )

        await runner.run(
            task=_task(),
            run=_run(suspended_step_key=step_key, suspension_kind="approval", pending_answer="no"),
        )

        assert journal.appended == []


class TestWaitForEventSuspension:
    @pytest.mark.asyncio
    async def test_a_json_answer_is_handed_back_as_the_event_payload(self) -> None:
        journal = _FakeJournal()
        runner = _runner(_EVENT_SOURCE, journal)
        step_key = (await runner.run(task=_task(), run=_run()))["step_key"]

        result = await runner.run(
            task=_task(),
            run=_run(
                suspended_step_key=step_key,
                suspension_kind="wait_for_event",
                pending_answer='{"pr": 42}',
            ),
        )

        assert result == {"status": "succeeded", "output": {"event": {"pr": 42}}}
        assert journal.entries[step_key].entry_kind == "wait"

    @pytest.mark.asyncio
    async def test_a_non_json_answer_is_wrapped_rather_than_dropped(self) -> None:
        journal = _FakeJournal()
        runner = _runner(_EVENT_SOURCE, journal)
        step_key = (await runner.run(task=_task(), run=_run()))["step_key"]

        result = await runner.run(
            task=_task(),
            run=_run(
                suspended_step_key=step_key,
                suspension_kind="wait_for_event",
                pending_answer="merged",
            ),
        )

        assert result == {"status": "succeeded", "output": {"event": {"answer": "merged"}}}
