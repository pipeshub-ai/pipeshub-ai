"""`TaskExecutor`: Phase 4 -- drives a dispatched `TaskRun` to completion.

Consumes `Topic.TASK_EVENTS` (the `IMessagingConsumer` message-handler model:
`handle_dispatch` is registered once via `consumer.start(handler)`, never
polled directly -- see `handle_dispatch`'s own docstring for why it always
returns `True`).

State machine per claimed run:

  PENDING --(claim_for_execution)--> RUNNING --> SUCCEEDED
                                          |
                                          +-----> AWAITING_INPUT  (needs_input)
                                          |
                                          +-----> FAILED           (orderly AgentResult failure -- no auto-retry)
                                          |
                                          `-- crash/exception --> _recover_or_finalize:
                                                  had_write_side_effect and no checkpoint -> DLQ
                                                  attempts remain                          -> PENDING (retry, after backoff)
                                                  attempts exhausted                        -> DLQ

`ITaskRunStore.reap_abandoned()` (Phase 1) finds RUNNING runs whose worker
died outright (no exception was ever caught by a live process) -- `run_reaper_tick`
runs it periodically and feeds every reaped run through the SAME
`_recover_or_finalize` decision used for an in-process crash, since "reaped
as ABANDONED" and "caught an exception mid-execution" are the same failure
mode observed by two different code paths.

Broker-level redelivery is deliberately NOT used for retry: `handle_dispatch`
always returns `True` (commit) once a message has been read, and this
class's own state machine (PENDING re-publish with backoff) is the sole
retry mechanism. Relying on broker redelivery-on-`False` here would retry
without backoff and would race with `_recover_or_finalize`'s own decision
about whether a retry is even safe (the side-effect gate).

An orderly `AgentResult(success=False)` (the agent ran to completion and
reported failure -- hit `max_turns`, a hook blocked it, the model gave up)
is intentionally NOT auto-retried, unlike a crash. That class of failure is
attributable to the task/model, not infrastructure flakiness; retrying it
blindly would likely reproduce the same failure. Only crash-class failures
(an uncaught exception, or a worker that died outright and was reaped)
go through `_recover_or_finalize`'s retry/DLQ decision.

`checkpoint_store_factory`/`timeline_store_factory` (Phase 9): this process
serves every org in the deployment, but `GraphCheckpointStore`/
`GraphTimelineStore` are each bound to a single `org_id` at construction
(they tag every write with it, and `GraphCheckpointStore.load` rejects a
document whose `orgId` doesn't match -- see those classes' own docstrings).
A single shared instance would silently mislabel -- or reject -- every
org's checkpoints except the one it happened to be built for. This class
therefore takes a cheap, synchronous `org_id -> store` factory and calls it
fresh for every claimed run's own `task.principal.org_id`, never holding a
pre-built instance across runs.

`had_write_side_effect` (Phase 7): set via `app.agents.agent_loop.hooks
.task_side_effect.track_side_effects`, wired onto the per-run
`AgentRuntime.hooks` by `_execute_claimed_run` before `assemble()`'s
`Agent` is ever run. Persisted onto `run.had_write_side_effect` as soon as
it flips (via `_persist_side_effect_flag`, an `on_first_side_effect`
callback), not just at `_finalize_result` time -- the crash this flag
exists to protect against is exactly a crash `_finalize_result` never
gets to run.

Prerequisites (Phase 7) are re-validated immediately before every
`assemble()` call, never replayed from `TaskEngine.create()`'s
creation-time snapshot (Part E: "Prerequisites re-validated per run") --
a connector token can expire, or a collection can be un-shared, at any
point between a task's creation and any later scheduled fire. A blocking
failure here is an ORDERLY failure (the run never started), not a crash:
it goes straight to `_finalize_result`-shaped FAILED bookkeeping (feeding
`_on_run_failed`'s consecutive-failure/auto-disable counter, same as any
other orderly failure), never through `_recover_or_finalize`'s crash-retry
path -- retrying an unchanged missing prerequisite immediately would just
reproduce the identical failure.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timezone
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.hooks.task_side_effect import (
    SideEffectFlag,
    track_side_effects,
)
from app.services.messaging.config import Topic
from app.services.tasks.application.prerequisites import (
    PrerequisiteCheckResult,
    PrerequisiteIssue,
)
from app.services.tasks.domain.errors import (
    OptimisticConcurrencyError,
    ToolResolutionError,
)
from app.services.tasks.domain.models import RunStatus, TaskStatus, TriggerKind
from app.services.tasks.domain.schedule_calculator import ScheduleCalculator
from app.services.tasks.interface.clock import SystemClock
from app.services.tasks.interface.notifier import TaskNotification, TaskNotificationKind

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from datetime import datetime
    from logging import Logger

    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.core.types import AgentResult, Goal
    from app.agent_loop_lib.hooks.registry import HookRegistry
    from app.agent_loop_lib.modules.stores.checkpoint.base import (
        AgentCheckpoint,
        CheckpointStore,
    )
    from app.agent_loop_lib.modules.stores.timeline.base import TimelineStore
    from app.config.configuration_service import ConfigurationService
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.messaging.config import StreamMessage
    from app.services.messaging.interface.producer import IMessagingProducer
    from app.services.tasks.application.prerequisites import PrerequisiteValidator
    from app.services.tasks.domain.models import TaskDefinition, TaskRun, TaskTrigger
    from app.services.tasks.interface.clock import IClock
    from app.services.tasks.interface.notifier import ITaskNotifier
    from app.services.tasks.interface.run_store import ITaskRunStore
    from app.services.tasks.interface.task_store import ITaskStore
    from app.services.tasks.interface.trigger_store import ITriggerStore
    from app.services.tasks.runtime.spec_assembler import TaskSpecAssembler
    from app.services.workflows.interface.agent_provisioning import IAgentProvisioning
    from app.services.workflows.interface.agent_runner import IWorkflowAgentRunner
    from app.services.workflows.interface.conversation_writer import IConversationWriter
    from app.services.workflows.interface.broker import IPlatformBroker
    from app.services.workflows.interface.state_store import IWorkflowStateStore
    from app.services.workflows.runtime.code_runner import CodeWorkflowRunner

__all__ = ["TaskExecutor"]

_TASK_RUN_DISPATCH_EVENT = "task_run_dispatch"
_TASK_STORE_UPDATE_RETRIES = 5
_LEASE_LOST_LOG = (
    "Run %s finished but its lease had already been reclaimed by another worker; "
    "discarding this result so the reclaiming worker's outcome stands"
)
# Kinds a dry run may still publish -- they exist to drive the live run card,
# not to notify anyone. Every way a run can stop is here: whichever one arrives
# is what moves the card out of "started". See `_notify`.
_DRY_RUN_NOTIFIABLE_KINDS = frozenset({
    TaskNotificationKind.RUN_SUCCEEDED,
    TaskNotificationKind.RUN_FAILED,
    TaskNotificationKind.AWAITING_INPUT,
    TaskNotificationKind.TASK_DLQ,
    TaskNotificationKind.PREREQUISITE_MISSING,
})


def _suspension_summary(result: dict[str, Any]) -> str:
    """The question a suspended code workflow is waiting on, in the user's words.

    This string is the whole message: it is what the notification says, what
    the chat card shows, and what the run list displays. `suspended:approval`
    -- the previous value -- told the person being asked nothing about what
    they were approving.
    """
    kind = result.get("suspension_kind")
    if kind == "approval":
        label = str(result.get("label") or "").strip()
        return label or "This workflow is waiting for your approval."
    if kind == "wait_for_event":
        event_type = result.get("event_type") or "an external event"
        return f"Waiting for {event_type}."
    return "This workflow is waiting for input."


def _tool_resolution_check_result(exc: ToolResolutionError) -> "PrerequisiteCheckResult":
    """Present an unresolvable tool the same way a missing connector or
    toolset is presented, so the user gets one consistent "prerequisites not
    met" message naming the tool and its closest available match."""
    return PrerequisiteCheckResult(issues=[
        PrerequisiteIssue(
            kind="tool",
            id=name,
            reason=(
                f"not available to this task; closest available: {', '.join(close)}"
                if (close := exc.suggestions.get(name))
                else "not available to this task"
            ),
        )
        for name in exc.missing
    ])


class TaskExecutor:
    """One instance per Query service process. Like `SchedulerLoop`, holds
    no per-run state between calls other than a set of tracked background
    tasks (retry backoff timers, the per-run heartbeat loop) for graceful
    shutdown."""

    def __init__(
        self,
        *,
        task_store: "ITaskStore",
        run_store: "ITaskRunStore",
        checkpoint_store_factory: "Callable[[str], CheckpointStore]",
        spec_assembler: "TaskSpecAssembler",
        graph_provider: "IGraphDBProvider",
        config_service: "ConfigurationService",
        producer: "IMessagingProducer",
        notifier: "ITaskNotifier",
        prerequisite_validator: "PrerequisiteValidator | None" = None,
        timeline_store_factory: "Callable[[str], TimelineStore] | None" = None,
        blob_store: "BlobStorage | None" = None,
        clock: "IClock | None" = None,
        owner: str | None = None,
        code_workflow_runner: "CodeWorkflowRunner | None" = None,
        trigger_store: "ITriggerStore | None" = None,
        conversation_writer: "IConversationWriter | None" = None,
        workflow_state_store: "IWorkflowStateStore | None" = None,
        agent_provisioning_service: "IAgentProvisioning | None" = None,
        agent_runner: "IWorkflowAgentRunner | None" = None,
        lease_seconds: float = 120.0,
        heartbeat_interval_seconds: float = 30.0,
        stale_pending_after_seconds: float = 60.0,
        reap_tick_interval_seconds: float = 30.0,
        logger: "Logger | None" = None,
    ) -> None:
        self._task_store = task_store
        self._run_store = run_store
        self._checkpoint_store_factory = checkpoint_store_factory
        self._timeline_store_factory = timeline_store_factory
        self._spec_assembler = spec_assembler
        self._graph_provider = graph_provider
        self._config_service = config_service
        self._blob_store = blob_store
        self._producer = producer
        self._notifier = notifier
        self._prerequisite_validator = prerequisite_validator
        self._clock: IClock = clock or SystemClock()
        self._owner = owner or f"executor-{id(self)}"
        self._code_workflow_runner = code_workflow_runner
        self._trigger_store = trigger_store
        self._conversation_writer = conversation_writer
        self._workflow_state_store = workflow_state_store
        self._agent_provisioning_service = agent_provisioning_service
        self._agent_runner = agent_runner
        self._schedule_calculator = ScheduleCalculator()
        self._lease_seconds = lease_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._stale_pending_after_seconds = stale_pending_after_seconds
        self._reap_tick_interval_seconds = reap_tick_interval_seconds
        self._logger = logger or logging.getLogger(__name__)
        self._background_tasks: set[asyncio.Task] = set()
        self._reaper_task: asyncio.Task | None = None
        self._running = False

    @property
    def owner(self) -> str:
        return self._owner

    def start_reaper(self) -> None:
        """Starts the periodic `reap_abandoned` background loop. Independent
        of message consumption -- a caller wires `handle_dispatch` into an
        `IMessagingConsumer` separately (see module docstring); this only
        owns the reaper's own lifecycle."""
        if self._running:
            return
        self._running = True
        self._reaper_task = asyncio.create_task(self._reaper_loop())

    async def stop(self) -> None:
        self._running = False
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None
        for task in list(self._background_tasks):
            task.cancel()
        for task in list(self._background_tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._background_tasks.clear()
        if self._conversation_writer is not None:
            try:
                await self._conversation_writer.aclose()
            except Exception:
                self._logger.debug("conversation_writer.aclose raised during shutdown", exc_info=True)

    def _spawn_background(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    # -- Message handler --------------------------------------------------

    async def handle_dispatch(self, message: "StreamMessage") -> bool:
        """`IMessagingConsumer` message handler for `Topic.TASK_EVENTS`.
        Always returns `True` (commit) -- see module docstring for why
        broker redelivery is not this class's retry mechanism. A run this
        handler cannot usefully act on (missing row, already claimed,
        already terminal) is a safe no-op, not a failure to retry."""
        run_id = message.payload.get("run_id")
        if not run_id or not isinstance(run_id, str):
            self._logger.error("task_run_dispatch event missing run_id: %s", message.payload)
            return True

        run = await self._run_store.get(run_id)
        if run is None:
            self._logger.warning("Dispatch event for unknown run %s -- dropping", run_id)
            return True

        claimed = await self._run_store.claim_for_execution(
            run_id, owner=self._owner, lease_seconds=self._lease_seconds,
        )
        if claimed is None:
            return True

        task = await self._task_store.get(claimed.task_id, claimed.org_id)
        if task is None or not task.enabled:
            await self._finalize_missing_task(claimed)
            return True

        await self._execute_claimed_run(claimed, task)
        return True

    async def _finalize_missing_task(self, run: "TaskRun") -> None:
        updated = run.model_copy(update={
            "status": RunStatus.FAILED,
            "lease_owner": None,
            "lease_expires_at": None,
            "error": "Task no longer exists or is disabled",
            "completed_at": self._clock.now().astimezone(timezone.utc).isoformat(),
        })
        await self._run_store.update(updated)

    # -- Execution ----------------------------------------------------------

    async def _execute_claimed_run(self, run: "TaskRun", task: "TaskDefinition") -> None:
        prereq_failure = await self._check_prerequisites(task)
        if prereq_failure is not None:
            await self._finalize_prerequisite_failure(run, task, prereq_failure)
            return

        checkpoint_store = self._checkpoint_store_factory(task.principal.org_id)
        timeline_store = (
            self._timeline_store_factory(task.principal.org_id) if self._timeline_store_factory else None
        )

        execution_kind = getattr(task, "execution_kind", "agent_task")
        if execution_kind == "code":
            # Falling through to the agent path would run the task's natural
            # language `instructions` through an LLM instead of its pinned
            # code -- a silently different workflow. Fail loudly instead.
            if self._code_workflow_runner is None:
                await self._finalize_dlq(
                    run, task,
                    error="Code workflows are not available on this instance "
                          "(CodeWorkflowRunner is not wired).",
                )
                return

            heartbeat_task = self._spawn_background(self._heartbeat_loop(run.run_id))
            try:
                result = await self._code_workflow_runner.run(
                    task=task,
                    run=run,
                    trigger_payload=await self._trigger_payload(run),
                    broker=await self._build_run_broker(task),
                )
            except asyncio.CancelledError:
                raise
            except ToolResolutionError as exc:
                # `_build_run_broker` assembles the same registry the agent path
                # does, so an unresolvable tool is deterministic here too and
                # must finalize instead of burning the retry budget.
                self._logger.warning(
                    "Code workflow run %s blocked: unresolvable tools %s", run.run_id, exc.missing,
                )
                run = await self._run_store.get(run.run_id) or run
                await self._finalize_prerequisite_failure(
                    run, task, _tool_resolution_check_result(exc),
                )
                return
            except Exception as exc:
                self._logger.exception("Code workflow run %s crashed", run.run_id)
                run = await self._run_store.get(run.run_id) or run
                await self._recover_or_finalize(
                    run, task, error=f"{type(exc).__name__}: {exc}", expected_owner=self._owner,
                )
                return
            finally:
                heartbeat_task.cancel()

            run = await self._run_store.get(run.run_id) or run
            now_iso = self._clock.now().astimezone(timezone.utc).isoformat()

            if result.get("status") == "awaiting_input":
                output_summary = _suspension_summary(result)
                updated = run.model_copy(update={
                    "status": RunStatus.AWAITING_INPUT,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "output_summary": output_summary,
                    "suspended_step_key": result.get("step_key"),
                    "suspension_kind": result.get("suspension_kind"),
                    # Indexed by the run store; this is how `fire_event` finds
                    # the run again. Absent for an approval suspension, which
                    # is resumed by a user answering rather than by an event.
                    "awaiting_event_type": result.get("event_type"),
                    # Answering after this would replay the workflow against an
                    # expired journal, so `TaskEngine` refuses past it and the
                    # reaper fails runs that reach it. None means the journal
                    # held nothing to keep alive, i.e. nothing to lose.
                    "resume_deadline_at": result.get("resume_deadline_at"),
                })
                self._logger.info(
                    "Run %s suspended (%s) at step %s awaiting %s; resume deadline %s",
                    run.run_id, result.get("suspension_kind"), result.get("step_key"),
                    result.get("event_type") or "user answer",
                    result.get("resume_deadline_at") or "<none>",
                )
                if await self._run_store.update(updated, expected_owner=self._owner) is None:
                    self._logger.warning(
                        "Run %s suspended but its lease was reclaimed; not persisting", run.run_id,
                    )
                    return
                await self._notify(
                    task, updated, TaskNotificationKind.AWAITING_INPUT,
                    title=task.title, message=output_summary,
                )
                # A workflow started from chat asks its question in chat.
                # Without this the run stops silently and the only trace is a
                # notification, so nobody knows anything is waiting on them.
                await self._append_run_result_to_conversation(task, updated)
                return

            if result.get("status") == "failed":
                error = str(result.get("error") or "Code workflow run failed")
                self._logger.warning("Code workflow run %s failed: %s", run.run_id, error)
                await self._recover_or_finalize(run, task, error=error, expected_owner=self._owner)
                return

            output_summary = str(result.get("output", ""))[:500]
            updated = run.model_copy(update={
                "status": RunStatus.SUCCEEDED,
                "lease_owner": None,
                "lease_expires_at": None,
                "output_summary": output_summary,
                "completed_at": now_iso,
            })
            if await self._run_store.update(updated, expected_owner=self._owner) is None:
                self._logger.warning(
                    "Run %s completed but its lease was reclaimed; not persisting", run.run_id,
                )
                return
            await self._on_run_succeeded(task, updated)
            await self._notify(
                task, updated, TaskNotificationKind.RUN_SUCCEEDED,
                title=task.title, message="Workflow completed",
            )
            await self._append_run_result_to_conversation(task, updated)
            return

        heartbeat_task = self._spawn_background(self._heartbeat_loop(run.run_id))
        side_effect_flag = SideEffectFlag()
        try:
            agent, goal = await self._spec_assembler.assemble(
                task,
                graph_provider=self._graph_provider,
                config_service=self._config_service,
                blob_store=self._blob_store,
                logger=self._logger,
                checkpoint_store=checkpoint_store,
                timeline_store=timeline_store,
                is_dry_run=run.is_dry_run,
            )
            spec = getattr(agent, "spec", None)
            tool_names = getattr(spec, "tool_names", []) or []
            system_prompt = getattr(spec, "system_prompt", None)
            self._logger.info(
                "Assembled agent for run_id=%s task_id=%s: "
                "system_prompt=%r, goal=%r, tool_count=%d, tool_names=%s",
                run.run_id, task.task_id,
                system_prompt[:120] if system_prompt else None,
                goal.description[:200] if goal.description else None,
                len(tool_names),
                tool_names,
            )
            self._wire_side_effect_tracking(agent, run, side_effect_flag)
            result = await self._run_agent(agent, goal, run)
        except asyncio.CancelledError:
            raise
        except ToolResolutionError as exc:
            # Deterministic: the same registry will be assembled on every
            # retry, so this must finalize rather than go through
            # `_recover_or_finalize`'s retry path.
            self._logger.warning(
                "Task run %s blocked: unresolvable tools %s", run.run_id, exc.missing,
            )
            run = await self._run_store.get(run.run_id) or run
            await self._finalize_prerequisite_failure(
                run, task, _tool_resolution_check_result(exc),
            )
            return
        except Exception as exc:
            self._logger.exception("Task run %s crashed during execution", run.run_id)
            run = await self._run_store.get(run.run_id) or run
            await self._recover_or_finalize(
                run, task, error=f"{type(exc).__name__}: {exc}", expected_owner=self._owner,
            )
            return
        finally:
            heartbeat_task.cancel()

        self._logger.info(
            "Agent run completed for run_id=%s: success=%s, output=%r, error=%r, needs_input=%r",
            run.run_id, result.success,
            str(result.output)[:300] if result.output is not None else None,
            result.error,
            result.needs_input,
        )

        run = await self._run_store.get(run.run_id) or run
        await self._finalize_result(
            run, task, agent, result, checkpoint_store=checkpoint_store, timeline_store=timeline_store,
        )

    async def _check_prerequisites(self, task: "TaskDefinition") -> "PrerequisiteCheckResult | None":
        """Returns a failing `PrerequisiteCheckResult` (`.ok is False`), or
        `None` if prerequisites pass (or no validator is configured -- a
        deployment that never injected one gets today's pre-Phase-7
        behaviour: assemble and let a missing tool/connector surface as a
        normal agent-level failure instead)."""
        if self._prerequisite_validator is None:
            return None
        result = await self._prerequisite_validator.validate_task(
            task,
            graph_provider=self._graph_provider,
            config_service=self._config_service,
        )
        return None if result.ok else result

    async def _finalize_prerequisite_failure(
        self, run: "TaskRun", task: "TaskDefinition", check: "PrerequisiteCheckResult",
    ) -> None:
        """Orderly failure, not a crash -- see module docstring on why this
        never goes through `_recover_or_finalize`."""
        error = f"Prerequisites not met: {check.summary()}"
        updated = run.model_copy(update={
            "status": RunStatus.FAILED,
            "lease_owner": None,
            "lease_expires_at": None,
            "error": error,
            "completed_at": self._clock.now().astimezone(timezone.utc).isoformat(),
        })
        if await self._run_store.update(updated, expected_owner=self._owner) is None:
            self._logger.warning(_LEASE_LOST_LOG, run.run_id)
            return
        await self._on_run_failed(task, updated)
        await self._notify(
            task, updated, TaskNotificationKind.PREREQUISITE_MISSING,
            title=task.title, message="Task run blocked — missing prerequisite",
        )

    def _wire_side_effect_tracking(self, agent: "Agent", run: "TaskRun", flag: SideEffectFlag) -> None:
        hooks: "HookRegistry | None" = agent.runtime.hooks
        if hooks is None:
            return

        async def _persist() -> None:
            await self._run_store.update(run.model_copy(update={"had_write_side_effect": True}))

        track_side_effects(hooks, flag, on_first_side_effect=_persist)

    async def _run_agent(self, agent: "Agent", goal: "Goal", run: "TaskRun") -> "AgentResult":
        if run.checkpoint_id and run.pending_answer is not None:
            # `TaskEngine.answer_run` -> `ITaskRunStore.resume_with_answer`
            # stashed this on the SAME run whose `checkpoint_id`/
            # `hil_question_id` were set together by the `_finalize_result`
            # call that put it into AWAITING_INPUT -- the three are always
            # mutually consistent at this point (see that method).
            hil_responses = {run.hil_question_id: run.pending_answer} if run.hil_question_id else None
            return await agent.resume(run.checkpoint_id, hil_responses=hil_responses)
        if run.checkpoint_id:
            return await agent.resume(run.checkpoint_id)
        return await agent.run(goal)

    async def _heartbeat_loop(self, run_id: str) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            ok = await self._run_store.heartbeat(run_id, self._owner, self._lease_seconds)
            if not ok:
                self._logger.warning("Lost lease on run %s during execution", run_id)
                return

    async def _latest_checkpoint(self, checkpoint_store: "CheckpointStore", agent_run_id: str) -> "AgentCheckpoint | None":
        return await checkpoint_store.latest(agent_run_id)

    async def _step_report_fields(self, timeline_store: "TimelineStore | None", agent_run_id: str) -> dict[str, Any]:
        """Pulls `TaskDagLoop`'s `completed_steps`/`failed_step_id`/
        `skipped_steps` (see `spec_assembler.TaskStepReport`) out of the
        timeline entry `agent.succeed`/`agent.fail` appended for this run,
        the only place that structured `detail` survives -- `AgentResult`
        itself (`agent_loop_lib/core/types.py`) has no field for it, by
        design (a core type must not carry task-engine-specific keys).
        `{}` for a non-DAG task (whose `detail` is always empty) or when no
        `timeline_store` is configured -- the caller's `**common` merge
        then leaves `TaskRun`'s own empty-list/`None` defaults untouched."""
        if timeline_store is None:
            return {}
        entries = await timeline_store.get_by_run(agent_run_id)
        if not entries:
            return {}
        detail = entries[-1].detail
        fields = {k: detail[k] for k in ("completed_steps", "failed_step_id", "skipped_steps") if k in detail}
        return {f"{k}": v for k, v in fields.items()}

    async def _finalize_result(
        self,
        run: "TaskRun",
        task: "TaskDefinition",
        agent: "Agent",
        result: "AgentResult",
        *,
        checkpoint_store: "CheckpointStore",
        timeline_store: "TimelineStore | None" = None,
    ) -> None:
        latest_checkpoint = await self._latest_checkpoint(checkpoint_store, agent.run_ctx.run_id)
        checkpoint_id = latest_checkpoint.checkpoint_id if latest_checkpoint else None
        now_iso = self._clock.now().astimezone(timezone.utc).isoformat()
        common = {
            "lease_owner": None,
            "lease_expires_at": None,
            "agent_run_id": agent.run_ctx.run_id,
            "checkpoint_id": checkpoint_id,
            "usage": result.usage.model_dump(),
            # Always clear a consumed answer -- see `_run_agent`'s docstring
            # on the invariant that pending_answer/hil_question_id/
            # checkpoint_id are only ever mutually consistent as of the
            # SAME `_finalize_result` call.
            "pending_answer": None,
            **await self._step_report_fields(timeline_store, agent.run_ctx.run_id),
        }

        if result.needs_input:
            updated = run.model_copy(update={
                **common,
                "status": RunStatus.AWAITING_INPUT,
                "hil_question_id": latest_checkpoint.hil_request_id if latest_checkpoint else None,
                "output_summary": result.needs_input,
            })
            if await self._run_store.update(updated, expected_owner=self._owner) is None:
                self._logger.warning(_LEASE_LOST_LOG, run.run_id)
                return
            self._logger.info(
                "Run %s awaiting input: question=%r hil_question_id=%s",
                run.run_id, result.needs_input[:200], updated.hil_question_id,
            )
            await self._notify(
                task, updated, TaskNotificationKind.AWAITING_INPUT,
                title=task.title, message="Task needs your input",
            )
            # Same reason as the code path: a question nobody can see is a run
            # that looks hung.
            await self._append_run_result_to_conversation(task, updated)
            return

        if result.success:
            output_summary = str(result.output) if result.output is not None else None
            self._logger.info(
                "Finalizing SUCCEEDED run_id=%s task_id=%s: output_summary=%r",
                run.run_id, task.task_id, output_summary[:300] if output_summary else None,
            )
            updated = run.model_copy(update={
                **common,
                "status": RunStatus.SUCCEEDED,
                "hil_question_id": None,
                "output_summary": output_summary,
                "completed_at": now_iso,
            })
            if await self._run_store.update(updated, expected_owner=self._owner) is None:
                self._logger.warning(_LEASE_LOST_LOG, run.run_id)
                return
            await self._on_run_succeeded(task, updated)
            await self._notify(
                task, updated, TaskNotificationKind.RUN_SUCCEEDED,
                title=task.title, message="Task completed",
            )
            await self._append_run_result_to_conversation(task, updated)
            return

        error = result.error or "Agent run did not succeed"
        updated = run.model_copy(update={
            **common,
            "status": RunStatus.FAILED,
            "hil_question_id": None,
            "error": error,
            "completed_at": now_iso,
        })
        if await self._run_store.update(updated, expected_owner=self._owner) is None:
            self._logger.warning(_LEASE_LOST_LOG, run.run_id)
            return
        await self._on_run_failed(task, updated)
        await self._notify(task, updated, TaskNotificationKind.RUN_FAILED, title=task.title, message="Task run failed")
        await self._append_run_result_to_conversation(task, updated)

    # -- Code workflow support ----------------------------------------------

    async def _build_run_broker(self, task: "TaskDefinition") -> "IPlatformBroker":
        """Build the per-run `PlatformBroker` for a code workflow.

        The registry is resolved the same way the agent path resolves it, so
        `ctx.tool()` sees exactly the tools an agent task for the same
        `TaskDefinition` would see -- including host-side credentials, which
        never cross into the sandbox.
        """
        from app.services.workflows.runtime.broker import build_platform_broker

        _, tool_registry, _ = await self._spec_assembler.build_context_and_tools(
            task,
            graph_provider=self._graph_provider,
            config_service=self._config_service,
            blob_store=self._blob_store,
            logger=self._logger,
        )
        return build_platform_broker(
            tool_registry=tool_registry,
            conversation_writer=self._conversation_writer,
            provisioning_service=self._agent_provisioning_service,
            state_store=self._workflow_state_store,
            agent_runner=self._agent_runner,
        )

    async def _trigger_payload(self, run: "TaskRun") -> dict[str, Any]:
        """Payload the trigger fired with, handed to the workflow entry point.

        Without this an event- or webhook-triggered workflow receives an empty
        dict and cannot see the event that started it.
        """
        if not run.trigger_id or self._trigger_store is None:
            return {}
        try:
            trigger = await self._trigger_store.get(run.trigger_id)
        except Exception:
            self._logger.warning("Could not load trigger %s for run %s", run.trigger_id, run.run_id)
            return {}
        if trigger is None:
            return {}
        return {
            "trigger_id": trigger.trigger_id,
            "trigger_kind": trigger.kind.value,
            "scheduled_for": run.scheduled_for,
            "payload": run.trigger_payload or {},
        }

    # -- Crash / abandonment recovery ---------------------------------------

    async def _recover_or_finalize(
        self, run: "TaskRun", task: "TaskDefinition", *, error: str, expected_owner: str | None = None,
    ) -> None:
        """`expected_owner` is passed by the in-execution crash path and left
        None by the reaper, which by definition never held the lease."""
        if run.is_dry_run:
            await self._finalize_dlq(
                run, task, error=f"Dry run failed: {error}", expected_owner=expected_owner,
            )
            return
        if run.had_write_side_effect and not run.checkpoint_id:
            await self._finalize_dlq(
                run, task,
                error=f"Unsafe to retry: a write side effect occurred with no checkpoint to resume from. {error}",
                expected_owner=expected_owner,
            )
            return

        max_attempts = task.retry_policy.max_attempts
        if run.attempt >= max_attempts:
            await self._finalize_dlq(
                run, task,
                error=f"Retries exhausted ({run.attempt}/{max_attempts}). {error}",
                expected_owner=expected_owner,
            )
            return

        await self._schedule_retry(run, task, error=error, expected_owner=expected_owner)

    async def _schedule_retry(
        self, run: "TaskRun", task: "TaskDefinition", *, error: str, expected_owner: str | None = None,
    ) -> None:
        delay = task.retry_policy.delay_for_attempt(run.attempt)
        retried = run.model_copy(update={
            "status": RunStatus.PENDING,
            "lease_owner": None,
            "lease_expires_at": None,
            "attempt": run.attempt + 1,
            "error": error,
        })
        if await self._run_store.update(retried, expected_owner=expected_owner) is None:
            self._logger.warning(
                "Skipping retry of run %s: its lease was reclaimed by another worker", run.run_id,
            )
            return
        self._spawn_background(self._retry_after_delay(retried, delay))

    async def _retry_after_delay(self, run: "TaskRun", delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self._publish_dispatch(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("Failed to republish retry for run %s", run.run_id)

    async def _finalize_dlq(
        self, run: "TaskRun", task: "TaskDefinition", *, error: str, expected_owner: str | None = None,
    ) -> None:
        updated = run.model_copy(update={
            "status": RunStatus.DLQ,
            "lease_owner": None,
            "lease_expires_at": None,
            "error": error,
            "completed_at": self._clock.now().astimezone(timezone.utc).isoformat(),
        })
        if await self._run_store.update(updated, expected_owner=expected_owner) is None:
            self._logger.warning(
                "Skipping DLQ of run %s: its lease was reclaimed by another worker", run.run_id,
            )
            return
        # A DLQ'd run is given up on: it will not retry and nothing else will
        # move it, so this line is the only record of why it stopped.
        self._logger.error(
            "Run %s DLQ'd: task=%s org=%s attempt=%d trigger=%s error=%s",
            run.run_id, run.task_id, task.org_id, run.attempt,
            run.trigger_id or "<manual>", error,
        )
        await self._on_run_failed(task, updated)
        await self._notify(task, updated, TaskNotificationKind.TASK_DLQ, title=task.title, message="Task run moved to DLQ")
        await self._append_run_result_to_conversation(task, updated)

    # -- Task-level bookkeeping (consecutive failures / auto-disable) -------

    async def _on_run_succeeded(self, task: "TaskDefinition", run: "TaskRun") -> None:
        # Reset consecutive_failure_count if needed.
        if task.consecutive_failure_count > 0:
            for _ in range(_TASK_STORE_UPDATE_RETRIES):
                current = await self._task_store.get(task.task_id, task.org_id)
                if current is None or current.consecutive_failure_count == 0:
                    break
                updated = current.model_copy(update={"consecutive_failure_count": 0})
                try:
                    await self._task_store.update(updated, expected_revision=current.revision)
                    break
                except OptimisticConcurrencyError:
                    continue
            else:
                self._logger.warning(
                    "Could not reset consecutive_failure_count for task %s (concurrent updates)", task.task_id,
                )

        # Auto-complete one-time workflows.
        # run_now calls have trigger_id=None and must never complete a workflow.
        if run.trigger_id is None or self._trigger_store is None:
            return
        try:
            triggers = await self._trigger_store.list_for_task(task.task_id)
        except Exception:
            self._logger.warning("Could not list triggers for auto-complete check: task=%s", task.task_id)
            return
        if not triggers:
            return

        def _can_fire_again(t: "TaskTrigger") -> bool:
            # EVENT/WEBHOOK triggers fire indefinitely on external signals.
            if t.kind in (TriggerKind.EVENT, TriggerKind.WEBHOOK):
                return True
            # The ONE_TIME trigger that produced this run has just fired.
            # The scheduler hasn't persisted run_count yet (it publishes to
            # Kafka *before* upsert), so we use run.trigger_id to detect it
            # instead of reading run_count from Redis.
            if t.trigger_id == run.trigger_id and t.kind == TriggerKind.ONE_TIME:
                return False
            return self._schedule_calculator.next_run_at(t) is not None

        if not any(_can_fire_again(t) for t in triggers):
            await self._mark_completed(task)

    async def _mark_completed(self, task: "TaskDefinition") -> None:
        """Transition a task to COMPLETED after all its scheduled triggers are exhausted."""
        for _ in range(_TASK_STORE_UPDATE_RETRIES):
            current = await self._task_store.get(task.task_id, task.org_id)
            if current is None:
                return
            if current.status == TaskStatus.COMPLETED:
                return
            updated = current.model_copy(update={
                "status": TaskStatus.COMPLETED,
                "enabled": False,
            })
            try:
                await self._task_store.update(updated, expected_revision=current.revision)
                self._logger.info("Task %s auto-completed (all one-time triggers exhausted)", task.task_id)
                return
            except OptimisticConcurrencyError:
                continue
        self._logger.warning("Could not mark task %s as completed (concurrent updates)", task.task_id)

    async def _on_run_failed(self, task: "TaskDefinition", run: "TaskRun") -> None:
        if run.is_dry_run:
            return
        for _ in range(_TASK_STORE_UPDATE_RETRIES):
            current = await self._task_store.get(task.task_id, task.org_id)
            if current is None:
                return
            count = current.consecutive_failure_count + 1
            auto_disable = count >= current.budget.max_consecutive_failures and current.enabled
            updates: dict[str, Any] = {"consecutive_failure_count": count}
            if auto_disable:
                updates["status"] = TaskStatus.DISABLED
                updates["enabled"] = False
            updated = current.model_copy(update=updates)
            try:
                await self._task_store.update(updated, expected_revision=current.revision)
            except OptimisticConcurrencyError:
                continue
            if auto_disable:
                await self._notify(
                    updated, run, TaskNotificationKind.TASK_AUTO_DISABLED,
                    title=updated.title,
                    message=f"Auto-disabled after {count} consecutive failures",
                )
            return
        self._logger.warning("Could not update consecutive_failure_count for task %s (concurrent updates)", task.task_id)

    async def _append_run_result_to_conversation(
        self, task: "TaskDefinition", run: "TaskRun"
    ) -> None:
        """Post the run result back to the originating conversation via Node,
        when `conversation_writer` is configured and the task was created from
        a conversation."""
        # Both skips end with the user seeing nothing in chat, which is
        # indistinguishable from a failed write unless the reason is recorded.
        if self._conversation_writer is None:
            self._logger.info(
                "Run %s result not posted to chat: no conversation writer wired", run.run_id,
            )
            return
        cid = getattr(task, "created_from_conversation_id", None)
        if not cid:
            self._logger.info(
                "Run %s result not posted to chat: task %s was not created from a conversation",
                run.run_id, task.task_id,
            )
            return
        try:
            from app.services.workflows.domain.models import RunResultMessage
            redirect_link = f"/chat?conversationId={cid}"

            trigger_kind: str | None = None
            if run.trigger_id and self._trigger_store is not None:
                try:
                    trigger = await self._trigger_store.get(run.trigger_id)
                    if trigger is not None:
                        trigger_kind = trigger.kind.value
                except Exception:
                    pass

            msg = RunResultMessage(
                workflow_id=task.task_id,
                run_id=run.run_id,
                status=run.status.value,
                output_summary=run.output_summary,
                redirect_link=redirect_link,
                workflow_name=task.title,
                error=run.error,
                is_dry_run=run.is_dry_run,
                trigger_kind=trigger_kind,
                started_at=run.started_at,
                completed_at=run.completed_at,
                suspension_kind=run.suspension_kind,
            )
            await self._conversation_writer.append_result(cid, task.org_id, msg)
            self._logger.info(
                "Posted run %s to conversation %s: status=%s, dry_run=%s, "
                "trigger=%s, summary_chars=%d, error=%r",
                run.run_id, cid, run.status.value, run.is_dry_run, trigger_kind,
                len(run.output_summary or ""), run.error,
            )
        except Exception:
            self._logger.exception(
                "_append_run_result_to_conversation: failed for run_id=%s task_id=%s",
                run.run_id, task.task_id,
            )

    async def _notify(
        self, task: "TaskDefinition", run: "TaskRun", kind: TaskNotificationKind, *, title: str, message: str,
    ) -> None:
        # A dry run must not land in anyone's notification inbox, but the
        # in-chat dry-run card follows the same live socket event as a real
        # run and would otherwise never leave "started". So run-lifecycle
        # kinds are still published (the Node consumer skips persistence for
        # `isDryRun`) while everything else stays suppressed.
        if run.is_dry_run and kind not in _DRY_RUN_NOTIFIABLE_KINDS:
            self._logger.info("_notify: dry run %s — notification suppressed (kind=%s)", run.run_id, kind.value)
            return
        try:
            cid = task.created_from_conversation_id
            # Deep-link to the originating conversation when available; fall
            # back to the workflows detail page so the link is never null.
            redirect_link = (
                f"/chat?conversationId={cid}" if cid else f"/workflows?workflowId={task.task_id}"
            )
            # Find the trigger kind for metadata (best-effort).
            trigger_kind: str | None = None
            if run.trigger_id and self._trigger_store is not None:
                try:
                    trigger = await self._trigger_store.get(run.trigger_id)
                    if trigger is not None:
                        trigger_kind = trigger.kind.value
                except Exception:
                    pass

            await self._notifier.notify(TaskNotification(
                kind=kind,
                org_id=task.org_id,
                user_id=task.principal.user_id,
                task_id=task.task_id,
                run_id=run.run_id,
                title=title,
                message=message,
                # workflow_id == task_id; required so the Node consumer's
                # WORKFLOW_RUN_TYPES branch pushes a live workflowRunUpdate
                # socket event.
                workflow_id=task.task_id,
                conversation_id=cid,
                redirect_link=redirect_link,
                run_status=run.status.value if run.status else None,
                trigger_kind=trigger_kind,
                output_summary=run.output_summary,
                workflow_name=task.title,
                is_dry_run=run.is_dry_run,
            ))
        except Exception:
            self._logger.exception("Notifier failed for task %s run %s", task.task_id, run.run_id)

    # -- Dispatch (retry republish + outbox reaper) --------------------------

    async def _publish_dispatch(self, run: "TaskRun") -> bool:
        return await self._producer.send_event(
            topic=Topic.TASK_EVENTS.value,
            event_type=_TASK_RUN_DISPATCH_EVENT,
            payload={
                "run_id": run.run_id,
                "task_id": run.task_id,
                "trigger_id": run.trigger_id,
                "org_id": run.org_id,
                "scheduled_for": run.scheduled_for,
                "idempotency_key": run.idempotency_key,
            },
            key=run.task_id,
        )

    # -- Reaper: runs whose worker died outright (no exception caught here) --

    async def _reaper_loop(self) -> None:
        while self._running:
            try:
                await self.run_reaper_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("Unhandled error in task reaper tick (owner=%s)", self._owner)
            try:
                await asyncio.sleep(self._reap_tick_interval_seconds)
            except asyncio.CancelledError:
                break

    async def run_reaper_tick(self) -> int:
        now = self._clock.now()
        reaped = await self._run_store.reap_abandoned(now=now)
        for run in reaped:
            task = await self._task_store.get(run.task_id, run.org_id)
            if task is None:
                self._logger.warning("Abandoned run %s references a missing task %s", run.run_id, run.task_id)
                continue
            await self._recover_or_finalize(run, task, error="Worker died without heartbeat (lease expired)")
        expired = await self._expire_stale_suspensions(now)
        return len(reaped) + expired

    async def _expire_stale_suspensions(self, now: "datetime") -> int:
        """Retire suspended runs that outlived their execution journal.

        A run parked on an approval nobody answered keeps offering itself as
        answerable long after its journal is gone; accepting that answer would
        replay the workflow from the top and repeat every step the first
        attempt completed. Failing it here makes the outcome deliberate and
        visible instead of deferred to whoever eventually clicks Approve.
        """
        stale = await self._run_store.list_expired_suspensions(now=now)
        for run in stale:
            self._logger.warning(
                "Failing run %s: suspended (%s) past its replay deadline %s",
                run.run_id, run.suspension_kind or "unknown", run.resume_deadline_at,
            )
            updated = run.model_copy(update={
                "status": RunStatus.FAILED,
                "error": (
                    "Suspended longer than the execution journal is retained; "
                    "resuming would have re-run completed steps."
                ),
                "completed_at": now.astimezone(timezone.utc).isoformat(),
            })
            if await self._run_store.update(updated) is None:
                continue
            task = await self._task_store.get(run.task_id, run.org_id)
            if task is None:
                continue
            await self._on_run_failed(task, updated)
            await self._notify(
                task, updated, TaskNotificationKind.RUN_FAILED,
                title="Workflow expired", message=updated.error or "",
            )
        return len(stale)
