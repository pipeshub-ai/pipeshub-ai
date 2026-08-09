"""Domain models for the code-first workflow system.

Pure data. Zero I/O. Zero dependency on any infra.
Boundary rule: nothing here may import from agent_loop_lib, graph_db, messaging,
redis, or any concrete adapter.
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

JsonValue = Any  # recursive type alias


class WorkflowKind(str, Enum):
    AGENT_TASK = "agent_task"
    CODE = "code"


class WorkflowStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"
    DRAFT = "draft"
    COMPLETED = "completed"
    """All one-time triggers fired successfully; the workflow is archived."""


class FilterOp(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    PREFIX = "prefix"
    EXISTS = "exists"
    GT = "gt"
    LT = "lt"
    GTE = "gte"
    LTE = "lte"


class TriggerSummary(BaseModel):
    trigger_id: str
    kind: str
    next_run_at: str | None = None
    last_fire_at: str | None = None
    enabled: bool = True


class TraceEntry(BaseModel):
    """One step of a run, normalised across execution kinds.

    A code workflow's trace comes from the replay journal and an agent task's
    from the agent timeline; they carry different fields, but the run inspector
    should not have to know which kind it is looking at.
    """
    seq: int
    kind: str
    """Journal `entry_kind` (tool/agent/state/...) or agent `event_type`."""
    label: str
    outcome: str
    target: str | None = None
    """What the step acted on -- a tool path, agent id, or state key. Matches
    `IRNode.metadata['tool_path']`, which is how the run inspector maps a
    graph node to the trace rows it produced."""
    timestamp: str | None = None
    error: str | None = None
    attempt: int = 1
    detail: dict[str, JsonValue] = Field(default_factory=dict)


class FilterPredicate(BaseModel):
    """One predicate in an EventSubscription filter conjunction (§4.5)."""
    field: str
    op: FilterOp
    value: JsonValue
    display_label: str | None = None


class EventSubscription(BaseModel):
    """A workflow's subscription to an app event type with optional filters."""
    subscription_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str
    event_type: str                         # namespaced: "slack.message.posted"
    filter: list[FilterPredicate] = Field(default_factory=list)
    display_labels: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    created_at: str = ""


class Workflow(BaseModel):
    """User-facing projection of a TaskDefinition. workflow_id == task_id."""
    workflow_id: str
    org_id: str
    kind: WorkflowKind
    name: str
    description: str
    current_version_id: str | None = None
    triggers: list[TriggerSummary] = Field(default_factory=list)
    subscriptions: list[EventSubscription] = Field(default_factory=list)
    status: WorkflowStatus
    required_scopes: list[str] = Field(default_factory=list)
    execution_kind: str = "agent_task"
    created_by_user_id: str
    created_at: str = ""
    updated_at: str = ""
    conversation_id: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    connector_ids: list[str] = Field(default_factory=list)
    collection_ids: list[str] = Field(default_factory=list)
    max_turns: int | None = None
    timeout_seconds: int | None = None


class ArtifactRef(BaseModel):
    artifact_id: str
    version: str | None = None


class StepOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ResultRef(BaseModel):
    """Inline small results; ArtifactRef above threshold."""
    inline: JsonValue | None = None
    artifact: ArtifactRef | None = None


class ErrorRecord(BaseModel):
    code: str
    message: str
    traceback: str | None = None


class JournalEntry(BaseModel):
    """One durable step record for journaled-effect replay."""
    run_id: str
    seq: int
    step_key: str
    entry_kind: Literal[
        "step", "tool", "agent", "clock", "random", "wait", "approval", "uuid",
        "knowledge", "state", "emit", "sleep",
    ]
    idempotency_key: str
    outcome: StepOutcome
    result_ref: ResultRef | None = None
    error: ErrorRecord | None = None
    attempt: int = 1


class IRNodeKind(str, Enum):
    WORKFLOW = "workflow"
    STEP = "step"
    AGENT_CALL = "agent_call"
    TOOL_CALL = "tool_call"
    BRANCH = "branch"
    LOOP = "loop"
    UNRESOLVED = "unresolved"


class IRNode(BaseModel):
    node_id: str
    kind: IRNodeKind
    label: str
    source_start: int | None = None
    """1-based source line where this node starts; drives graph -> editor jumps."""
    source_end: int | None = None
    children: list[str] = Field(default_factory=list)   # child node_ids
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class IREdge(BaseModel):
    from_node: str
    to_node: str
    label: str | None = None


class WorkflowIR(BaseModel):
    """Versioned, deterministic graph extracted from workflow source code."""
    schema_version: int = 1
    nodes: list[IRNode] = Field(default_factory=list)
    edges: list[IREdge] = Field(default_factory=list)
    entry_node_id: str | None = None


class WorkflowVersion(BaseModel):
    """Immutable: created at save time, never mutated."""
    version_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    version_number: int = 0
    """Monotonic per workflow, assigned by the store on save. Ordering by
    `created_at` alone is ambiguous for versions saved in the same
    millisecond, which rollback and race detection both depend on."""
    workflow_id: str
    org_id: str
    bundle_ref: ArtifactRef | None = None
    sdk_version: str = "0.1.0"
    tool_pins: dict[str, str] = Field(default_factory=dict)
    """Every tool this version's source can reach, keyed by normalised
    `Tool.name` and valued by the literal the code passes to `ctx.tool()`.

    Derived from the IR at save time, so the run's grant is pinned to the code
    that was actually generated rather than to whatever the run's registry
    happens to resolve. See `domain/grants.compute_run_grant`."""
    agent_pins: set[str] = Field(default_factory=set)
    """Agent ids this version's source passes to `ctx.agent()`.

    The AGENT_RUN counterpart to `tool_pins`: an empty `grant.agent_ids` lets
    the run drive any agent in the org, so the grant is pinned to the ids the
    generated code was written against."""
    ir: WorkflowIR = Field(default_factory=WorkflowIR)
    generation_spec: str | None = None
    content_hash: str = ""
    created_at: str = ""
    created_by_user_id: str = ""
    verifier_version: int = 0
    """The `codegen.verifier.CURRENT_VERIFIER_VERSION` in effect when this
    version's source last passed verification. `0` means "predates this
    field" — every version written before Phase 4 landed. Compared against
    the live constant to flag a version as needing regeneration without
    re-running the verifier against every historical version on every
    deploy (see `codegen.verifier.is_version_stale` and
    `application/version_writer.py`)."""
    verified_at: str | None = None
    """ISO-8601 timestamp of the verification recorded in `verifier_version`."""


class RunResultMessage(BaseModel):
    """Compact result posted back to the originating conversation (§5.3)."""
    workflow_id: str
    run_id: str
    status: str
    output_summary: str | None = None
    redirect_link: str
    workflow_name: str | None = None
    error: str | None = None
    is_dry_run: bool = False
    trigger_kind: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    suspension_kind: str | None = None
    """"approval" or "wait_for_event" when `status` is awaiting_input. Decides
    whether the chat card offers approve/reject buttons or just reports what
    the run is waiting on -- the two are not answerable the same way."""


class WorkflowQuery(BaseModel):
    org_id: str
    created_by_user_id: str | None = None
    status: WorkflowStatus | None = None
    kind: WorkflowKind | None = None
    text_search: str | None = None
    limit: int = 50
    offset: int = 0
