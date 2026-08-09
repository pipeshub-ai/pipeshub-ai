"""Domain models for the task scheduling engine.

Pure data. Zero I/O, zero dependency on `agent_loop_lib`, zero dependency on
any storage backend (`IGraphDBProvider`, Redis, etc). Everything here is
JSON-safe and carries `schema_version` so adapters can migrate stored
payloads independently of this module's evolution.

Boundary rule: nothing in `app.services.tasks.domain` may import from
`app.agent_loop_lib` or any `app.services.graph_db` / `app.services.messaging`
module. The only file allowed to import both this domain and `agent_loop_lib`
is `runtime/spec_assembler.py` (the anti-corruption layer).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, model_validator

from app.services.tasks.domain.policies import BudgetPolicy, MisfirePolicy, RetryPolicy

CURRENT_SCHEMA_VERSION = 2


class TaskStatus(str, Enum):
    """Lifecycle of a `TaskDefinition`."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    """Terminal: all one-time triggers have fired and the last run succeeded.
    A completed task never fires again (enabled=False) but is preserved for
    history. Distinct from DISABLED (admin/auto-disabled on failures) and
    CANCELLED (soft-delete, no future runs intended)."""


class TriggerKind(str, Enum):
    ONE_TIME = "one_time"
    CRON = "cron"
    INTERVAL = "interval"
    EVENT = "event"
    WEBHOOK = "webhook"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"
    AWAITING_INPUT = "awaiting_input"
    DLQ = "dlq"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.ABANDONED, RunStatus.DLQ, RunStatus.CANCELLED}
)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStep(BaseModel):
    """One node in a task's sub-task DAG. Mirrors the shape of
    `agent_loop_lib.modules.pipeline.planner.base.PlanStep` deliberately —
    the `TaskSpecAssembler` translates a list of these into synthetic
    `spawn_agent` calls, so keeping the field names aligned avoids a mapping
    table that would just be duplicated documentation.
    """

    id: str
    description: str
    domain: str = ""
    tool_names: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    output_format: str | None = None
    status: StepStatus = StepStatus.PENDING


class TaskPrincipal(BaseModel):
    """The identity and authority a task runs with. Persisted at creation
    time so a headless scheduler tick has something to build an
    `AgentContext` from, but every field is RE-VALIDATED at run time
    (permissions re-resolved, user re-checked for disabled/removed) rather
    than trusted as a cached snapshot — see `application/prerequisites.py`.
    """

    org_id: str
    user_id: str
    user_email: str
    is_service_account: bool = False


class TaskDefinition(BaseModel):
    """What to do. Owned by the graph store (`ITaskStore`)."""

    schema_version: int = CURRENT_SCHEMA_VERSION

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    org_id: str
    created_by_user_id: str
    revision: int = 0
    """Optimistic concurrency token. `ITaskStore.update()` must be called
    with the revision the caller last read; a mismatch means someone else
    mutated the task since, and the caller must re-read and retry."""

    principal: TaskPrincipal

    title: str
    description: str
    """Original natural-language request, verbatim."""
    instructions: str
    """Assembled prompt actually handed to the agent at execution time --
    distinct from `description` because it folds in clarifications and
    reasoning, per Part A2 of the plan."""
    clarifications: list[dict[str, Any]] = Field(default_factory=list)
    reasoning: str | None = None

    steps: list[TaskStep] | None = None

    tool_names: list[str] = Field(default_factory=list)
    toolset_ids: list[str] = Field(default_factory=list)
    mcp_server_ids: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    collection_ids: list[str] = Field(default_factory=list)
    connector_ids: list[str] = Field(default_factory=list)

    model_ref: str | None = None
    loop_strategy_name: str = "react"
    execution_kind: str = "agent_task"
    """'agent_task' (default, existing behavior) or 'code' (runs a WorkflowVersion)."""
    workflow_version_id: str | None = None
    """Set only when execution_kind == 'code'. Identifies the pinned WorkflowVersion."""
    max_turns: int = 15
    timeout_seconds: int = 900
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    budget: BudgetPolicy = Field(default_factory=BudgetPolicy)

    status: TaskStatus = TaskStatus.DRAFT
    enabled: bool = True
    consecutive_failure_count: int = 0
    promoted_agent_id: str | None = None

    created_from_conversation_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def next_revision(self) -> int:
        return self.revision + 1


class TaskTrigger(BaseModel):
    """When to run. 0..N per task. Owned by Redis (`ITriggerStore`)."""

    schema_version: int = CURRENT_SCHEMA_VERSION

    trigger_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    org_id: str
    kind: TriggerKind

    cron_expression: str | None = None
    interval_seconds: int | None = None
    fire_at: str | None = None
    """ISO-8601. Used by ONE_TIME triggers."""
    timezone: str = "UTC"

    event_filter: dict[str, Any] | None = None
    webhook_id: str | None = None

    next_run_at: str | None = None
    """ISO-8601 UTC. None means "never fires again" (e.g. exhausted
    ONE_TIME or max_runs reached)."""
    last_fire_at: str | None = None
    misfire_policy: MisfirePolicy = MisfirePolicy.SKIP
    max_runs: int | None = None
    run_count: int = 0
    enabled: bool = True

    lease_owner: str | None = None
    lease_expires_at: str | None = None

    def is_exhausted(self) -> bool:
        return self.max_runs is not None and self.run_count >= self.max_runs

    @model_validator(mode="after")
    def _check_kind_specific_fields(self) -> "TaskTrigger":
        """Every field access in `ScheduleCalculator`/`RedisTriggerStore`/
        `WebhookDispatchService` assumes its kind's required field is
        present -- enforced once here instead of at every call site (and at
        every deserialization path: `_hash_to_trigger`, REST bodies,
        `task_manage(action="create")` specs all funnel through this same
        constructor)."""
        if self.kind == TriggerKind.CRON and not self.cron_expression:
            raise ValueError("cron trigger requires cron_expression")
        if self.kind == TriggerKind.INTERVAL and not (self.interval_seconds and self.interval_seconds > 0):
            raise ValueError("interval trigger requires a positive interval_seconds")
        if self.kind == TriggerKind.ONE_TIME and not self.fire_at:
            raise ValueError("one_time trigger requires fire_at")
        if self.kind == TriggerKind.EVENT and not (self.event_filter and self.event_filter.get("event_type")):
            raise ValueError("event trigger requires event_filter.event_type")
        if self.kind == TriggerKind.WEBHOOK and not self.webhook_id:
            raise ValueError("webhook trigger requires webhook_id (server-generated -- do not set it yourself)")
        return self


class TaskRun(BaseModel):
    """One execution. Owned by Redis (`ITaskRunStore`)."""

    schema_version: int = CURRENT_SCHEMA_VERSION

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    trigger_id: str | None = None
    org_id: str

    idempotency_key: str

    status: RunStatus = RunStatus.PENDING
    had_write_side_effect: bool = False
    is_dry_run: bool = False

    trigger_payload: dict[str, Any] = Field(default_factory=dict)
    """Body the trigger fired with (webhook body, normalized app event).
    Empty for schedule-driven runs. Code workflows receive this as their
    entry point's second argument; without it an event-triggered workflow
    cannot see the event that started it."""

    lease_owner: str | None = None
    lease_expires_at: str | None = None

    completed_steps: list[str] = Field(default_factory=list)
    failed_step_id: str | None = None
    skipped_steps: list[str] = Field(default_factory=list)

    attempt: int = 1
    agent_run_id: str | None = None
    checkpoint_id: str | None = None
    hil_question_id: str | None = None
    pending_answer: str | None = None
    """Set by `ITaskRunStore.resume_with_answer` when a user answers an
    AWAITING_INPUT run's question; consumed and cleared by `TaskExecutor`
    on its next claim (injected into `Agent.resume(hil_responses=...)`
    keyed by `hil_question_id`)."""

    suspended_step_key: str | None = None
    """Journal step key a code workflow suspended on (`ctx.wait_for_event`
    or `ctx.request_approval`). Resumption appends a journal entry under
    this key so the replayed run returns the answer instead of re-suspending."""
    suspension_kind: str | None = None
    """"wait_for_event" or "approval" -- which primitive parked the run."""
    awaiting_event_type: str | None = None
    """Event type a `ctx.wait_for_event` suspension is parked on.

    `fire_event` looks runs up by this to resume them; without it a parked run
    is unreachable and waits until its journal TTL instead of until the event
    it named."""
    resume_deadline_at: str | None = None
    """ISO-8601 UTC. Past this point the suspended run's journal may already
    have expired, and replaying against a missing journal would re-execute
    completed steps as if they were new. Set from the journal's own retention
    window at suspension; the reaper fails runs that reach it unanswered."""

    output_summary: str | None = None
    error: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)

    scheduled_for: str | None = None
    """ISO-8601 UTC fire time this run corresponds to -- part of the
    idempotency key, kept here too for display/debugging."""
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str = ""

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES


def compute_idempotency_key(task_id: str, occasion: str) -> str:
    """Deterministic key so the same (task, occasion) pair, dispatched
    twice (e.g. scheduler crash-restart re-claims a trigger it already
    claimed), produces exactly one `TaskRun` via
    `ITaskRunStore.create_if_absent`.

    `occasion` identifies *what* the run is for. Clock-driven triggers pass
    their fire time; event- and webhook-driven ones pass the provider's
    delivery id, because their fire time is "whenever the message was
    consumed" and so differs on every redelivery of the same event.
    """
    raw = f"{task_id}:{occasion}".encode()
    return hashlib.sha256(raw).hexdigest()


DECLARATIVE_TRIGGER_PREFIX = "decl-"
"""Marks a trigger derived from `@workflow(triggers=[...])` in workflow source,
so reconciliation can prune stale ones without touching hand-created triggers."""


def compute_declarative_trigger_id(task_id: str, spec: dict[str, Any]) -> str:
    """Deterministic `trigger_id` for a trigger declared in workflow source.

    Code generation re-derives the same `@workflow(triggers=[...])` list on
    every regeneration; without a stable id each regeneration would insert a
    fresh row and the workflow would fire N times per schedule.
    """
    parts = [
        task_id,
        str(spec.get("kind", "")),
        str(spec.get("cron_expression") or ""),
        str(spec.get("interval_seconds") or ""),
        str(spec.get("fire_at") or ""),
        str(spec.get("timezone") or ""),
        json.dumps(spec.get("event_filter") or {}, sort_keys=True),
    ]
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return f"{DECLARATIVE_TRIGGER_PREFIX}{digest[:32]}"


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Minimal cursor-free pagination envelope for task list endpoints.
    Deliberately not a repo-wide shared type -- no such generic exists
    today (checked), and inventing one here would overreach this feature's
    scope."""

    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class TaskQuery(BaseModel):
    """Filter/sort parameters for `ITaskStore.list()`."""

    org_id: str
    created_by_user_id: str | None = None
    status: TaskStatus | None = None
    enabled: bool | None = None
    text_search: str | None = None
    created_from_conversation_id: str | None = None
    limit: int = 50
    offset: int = 0
