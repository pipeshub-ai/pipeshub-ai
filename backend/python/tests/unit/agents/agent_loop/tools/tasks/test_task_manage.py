"""Unit tests for `TaskManageTool` -- Part A3's write surface (action
dispatch). Uses a `FakeTaskEngine` double so these tests only exercise
the tool's own argument validation/shaping and action routing -- `TaskEngine`
itself is proven separately by `test_engine.py`."""
from __future__ import annotations

from app.agent_loop_lib.tools.base import Tag
from app.agents.agent_loop.tools.tasks.task_manage import TaskManageTool
from app.services.tasks.domain.errors import (
    PrerequisiteError,
    StaleAnswerError,
    TaskNotFoundError,
)
from app.services.tasks.domain.models import (
    RunStatus,
    TaskDefinition,
    TaskPrincipal,
    TaskRun,
    TaskStatus,
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
        self.create_calls: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []
        self.run_now_calls: list[str] = []
        self.promote_calls: list[str] = []
        self.raise_on_create: Exception | None = None
        self.answer_calls: list[tuple[str, str, str, str]] = []
        self.raise_on_answer: Exception | None = None
        self.runs: dict[str, TaskRun] = {}

    async def create(self, **kwargs: object) -> tuple[TaskDefinition, list, object, dict]:
        self.create_calls.append(kwargs)
        if self.raise_on_create is not None:
            raise self.raise_on_create
        task = _make_task(title=kwargs["title"], description=kwargs["description"], instructions=kwargs["instructions"])
        self.tasks[task.task_id] = task
        return task, [], None, {}

    async def pause(self, task_id: str, org_id: str) -> TaskDefinition:
        task = self.tasks[task_id].model_copy(update={"status": TaskStatus.PAUSED, "enabled": False})
        self.tasks[task_id] = task
        return task

    async def unpause(self, task_id: str, org_id: str) -> TaskDefinition:
        task = self.tasks[task_id].model_copy(update={"status": TaskStatus.ACTIVE, "enabled": True})
        self.tasks[task_id] = task
        return task

    async def cancel(self, task_id: str, org_id: str) -> TaskDefinition:
        task = self.tasks[task_id].model_copy(update={"status": TaskStatus.CANCELLED, "enabled": False})
        self.tasks[task_id] = task
        return task

    async def run_now(self, task_id: str, org_id: str) -> TaskRun:
        self.run_now_calls.append(task_id)
        return TaskRun(task_id=task_id, org_id=org_id, idempotency_key="k1", scheduled_for="now", created_at="now")

    async def update_fields(self, task_id: str, org_id: str, **fields: object) -> TaskDefinition:
        self.update_calls.append({"task_id": task_id, **fields})
        if task_id not in self.tasks:
            raise TaskNotFoundError(task_id)
        task = self.tasks[task_id].model_copy(update=fields)
        self.tasks[task_id] = task
        return task

    async def answer_run(self, run_id: str, task_id: str, org_id: str, answer: str) -> TaskRun:
        self.answer_calls.append((run_id, task_id, org_id, answer))
        if self.raise_on_answer is not None:
            raise self.raise_on_answer
        run = self.runs.get(run_id) or TaskRun(
            run_id=run_id, task_id=task_id, org_id=org_id, idempotency_key="k1",
            scheduled_for="now", created_at="now",
        )
        updated = run.model_copy(update={"status": RunStatus.PENDING, "pending_answer": answer})
        self.runs[run_id] = updated
        return updated

    async def promote_to_agent(self, task_id: str, org_id: str, *, graph_provider: object, config_service: object) -> str:
        self.promote_calls.append(task_id)
        return "agent-123"


def _make_tool(engine: FakeTaskEngine, **overrides: object) -> TaskManageTool:
    defaults: dict[str, object] = {
        "org_id": "org-1", "user_id": "user-1", "user_email": "a@b.com",
        "graph_provider": object(), "config_service": object(), "conversation_id": "conv-1",
    }
    defaults.update(overrides)
    return TaskManageTool(engine, **defaults)


class TestTags:
    def test_task_manage_is_tagged_write(self) -> None:
        tool = _make_tool(FakeTaskEngine())
        assert Tag("category", "write") in tool.tags


class TestCreate:
    async def test_create_requires_title_description_instructions(self) -> None:
        engine = FakeTaskEngine()
        tool = _make_tool(engine)

        result = await tool.execute(action="create", title="t", description=None, instructions="i")

        assert result.success is False
        assert "requires" in result.error
        assert engine.create_calls == []

    async def test_create_happy_path(self) -> None:
        engine = FakeTaskEngine()
        tool = _make_tool(engine)

        result = await tool.execute(action="create", title="Daily digest", description="d", instructions="i")

        assert result.success is True
        assert result.data["status"] == "active"
        assert len(engine.create_calls) == 1
        assert engine.create_calls[0]["created_from_conversation_id"] == "conv-1"

    async def test_create_forwards_prerequisite_error_as_failure(self) -> None:
        engine = FakeTaskEngine()
        engine.raise_on_create = PrerequisiteError("missing connector", missing=["connector:slack"])
        tool = _make_tool(engine)

        result = await tool.execute(action="create", title="t", description="d", instructions="i")

        assert result.success is False
        assert "missing connector" in result.error

    async def test_create_rejects_malformed_steps(self) -> None:
        engine = FakeTaskEngine()
        tool = _make_tool(engine)

        result = await tool.execute(
            action="create", title="t", description="d", instructions="i",
            steps=[{"description": "missing required id field"}],
        )

        assert result.success is False
        assert "Invalid steps" in result.error
        assert engine.create_calls == []

    async def test_create_parses_valid_steps(self) -> None:
        engine = FakeTaskEngine()
        tool = _make_tool(engine)

        result = await tool.execute(
            action="create", title="t", description="d", instructions="i",
            steps=[{"id": "a", "description": "do a"}, {"id": "b", "description": "do b", "depends_on": ["a"]}],
        )

        assert result.success is True
        assert len(engine.create_calls[0]["steps"]) == 2

    async def test_create_surfaces_non_blocking_prerequisite_notes(self) -> None:
        class _FakeCheckResult:
            issues = ["mcp_server 'mcp-1': unverifiable"]

            def summary(self) -> str:
                return "mcp_server 'mcp-1': unverifiable"

        class EngineWithNotes(FakeTaskEngine):
            async def create(self, **kwargs: object) -> tuple[TaskDefinition, list, object, dict]:
                task = _make_task(title=kwargs["title"], description=kwargs["description"], instructions=kwargs["instructions"])
                return task, [], _FakeCheckResult(), {}

        tool = _make_tool(EngineWithNotes())
        result = await tool.execute(action="create", title="t", description="d", instructions="i")

        assert result.success is True
        assert "prerequisite_notes" in result.data


class TestActionsRequiringTaskId:
    async def test_pause_requires_task_id(self) -> None:
        tool = _make_tool(FakeTaskEngine())
        result = await tool.execute(action="pause")
        assert result.success is False
        assert "task_id" in result.error

    async def test_pause_happy_path(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine)

        result = await tool.execute(action="pause", task_id=task.task_id)

        assert result.success is True
        assert result.data["status"] == "paused"

    async def test_resume_happy_path(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task(status=TaskStatus.PAUSED, enabled=False)
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine)

        result = await tool.execute(action="resume", task_id=task.task_id)

        assert result.success is True
        assert result.data["status"] == "active"

    async def test_cancel_happy_path(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine)

        result = await tool.execute(action="cancel", task_id=task.task_id)

        assert result.success is True
        assert result.data["status"] == "cancelled"

    async def test_run_now_happy_path(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine)

        result = await tool.execute(action="run_now", task_id=task.task_id)

        assert result.success is True
        assert result.data["run_id"]
        assert engine.run_now_calls == [task.task_id]

    async def test_unknown_action_returns_failure(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine)

        result = await tool.execute(action="delete", task_id=task.task_id)

        assert result.success is False
        assert "Unknown action" in result.error


class TestAnswer:
    async def test_answer_requires_run_id_and_answer(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine)

        result = await tool.execute(action="answer", task_id=task.task_id)

        assert result.success is False
        assert "run_id" in result.error
        assert engine.answer_calls == []

    async def test_answer_happy_path(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine)

        result = await tool.execute(action="answer", task_id=task.task_id, run_id="run-1", answer="yes, proceed")

        assert result.success is True
        assert result.data == {"task_id": task.task_id, "run_id": "run-1", "status": "pending"}
        assert engine.answer_calls == [("run-1", task.task_id, "org-1", "yes, proceed")]

    async def test_answer_surfaces_stale_answer_error(self) -> None:
        engine = FakeTaskEngine()
        engine.raise_on_answer = StaleAnswerError("run-1")
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine)

        result = await tool.execute(action="answer", task_id=task.task_id, run_id="run-1", answer="yes")

        assert result.success is False
        assert "not awaiting input" in result.error


class TestUpdate:
    async def test_update_requires_at_least_one_field(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine)

        result = await tool.execute(action="update", task_id=task.task_id)

        assert result.success is False
        assert "at least one field" in result.error
        assert engine.update_calls == []

    async def test_update_forwards_only_changed_fields(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine)

        result = await tool.execute(action="update", task_id=task.task_id, title="New title")

        assert result.success is True
        assert result.data["updated_fields"] == ["title"]
        assert engine.update_calls[0] == {"task_id": task.task_id, "title": "New title"}

    async def test_update_missing_task_surfaces_not_found(self) -> None:
        engine = FakeTaskEngine()
        tool = _make_tool(engine)

        result = await tool.execute(action="update", task_id="nonexistent", title="New title")

        assert result.success is False
        assert "not found" in result.error.lower()


class TestPromoteToAgent:
    async def test_promote_happy_path(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine)

        result = await tool.execute(action="promote_to_agent", task_id=task.task_id)

        assert result.success is True
        assert result.data["promoted_agent_id"] == "agent-123"
        assert engine.promote_calls == [task.task_id]

    async def test_promote_unavailable_without_graph_provider(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine, graph_provider=None)

        result = await tool.execute(action="promote_to_agent", task_id=task.task_id)

        assert result.success is False
        assert "unavailable" in result.error
        assert engine.promote_calls == []

    async def test_promote_unavailable_without_config_service(self) -> None:
        engine = FakeTaskEngine()
        task = _make_task()
        engine.tasks[task.task_id] = task
        tool = _make_tool(engine, config_service=None)

        result = await tool.execute(action="promote_to_agent", task_id=task.task_id)

        assert result.success is False
        assert engine.promote_calls == []
