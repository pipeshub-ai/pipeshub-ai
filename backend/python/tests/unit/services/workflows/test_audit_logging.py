"""The destructive and approval operations have to name who performed them.

`TaskEngine` only ever sees an org, so if `WorkflowService` drops the acting
user there is nowhere left to recover it from -- these pin the actor to the
operations where "who did this" is the question someone will eventually ask.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.workflows.application.workflow_service import WorkflowService


def _make_task() -> MagicMock:
    task = MagicMock()
    task.task_id = "wf-1"
    task.org_id = "org-1"
    task.workflow_version_id = None
    task.execution_kind = "agent_task"
    task.status = MagicMock(value="cancelled")
    task.title = "My Workflow"
    task.description = ""
    task.created_by_user_id = "u-1"
    task.created_from_conversation_id = None
    task.tool_names = []
    task.connector_ids = []
    task.collection_ids = []
    task.required_scopes = []
    task.max_turns = None
    task.timeout_seconds = None
    task.created_at = "2026-01-01T00:00:00+00:00"
    task.updated_at = "2026-01-01T00:00:00+00:00"
    return task


def _make_service() -> tuple[WorkflowService, MagicMock]:
    task = _make_task()
    engine = MagicMock()
    engine.get = AsyncMock(return_value=task)
    engine.pause = AsyncMock(return_value=task)
    engine.unpause = AsyncMock(return_value=task)
    engine.cancel = AsyncMock(return_value=task)
    engine.delete = AsyncMock(return_value=True)
    engine.answer_run = AsyncMock(
        return_value=MagicMock(status=MagicMock(value="pending"))
    )
    return WorkflowService(task_engine=engine), engine


@pytest.mark.parametrize(
    ("method", "action"),
    [("pause", "pause"), ("resume", "resume"), ("cancel", "cancel")],
)
async def test_lifecycle_changes_name_the_acting_user(
    method: str, action: str, caplog: pytest.LogCaptureFixture,
) -> None:
    service, _engine = _make_service()

    with caplog.at_level(logging.INFO):
        await getattr(service, method)(
            workflow_id="wf-1", org_id="org-1", user_id="u-42",
        )

    assert any(
        f"action={action}" in r.getMessage() and "user=u-42" in r.getMessage()
        for r in caplog.records
    ), caplog.text


async def test_delete_names_the_acting_user(caplog: pytest.LogCaptureFixture) -> None:
    service, _engine = _make_service()

    with caplog.at_level(logging.INFO):
        await service.delete(workflow_id="wf-1", org_id="org-1", user_id="u-42")

    assert any(
        "action=delete" in r.getMessage() and "user=u-42" in r.getMessage()
        for r in caplog.records
    ), caplog.text


async def test_answering_a_run_records_who_approved_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An answer can release a destructive step past an approval gate, so this
    is the audit line that matters most."""
    service, _engine = _make_service()

    with caplog.at_level(logging.INFO):
        await service.answer_run(
            workflow_id="wf-1", run_id="run-1", org_id="org-1",
            answer="approve", user_id="u-42",
        )

    assert any(
        "action=answer_run" in r.getMessage()
        and "user=u-42" in r.getMessage()
        and "run_id=run-1" in r.getMessage()
        for r in caplog.records
    ), caplog.text


async def test_an_unattributed_call_is_marked_rather_than_omitted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A caller that does not pass a user should be visibly unattributed, not
    silently indistinguishable from one that did."""
    service, _engine = _make_service()

    with caplog.at_level(logging.INFO):
        await service.cancel(workflow_id="wf-1", org_id="org-1")

    assert any("user=<unknown>" in r.getMessage() for r in caplog.records), caplog.text
