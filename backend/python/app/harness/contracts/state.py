from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.harness.contracts.generated.pipeshub import enums
from app.utils.output_language import LOCALE_PATTERN, MAX_LOCALE_CHARS
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec

RunStatus = enums.ExecutionStatus


class WireModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class ImmutableObjectRef(WireModel):
    object_id: str = Field(alias="objectId")
    content_hash: str = Field(alias="contentHash")
    size_bytes: int = Field(alias="sizeBytes", ge=0)


class RunModelInput(WireModel):
    model_key: str | None = Field(default=None, alias="modelKey")
    model_name: str | None = Field(default=None, alias="modelName")
    model_provider: str | None = Field(default=None, alias="modelProvider")
    model_friendly_name: str | None = Field(default=None, alias="modelFriendlyName")
    reasoning_effort: str | None = Field(default=None, alias="reasoningEffort")
    chat_mode: str = Field(alias="chatMode")


class RunCaller(WireModel):
    """The person behind a service credential (a Slack user), for the model's context only.

    Node sets the email from the verified token and the display name from the bot;
    as in legacy, neither changes whose knowledge the run reads.
    """

    email: str | None = Field(default=None, max_length=320)
    display_name: str | None = Field(default=None, alias="displayName", max_length=256)


class RunInputArtifact(WireModel):
    """An artifact the message names for an engine run to work on (E1b); ``version`` absent: its latest."""

    artifact_id: str = Field(alias="artifactId", min_length=1, max_length=512)
    version: int | None = Field(default=None, ge=1)


class RunCommand(WireModel):
    """The /command the query was expanded from (SLASH1). The query is already the expanded text; this is the record."""

    name: str = Field(max_length=64)
    version: str = Field(max_length=64)
    content_digest: str = Field(alias="contentDigest", pattern=r"^sha256:[0-9a-f]{64}$")
    args: dict[str, str] = Field(default_factory=dict)
    typed: str = Field(max_length=100_000)
    owner: str = Field(max_length=32)


class RunCompact(WireModel):
    """The person's ``/compact [focus]`` (SLASH2 C2), taken into this run at admission: compact before its first turn."""

    focus: str | None = Field(default=None, min_length=1, max_length=500)


class RunSandboxCommand(WireModel):
    """The person's ``/run`` (SLASH3): no model call; the query's line (after ``/run ``) runs through ``run_code``."""

    timeout_s: int = Field(alias="timeoutS", ge=1, le=600)


_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_ISO = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$"


class RunTriggerWindow(WireModel):
    """The coalescing window an event trigger's firing covers: every event in it is this one run."""

    start: str = Field(pattern=_ISO)
    end: str = Field(pattern=_ISO)


class RunTriggerEvent(WireModel):
    """The connector event that opened the window. ``record_id`` is absent when the actor may not read the record."""

    connector_id: str = Field(alias="connectorId", pattern=_ID)
    event_type: Literal["newRecord"] = Field(alias="eventType")
    at: str = Field(pattern=_ISO)
    record_id: str | None = Field(default=None, alias="recordId", pattern=_ID)


class RunTriggerWebhook(WireModel):
    """The webhook request that fired the run, described, never its body (outsider-written content)."""

    received_at: str = Field(alias="receivedAt", pattern=_ISO)
    body_bytes: int = Field(alias="bodyBytes", ge=0)
    body_sha256: str = Field(alias="bodySha256", pattern=r"^sha256:[0-9a-f]{64}$")
    content_type: str | None = Field(default=None, alias="contentType", max_length=255)


class RunTrigger(WireModel):
    """What fired a scheduled run (TR-03): ids and times only. An engine run finds it at ``in/trigger.json``."""

    kind: Literal["cron", "manual", "webhook", "connector"]
    schedule_id: str = Field(alias="scheduleId", pattern=_ID)
    firing_id: str = Field(alias="firingId", pattern=_ID)
    slot_at: str = Field(alias="slotAt", pattern=_ISO)
    window: RunTriggerWindow | None = None
    event: RunTriggerEvent | None = None
    webhook: RunTriggerWebhook | None = None


class RunInputSnapshot(WireModel):
    query: str
    record_ids: tuple[str, ...] = Field(default=(), alias="recordIds")
    filters: dict[str, Any] = Field(default_factory=dict)
    applied_filters: dict[str, Any] | None = Field(default=None, alias="appliedFilters")
    attachments: tuple[dict[str, Any], ...] = ()
    tools: tuple[str, ...] | None = None
    agent_capabilities: dict[str, bool] | None = Field(
        default=None, alias="agentCapabilities"
    )
    model: RunModelInput
    timezone: str | None = None
    current_time: str | None = Field(default=None, alias="currentTime")
    # G-52: the person's locale (a BCP 47 tag, from the request's Accept-Language); the answer's language. Sent only
    # to a worker that advertises ``output_locale``.
    locale: str | None = Field(default=None, max_length=MAX_LOCALE_CHARS, pattern=LOCALE_PATTERN)
    previous_conversations: tuple[dict[str, Any], ...] = Field(
        default=(), alias="previousConversations"
    )
    caller: RunCaller | None = None
    input_artifacts: tuple[RunInputArtifact, ...] = Field(default=(), alias="inputArtifacts", max_length=20)
    command: RunCommand | None = None
    compact: RunCompact | None = None
    sandbox_command: RunSandboxCommand | None = Field(default=None, alias="sandboxCommand")
    trigger: RunTrigger | None = None
    # Opt-in measurement frames (``retrieval_context``, ``run_usage``) on the run's stream (SYNC2-F1).
    include_retrieval_context: bool = Field(default=False, alias="includeRetrievalContext")
    # How long each knowledge search waits for its keyword matches (the chat turn's ``keywordSearchBudget``,
    # R33JGAPS-FU-1). Sent only to a worker that advertises ``keyword_search_budget``.
    keyword_search_budget: Literal["short", "full"] | None = Field(default=None, alias="keywordSearchBudget")


class HarnessRun(WireModel):
    run_id: str = Field(alias="runId")
    org_id: str = Field(alias="orgId")
    session_id: str = Field(alias="sessionId")
    actor_id: str = Field(alias="actorId")
    agent_key: str | None = Field(default=None, alias="agentKey")
    user_message_key: str = Field(alias="userMessageKey")
    idempotency_key: str = Field(alias="idempotencyKey")
    payload_hash: str = Field(alias="payloadHash")
    # Pinned by Node at admission; runs admitted before pinning are durable v1.
    harness_major: Literal[1, 2] = Field(default=1, alias="harnessMajor")
    profile: Literal["inline", "dispatched"]
    status: RunStatus
    outcome: enums.RunOutcome | None
    revision: int = Field(ge=0)
    fence: int = Field(ge=0)
    attempt_id: str | None = Field(alias="attemptId")
    worker_id: str | None = Field(alias="workerId")
    lease_expires_at: int | None = Field(alias="leaseExpiresAt")
    absolute_deadline_at: int = Field(alias="absoluteDeadlineAt")
    checkpoint: ImmutableObjectRef | None
    # Claims since the run last left running cleanly; the quarantine cap counts it.
    attempts: int | None = Field(default=None, ge=0)
    not_before: int | None = Field(default=None, alias="notBefore")
    checkpoint_ref: str | None = Field(default=None, alias="checkpointRef")
    input: RunInputSnapshot | None = None
    created_at: int = Field(alias="createdAt")
    updated_at: int = Field(alias="updatedAt")


class PinnedRunSpec(WireModel):
    """The spec Node pinned on the run's first claim (harness major 2 only).

    ``spec_digest`` identifies the pin in ``run.spec_pinned``; ``spec`` is the
    pin as stored, at a schema version this worker advertised it reads.
    """

    spec: dict[str, Any]
    spec_digest: str = Field(alias="specDigest", pattern=r"^sha256:[0-9a-f]{64}$")
    capabilities: dict[str, Any]
    capabilities_digest: str = Field(
        alias="capabilitiesDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )

    def run_spec(self) -> RunSpec:
        return validate_contract(RunSpec, self.spec)


class WorkerCredentialProvider(Protocol):
    """The per-deployment worker credential, presented only to claim."""

    async def worker_credential(self) -> str: ...
