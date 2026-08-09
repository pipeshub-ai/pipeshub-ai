"""`workflow_manage`: Write surface for workflow lifecycle management.

Delegates to the same TaskEngine methods as `task_manage` but exposes
workflow vocabulary to the model. During the Phase 1 transition period both
tools exist; `workflow_manage` is the preferred surface going forward.

Actions:
  create          — create a new workflow (with optional triggers)
  update          — edit title/description/instructions and other fields
  pause           — pause all triggers
  resume          — unpause all triggers
  cancel          — soft-delete: removes triggers so the workflow never fires
  run_now         — execute a workflow immediately (returns run_id)
  answer          — respond to a run that is AWAITING_INPUT
  delete          — alias for cancel (same soft-delete semantics)
  subscribe       — subscribe to an app event with optional filter predicates (Phase 4 stub)
  unsubscribe     — remove an event subscription (Phase 4 stub)
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
    tool_name_issues,
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

__all__ = ["WorkflowManageTool"]

logger = logging.getLogger(__name__)


_ACTIONS = (
    "create", "update", "pause", "resume", "cancel", "run_now", "dry_run",
    "validate", "answer", "delete", "subscribe", "unsubscribe",
)


class WorkflowManageTool(Tool):
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
        # Optional codegen dependencies (wired in tasks_wiring.py).
        # When None the tool creates agent_task workflows (the pre-codegen path).
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
        return "workflow_manage"

    @property
    def short_description(self) -> str:
        return "Create, update, pause, resume, cancel, run, or subscribe a workflow."

    @property
    def description(self) -> str:
        return (
            "Manage scheduled/recurring workflows. Actions:\n"
            "- validate: MUST be called before create when tool_names, connector_ids, "
            "collection_ids, or triggers are specified. Checks that every declared tool "
            "name exists in this session, all declared connectors are authenticated, all "
            "collections are accessible, and all triggers are schedulable. Returns a "
            "structured list of issues (blocking or informational). No side effects. "
            "If validate returns blocking issues, resolve them before calling create.\n"
            "- create: requires title, description, instructions. Optional: steps (a "
            "decomposed sub-task DAG -- see `steps` param), tool_names, collection_ids, "
            "connector_ids, model_ref, max_turns, timeout_seconds, triggers (schedule(s) "
            "-- see `triggers` param), clarifications, reasoning. Before calling with "
            "`triggers`, confirm the interpreted schedule with the user in plain language, "
            "and confirm the decomposition for a multi-step workflow -- do not create silently.\n"
            "- update: requires workflow_id; any of title/description/instructions/tool_names/"
            "collection_ids/connector_ids/model_ref/max_turns/timeout_seconds as partial updates.\n"
            "- pause / resume / cancel / run_now / delete: requires workflow_id.\n"
            "- answer: requires workflow_id, run_id, and answer -- responds to a run that is "
            "AWAITING_INPUT (see workflow_find's include_runs) so it can resume.\n"
            "- subscribe: requires workflow_id and event_type -- subscribes to an app event "
            "(Phase 4 stub; creates an EVENT trigger).\n"
            "- unsubscribe: requires workflow_id -- removes an event subscription (Phase 4 stub)."
        )

    @property
    def path(self) -> str:
        return "/toolsets/tasks/workflow_manage"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("category", "write")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="action", type=ParameterType.STRING, required=True, description="One of: create, update, pause, resume, cancel, run_now, dry_run, validate, answer, delete, subscribe, unsubscribe. Use validate before create to check prerequisites (connectors, collections, schedule feasibility). Use dry_run to test a workflow without executing WRITE steps or sending notifications.", enum=list(_ACTIONS)),
            ToolParameter(name="workflow_id", type=ParameterType.STRING, required=False, description="Required for every action except create."),
            ToolParameter(name="run_id", type=ParameterType.STRING, required=False, description="[answer] The run id currently AWAITING_INPUT."),
            ToolParameter(name="answer", type=ParameterType.STRING, required=False, description="[answer] Your reply to the run's question, verbatim."),
            ToolParameter(name="title", type=ParameterType.STRING, required=False, description="[create/update] Short workflow name."),
            ToolParameter(name="description", type=ParameterType.STRING, required=False, description="[create/update] Original natural-language request, verbatim."),
            ToolParameter(name="instructions", type=ParameterType.STRING, required=False, description="[create/update] Fully assembled prompt the agent executes -- fold in any clarifications and your own reasoning about scope."),
            ToolParameter(
                name="steps", type=ParameterType.ARRAY, required=False, default=None,
                description=(
                    "[create] Optional sub-task DAG for a multi-step workflow. Each step: "
                    "{id: string, description: string, domain: string, tool_names: [string], "
                    "depends_on: [string]}. `id` must be unique; `depends_on` lists ids of "
                    "steps that must complete first. Omit for a single-goal workflow."
                ),
                items={"type": "object"},
            ),
            ToolParameter(name="tool_names", type=ParameterType.ARRAY, required=False, description="[create/update/validate] Exact tool names this workflow is allowed to use, copied verbatim from the tool list available to you this turn (e.g. 'dynamic__web_search', not 'web search'). Unknown names are rejected. Leave empty only when the workflow needs no tools.", items={"type": "string"}),
            ToolParameter(name="collection_ids", type=ParameterType.ARRAY, required=False, description="[create/update] Knowledge-base collection ids this workflow may read.", items={"type": "string"}),
            ToolParameter(name="connector_ids", type=ParameterType.ARRAY, required=False, description="[create/update] Connector instance ids this workflow depends on.", items={"type": "string"}),
            ToolParameter(name="model_ref", type=ParameterType.STRING, required=False, description="[create/update] Pin a specific configured model (modelKey); omit to use the org default."),
            ToolParameter(name="max_turns", type=ParameterType.INTEGER, required=False, description="[create/update] Max agent turns per run."),
            ToolParameter(name="timeout_seconds", type=ParameterType.INTEGER, required=False, description="[create/update] Wall-clock timeout per run."),
            ToolParameter(
                name="triggers", type=ParameterType.ARRAY, required=False, default=None,
                description=(
                    "[create] One or more schedules for this workflow. Each object's 'kind' "
                    "field is REQUIRED -- one of 'one_time'|'cron'|'interval'|'event'|'webhook' "
                    "-- and determines which other field is also required: cron_expression "
                    "(cron), interval_seconds (interval), fire_at (one_time -- ISO-8601 "
                    "string), event_filter.event_type (event). 'webhook' needs no extra "
                    "fields -- a webhook_id and one-time-revealed secret are generated "
                    "automatically -- show the returned URL and secret to the user "
                    "immediately. Optional on every kind: timezone (default 'UTC'), "
                    "misfire_policy: 'skip'|'run_once'|'run_all' (default 'skip'), max_runs: "
                    "int. Example one-time trigger: {\"kind\": \"one_time\", \"fire_at\": "
                    "\"2026-01-01T09:00:00Z\"}. Confirm the schedule in plain language before "
                    "creating. Omit entirely for a workflow only ever run via run_now."
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
            ToolParameter(name="event_type", type=ParameterType.STRING, required=False, description="[subscribe] The event type to subscribe to (e.g. 'record.created')."),
            ToolParameter(name="filter", type=ParameterType.OBJECT, required=False, description="[subscribe] Filter predicates as a dict ({field, op, value})."),
            ToolParameter(name="clarifications", type=ParameterType.ARRAY, required=False, description="[create] {question, answer} pairs gathered while scoping this workflow.", items={"type": "object"}),
            ToolParameter(name="reasoning", type=ParameterType.STRING, required=False, description="[create] Your own reasoning about decomposition/schedule, kept for audit."),
        ]

    async def execute(
        self,
        action: str,
        workflow_id: str | None = None,
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
        event_type: str | None = None,
        filter: "dict[str, Any] | None" = None,  # noqa: A002
        **kwargs: object,
    ) -> ToolOutput:
        try:
            logger.info("workflow_manage: action=%s workflow_id=%s", action, workflow_id or "(new)")
            if action == "validate":
                return await self._validate(
                    connector_ids=connector_ids, collection_ids=collection_ids,
                    triggers=triggers, tool_names=tool_names,
                )
            if action == "create":
                return await self._create(
                    title=title, description=description, instructions=instructions,
                    steps=steps, tool_names=tool_names, collection_ids=collection_ids,
                    connector_ids=connector_ids, model_ref=model_ref, max_turns=max_turns,
                    timeout_seconds=timeout_seconds, triggers=triggers,
                    clarifications=clarifications, reasoning=reasoning,
                )
            if not workflow_id:
                return ToolOutput(success=False, error=f"'{action}' requires 'workflow_id'")
            if action == "update":
                return await self._update(
                    workflow_id, title=title, description=description, instructions=instructions,
                    tool_names=tool_names, collection_ids=collection_ids, connector_ids=connector_ids,
                    model_ref=model_ref, max_turns=max_turns, timeout_seconds=timeout_seconds,
                )
            if action == "pause":
                task = await self._engine.pause(workflow_id, self._org_id)
                return ToolOutput(success=True, data={"workflow_id": task.task_id, "status": task.status.value})
            if action == "resume":
                task = await self._engine.unpause(workflow_id, self._org_id)
                return ToolOutput(success=True, data={"workflow_id": task.task_id, "status": task.status.value})
            if action in {"cancel", "delete"}:
                task = await self._engine.cancel(workflow_id, self._org_id)
                await self._link_workflow_to_conversation(
                    workflow_id, action="remove",
                    conversation_id=task.created_from_conversation_id,
                )
                return ToolOutput(success=True, data={"workflow_id": task.task_id, "status": task.status.value})
            if action == "run_now":
                run = await self._engine.run_now(workflow_id, self._org_id)
                return ToolOutput(success=True, data={"workflow_id": workflow_id, "run_id": run.run_id, "status": run.status.value})
            if action == "dry_run":
                run = await self._engine.dry_run(workflow_id, self._org_id)
                return ToolOutput(success=True, data={"workflow_id": workflow_id, "run_id": run.run_id, "status": run.status.value, "is_dry_run": True})
            if action == "answer":
                if not run_id or not answer:
                    return ToolOutput(success=False, error="'answer' requires 'run_id' and 'answer'")
                resumed = await self._engine.answer_run(run_id, workflow_id, self._org_id, answer)
                return ToolOutput(success=True, data={"workflow_id": workflow_id, "run_id": resumed.run_id, "status": resumed.status.value})
            if action == "subscribe":
                return await self._subscribe(workflow_id, event_type=event_type, filter_data=filter)
            if action == "unsubscribe":
                return ToolOutput(success=True, data={"workflow_id": workflow_id, "unsubscribed": True, "note": "Phase 4 will implement trigger removal by event_type."})
            return ToolOutput(success=False, error=f"Unknown action: {action!r}")
        except ValueError as e:
            return ToolOutput(success=False, error=str(e))
        except TaskEngineError as e:
            return ToolOutput(success=False, error=str(e))

    async def _validate(
        self,
        *,
        connector_ids: list[str] | None,
        collection_ids: list[str] | None,
        triggers: "list[dict[str, Any]] | None",
        tool_names: list[str] | None = None,
    ) -> ToolOutput:
        """Read-only prerequisite check. Validates tool names, connectors,
        collections, and schedule feasibility without creating anything.
        Returns a structured issues list the agent can surface to the user
        before calling create."""
        import datetime as _dt

        issues: list[dict[str, Any]] = tool_name_issues(tool_names, self._available_tool_names)

        if connector_ids or collection_ids or self._session_toolset_ids:
            if self._graph_provider is None:
                return ToolOutput(success=False, error="Cannot validate: no graph provider available in this context.")
            from app.services.tasks.application.prerequisites import (
                PrerequisiteValidator,
            )
            result = await PrerequisiteValidator().validate(
                org_id=self._org_id,
                user_id=self._user_id,
                connector_ids=connector_ids or [],
                collection_ids=collection_ids or [],
                mcp_server_ids=[],
                # Same set `_create` will persist, so the pre-flight and the
                # creation check can't disagree.
                toolset_ids=self._session_toolset_ids,
                graph_provider=self._graph_provider,
                config_service=self._config_service,
            )
            for issue in result.issues:
                issues.append({
                    "kind": issue.kind,
                    "id": issue.id,
                    "reason": issue.reason,
                    "blocking": issue.blocking,
                })

        if triggers:
            now_utc = _dt.datetime.now(_dt.timezone.utc)
            for trigger in triggers:
                kind = trigger.get("kind")
                if kind == "one_time":
                    fire_at_str = trigger.get("fire_at")
                    if fire_at_str:
                        try:
                            fire_at = _dt.datetime.fromisoformat(
                                fire_at_str.replace("Z", "+00:00")
                            )
                            if fire_at.tzinfo is None:
                                fire_at = fire_at.replace(tzinfo=_dt.timezone.utc)
                            if fire_at <= now_utc:
                                # Under the default 'skip' policy the engine
                                # refuses this outright (no computable next
                                # fire), so reporting it as non-blocking would
                                # send the agent into a create that 400s.
                                skips = trigger.get("misfire_policy", "skip") == "skip"
                                issues.append({
                                    "kind": "schedule",
                                    "id": f"one_time:{fire_at_str}",
                                    "reason": (
                                        f"fire_at {fire_at_str!r} is in the past. "
                                        + ("Pick a future time." if skips else
                                           "With this misfire_policy it will fire immediately.")
                                    ),
                                    "blocking": skips,
                                })
                        except ValueError:
                            issues.append({
                                "kind": "schedule",
                                "id": f"one_time:{fire_at_str}",
                                "reason": f"fire_at {fire_at_str!r} is not a valid ISO-8601 timestamp.",
                                "blocking": True,
                            })

        blocking = [i for i in issues if i.get("blocking")]
        return ToolOutput(success=True, data={
            "action": "validate",
            "ok": len(blocking) == 0,
            "issues": issues,
            "summary": (
                "All prerequisites satisfied."
                if not issues
                else "; ".join(f'{i["kind"]} {i["id"]!r}: {i["reason"]}' for i in issues)
            ),
        })

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

        extra: dict[str, Any] = {}
        if max_turns is not None:
            extra["max_turns"] = max_turns
        if timeout_seconds is not None:
            extra["timeout_seconds"] = timeout_seconds

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
            **extra,
        )
        data: dict[str, Any] = {
            "workflow_id": task.task_id,
            "title": task.title,
            "status": task.status.value,
            "execution_kind": task.execution_kind,
            "tool_names": list(task.tool_names) if task.tool_names else [],
            "connector_ids": list(task.connector_ids) if task.connector_ids else [],
            "collection_ids": list(task.collection_ids) if task.collection_ids else [],
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

        codegen_deps_ok = bool(self._workflow_builder and self._code_store and self._version_store)
        logger.info(
            "workflow_manage._create: workflow=%s codegen_deps_available=%s "
            "(builder=%s code_store=%s version_store=%s)",
            task.task_id, codegen_deps_ok,
            type(self._workflow_builder).__name__ if self._workflow_builder else "None",
            type(self._code_store).__name__ if self._code_store else "None",
            type(self._version_store).__name__ if self._version_store else "None",
        )
        if codegen_deps_ok:
            codegen_result = await self._run_codegen(
                workflow_id=task.task_id,
                spec=instructions,
                tool_names=tool_names,
            )
            logger.info(
                "workflow_manage._create: codegen result for workflow=%s ok=%s pinned=%s failure_reason=%s",
                task.task_id, codegen_result.get("ok"), codegen_result.get("pinned"),
                codegen_result.get("failure_reason", "none"),
            )
            if codegen_result.get("ok") and codegen_result.get("pinned"):
                data["execution_kind"] = "code"
                data["workflow_version_id"] = codegen_result.get("version_id")
            elif codegen_result.get("ok"):
                # Generated and saved, but not activated -- still visible via
                # /versions and can be activated manually.
                data["workflow_version_id"] = codegen_result.get("version_id")
                data["codegen_note"] = (
                    f"Code generation succeeded (version {codegen_result.get('version_id')}) "
                    f"but could not be activated: {codegen_result.get('failure_reason')}. "
                    "The generated code is saved and can be activated from the workflow's "
                    "version history."
                )
            else:
                data["codegen_note"] = (
                    f"Code generation failed: {codegen_result.get('failure_reason', 'unknown error')}. "
                    "Workflow will run in agent mode. Use workflow_manage(action='update') to "
                    "retry once the issue is resolved."
                )
        else:
            # Codegen was never attempted -- distinct from "attempted and
            # failed" above. Without this, a workflow can sit in agent_task
            # mode forever with nothing in the logs or chat response
            # explaining why, which is exactly what made the missing-code
            # bug undiagnosable.
            missing = [
                name for name, val in (
                    ("workflow_builder", self._workflow_builder),
                    ("code_store", self._code_store),
                    ("version_store", self._version_store),
                )
                if val is None
            ]
            logger.warning(
                "Codegen not attempted for new workflow %s: missing %s",
                task.task_id, ", ".join(missing),
            )

        # Best-effort: link the workflow to the originating conversation so
        # the WorkflowPanel can query by conversation_id without a Python round-trip.
        if self._conversation_id and self._config_service:
            await self._link_workflow_to_conversation(task.task_id, action="add")

        return ToolOutput(success=True, data=data)

    async def _update(
        self,
        workflow_id: str,
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
        task = await self._engine.update_fields(workflow_id, self._org_id, **fields)
        result: dict[str, Any] = {"workflow_id": task.task_id, "updated_fields": list(fields.keys())}

        # Regenerate code when instructions or tools changed.
        _regen_fields = {"instructions", "tool_names"}
        if self._workflow_builder and self._code_store and self._version_store and (
            _regen_fields & set(fields.keys())
        ):
            existing_source: str | None = None
            if task.workflow_version_id and self._version_store:
                try:
                    ver = await self._version_store.get(task.workflow_version_id, self._org_id)
                    if ver and ver.bundle_ref:
                        src_bytes = await self._code_store.get(ver.bundle_ref)
                        existing_source = src_bytes.decode("utf-8")
                except Exception:
                    # Regenerating from scratch here would silently discard the
                    # user's current code on what they asked for as an
                    # incremental edit; keep the pinned version instead.
                    logger.exception(
                        "Could not load existing source for workflow %s; skipping regeneration",
                        workflow_id,
                    )
                    result["codegen_note"] = (
                        "Existing workflow code could not be loaded, so it was left unchanged. "
                        "Retry the edit, or ask to regenerate the workflow from scratch."
                    )
                    return ToolOutput(success=True, data=result)
                if existing_source is None:
                    logger.warning(
                        "Workflow %s pins version %s with no source bundle; skipping regeneration",
                        workflow_id, task.workflow_version_id,
                    )
                    result["codegen_note"] = (
                        "The pinned workflow version has no stored source, so the code was left unchanged."
                    )
                    return ToolOutput(success=True, data=result)

            codegen_result = await self._run_codegen(
                workflow_id=task.task_id,
                spec=task.instructions,
                tool_names=list(task.tool_names) if task.tool_names else None,
                existing_source=existing_source,
            )
            if codegen_result.get("ok") and codegen_result.get("pinned"):
                result["workflow_version_id"] = codegen_result.get("version_id")
                result["regenerated"] = True
            elif codegen_result.get("ok"):
                result["workflow_version_id"] = codegen_result.get("version_id")
                result["codegen_note"] = (
                    f"Code regenerated (version {codegen_result.get('version_id')}) but could "
                    f"not be activated: {codegen_result.get('failure_reason')}. The previously "
                    "active version remains in effect; activate the new one from version history."
                )
            else:
                result["codegen_note"] = (
                    f"Code regeneration failed: {codegen_result.get('failure_reason', 'unknown error')}; "
                    "existing code version retained."
                )
        elif _regen_fields & set(fields.keys()):
            # The user changed instructions/tools -- which normally
            # regenerates code -- but codegen deps aren't wired for this
            # turn. Silently doing nothing here is the same trap as the
            # `_create` skip path: no log, no note, no way to tell "not
            # attempted" apart from "attempted and failed".
            missing = [
                name for name, val in (
                    ("workflow_builder", self._workflow_builder),
                    ("code_store", self._code_store),
                    ("version_store", self._version_store),
                )
                if val is None
            ]
            logger.warning(
                "Codegen not attempted for workflow %s update: missing %s",
                workflow_id, ", ".join(missing),
            )

        return ToolOutput(success=True, data=result)

    async def _run_codegen(
        self,
        *,
        workflow_id: str,
        spec: str | None,
        tool_names: list[str] | None = None,
        existing_source: str | None = None,
    ) -> dict[str, Any]:
        """Delegate to the shared ``run_codegen`` helper in ``_shared``."""
        if not spec or self._code_store is None or self._version_store is None or self._workflow_builder is None:
            missing = [
                name for name, val in (
                    ("spec", spec), ("workflow_builder", self._workflow_builder),
                    ("code_store", self._code_store), ("version_store", self._version_store),
                )
                if not val
            ]
            logger.warning("Codegen skipped for workflow %s: missing %s", workflow_id, ", ".join(missing))
            return {"ok": False, "pinned": False, "failure_reason": "No instructions or code/version store configured"}
        return await run_codegen(
            workflow_builder=self._workflow_builder,
            code_store=self._code_store,
            version_store=self._version_store,
            engine=self._engine,
            org_id=self._org_id,
            user_id=self._user_id,
            workflow_id=workflow_id,
            spec=spec,
            tool_names=tool_names,
            existing_source=existing_source,
        )

    async def _link_workflow_to_conversation(
        self, workflow_id: str, *, action: str, conversation_id: str | None = None,
    ) -> None:
        """Best-effort: update `connectedWorkflowIds` on the originating conversation.
        Swallows all exceptions so create/cancel are never blocked by a write-back failure.

        `conversation_id` defaults to the current session's, which is only
        correct for `add`. Removal must pass the workflow's own
        `created_from_conversation_id`, since a workflow can be cancelled from
        any conversation and unlinking it from the wrong one leaves it listed
        forever in the panel of the conversation that created it.
        """
        conversation_id = conversation_id or self._conversation_id
        if not conversation_id or not self._config_service:
            return
        try:
            from app.services.workflows.adapters.node.conversation_writer import (
                build_node_conversation_writer,
            )
            writer = await build_node_conversation_writer(self._config_service)
            if writer is None:
                return
            try:
                if action == "add":
                    await writer.link_workflow(conversation_id, self._org_id, workflow_id)
                else:
                    await writer.unlink_workflow(conversation_id, self._org_id, workflow_id)
            finally:
                await writer.aclose()
        except Exception:
            logger.exception(
                "_link_workflow_to_conversation: failed for conversation=%s workflow=%s action=%s",
                conversation_id, workflow_id, action,
            )

    async def _subscribe(
        self,
        workflow_id: str,
        *,
        event_type: str | None,
        filter_data: "dict[str, Any] | None",
    ) -> ToolOutput:
        """Create an EVENT trigger for this workflow."""
        if not event_type:
            return ToolOutput(success=False, error="'subscribe' requires 'event_type'")

        event_filter: dict[str, Any] = {"event_type": event_type}
        if isinstance(filter_data, dict):
            event_filter.update(filter_data)

        # `add_trigger(task_id, org_id, spec, *, trigger_id=None)`: a spec
        # dict, not a `TaskTrigger`, so it goes through the same validation and
        # `next_run_at` computation `create` uses.
        trigger, _ = await self._engine.add_trigger(
            workflow_id, self._org_id, {"kind": "event", "event_filter": event_filter},
        )
        return ToolOutput(success=True, data={
            "workflow_id": workflow_id,
            "trigger_id": trigger.trigger_id,
            "event_type": event_type,
            "subscribed": True,
        })
