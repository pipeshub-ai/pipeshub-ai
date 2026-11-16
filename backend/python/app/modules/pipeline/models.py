"""Contracts shared by the pipeline coordinator, the stage worker and the stages.

Everything that crosses a component boundary in the staged pipeline is one of these
models. Wire and storage forms are camelCase (``model_dump(by_alias=True)``), matching
the job envelope and the graph's record fields; parsing accepts either spelling.
"""

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_serializer,
    field_validator,
)
from pydantic.alias_generators import to_camel

from app.config.constants.arangodb import ProgressStatus
from app.services.resource_governor.models import ParseTier

_WIRE = ConfigDict(frozen=True, alias_generator=to_camel, populate_by_name=True, extra="ignore")


_KNOWN_STATUSES = frozenset(status.value for status in ProgressStatus)
_logger = logging.getLogger(__name__)
#: Unknown stored values already reported, so a row's value is logged once per process and not once per read.
_REPORTED: set[tuple[str, str]] = set()


#: What a reader reports for a row whose status a newer release wrote.
FOREIGN_REASON = "handled by a newer release"


@dataclass(frozen=True)
class ForeignStatus:
    """A stage status this release does not know: a newer release wrote it, and that release owns the row.

    Never mapped to a known status, because acting on a guess is how a row gets stuck or overwritten. This
    release reads such a row but never compares-and-sets, sweeps or reclaims it.
    """

    value: str


def _report_unknown(field: str, value: str, mapped_to: str) -> None:
    if (field, value) not in _REPORTED:
        _REPORTED.add((field, value))
        _logger.warning(
            "Stage state with unknown %s %r (written by a newer release): read as %s", field, value, mapped_to
        )


class Priority(StrEnum):
    INTERACTIVE = "interactive"
    BULK = "bulk"


def _tier_or_heavy(value: object) -> object:
    """A tier a later release added reads as HEAVY, the pool that is safe for any document."""
    if isinstance(value, str) and value not in {tier.value for tier in ParseTier}:
        _report_unknown("tier", value, ParseTier.HEAVY.value)
        return ParseTier.HEAVY
    return value


def _priority_or_bulk(value: object) -> object:
    """A priority a later release added reads as ``bulk``: never ahead of an interactive job."""
    if isinstance(value, str) and value not in {priority.value for priority in Priority}:
        _report_unknown("priority", value, Priority.BULK.value)
        return Priority.BULK
    return value


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


# Stages an API caller may re-run, and the record field that reports each; None for a stage
# with no record field, whose records are selected by its own stage state. The registry
# refuses a stage whose headline disagrees, so the two cannot drift.
RETRYABLE_STAGES: Mapping[str, HeadlineField | None] = MappingProxyType(
    {"classify": HeadlineField.EXTRACTION, "entities": None}
)


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


def stage_state_rev(key: str) -> str:
    """The revision in a ``stage_state_key``; a revision and a stage name hold no colon."""
    return key.rsplit(":", 2)[1]


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

    # A job a newer release published may carry a tier or priority this release lacks; refusing it would
    # dead-letter the job, and only the sweep's republish backstop would bring it back.
    @field_validator("tier", mode="before")
    @classmethod
    def _unknown_tier_is_heavy(cls, value: object) -> object:
        return _tier_or_heavy(value)

    @field_validator("priority", mode="before")
    @classmethod
    def _unknown_priority_is_bulk(cls, value: object) -> object:
        return _priority_or_bulk(value)

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
    # What a PAUSED outcome says about the model call behind it, for the pause budget
    # (``pause_budget``). Not stored: none of it reaches a record or a stage state.
    # ``sent``: a request reached the wire (an open circuit is a refusal, not a send);
    # a stage that cannot tell says it did, so a paused job never gets free sends.
    sent: bool = True
    # Requests on the wire behind this outcome (None: one when ``sent``); the pause budget counts these as sends.
    requests: int | None = None
    # A half-open trial: carried for the fleet, never charged to the job.
    trial: bool = False
    # A failed send made with the breaker closed while the provider was serving others.
    chargeable: bool = False
    # How many of the run's failed sends were chargeable (None: one when ``chargeable``).
    charged: int | None = None
    # Digest and size class of the provider key's breaker, for its hold ledger.
    provider_pk: str | None = None
    size_class: str | None = None
    # The provider's readable key (class, model and endpoint host; never a credential), for reasons and notices.
    provider_label: str | None = None

    @classmethod
    def paused_for(
        cls, exc: BaseException | None, reason: str, *, traced: Mapping[str, object] | None = None
    ) -> "StageResult":
        """A PAUSED outcome for model calls the provider could not serve, carrying what the pause budget needs to count
        them. ``traced`` is the run's own count (the job trace's ``pause_fields``), which wins over what one exception
        says; without it, ``exc`` (``LLMUnavailableError``) is read, and an error that does not say counts as a send."""
        retry_after = getattr(exc, "retry_after", None)
        if traced:
            return cls(outcome=StageOutcome.PAUSED, reason=reason, retry_after_s=retry_after, **traced)  # type: ignore[arg-type]
        return cls(
            outcome=StageOutcome.PAUSED,
            reason=reason,
            retry_after_s=retry_after,
            sent=getattr(exc, "sent", True),
            requests=getattr(exc, "requests", None),
            trial=getattr(exc, "trial", False),
            chargeable=getattr(exc, "chargeable", False),
            charged=getattr(exc, "charged", None),
            provider_pk=getattr(exc, "pk", None),
            size_class=getattr(exc, "size_class", None),
            provider_label=getattr(exc, "provider", None),
        )


class StageState(BaseModel):
    """Durable status of one stage for one ``(virtual record, revision)``.

    Carries the dispatch envelope of the job that last claimed it, so the sweeper can
    re-publish that job exactly.
    """

    model_config = _WIRE

    key: str
    # None for a stored state that names no organisation: the sweep skips it rather than guess one.
    org_id: str | None = None
    virtual_record_id: str
    rev: str
    stage: str
    status: ProgressStatus | ForeignStatus
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

    @field_validator("status", mode="before")
    @classmethod
    def _unknown_status_is_foreign(cls, value: object) -> object:
        """A status a later release wrote (the stored schema accepts any string) reads as foreign: the read
        never fails, and nothing in this release acts on the row."""
        if isinstance(value, str) and value not in _KNOWN_STATUSES:
            _report_unknown("status", value, f"foreign ({FOREIGN_REASON})")
            return ForeignStatus(value)
        return value

    @field_serializer("status", when_used="json")
    def _status_as_stored(self, status: ProgressStatus | ForeignStatus) -> str:
        return status.value

    @property
    def foreign(self) -> bool:
        """Whether a newer release owns this row (its status is one this release does not know)."""
        return isinstance(self.status, ForeignStatus)

    @field_validator("tier", mode="before")
    @classmethod
    def _unknown_tier_is_heavy(cls, value: object) -> object:
        return _tier_or_heavy(value)

    @field_validator("priority", mode="before")
    @classmethod
    def _unknown_priority_is_bulk(cls, value: object) -> object:
        return _priority_or_bulk(value)

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
        if not self.org_id:
            raise ValueError(f"stage state {self.key} has no organisation")
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
    # A string for a row a newer release owns, whose status this release does not know.
    status: ProgressStatus | str
    reason: str | None = None
    attempt: int = 0
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    updated_at_ms: int | None = None

    @classmethod
    def of(cls, state: StageState) -> "StageStateSummary":
        if isinstance(state.status, ForeignStatus):
            status, reason = state.status.value, FOREIGN_REASON
        else:
            status, reason = state.status, state.reason
        return cls(
            stage=state.stage,
            status=status,
            reason=reason,
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
