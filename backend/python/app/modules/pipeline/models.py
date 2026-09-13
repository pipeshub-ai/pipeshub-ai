"""Contracts shared by the pipeline coordinator, the stage worker and the stages.

Everything that crosses a component boundary in the staged pipeline is one of these
models. Wire and storage forms are camelCase (``model_dump(by_alias=True)``), matching
the job envelope and the graph's record fields; parsing accepts either spelling.
"""

import hashlib
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, computed_field
from pydantic.alias_generators import to_camel

from app.config.constants.arangodb import ProgressStatus
from app.services.resource_governor.models import ParseTier

_WIRE = ConfigDict(frozen=True, alias_generator=to_camel, populate_by_name=True, extra="ignore")


class Priority(StrEnum):
    INTERACTIVE = "interactive"
    BULK = "bulk"


class Workload(StrEnum):
    """What a stage's permit is sized by; picks the governor pool its consumer admits through."""

    PARSE_HEAVY = "parse_heavy"
    PARSE_LIGHT = "parse_light"
    EMBEDDING = "embedding"
    LLM = "llm"
    IO = "io"


class HeadlineField(StrEnum):
    """Record-node status fields; each is owned by exactly one stage."""

    PARSING = "parsingStatus"
    INDEXING = "indexingStatus"
    EXTRACTION = "extractionStatus"


# Stages an API caller may re-run, and the record field that reports each. The registry
# refuses a stage whose headline disagrees, so the two cannot drift.
RETRYABLE_STAGES: Mapping[str, HeadlineField] = MappingProxyType({"classify": HeadlineField.EXTRACTION})


class StageOutcome(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    # The record moved to a newer revision while this job ran; its writes are discarded.
    STALE = "stale"
    RETRY = "retry"
    # A dependency's breaker is open: requeue without counting an attempt.
    PAUSED = "paused"
    FAILED = "failed"


def stage_state_key(virtual_record_id: str, rev: str, stage: str) -> str:
    return f"{virtual_record_id}:{rev}:{stage}"


class StageJob(BaseModel):
    """One unit of stage work for one content revision; ``job_id`` is its idempotency key."""

    model_config = _WIRE

    stage: str
    stage_version: int = Field(ge=1)
    org_id: str
    virtual_record_id: str
    rev: str
    # Records bound to the revision when the job was dispatched.
    record_ids: tuple[str, ...] = Field(min_length=1)
    connector_id: str
    tier: ParseTier
    priority: Priority
    # Upstream stage or event type, for tracing.
    trigger: str
    attempt: int = Field(default=1, ge=1)
    not_before_ms: int = Field(default=0, ge=0)
    # Run even when the stored fingerprint matches.
    force: bool = False
    # Content facts of the revision, so successors' ``applies`` needs no content read.
    mime_type: str | None = None
    text_digest: str
    blocks_digest: str
    text_chars: int = Field(ge=0)
    has_tables: bool
    has_images: bool

    @computed_field
    @property
    def job_id(self) -> str:
        return f"{self.virtual_record_id}:{self.rev}:{self.stage}@{self.stage_version}"

    @property
    def state_key(self) -> str:
        return stage_state_key(self.virtual_record_id, self.rev, self.stage)

    @property
    def view(self) -> "RecordView":
        return RecordView(
            org_id=self.org_id,
            virtual_record_id=self.virtual_record_id,
            rev=self.rev,
            record_ids=self.record_ids,
            connector_id=self.connector_id,
            tier=self.tier,
            mime_type=self.mime_type,
            text_digest=self.text_digest,
            blocks_digest=self.blocks_digest,
            text_chars=self.text_chars,
            has_tables=self.has_tables,
            has_images=self.has_images,
        )

    @classmethod
    def for_view(
        cls,
        view: "RecordView",
        *,
        stage: str,
        stage_version: int,
        priority: Priority,
        trigger: str,
        force: bool = False,
    ) -> "StageJob":
        return cls(
            stage=stage,
            stage_version=stage_version,
            org_id=view.org_id,
            virtual_record_id=view.virtual_record_id,
            rev=view.rev,
            record_ids=view.record_ids,
            connector_id=view.connector_id,
            tier=view.tier,
            priority=priority,
            trigger=trigger,
            force=force,
            mime_type=view.mime_type,
            text_digest=view.text_digest,
            blocks_digest=view.blocks_digest,
            text_chars=view.text_chars,
            has_tables=view.has_tables,
            has_images=view.has_images,
        )


class RecordView(BaseModel):
    """What successors need to decide ``applies`` without reading the record's content."""

    model_config = _WIRE

    org_id: str
    virtual_record_id: str
    rev: str
    record_ids: tuple[str, ...] = Field(min_length=1)
    connector_id: str
    tier: ParseTier
    mime_type: str | None = None
    text_digest: str
    blocks_digest: str
    text_chars: int = Field(ge=0)
    has_tables: bool
    has_images: bool


class StageResult(BaseModel):
    model_config = _WIRE

    outcome: StageOutcome
    fingerprint: str | None = None
    # Blocks embedded, entities found, and so on.
    items: int = Field(default=0, ge=0)
    reason: str | None = None
    retry_after_s: float | None = Field(default=None, ge=0)
    record_view: RecordView | None = None
    # Kept with the stage's state on COMPLETED (see StageState.output).
    output: str | None = None


class StageState(BaseModel):
    """Durable status of one stage for one ``(virtual record, revision)``.

    Carries the dispatch envelope of the job that last claimed it, so the sweeper can
    re-publish that job exactly.
    """

    model_config = _WIRE

    key: str
    org_id: str
    virtual_record_id: str
    rev: str
    stage: str
    status: ProgressStatus
    stage_version: int = Field(ge=1)
    record_ids: tuple[str, ...] = Field(min_length=1)
    connector_id: str
    tier: ParseTier
    priority: Priority
    trigger: str
    force: bool = False
    # Content facts of the revision, so successors' ``applies`` needs no content read.
    mime_type: str | None = None
    text_digest: str
    blocks_digest: str
    text_chars: int = Field(ge=0)
    has_tables: bool
    has_images: bool
    fingerprint: str | None = None
    # The completed run's output, when it lives outside the state (JSON): an unchanged
    # re-index puts it back where readers find it without running the stage again.
    output: str | None = None
    attempt: int = Field(default=0, ge=0)
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    # When the job last reached the broker; None while a claim is unpublished.
    published_at_ms: int | None = None
    updated_at_ms: int = Field(ge=0)
    worker_id: str | None = None
    reason: str | None = None

    @classmethod
    def from_job(
        cls, job: StageJob, status: ProgressStatus, now_ms: int, *, reason: str | None = None
    ) -> "StageState":
        return cls(
            key=job.state_key,
            org_id=job.org_id,
            virtual_record_id=job.virtual_record_id,
            rev=job.rev,
            stage=job.stage,
            status=status,
            stage_version=job.stage_version,
            record_ids=job.record_ids,
            connector_id=job.connector_id,
            tier=job.tier,
            priority=job.priority,
            trigger=job.trigger,
            force=job.force,
            mime_type=job.mime_type,
            text_digest=job.text_digest,
            blocks_digest=job.blocks_digest,
            text_chars=job.text_chars,
            has_tables=job.has_tables,
            has_images=job.has_images,
            updated_at_ms=now_ms,
            reason=reason,
        )

    def to_job(self) -> StageJob:
        return StageJob(
            stage=self.stage,
            stage_version=self.stage_version,
            org_id=self.org_id,
            virtual_record_id=self.virtual_record_id,
            rev=self.rev,
            record_ids=self.record_ids,
            connector_id=self.connector_id,
            tier=self.tier,
            priority=self.priority,
            trigger=self.trigger,
            force=self.force,
            mime_type=self.mime_type,
            text_digest=self.text_digest,
            blocks_digest=self.blocks_digest,
            text_chars=self.text_chars,
            has_tables=self.has_tables,
            has_images=self.has_images,
        )


class StageStateSummary(BaseModel):
    """What an API caller sees of one stage's state for a record's current revision."""

    model_config = _WIRE

    stage: str
    status: ProgressStatus
    reason: str | None = None
    attempt: int = 0
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    updated_at_ms: int | None = None

    @classmethod
    def of(cls, state: StageState) -> "StageStateSummary":
        return cls(
            stage=state.stage,
            status=state.status,
            reason=state.reason,
            attempt=state.attempt,
            started_at_ms=state.started_at_ms,
            finished_at_ms=state.finished_at_ms,
            updated_at_ms=state.updated_at_ms,
        )


class StageStatePatch(BaseModel):
    """Fields a CAS transition also writes. Only explicitly set fields are written,
    so ``StageStatePatch(reason=None)`` clears ``reason`` and leaves the rest alone."""

    model_config = _WIRE

    stage_version: int | None = Field(default=None, ge=1)
    record_ids: tuple[str, ...] | None = Field(default=None, min_length=1)
    connector_id: str | None = None
    tier: ParseTier | None = None
    priority: Priority | None = None
    trigger: str | None = None
    force: bool | None = None
    fingerprint: str | None = None
    output: str | None = None
    attempt: int | None = Field(default=None, ge=0)
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    published_at_ms: int | None = None
    worker_id: str | None = None
    reason: str | None = None

    @classmethod
    def dispatch(cls, job: StageJob) -> "StageStatePatch":
        """The envelope of a re-claim: the latest dispatch wins, and a stale reason is cleared."""
        return cls(
            stage_version=job.stage_version,
            record_ids=job.record_ids,
            connector_id=job.connector_id,
            tier=job.tier,
            priority=job.priority,
            trigger=job.trigger,
            force=job.force,
            reason=None,
        )

    def fields(self) -> dict[str, object]:
        """Storage form of the explicitly set fields."""
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)


class StageFingerprint(BaseModel):
    """What a stage's output depends on; an equal digest means the stored output is current."""

    model_config = _WIRE

    stage: str
    stage_version: int = Field(ge=1)
    # Digest of the upstream output the stage reads.
    input_digest: str
    # Digest of model, prompt, chain and policy inputs.
    config_digest: str

    def digest(self) -> str:
        raw = f"{self.stage}@{self.stage_version}|{self.input_digest}|{self.config_digest}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]
