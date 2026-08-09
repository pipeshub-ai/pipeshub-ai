"""`workflow_find`: Read-only workflow search tool (user-facing vocabulary).

Identical functionality to `task_find` but uses workflow terminology in all
tool descriptions and output keys, so users and the model never see "task".
Both tools are registered during the transition period (Phase 1 → Phase 8);
`workflow_find` is preferred, `task_find` is a time-boxed alias.
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

__all__ = ["WorkflowFindTool"]


def _workflow_overview(task: "TaskDefinition") -> dict[str, Any]:
    return {
        "workflow_id": task.task_id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value,
        "enabled": task.enabled,
        "kind": getattr(task, "execution_kind", "agent_task"),
        "has_steps": bool(task.steps),
        "step_count": len(task.steps) if task.steps else 0,
        "connector_ids": task.connector_ids,
        "collection_ids": task.collection_ids,
        "consecutive_failure_count": task.consecutive_failure_count,
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
        "agent_run_id": getattr(run, "agent_run_id", None),
    }


class WorkflowFindTool(Tool):
    """Read-only. Always scoped to `org_id`; further narrowed to
    `created_by_user_id=user_id` unless `all_users=true` -- a non-admin
    caller resolving "my daily report workflow" should not silently match
    someone else's identically-titled workflow."""

    def __init__(self, engine: "TaskEngine", *, org_id: str, user_id: str) -> None:
        self._engine = engine
        self._org_id = org_id
        self._user_id = user_id

    @property
    def name(self) -> str:
        return "workflow_find"

    @property
    def short_description(self) -> str:
        return "List and search scheduled/recurring workflows you've created."

    @property
    def description(self) -> str:
        return (
            "Find workflows. Pass `workflow_id` for an exact lookup (optionally with "
            "`include_triggers`/`include_runs` for full detail), or `query` to search by "
            "title/description keyword when resolving a natural-language reference "
            "(e.g. 'the daily Jira digest'). With no arguments, lists your recent workflows. "
            "If `query` matches more than one workflow, the response sets "
            "`disambiguation_required: true` -- ask the user which one they mean "
            "(via ask_user_question) rather than guessing."
        )

    @property
    def path(self) -> str:
        return "/toolsets/tasks/workflow_find"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="workflow_id", type=ParameterType.STRING, required=False, description="Exact workflow id for a direct lookup."),
            ToolParameter(name="query", type=ParameterType.STRING, required=False, description="Keyword search across title/description."),
            ToolParameter(
                name="status", type=ParameterType.STRING, required=False,
                description="Filter by lifecycle status.", enum=[s.value for s in TaskStatus],
            ),
            ToolParameter(
                name="all_users", type=ParameterType.BOOLEAN, required=False, default=False,
                description="Search workflows created by anyone in the org, not just you (default false).",
            ),
            ToolParameter(
                name="include_triggers", type=ParameterType.BOOLEAN, required=False, default=False,
                description="[workflow_id only] Include this workflow's schedule/trigger detail.",
            ),
            ToolParameter(
                name="include_runs", type=ParameterType.BOOLEAN, required=False, default=False,
                description="[workflow_id only] Include this workflow's recent run history.",
            ),
            ToolParameter(name="limit", type=ParameterType.INTEGER, required=False, default=20, description="Max results (default 20)."),
        ]

    async def execute(
        self,
        workflow_id: str | None = None,
        query: str | None = None,
        status: str | None = None,
        all_users: bool = False,  # noqa: FBT001, FBT002
        include_triggers: bool = False,  # noqa: FBT001, FBT002
        include_runs: bool = False,  # noqa: FBT001, FBT002
        limit: int = 20,
        **kwargs: object,
    ) -> ToolOutput:
        try:
            if workflow_id:
                return await self._find_one(workflow_id, include_triggers=include_triggers, include_runs=include_runs)
            return await self._search(query=query, status=status, all_users=all_users, limit=limit)
        except TaskEngineError as e:
            return ToolOutput(success=False, error=str(e))

    async def _find_one(self, workflow_id: str, *, include_triggers: bool, include_runs: bool) -> ToolOutput:
        task = await self._engine.get(workflow_id, self._org_id)
        data: dict[str, Any] = {"found": True, "workflow": _workflow_overview(task)}
        if include_triggers:
            triggers = await self._engine.list_triggers(workflow_id)
            data["triggers"] = [_trigger_overview(t) for t in triggers]
        if include_runs:
            page = await self._engine.list_runs(workflow_id, limit=10)
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
        candidates = [_workflow_overview(t) for t in result.items]
        return ToolOutput(success=True, data={
            "workflows": candidates,
            "total": result.total,
            "disambiguation_required": bool(query) and len(candidates) > 1,
        })
