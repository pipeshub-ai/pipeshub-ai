"""`task_manage`: Part A3's write surface for the task engine (action-
dispatch, same convention as `skill_manage` -- see that tool's own
docstring) -- create, update, pause, resume, cancel, run_now,
promote_to_agent. Tagged `Tag("category", "write")` so approval-style
middleware (and the plan's prompt-level "confirm before creating"
convention -- Part A3: "Confirmation... uses ask_user_question") can gate
on it uniformly with every other write tool in the system.

Deliberately thin: every action is one `TaskEngine` call plus response
shaping. All prerequisite validation, DAG cycle checking, optimistic-
concurrency retry, and trigger scheduling math live in `TaskEngine`
itself (`application/engine.py`) -- this tool has zero business logic of
its own, matching `skill_manage`'s relationship to `SkillManager`.

No `delete` action: `cancel` IS the delete, by design (Part L: "cancel is
soft-delete") -- the row survives for run-history/audit, only its triggers
are removed so it never fires again.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)
from app.agents.agent_loop.tools.tasks._shared import (
    UPDATABLE_FIELDS,
    parse_steps,
    run_codegen,
    tool_name_error,
)
from app.services.tasks.domain.errors import TaskEngineError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.tasks.application.engine import TaskEngine
    from app.services.workflows.codegen.agent import WorkflowBuilderAgent
    from app.services.workflows.interface.code_store import ICodeStore
    from app.services.workflows.interface.version_store import IWorkflowVersionStore

__all__ = ["TaskManageTool"]

logger = logging.getLogger(__name__)

_ACTIONS = ("create", "update", "pause", "resume", "cancel", "run_now", "answer", "promote_to_agent")


class TaskManageTool(Tool):
    def __init__(
        self,
        engine: "TaskEngine",
        *,
        org_id: str,
        user_id: str,
        user_email: str,
        graph_provider: "IGraphDBProvider | None",
        config_service: "ConfigurationService | None",
        conversation_id: str | None = None,
        session_toolset_ids: list[str] | None = None,
        available_tool_names: "Callable[[], Sequence[str]] | None" = None,
        workflow_builder: "WorkflowBuilderAgent | None" = None,
        code_store: "ICodeStore | None" = None,
        version_store: "IWorkflowVersionStore | None" = None,
    ) -> None:
        self._engine = engine
        self._org_id = org_id
        self._user_id = user_id
        self._user_email = user_email
        self._graph_provider = graph_provider
        self._config_service = config_service
        self._conversation_id = conversation_id
        self._session_toolset_ids = session_toolset_ids or []
        self._available_tool_names = available_tool_names
        self._workflow_builder = workflow_builder
        self._code_store = code_store
        self._version_store = version_store

    @property
    def name(self) -> str:
        return "task_manage"

    @property
    def short_description(self) -> str:
        return "Create, update, pause, resume, cancel, run, or promote a scheduled task."

    @property
    def description(self) -> str:
        return (
            "Manage scheduled/recurring tasks. Actions:\n"
            "- create: requires title, description, instructions. Optional: steps (a "
            "decomposed sub-task DAG -- see `steps` param), tool_names, collection_ids, "
            "connector_ids, model_ref, max_turns, timeout_seconds, triggers (schedule(s) "
            "-- see `triggers` param), clarifications, reasoning. Before calling with "
            "`triggers`, confirm the interpreted schedule with the user in plain language, "
            "and confirm the decomposition for a multi-step task -- do not create silently.\n"
            "- update: requires task_id; any of title/description/instructions/tool_names/"
            "collection_ids/connector_ids/model_ref/max_turns/timeout_seconds as partial updates.\n"
            "- pause / resume / cancel / run_now: requires task_id.\n"
            "- answer: requires task_id, run_id, and answer -- responds to a run that is "
            "AWAITING_INPUT (see task_find's include_runs) so it can resume. If the run is no "
            "longer awaiting input (already answered, or moved on), this fails cleanly -- "
            "re-check the run's current status with task_find rather than retrying blindly.\n"
            "- promote_to_agent: requires task_id -- creates a standalone Agent Builder agent "
            "from this task's instructions, tools, and knowledge scope (one-way copy)."
        )

    @property
    def path(self) -> str:
        return "/toolsets/tasks/task_manage"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("category", "write")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="action", type=ParameterType.STRING, required=True, description="One of: create, update, pause, resume, cancel, run_now, answer, promote_to_agent.", enum=list(_ACTIONS)),
            ToolParameter(name="task_id", type=ParameterType.STRING, required=False, description="Required for every action except create."),
            ToolParameter(name="run_id", type=ParameterType.STRING, required=False, description="[answer] The run id currently AWAITING_INPUT."),
            ToolParameter(name="answer", type=ParameterType.STRING, required=False, description="[answer] Your reply to the run's question, verbatim."),
            ToolParameter(name="title", type=ParameterType.STRING, required=False, description="[create/update] Short task name."),
            ToolParameter(name="description", type=ParameterType.STRING, required=False, description="[create/update] Original natural-language request, verbatim."),
            ToolParameter(name="instructions", type=ParameterType.STRING, required=False, description="[create/update] Fully assembled prompt the agent executes -- fold in any clarifications and your own reasoning about scope, not just the user's raw words."),
            ToolParameter(
                name="steps", type=ParameterType.ARRAY, required=False, default=None,
                description=(
                    "[create] Optional sub-task DAG for a multi-step task. Each step: "
                    "{id: string, description: string, domain: string, tool_names: [string], "
                    "depends_on: [string]}. `id` must be unique; `depends_on` lists ids of "
                    "steps that must complete first. Omit for a single-goal task."
                ),
                items={"type": "object"},
            ),
            ToolParameter(name="tool_names", type=ParameterType.ARRAY, required=False, description="[create/update] Exact tool names this task is allowed to use, copied verbatim from the tool list available to you this turn (e.g. 'dynamic__web_search', not 'web search'). Unknown names are rejected. Leave empty only when the task needs no tools.", items={"type": "string"}),
            ToolParameter(name="collection_ids", type=ParameterType.ARRAY, required=False, description="[create/update] Knowledge-base collection ids this task may read.", items={"type": "string"}),
            ToolParameter(name="connector_ids", type=ParameterType.ARRAY, required=False, description="[create/update] Connector instance ids this task depends on (checked at creation and before every run).", items={"type": "string"}),
            ToolParameter(name="model_ref", type=ParameterType.STRING, required=False, description="[create/update] Pin a specific configured model (modelKey); omit to use the org default."),
            ToolParameter(name="max_turns", type=ParameterType.INTEGER, required=False, description="[create/update] Max agent turns per run."),
            ToolParameter(name="timeout_seconds", type=ParameterType.INTEGER, required=False, description="[create/update] Wall-clock timeout per run."),
            ToolParameter(
                name="triggers", type=ParameterType.ARRAY, required=False, default=None,
                description=(
                    "[create] One or more schedules for this task. Each object's 'kind' field "
                    "is REQUIRED -- one of 'one_time'|'cron'|'interval'|'event'|'webhook' -- "
                    "and determines which other field is also required: cron_expression (cron), "
                    "interval_seconds (interval), fire_at (one_time -- ISO-8601 string), "
                    "event_filter.event_type (event, e.g. {event_type: 'record.created', "
                    "connectorId: 'conn-1'}). 'webhook' needs no extra fields -- a webhook_id "
                    "and one-time-revealed secret are generated automatically; show the "
                    "returned webhook URL and secret to the user immediately, since the secret "
                    "cannot be retrieved again. Optional on every kind: timezone (default "
                    "'UTC'), misfire_policy: 'skip'|'run_once'|'run_all' (default 'skip'), "
                    "max_runs: int. Example one-time trigger: {\"kind\": \"one_time\", "
                    "\"fire_at\": \"2026-01-01T09:00:00Z\"}. Confirm the interpreted schedule "
                    "in plain language with the user before creating. Omit entirely for a task "
                    "only ever run via run_now."
                ),
                items={
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["one_time", "cron", "interval", "event", "webhook"],
                            "description": "Required. Selects which other field(s) apply.",
                        },
                        "cron_expression": {"type": "string", "description": "Required when kind='cron'. Standard 5-field cron expression."},
                        "interval_seconds": {"type": "integer", "description": "Required when kind='interval'. Must be > 0."},
                        "fire_at": {"type": "string", "description": "Required when kind='one_time'. ISO-8601 timestamp, e.g. '2026-01-01T09:00:00Z'."},
                        "timezone": {"type": "string", "description": "IANA timezone name. Defaults to 'UTC'."},
                        "misfire_policy": {"type": "string", "enum": ["skip", "run_once", "run_all"], "description": "Defaults to 'skip'."},
                        "max_runs": {"type": "integer", "description": "Optional cap on total firings."},
                        "event_filter": {
                            "type": "object",
                            "description": "Required when kind='event'. Must include 'event_type'.",
                        },
                    },
                    "required": ["kind"],
                },
            ),
            ToolParameter(name="clarifications", type=ParameterType.ARRAY, required=False, description="[create] {question, answer} pairs gathered while scoping this task.", items={"type": "object"}),
            ToolParameter(name="reasoning", type=ParameterType.STRING, required=False, description="[create] Your own reasoning about decomposition/schedule, kept for audit."),
        ]

    async def execute(
        self,
        action: str,
        task_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
        steps: "list[dict[str, Any]] | None" = None,
        tool_names: list[str] | None = None,
        collection_ids: list[str] | None = None,
        connector_ids: list[str] | None = None,
        model_ref: str | None = None,
        max_turns: int | None = None,
        timeout_seconds: int | None = None,
        triggers: "list[dict[str, Any]] | None" = None,
        clarifications: "list[dict[str, Any]] | None" = None,
        reasoning: str | None = None,
        run_id: str | None = None,
        answer: str | None = None,
        **kwargs: object,
    ) -> ToolOutput:
        try:
            if action == "create":
                return await self._create(
                    title=title, description=description, instructions=instructions,
                    steps=steps, tool_names=tool_names, collection_ids=collection_ids,
                    connector_ids=connector_ids, model_ref=model_ref, max_turns=max_turns,
                    timeout_seconds=timeout_seconds, triggers=triggers,
                    clarifications=clarifications, reasoning=reasoning,
                )
            if not task_id:
                return ToolOutput(success=False, error=f"'{action}' requires 'task_id'")
            if action == "update":
                return await self._update(
                    task_id, title=title, description=description, instructions=instructions,
                    tool_names=tool_names, collection_ids=collection_ids, connector_ids=connector_ids,
                    model_ref=model_ref, max_turns=max_turns, timeout_seconds=timeout_seconds,
                )
            if action == "pause":
                task = await self._engine.pause(task_id, self._org_id)
                return ToolOutput(success=True, data={"task_id": task.task_id, "status": task.status.value})
            if action == "resume":
                task = await self._engine.unpause(task_id, self._org_id)
                return ToolOutput(success=True, data={"task_id": task.task_id, "status": task.status.value})
            if action == "cancel":
                task = await self._engine.cancel(task_id, self._org_id)
                return ToolOutput(success=True, data={"task_id": task.task_id, "status": task.status.value})
            if action == "run_now":
                run = await self._engine.run_now(task_id, self._org_id)
                return ToolOutput(success=True, data={"task_id": task_id, "run_id": run.run_id, "status": run.status.value})
            if action == "answer":
                if not run_id or not answer:
                    return ToolOutput(success=False, error="'answer' requires 'run_id' and 'answer'")
                resumed = await self._engine.answer_run(run_id, task_id, self._org_id, answer)
                return ToolOutput(success=True, data={"task_id": task_id, "run_id": resumed.run_id, "status": resumed.status.value})
            if action == "promote_to_agent":
                return await self._promote_to_agent(task_id)
            return ToolOutput(success=False, error=f"Unknown action: {action!r}")
        except ValueError as e:
            return ToolOutput(success=False, error=str(e))
        except TaskEngineError as e:
            return ToolOutput(success=False, error=str(e))

    async def _create(
        self,
        *,
        title: str | None,
        description: str | None,
        instructions: str | None,
        steps: "list[dict[str, Any]] | None",
        tool_names: list[str] | None,
        collection_ids: list[str] | None,
        connector_ids: list[str] | None,
        model_ref: str | None,
        max_turns: int | None,
        timeout_seconds: int | None,
        triggers: "list[dict[str, Any]] | None",
        clarifications: "list[dict[str, Any]] | None",
        reasoning: str | None,
    ) -> ToolOutput:
        if not title or not description or not instructions:
            return ToolOutput(success=False, error="'create' requires 'title', 'description', and 'instructions'")
        tool_error = tool_name_error(tool_names, self._available_tool_names)
        if tool_error:
            return ToolOutput(success=False, error=tool_error)
        parsed_steps = parse_steps(steps)

        kwargs: dict[str, Any] = {}
        if max_turns is not None:
            kwargs["max_turns"] = max_turns
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds

        task, created_triggers, check_result, webhook_secrets = await self._engine.create(
            org_id=self._org_id, user_id=self._user_id, user_email=self._user_email,
            title=title, description=description, instructions=instructions,
            steps=parsed_steps, tool_names=tool_names or [], collection_ids=collection_ids or [],
            connector_ids=connector_ids or [], model_ref=model_ref, triggers=triggers,
            toolset_ids=self._session_toolset_ids or None,
            clarifications=clarifications, reasoning=reasoning,
            created_from_conversation_id=self._conversation_id,
            graph_provider=self._graph_provider,
            config_service=self._config_service,
            **kwargs,
        )
        data: dict[str, Any] = {
            "task_id": task.task_id,
            "title": task.title,
            "status": task.status.value,
            "triggers": [
                {
                    "trigger_id": t.trigger_id, "kind": t.kind.value, "next_run_at": t.next_run_at,
                    **({"webhook_id": t.webhook_id, "webhook_secret": webhook_secrets[t.trigger_id]}
                       if t.trigger_id in webhook_secrets else {}),
                }
                for t in created_triggers
            ],
        }
        if webhook_secrets:
            data["webhook_secret_warning"] = (
                "The webhook secret(s) above are shown ONCE and cannot be retrieved again -- "
                "relay them to the user now."
            )
        if check_result is not None and check_result.issues:
            data["prerequisite_notes"] = check_result.summary()

        if self._workflow_builder and self._code_store and self._version_store:
            codegen_result = await run_codegen(
                workflow_builder=self._workflow_builder,
                code_store=self._code_store,
                version_store=self._version_store,
                engine=self._engine,
                org_id=self._org_id,
                user_id=self._user_id,
                workflow_id=task.task_id,
                spec=instructions,
                tool_names=tool_names,
            )
            if codegen_result.get("ok"):
                data["execution_kind"] = "code"
                data["codegen_version_id"] = codegen_result.get("version_id")
                if not codegen_result.get("pinned"):
                    data["codegen_note"] = (
                        f"Code generated and saved but pin failed: {codegen_result.get('failure_reason')}"
                    )
            else:
                data["execution_kind"] = "agent_task"
                data["codegen_note"] = (
                    f"Code generation attempted but failed: {codegen_result.get('failure_reason')}. "
                    "Running as agent_task."
                )
        else:
            data["execution_kind"] = "agent_task"

        return ToolOutput(success=True, data=data)

    async def _update(
        self,
        task_id: str,
        *,
        title: str | None,
        description: str | None,
        instructions: str | None,
        tool_names: list[str] | None,
        collection_ids: list[str] | None,
        connector_ids: list[str] | None,
        model_ref: str | None,
        max_turns: int | None,
        timeout_seconds: int | None,
    ) -> ToolOutput:
        tool_error = tool_name_error(tool_names, self._available_tool_names)
        if tool_error:
            return ToolOutput(success=False, error=tool_error)
        candidate = {
            "title": title, "description": description, "instructions": instructions,
            "tool_names": tool_names, "collection_ids": collection_ids, "connector_ids": connector_ids,
            "model_ref": model_ref, "max_turns": max_turns, "timeout_seconds": timeout_seconds,
        }
        fields = {k: v for k, v in candidate.items() if v is not None and k in UPDATABLE_FIELDS}
        if not fields:
            return ToolOutput(success=False, error="'update' requires at least one field to change")
        task = await self._engine.update_fields(task_id, self._org_id, **fields)
        return ToolOutput(success=True, data={"task_id": task.task_id, "updated_fields": list(fields.keys())})

    async def _promote_to_agent(self, task_id: str) -> ToolOutput:
        if self._graph_provider is None or self._config_service is None:
            return ToolOutput(success=False, error="promote_to_agent is unavailable: no graph provider/config service on this request")
        agent_id = await self._engine.promote_to_agent(
            task_id, self._org_id, graph_provider=self._graph_provider, config_service=self._config_service,
        )
        return ToolOutput(success=True, data={"task_id": task_id, "promoted_agent_id": agent_id})
