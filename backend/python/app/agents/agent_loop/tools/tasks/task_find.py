"""`task_find`: Part A3's read-only half of task management -- list/filter
existing tasks, returning enough for the model to disambiguate a natural-
language reference ("pause the daily Jira digest") before calling
`task_manage`. Split from the write tool (`task_manage.py`) specifically so
only the write surface needs `Tag("category", "write")` confirmation.

`disambiguation_required` (Part A3: "Confluence returns
`disambiguation_required: true` when 2+ match. `task_find` follows this")
is set ONLY when the caller passed `query` (i.e. is resolving a specific
reference) and more than one task matched -- a plain unfiltered "list my
tasks" call returning many rows is not ambiguous, it is exactly what was
asked for.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agents.agent_loop.tools.tasks._shared import trigger_overview as _trigger_overview
from app.services.tasks.domain.errors import TaskEngineError
from app.services.tasks.domain.models import TaskQuery, TaskStatus

if TYPE_CHECKING:
    from app.services.tasks.application.engine import TaskEngine
    from app.services.tasks.domain.models import TaskDefinition, TaskRun

__all__ = ["TaskFindTool"]


def _task_overview(task: "TaskDefinition") -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value,
        "enabled": task.enabled,
        "loop_strategy_name": task.loop_strategy_name,
        "has_steps": bool(task.steps),
        "step_count": len(task.steps) if task.steps else 0,
        "connector_ids": task.connector_ids,
        "collection_ids": task.collection_ids,
        "consecutive_failure_count": task.consecutive_failure_count,
        "promoted_agent_id": task.promoted_agent_id,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _run_overview(run: "TaskRun") -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "scheduled_for": run.scheduled_for,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "output_summary": run.output_summary,
        "error": run.error,
        "attempt": run.attempt,
    }


class TaskFindTool(Tool):
    """Read-only. Always scoped to `org_id`; further narrowed to
    `created_by_user_id=user_id` unless `all_users=true` -- a non-admin
    caller resolving "my daily report task" should not silently match
    someone else's identically-titled task."""

    def __init__(self, engine: "TaskEngine", *, org_id: str, user_id: str) -> None:
        self._engine = engine
        self._org_id = org_id
        self._user_id = user_id

    @property
    def name(self) -> str:
        return "task_find"

    @property
    def short_description(self) -> str:
        return "List and search scheduled/recurring tasks you've created."

    @property
    def description(self) -> str:
        return (
            "Find scheduled tasks. Pass `task_id` for an exact lookup (optionally with "
            "`include_triggers`/`include_runs` for full detail), or `query` to search by "
            "title/description keyword when resolving a natural-language reference "
            "(e.g. 'the daily Jira digest'). With no arguments, lists your recent tasks. "
            "If `query` matches more than one task, the response sets "
            "`disambiguation_required: true` -- ask the user which one they mean "
            "(via ask_user_question) rather than guessing."
        )

    @property
    def path(self) -> str:
        return "/toolsets/tasks/task_find"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="task_id", type=ParameterType.STRING, required=False, description="Exact task id for a direct lookup."),
            ToolParameter(name="query", type=ParameterType.STRING, required=False, description="Keyword search across title/description."),
            ToolParameter(
                name="status", type=ParameterType.STRING, required=False,
                description="Filter by lifecycle status.", enum=[s.value for s in TaskStatus],
            ),
            ToolParameter(
                name="all_users", type=ParameterType.BOOLEAN, required=False, default=False,
                description="Search tasks created by anyone in the org, not just you (default false).",
            ),
            ToolParameter(
                name="include_triggers", type=ParameterType.BOOLEAN, required=False, default=False,
                description="[task_id only] Include this task's schedule/trigger detail.",
            ),
            ToolParameter(
                name="include_runs", type=ParameterType.BOOLEAN, required=False, default=False,
                description="[task_id only] Include this task's recent run history.",
            ),
            ToolParameter(name="limit", type=ParameterType.INTEGER, required=False, default=20, description="Max results (default 20)."),
        ]

    async def execute(
        self,
        task_id: str | None = None,
        query: str | None = None,
        status: str | None = None,
        all_users: bool = False,  # noqa: FBT001, FBT002 (Tool.execute kwargs mirror declared ToolParameter types 1:1)
        include_triggers: bool = False,  # noqa: FBT001, FBT002
        include_runs: bool = False,  # noqa: FBT001, FBT002
        limit: int = 20,
        **kwargs: object,
    ) -> ToolOutput:
        try:
            if task_id:
                return await self._find_one(task_id, include_triggers=include_triggers, include_runs=include_runs)
            return await self._search(query=query, status=status, all_users=all_users, limit=limit)
        except TaskEngineError as e:
            return ToolOutput(success=False, error=str(e))

    async def _find_one(self, task_id: str, *, include_triggers: bool, include_runs: bool) -> ToolOutput:
        task = await self._engine.get(task_id, self._org_id)
        data: dict[str, Any] = {"found": True, "task": _task_overview(task)}
        if include_triggers:
            triggers = await self._engine.list_triggers(task_id)
            data["triggers"] = [_trigger_overview(t) for t in triggers]
        if include_runs:
            page = await self._engine.list_runs(task_id, limit=10)
            data["runs"] = [_run_overview(r) for r in page.items]
        return ToolOutput(success=True, data=data)

    async def _search(self, *, query: str | None, status: str | None, all_users: bool, limit: int) -> ToolOutput:
        status_enum = TaskStatus(status) if status else None
        result = await self._engine.find(TaskQuery(
            org_id=self._org_id,
            created_by_user_id=None if all_users else self._user_id,
            status=status_enum,
            text_search=query,
            limit=limit,
        ))
        candidates = [_task_overview(t) for t in result.items]
        return ToolOutput(success=True, data={
            "tasks": candidates,
            "total": result.total,
            "disambiguation_required": bool(query) and len(candidates) > 1,
        })
