"""Unit tests for `WorkflowService.list_workflows`.

Two things are pinned down here. First, a page of workflows carries its
triggers without a per-row round trip -- the dashboard's "Next run" column and
the chat panel both read them, and the naive fix is an N+1. Second, listing by
conversation includes workflows *linked* to it, not only those created from
it: the link lives in Mongo, so the task store alone cannot answer it.

Fully mocked -- no ArangoDB, no Node, no Redis.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.services.tasks.domain.errors import TaskNotFoundError
from app.services.tasks.domain.models import Page, TaskTrigger, TriggerKind
from app.services.workflows.application.workflow_service import WorkflowService


def _make_task(task_id: str, *, conversation_id: str | None = None) -> MagicMock:
    task = MagicMock()
    task.task_id = task_id
    task.org_id = "org-1"
    task.workflow_version_id = None
    task.execution_kind = "agent_task"
    task.status = MagicMock(value="active")
    task.title = f"Workflow {task_id}"
    task.description = ""
    task.created_by_user_id = "u-1"
    task.created_from_conversation_id = conversation_id
    task.tool_names = []
    task.connector_ids = []
    task.collection_ids = []
    task.required_scopes = []
    task.max_turns = None
    task.timeout_seconds = None
    task.created_at = "2026-01-01T00:00:00+00:00"
    task.updated_at = "2026-01-01T00:00:00+00:00"
    return task


def _make_trigger(trigger_id: str, task_id: str) -> TaskTrigger:
    return TaskTrigger(
        trigger_id=trigger_id,
        task_id=task_id,
        org_id="org-1",
        kind=TriggerKind.CRON,
        cron_expression="0 9 * * *",
        next_run_at="2026-01-02T09:00:00+00:00",
    )


def _make_service(
    tasks: list[MagicMock],
    *,
    triggers: dict[str, list[TaskTrigger]] | None = None,
    linked_ids: list[str] | None = None,
    linked_tasks: dict[str, MagicMock] | None = None,
    writer_raises: bool = False,
) -> tuple[WorkflowService, Any]:
    engine = MagicMock()
    engine.find = AsyncMock(
        return_value=Page(items=tasks, total=len(tasks), limit=50, offset=0)
    )
    engine.list_triggers_for_tasks = AsyncMock(return_value=triggers or {})

    async def _get(workflow_id: str, _org_id: str) -> MagicMock:
        found = (linked_tasks or {}).get(workflow_id)
        if found is None:
            raise TaskNotFoundError(workflow_id)
        return found

    engine.get = AsyncMock(side_effect=_get)

    writer = None
    if linked_ids is not None or writer_raises:
        writer = MagicMock()
        writer.list_linked_workflows = AsyncMock(
            side_effect=RuntimeError("node down") if writer_raises else None,
            return_value=linked_ids or [],
        )

    service = WorkflowService(task_engine=engine, conversation_writer=writer)
    return service, engine


class TestTriggerProjection:
    async def test_a_page_carries_each_workflows_triggers(self) -> None:
        tasks = [_make_task("wf-1"), _make_task("wf-2")]
        service, engine = _make_service(
            tasks, triggers={"wf-1": [_make_trigger("trg-1", "wf-1")]}
        )

        page = await service.list_workflows(org_id="org-1", user_id="u-1")

        by_id = {w.workflow_id: w for w in page.items}
        assert [t.trigger_id for t in by_id["wf-1"].triggers] == ["trg-1"]
        assert by_id["wf-2"].triggers == []

    async def test_triggers_are_fetched_once_for_the_whole_page(self) -> None:
        """Regression: a per-row `list_triggers` turns a 50-row page into 50
        extra round trips."""
        tasks = [_make_task(f"wf-{i}") for i in range(5)]
        service, engine = _make_service(tasks)

        await service.list_workflows(org_id="org-1", user_id="u-1")

        engine.list_triggers_for_tasks.assert_awaited_once()
        assert engine.list_triggers_for_tasks.await_args.args[0] == [
            f"wf-{i}" for i in range(5)
        ]


class TestListByConversation:
    async def test_includes_a_workflow_linked_but_not_created_there(self) -> None:
        created = _make_task("wf-created", conversation_id="conv-1")
        linked = _make_task("wf-linked", conversation_id="conv-other")
        service, _engine = _make_service(
            [created],
            linked_ids=["wf-linked"],
            linked_tasks={"wf-linked": linked},
        )

        page = await service.list_workflows(
            org_id="org-1", user_id="u-1", conversation_id="conv-1",
        )

        assert {w.workflow_id for w in page.items} == {"wf-created", "wf-linked"}
        assert page.total == 2

    async def test_does_not_duplicate_a_workflow_that_is_both(self) -> None:
        created = _make_task("wf-1", conversation_id="conv-1")
        service, _engine = _make_service(
            [created], linked_ids=["wf-1"], linked_tasks={"wf-1": created},
        )

        page = await service.list_workflows(
            org_id="org-1", user_id="u-1", conversation_id="conv-1",
        )

        assert [w.workflow_id for w in page.items] == ["wf-1"]

    async def test_drops_a_link_whose_workflow_was_deleted(self) -> None:
        """Nothing prunes the link when a workflow is deleted, so a dangling id
        is expected rather than an error."""
        created = _make_task("wf-1", conversation_id="conv-1")
        service, _engine = _make_service(
            [created], linked_ids=["wf-gone"], linked_tasks={},
        )

        page = await service.list_workflows(
            org_id="org-1", user_id="u-1", conversation_id="conv-1",
        )

        assert [w.workflow_id for w in page.items] == ["wf-1"]

    async def test_an_unreachable_conversation_store_does_not_fail_the_listing(
        self,
    ) -> None:
        created = _make_task("wf-1", conversation_id="conv-1")
        service, _engine = _make_service([created], writer_raises=True)

        page = await service.list_workflows(
            org_id="org-1", user_id="u-1", conversation_id="conv-1",
        )

        assert [w.workflow_id for w in page.items] == ["wf-1"]

    async def test_links_are_not_read_when_not_filtering_by_conversation(self) -> None:
        service, _engine = _make_service([_make_task("wf-1")], linked_ids=["wf-2"])

        page = await service.list_workflows(org_id="org-1", user_id="u-1")

        assert [w.workflow_id for w in page.items] == ["wf-1"]
