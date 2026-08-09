"""WorkflowService — vocabulary boundary: above here 'workflow', below here TaskEngine.

Delegates to TaskEngine for all persistence/trigger/scheduling logic;
translates between user-facing Workflow vocabulary and internal TaskDefinition.
Never reimplements optimistic concurrency, trigger validation, or prerequisite gates.

Every method that accepts a `workflow_id` verifies the caller's `org_id` owns
it (via `TaskEngine.get`, which 404s across orgs) before touching triggers or
runs -- `ITaskRunStore`/`ITriggerStore` are keyed by id alone, so skipping this
would let any authenticated caller read another org's data by guessing ids.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.services.tasks.domain.errors import TaskNotFoundError, TriggerNotFoundError
from app.services.workflows.application.version_writer import WorkflowVersionWriter
from app.services.workflows.domain.errors import WorkflowNotFoundError
from app.services.workflows.domain.models import (
    TraceEntry,
    TriggerSummary,
    Workflow,
    WorkflowKind,
    WorkflowStatus,
)

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.tasks.application.engine import TaskEngine
    from app.services.tasks.domain.models import (
        Page,
        TaskDefinition,
        TaskRun,
        TaskTrigger,
    )
    from app.services.workflows.codegen.agent import WorkflowBuilderAgent
    from app.services.workflows.domain.models import WorkflowVersion
    from app.services.workflows.interface.code_store import ICodeStore
    from app.services.workflows.interface.conversation_writer import IConversationWriter
    from app.services.workflows.interface.journal import IExecutionJournal
    from app.services.workflows.interface.version_store import IWorkflowVersionStore

__all__ = ["WorkflowService"]


def _task_to_workflow(task: "TaskDefinition", triggers: list["TaskTrigger"] | None = None) -> Workflow:
    """Project a TaskDefinition + its triggers into the user-facing Workflow shape."""
    status_map = {
        "draft": WorkflowStatus.DRAFT,
        "active": WorkflowStatus.ACTIVE,
        "paused": WorkflowStatus.PAUSED,
        "disabled": WorkflowStatus.DISABLED,
        "cancelled": WorkflowStatus.DISABLED,
        "completed": WorkflowStatus.COMPLETED,
    }
    trigger_summaries: list[TriggerSummary] = []
    if triggers:
        trigger_summaries = [
            TriggerSummary(
                trigger_id=t.trigger_id,
                kind=t.kind.value,
                next_run_at=t.next_run_at,
                last_fire_at=t.last_fire_at,
                enabled=t.enabled,
            )
            for t in triggers
        ]
    kind = WorkflowKind.CODE if task.execution_kind == "code" else WorkflowKind.AGENT_TASK
    return Workflow(
        workflow_id=task.task_id,
        org_id=task.org_id,
        kind=kind,
        name=task.title,
        description=task.description,
        current_version_id=task.workflow_version_id,
        triggers=trigger_summaries,
        subscriptions=[],
        status=status_map.get(task.status.value, WorkflowStatus.DRAFT),
        execution_kind=task.execution_kind,
        created_by_user_id=task.created_by_user_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
        conversation_id=task.created_from_conversation_id,
        tool_names=task.tool_names or [],
        connector_ids=task.connector_ids or [],
        collection_ids=task.collection_ids or [],
        max_turns=task.max_turns,
        timeout_seconds=task.timeout_seconds,
    )


def _step_key_target(step_key: str) -> str | None:
    """`"ctx.tool:slack.send#0"` -> `"slack.send"`.

    `Ctx._next_step_key` composes keys as `<qualname>[:<target>]#<index>`;
    recovering the target here is what lets the run inspector line a trace row
    up with the IR node (`metadata.tool_path`) that produced it.
    """
    head = step_key.split("#", 1)[0]
    _, sep, target = head.partition(":")
    return target or None if sep else None


# Timeline `detail` keys that name what a step acted on, most specific first.
# `append_timeline` call sites use a different key per event kind ("tool" for
# tool calls, "agent_id" for spawns), so the agent trace has to look across
# them to fill the same `target` the journal trace fills from its step key.
_TIMELINE_TARGET_KEYS = ("tool", "agent_id", "role", "key")


def _timeline_target(detail: "dict[str, Any] | None") -> str | None:
    """What an agent-timeline step acted on, or None when the event names
    nothing (`llm_call`, `agent_start`)."""
    if not detail:
        return None
    for key in _TIMELINE_TARGET_KEYS:
        value = detail.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class WorkflowService:
    """Thin facade over TaskEngine using workflow vocabulary."""

    def __init__(
        self,
        *,
        task_engine: "TaskEngine",
        version_store: "IWorkflowVersionStore | None" = None,
        code_store: "ICodeStore | None" = None,
        journal: "IExecutionJournal | None" = None,
        builder_agent: "WorkflowBuilderAgent | None" = None,
        graph_provider: "IGraphDBProvider | None" = None,
        conversation_writer: "IConversationWriter | None" = None,
        logger: "Logger | None" = None,
    ) -> None:
        self._engine = task_engine
        self._conversation_writer = conversation_writer
        self._version_store = version_store
        self._code_store = code_store
        self._journal = journal
        self._builder_agent = builder_agent
        self._graph_provider = graph_provider
        self._logger = logger or logging.getLogger(__name__)
        self._version_writer: WorkflowVersionWriter | None = None
        if version_store is not None and code_store is not None:
            self._version_writer = WorkflowVersionWriter(
                version_store=version_store,
                code_store=code_store,
                task_engine=task_engine,
                logger=self._logger,
            )

    async def _get_owned_task(self, workflow_id: str, org_id: str) -> "TaskDefinition":
        """Ownership-checked read, shared by every method below that needs
        to touch a workflow's triggers/runs -- raises WorkflowNotFoundError
        for a missing workflow OR one belonging to a different org (the
        underlying `TaskEngine.get` already 404s across orgs)."""
        try:
            return await self._engine.get(workflow_id, org_id)
        except TaskNotFoundError:
            raise WorkflowNotFoundError(workflow_id)

    async def list_workflows(
        self,
        *,
        org_id: str,
        user_id: str,
        all_users: bool = False,
        status: WorkflowStatus | None = None,
        text_search: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        graph_db: Any = None,
    ) -> "Page[Workflow]":
        from app.services.tasks.domain.models import Page, TaskQuery, TaskStatus

        task_status = None
        if status is not None:
            status_map = {
                WorkflowStatus.ACTIVE: TaskStatus.ACTIVE,
                WorkflowStatus.PAUSED: TaskStatus.PAUSED,
                WorkflowStatus.DISABLED: TaskStatus.DISABLED,
                WorkflowStatus.DRAFT: TaskStatus.DRAFT,
                WorkflowStatus.COMPLETED: TaskStatus.COMPLETED,
            }
            task_status = status_map.get(status)

        query = TaskQuery(
            org_id=org_id,
            created_by_user_id=None if all_users else user_id,
            status=task_status,
            text_search=text_search,
            created_from_conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
        page = await self._engine.find(query)
        items = list(page.items)
        total = page.total
        if conversation_id:
            linked = await self._linked_tasks(
                conversation_id, org_id, already_listed={t.task_id for t in items},
            )
            items.extend(linked)
            total += len(linked)
        # The list is where a user decides whether a workflow is actually going
        # to run again, so it has to carry the triggers. Fetched for the whole
        # page at once rather than per row.
        triggers_by_task = await self._engine.list_triggers_for_tasks(
            [t.task_id for t in items],
        )
        workflow_items = [
            _task_to_workflow(t, triggers=triggers_by_task.get(t.task_id, []))
            for t in items
        ]
        return Page(items=workflow_items, total=total, limit=page.limit, offset=page.offset)

    async def _linked_tasks(
        self, conversation_id: str, org_id: str, *, already_listed: set[str],
    ) -> list["TaskDefinition"]:
        """Workflows attached to a conversation they were not created from.

        `created_from_conversation_id` only records where a workflow was born,
        so without this a workflow linked to a conversation afterwards is
        invisible in that conversation's panel. Best-effort throughout: a
        conversation store that cannot answer, or an id that no longer
        resolves, drops out rather than failing the listing.
        """
        if self._conversation_writer is None:
            return []
        try:
            linked_ids = await self._conversation_writer.list_linked_workflows(
                conversation_id, org_id,
            )
        except Exception:
            self._logger.warning(
                "list_workflows: could not read links for conversation %s",
                conversation_id, exc_info=True,
            )
            return []

        extra: list[TaskDefinition] = []
        for workflow_id in linked_ids:
            if workflow_id in already_listed:
                continue
            try:
                extra.append(await self._engine.get(workflow_id, org_id))
            except TaskNotFoundError:
                # Deleted since it was linked. The link is best-effort state
                # and nothing prunes it, so this is expected, not an error.
                self._logger.debug(
                    "list_workflows: linked workflow %s no longer exists", workflow_id,
                )
        return extra

    async def get_workflow(
        self, *, workflow_id: str, org_id: str, graph_db: Any = None
    ) -> Workflow:
        task = await self._get_owned_task(workflow_id, org_id)
        triggers = await self._engine.list_triggers(workflow_id)
        return _task_to_workflow(task, triggers=triggers)

    async def list_triggers(self, *, workflow_id: str, org_id: str) -> list["TaskTrigger"]:
        await self._get_owned_task(workflow_id, org_id)
        return await self._engine.list_triggers(workflow_id)

    async def add_trigger(
        self, *, workflow_id: str, org_id: str, spec: dict[str, Any],
    ) -> tuple["TaskTrigger", str | None]:
        """Attach a trigger. Returns `(trigger, webhook_secret_or_None)`.

        The secret is returned exactly once, here, and is not readable
        afterwards -- the caller has to show it to the user now or never.
        """
        await self._get_owned_task(workflow_id, org_id)
        return await self._engine.add_trigger(workflow_id, org_id, spec)

    async def update_trigger(
        self, *, workflow_id: str, org_id: str, trigger_id: str, enabled: bool,
    ) -> "TaskTrigger":
        """Enable or disable one trigger.

        Only `enabled` is mutable: changing a schedule in place would leave
        `next_run_at`, the due index and `run_count` describing the old one,
        so a genuine schedule change is a delete plus an add.
        """
        await self._get_owned_task(workflow_id, org_id)
        return await self._engine.set_trigger_enabled(
            trigger_id, org_id, enabled=enabled, task_id=workflow_id,
        )

    async def delete_trigger(self, *, workflow_id: str, org_id: str, trigger_id: str) -> bool:
        await self._get_owned_task(workflow_id, org_id)
        trigger = await self._engine.get_trigger(trigger_id, org_id)
        if trigger.task_id != workflow_id:
            # Ownership is per-org at the engine, so without this a caller
            # could delete another workflow's trigger through their own.
            raise TriggerNotFoundError(trigger_id)
        return await self._engine.delete_trigger(trigger_id, org_id)

    async def run_now(
        self, *, workflow_id: str, org_id: str, user_id: str, graph_db: Any = None
    ) -> "TaskRun":
        """Fire an immediate run."""
        try:
            run = await self._engine.run_now(workflow_id, org_id)
        except TaskNotFoundError:
            raise WorkflowNotFoundError(workflow_id)
        self._audit("run_now", workflow_id, org_id, user_id)
        return run

    async def dry_run(
        self, *, workflow_id: str, org_id: str, user_id: str, graph_db: Any = None
    ) -> "TaskRun":
        """Fire a dry run (WRITE steps skipped, no notifications)."""
        try:
            return await self._engine.dry_run(workflow_id, org_id)
        except TaskNotFoundError:
            raise WorkflowNotFoundError(workflow_id)

    def _audit(self, action: str, workflow_id: str, org_id: str, user_id: str | None) -> None:
        """One line per state-changing operation, naming who did it.

        These are the operations a user cannot undo or would need to explain
        later; the engine below only ever sees an org, so the actor has to be
        recorded here where the request context still has it.
        """
        self._logger.info(
            "workflow_audit action=%s workflow_id=%s org=%s user=%s",
            action, workflow_id, org_id, user_id or "<unknown>",
        )

    async def pause(
        self, *, workflow_id: str, org_id: str, user_id: str | None = None, graph_db: Any = None,
    ) -> Workflow:
        try:
            task = await self._engine.pause(workflow_id, org_id)
        except TaskNotFoundError:
            raise WorkflowNotFoundError(workflow_id)
        self._audit("pause", workflow_id, org_id, user_id)
        return _task_to_workflow(task)

    async def resume(
        self, *, workflow_id: str, org_id: str, user_id: str | None = None, graph_db: Any = None,
    ) -> Workflow:
        try:
            task = await self._engine.unpause(workflow_id, org_id)
        except TaskNotFoundError:
            raise WorkflowNotFoundError(workflow_id)
        self._audit("resume", workflow_id, org_id, user_id)
        return _task_to_workflow(task)

    async def cancel(
        self, *, workflow_id: str, org_id: str, user_id: str | None = None, graph_db: Any = None,
    ) -> Workflow:
        try:
            task = await self._engine.cancel(workflow_id, org_id)
        except TaskNotFoundError:
            raise WorkflowNotFoundError(workflow_id)
        self._audit("cancel", workflow_id, org_id, user_id)
        return _task_to_workflow(task)

    async def delete(
        self, *, workflow_id: str, org_id: str, user_id: str | None = None,
    ) -> bool:
        """Hard delete -- see `TaskEngine.delete`'s own docstring on when
        this (vs. `cancel`, a soft delete) is appropriate."""
        await self._get_owned_task(workflow_id, org_id)
        deleted = await self._engine.delete(workflow_id, org_id)
        self._audit("delete", workflow_id, org_id, user_id)
        return deleted

    async def promote_to_agent(
        self,
        *,
        workflow_id: str,
        org_id: str,
        graph_provider: "IGraphDBProvider",
        config_service: "ConfigurationService",
    ) -> str:
        """One-way copy into a standalone Agent Builder agent -- see
        `TaskEngine.promote_to_agent`'s own docstring."""
        await self._get_owned_task(workflow_id, org_id)
        return await self._engine.promote_to_agent(
            workflow_id, org_id, graph_provider=graph_provider, config_service=config_service,
        )

    async def list_runs(
        self, *, workflow_id: str, org_id: str, limit: int = 20, offset: int = 0
    ) -> "Page[TaskRun]":
        await self._get_owned_task(workflow_id, org_id)
        return await self._engine.list_runs(workflow_id, limit=limit, offset=offset)

    async def get_run(self, *, workflow_id: str, run_id: str, org_id: str) -> "TaskRun":
        """`TaskEngine.get_run` already raises `RunNotFoundError` for a
        missing run or one belonging to a different task/org -- nothing
        further to check here."""
        return await self._engine.get_run(run_id, workflow_id, org_id)

    async def get_run_trace(
        self, *, workflow_id: str, run_id: str, org_id: str,
    ) -> tuple["TaskRun", list[TraceEntry]]:
        """The run plus its execution trace, oldest step first.

        Two sources depending on how the run executed, normalised to one shape
        so the frontend does not branch on execution kind: a code workflow's
        trace is its replay journal, an agent task's is the agent timeline.
        `get_run` performs the org/workflow ownership check, so the
        id-keyed journal/timeline reads below cannot leak another org's trace.
        """
        run = await self._engine.get_run(run_id, workflow_id, org_id)

        if run.agent_run_id:
            return run, await self._agent_trace(run.agent_run_id, org_id)
        return run, await self._journal_trace(run.run_id)

    async def _journal_trace(self, run_id: str) -> list[TraceEntry]:
        if self._journal is None:
            return []
        try:
            entries = await self._journal.load(run_id)
        except Exception:
            self._logger.warning("Could not load journal for run %s", run_id, exc_info=True)
            return []
        return [
            TraceEntry(
                seq=e.seq,
                kind=e.entry_kind,
                label=e.step_key,
                target=_step_key_target(e.step_key),
                outcome=e.outcome.value if hasattr(e.outcome, "value") else str(e.outcome),
                error=e.error.message if e.error else None,
                attempt=e.attempt,
                detail={"idempotency_key": e.idempotency_key},
            )
            for e in sorted(entries, key=lambda e: e.seq)
        ]

    async def _agent_trace(self, agent_run_id: str, org_id: str) -> list[TraceEntry]:
        if self._graph_provider is None:
            return []
        from app.agent_loop_lib.modules.stores.timeline.graph_store import (
            GraphTimelineStore,
        )

        try:
            store = GraphTimelineStore(self._graph_provider, org_id)
            entries = await store.get_by_run(agent_run_id)
        except Exception:
            self._logger.warning(
                "Could not load agent timeline for run %s", agent_run_id, exc_info=True,
            )
            return []
        return [
            TraceEntry(
                seq=e.sequence_id,
                kind=e.event_type,
                label=e.summary,
                target=_timeline_target(e.detail),
                outcome=e.status.value if hasattr(e.status, "value") else str(e.status),
                timestamp=e.timestamp,
                detail=e.detail,
            )
            for e in sorted(entries, key=lambda e: e.sequence_id)
        ]

    async def answer_run(
        self, *, workflow_id: str, run_id: str, org_id: str, answer: str,
        user_id: str | None = None,
    ) -> "TaskRun":
        """Answers a run paused with `status=awaiting_input`. Raises
        `StaleAnswerError` (via TaskEngine) if the question was already
        answered or the run moved on."""
        run = await self._engine.answer_run(run_id, workflow_id, org_id, answer)
        # An answer can release a destructive step past an approval gate, so
        # who approved it is the one thing worth being able to look up later.
        self._logger.info(
            "workflow_audit action=answer_run workflow_id=%s run_id=%s org=%s "
            "user=%s status=%s",
            workflow_id, run_id, org_id, user_id or "<unknown>", run.status.value,
        )
        return run

    async def list_versions(self, *, workflow_id: str, org_id: str) -> list["WorkflowVersion"]:
        """Returns all stored versions for a workflow, newest first.

        Raises `VersionStoreUnavailableError` when the store cannot be
        queried -- distinct from "no versions exist yet" (an empty list),
        so the frontend can show a retry banner instead of "Generate Code."
        """
        from app.services.workflows.domain.errors import VersionStoreUnavailableError

        if self._version_store is None:
            return []
        # Ownership check before reading versions.
        await self._get_owned_task(workflow_id, org_id)
        try:
            return await self._version_store.list_for_workflow(
                workflow_id=workflow_id, org_id=org_id
            )
        except Exception as exc:
            self._logger.error(
                "list_versions: version store failed for workflow %s: %s",
                workflow_id, exc, exc_info=True,
            )
            raise VersionStoreUnavailableError(workflow_id, str(exc)) from exc

    async def get_version_source(
        self, *, workflow_id: str, version_id: str, org_id: str
    ) -> bytes:
        """Returns the raw source bytes for a specific version.

        Raises `WorkflowVersionNotFoundError` when the version does not exist,
        belongs to a different workflow/org, or has a corrupt/missing bundle
        reference (data-integrity issue -- logged distinctly from a transient
        code-store read failure, which propagates as-is so the route maps it
        to a 5xx rather than a misleading 404).
        """
        from app.services.workflows.domain.errors import WorkflowVersionNotFoundError

        if self._version_store is None or self._code_store is None:
            raise WorkflowVersionNotFoundError(version_id)
        await self._get_owned_task(workflow_id, org_id)
        version = await self._version_store.get(version_id, org_id)
        if version is None or version.workflow_id != workflow_id:
            raise WorkflowVersionNotFoundError(version_id)
        if version.bundle_ref is None:
            self._logger.error(
                "get_version_source: version %s for workflow %s has no bundle_ref "
                "(data corruption or dropped during deserialization)",
                version_id, workflow_id,
            )
            raise WorkflowVersionNotFoundError(version_id)
        try:
            return await self._code_store.get(version.bundle_ref)
        except KeyError as exc:
            self._logger.error(
                "get_version_source: artifact %s referenced by version %s is missing "
                "from the code store",
                version.bundle_ref.artifact_id, version_id,
            )
            raise WorkflowVersionNotFoundError(version_id) from exc

    async def edit_workflow(
        self,
        *,
        workflow_id: str,
        org_id: str,
        user_id: str,
        instructions: str,
    ) -> dict:
        """Propose edited workflow code — generation and verification only.

        Deliberately persists nothing: the caller reviews the diff and either
        calls `commit_version` to make it live or discards it. Pinning here
        would make "Discard" a lie, since the discarded code would already be
        what the next scheduled run executes.

        Returns ``{source, ir, previousSource, baseVersionId}``;
        ``baseVersionId`` is what `commit_version` checks the pin against so a
        concurrent edit cannot be silently overwritten.
        """
        from app.services.workflows.codegen.verifier import verify_workflow_source
        from app.services.workflows.domain.errors import WorkflowCodegenError
        from app.services.workflows.ir.extractor import extract_ir

        if self._version_store is None or self._code_store is None:
            raise WorkflowCodegenError("Code/version store not configured for edit")
        if self._builder_agent is None:
            raise WorkflowCodegenError("No workflow builder agent is configured")

        task = await self._get_owned_task(workflow_id, org_id)
        previous_source = await self._load_pinned_source(task, org_id)

        gen = await self._builder_agent.generate(
            spec=instructions,
            org_id=org_id,
            user_id=user_id,
            workflow_id=workflow_id,
            existing_source=previous_source or None,
            tool_names=list(task.tool_names) if task.tool_names else None,
        )
        if not gen.get("ok") or not gen.get("source"):
            raise WorkflowCodegenError(
                "Code generation failed", errors=list(gen.get("errors", [])),
            )
        new_source: str = gen["source"]

        verify_result = verify_workflow_source(new_source, allowed_tools=task.tool_names)
        if not verify_result.ok:
            raise WorkflowCodegenError(
                "Generated code failed verification",
                errors=verify_result.to_dict().get("errors", []),
            )

        return {
            "source": new_source,
            "ir": extract_ir(new_source).model_dump(),
            "previousSource": previous_source,
            "baseVersionId": task.workflow_version_id or "",
        }

    async def commit_version(
        self,
        *,
        workflow_id: str,
        org_id: str,
        user_id: str,
        source: str,
        base_version_id: str | None = None,
    ) -> "WorkflowVersion":
        """Persist reviewed source as a new immutable version and pin it.

        This is the only path that makes edited code live, whether the source
        came from `edit_workflow` or from the user typing in the editor.
        """
        from app.services.workflows.codegen.verifier import verify_workflow_source
        from app.services.workflows.domain.errors import WorkflowCodegenError
        from app.services.workflows.ir.extractor import extract_ir

        if self._version_writer is None:
            raise WorkflowCodegenError("Code/version store not configured")

        task = await self._get_owned_task(workflow_id, org_id)
        verify_result = verify_workflow_source(source, allowed_tools=task.tool_names)
        if not verify_result.ok:
            raise WorkflowCodegenError(
                "Workflow code failed verification",
                errors=verify_result.to_dict().get("errors", []),
            )

        from app.services.workflows.domain.errors import PinFailedError

        try:
            saved = await self._version_writer.persist(
                workflow_id=workflow_id,
                org_id=org_id,
                user_id=user_id,
                source=source,
                ir=extract_ir(source),
                expected_current_version_id=base_version_id,
            )
        except PinFailedError as exc:
            # The version was generated and stored successfully -- it is
            # listable via GET /versions and can be activated later -- only
            # pinning it as the workflow's active version failed. Distinct
            # from a 500 so the caller knows the code is not lost.
            self._logger.warning(
                "commit_version: workflow %s saved version %s but pin failed: %s",
                workflow_id, exc.version.version_id, exc,
            )
            raise
        self._logger.info(
            "commit_version: workflow %s pinned to version %s (v%d)",
            workflow_id, saved.version_id, saved.version_number,
        )
        return saved

    async def activate_version(
        self, *, workflow_id: str, version_id: str, org_id: str, user_id: str,
    ) -> "WorkflowVersion":
        """Roll back (or forward) to an existing version by re-pinning it."""
        from app.services.workflows.domain.errors import WorkflowVersionNotFoundError

        if self._version_store is None or self._version_writer is None:
            raise WorkflowVersionNotFoundError(version_id)

        await self._get_owned_task(workflow_id, org_id)
        version = await self._version_store.get(version_id, org_id)
        if version is None or version.workflow_id != workflow_id:
            raise WorkflowVersionNotFoundError(version_id)

        await self._version_writer.pin(workflow_id=workflow_id, org_id=org_id, version=version)
        self._logger.info(
            "activate_version: workflow %s re-pinned to version %s (v%d) by %s",
            workflow_id, version_id, version.version_number, user_id,
        )
        return version

    async def _load_pinned_source(self, task: "TaskDefinition", org_id: str) -> str:
        """Current source for the version the task is pinned to, or "" when the
        workflow has no code yet. A pinned version whose source cannot be read
        is an error, not an empty edit base -- returning "" there would make
        the builder rewrite the workflow from scratch."""
        from app.services.workflows.domain.errors import WorkflowVersionNotFoundError

        if not task.workflow_version_id or self._version_store is None or self._code_store is None:
            return ""
        version = await self._version_store.get(task.workflow_version_id, org_id)
        if version is None or version.bundle_ref is None:
            raise WorkflowVersionNotFoundError(task.workflow_version_id)
        try:
            return (await self._code_store.get(version.bundle_ref)).decode("utf-8")
        except KeyError as exc:
            raise WorkflowVersionNotFoundError(task.workflow_version_id) from exc
