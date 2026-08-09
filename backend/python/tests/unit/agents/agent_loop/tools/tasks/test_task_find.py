"""Unit tests for `TaskFindTool` -- Part A3's read-only lookup/search tool.
Uses a `FakeTaskEngine` double (the real `TaskEngine`'s own logic is
proven by `tests/unit/services/tasks/application/test_engine.py`), so
these tests only exercise the tool's own request-shaping and
disambiguation logic."""
from __future__ import annotations

from datetime import datetime, timezone

from app.agents.agent_loop.tools.tasks.task_find import TaskFindTool
from app.services.tasks.domain.errors import TaskNotFoundError
from app.services.tasks.domain.models import (
    Page,
    RunStatus,
    TaskDefinition,
    TaskPrincipal,
    TaskQuery,
    TaskRun,
    TaskStatus,
    TaskTrigger,
    TriggerKind,
)


def _make_task(**overrides: object) -> TaskDefinition:
    defaults: dict[str, object] = {
        "org_id": "org-1", "created_by_user_id": "user-1",
        "principal": TaskPrincipal(org_id="org-1", user_id="user-1", user_email="a@b.com"),
        "title": "Daily digest", "description": "d", "instructions": "i",
        "status": TaskStatus.ACTIVE,
    }
    defaults.update(overrides)
    return TaskDefinition(**defaults)


class FakeTaskEngine:
    def __init__(self) -> None:
        self.tasks: dict[str, TaskDefinition] = {}
        self.triggers: dict[str, list[TaskTrigger]] = {}
        self.runs: dict[str, list[TaskRun]] = {}
        self.last_query: TaskQuery | None = None

    async def get(self, task_id: str, org_id: str) -> TaskDefinition:
        task = self.tasks.get(task_id)
        if task is None or task.org_id != org_id:
            raise TaskNotFoundError(task_id)
        return task

    async def find(self, query: TaskQuery) -> Page[TaskDefinition]:
        self.last_query = query
        items = list(self.tasks.values())
        if query.created_by_user_id is not None:
            items = [t for t in items if t.created_by_user_id == query.created_by_user_id]
        if query.text_search:
            items = [t for t in items if query.text_search.lower() in t.title.lower()]
        if query.status is not None:
            items = [t for t in items if t.status == query.status]
        return Page(items=items, total=len(items), limit=query.limit, offset=0)

    async def list_triggers(self, task_id: str) -> list[TaskTrigger]:
        return self.triggers.get(task_id, [])

    async def list_runs(self, task_id: str, *, limit: int = 50, offset: int = 0) -> Page[TaskRun]:
        items = self.runs.get(task_id, [])
        return Page(items=items[:limit], total=len(items), limit=limit, offset=offset)


class TestFindOne:
    async def test_direct_lookup_by_task_id(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = TaskFindTool(engine, org_id="org-1", user_id="user-1")

        result = await tool.execute(task_id=task.task_id)

        assert result.success is True
        assert result.data["found"] is True
        assert result.data["task"]["task_id"] == task.task_id

    async def test_lookup_of_missing_task_returns_failure(self) -> None:
        engine = FakeTaskEngine()
        tool = TaskFindTool(engine, org_id="org-1", user_id="user-1")

        result = await tool.execute(task_id="nonexistent")

        assert result.success is False
        assert "not found" in result.error.lower()

    async def test_include_triggers_and_runs(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        engine.triggers[task.task_id] = [TaskTrigger(
            task_id=task.task_id, org_id="org-1", kind=TriggerKind.CRON, cron_expression="0 9 * * *",
        )]
        engine.runs[task.task_id] = [TaskRun(
            task_id=task.task_id, org_id="org-1", idempotency_key="k1",
            scheduled_for=datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
            status=RunStatus.SUCCEEDED,
        )]
        tool = TaskFindTool(engine, org_id="org-1", user_id="user-1")

        result = await tool.execute(task_id=task.task_id, include_triggers=True, include_runs=True)

        assert result.success is True
        assert len(result.data["triggers"]) == 1
        assert len(result.data["runs"]) == 1
        assert result.data["runs"][0]["status"] == "succeeded"


class TestSearch:
    async def test_list_with_no_arguments_scopes_to_caller(self) -> None:
        engine = FakeTaskEngine()
        mine = _make_task(title="Mine", created_by_user_id="user-1")
        theirs = _make_task(title="Theirs", created_by_user_id="user-2")
        engine.tasks = {mine.task_id: mine, theirs.task_id: theirs}
        tool = TaskFindTool(engine, org_id="org-1", user_id="user-1")

        result = await tool.execute()

        assert result.success is True
        assert [t["title"] for t in result.data["tasks"]] == ["Mine"]
        assert result.data["disambiguation_required"] is False

    async def test_all_users_flag_removes_the_creator_scope(self) -> None:
        engine = FakeTaskEngine()
        mine = _make_task(title="Mine", created_by_user_id="user-1")
        theirs = _make_task(title="Theirs", created_by_user_id="user-2")
        engine.tasks = {mine.task_id: mine, theirs.task_id: theirs}
        tool = TaskFindTool(engine, org_id="org-1", user_id="user-1")

        result = await tool.execute(all_users=True)

        assert {t["title"] for t in result.data["tasks"]} == {"Mine", "Theirs"}

    async def test_query_matching_multiple_tasks_sets_disambiguation_required(self) -> None:
        engine = FakeTaskEngine()
        a = _make_task(title="Jira digest morning")
        b = _make_task(title="Jira digest evening")
        engine.tasks = {a.task_id: a, b.task_id: b}
        tool = TaskFindTool(engine, org_id="org-1", user_id="user-1")

        result = await tool.execute(query="jira digest")

        assert len(result.data["tasks"]) == 2
        assert result.data["disambiguation_required"] is True

    async def test_query_matching_single_task_does_not_require_disambiguation(self) -> None:
        engine = FakeTaskEngine()
        a = _make_task(title="Jira digest")
        engine.tasks = {a.task_id: a}
        tool = TaskFindTool(engine, org_id="org-1", user_id="user-1")

        result = await tool.execute(query="jira digest")

        assert len(result.data["tasks"]) == 1
        assert result.data["disambiguation_required"] is False

    async def test_unfiltered_list_returning_many_is_never_ambiguous(self) -> None:
        """No `query` means the caller asked to list everything -- many
        results back is exactly what was asked for, not ambiguity."""
        engine = FakeTaskEngine()
        a = _make_task(title="A")
        b = _make_task(title="B")
        engine.tasks = {a.task_id: a, b.task_id: b}
        tool = TaskFindTool(engine, org_id="org-1", user_id="user-1")

        result = await tool.execute()

        assert len(result.data["tasks"]) == 2
        assert result.data["disambiguation_required"] is False

    async def test_status_filter_is_forwarded(self) -> None:
        engine = FakeTaskEngine()
        active = _make_task(title="Active", status=TaskStatus.ACTIVE)
        paused = _make_task(title="Paused", status=TaskStatus.PAUSED)
        engine.tasks = {active.task_id: active, paused.task_id: paused}
        tool = TaskFindTool(engine, org_id="org-1", user_id="user-1")

        result = await tool.execute(status="paused", all_users=True)

        assert [t["title"] for t in result.data["tasks"]] == ["Paused"]
