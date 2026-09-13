"""StageJobHandler: claim, skip when current, outcomes, abandonment (UNIT-HW-01..)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import ClassVar

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.exceptions.indexing_exceptions import ProcessingError
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.models import (
    HeadlineField,
    Priority,
    RecordView,
    StageFingerprint,
    StageJob,
    StageOutcome,
    StageResult,
    StageState,
    Workload,
)
from app.modules.pipeline.policy import DEFAULT_POLICY, PipelinePolicy
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.stage import Deadline, StageIO
from app.modules.pipeline.worker import (
    StageFailed,
    StageJobHandler,
    StagePaused,
    StageRetry,
)
from app.services.messaging.config import IndexingEvent, StreamMessage
from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
from app.services.resource_governor.models import ParseTier
from tests.unit.modules.pipeline.fakes import (
    Clock,
    InMemoryStageStateStore,
    RecordingHeadlines,
    RecordingProducer,
)


class _IO:
    def __init__(self, job: StageJob, deadline: Deadline) -> None:
        self._deadline = deadline

    @property
    def policy(self) -> PipelinePolicy:
        return DEFAULT_POLICY

    @property
    def deadline(self) -> Deadline:
        return self._deadline


class _Classify:
    name: ClassVar[str] = "classify"
    version: ClassVar[int] = 1
    requires: ClassVar[frozenset[str]] = frozenset({"embed"})
    workload: ClassVar[Workload] = Workload.LLM
    headline: ClassVar[HeadlineField | None] = HeadlineField.EXTRACTION
    budget_s: ClassVar[float] = 5.0

    def __init__(self) -> None:
        self.config = "model-a"
        self.result: StageResult | BaseException = StageResult(outcome=StageOutcome.COMPLETED)
        self.delay = 0.0
        self.runs = 0

    def applies(self, view: RecordView, policy: PipelinePolicy) -> bool:
        return True

    async def fingerprint(self, job: StageJob, io: StageIO) -> StageFingerprint:
        return StageFingerprint(stage=self.name, stage_version=self.version, input_digest=job.text_digest, config_digest=self.config)

    async def run(self, job: StageJob, io: StageIO) -> StageResult:
        self.runs += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _SummaryEmbed(_Classify):
    name: ClassVar[str] = "summary-embed"
    requires: ClassVar[frozenset[str]] = frozenset({"classify"})
    headline: ClassVar[HeadlineField | None] = None


def _job(**overrides: object) -> StageJob:
    fields: dict[str, object] = {
        "stage": "classify", "stage_version": 1, "org_id": "org-1", "virtual_record_id": "vr-1",
        "rev": "rev-a", "record_ids": ("rec-1",), "connector_id": "conn-1", "tier": ParseTier.LIGHT,
        "priority": Priority.BULK, "trigger": "embed", "text_digest": "t", "blocks_digest": "b",
        "text_chars": 10, "has_tables": False, "has_images": False,
    }
    fields.update(overrides)
    return StageJob.model_validate(fields)


class _Harness:
    def __init__(self) -> None:
        self.clock = Clock()
        self.states = InMemoryStageStateStore(self.clock)
        self.headlines = RecordingHeadlines()
        self.producer = RecordingProducer()
        self.stage = _Classify()
        self.registry = StageRegistry()
        self.registry.register_external("embed")
        self.registry.register(self.stage)
        self.registry.register(_SummaryEmbed())
        self.coordinator = Coordinator(
            self.registry, self.states, self.headlines, self.producer,  # type: ignore[arg-type]
            policy_for=lambda _org: DEFAULT_POLICY, logger=logging.getLogger("t"), clock_ms=self.clock,
        )
        self.handler = StageJobHandler(
            self.registry, self.states, self.headlines, self.coordinator, _IO,
            logger=logging.getLogger("t"), worker_id="w1", clock_ms=self.clock,
        )

    def seed(self, job: StageJob, status: ProgressStatus = ProgressStatus.QUEUED, **fields: object) -> None:
        self.states.put(StageState.from_job(job, status, self.clock()).model_copy(update=fields))

    def state(self, job: StageJob) -> StageState:
        return self.states.states[job.state_key]

    async def deliver(self, job: StageJob) -> list[IndexingEvent]:
        message = StreamMessage(eventType="stageJob", payload=job.model_dump(mode="json", by_alias=True))
        return [event.event async for event in self.handler(message)]

    def expected_fingerprint(self, job: StageJob) -> str:
        return StageFingerprint(stage="classify", stage_version=1, input_digest=job.text_digest, config_digest=self.stage.config).digest()


@pytest.mark.asyncio
async def test_a_completed_run_records_its_fingerprint_status_and_successors() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    assert await h.deliver(job) == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    state = h.state(job)
    assert state.status is ProgressStatus.COMPLETED
    assert state.fingerprint == h.expected_fingerprint(job) and state.attempt == 1 and state.worker_id is None
    assert (h.headlines.writes[-1][2], h.headlines.cleared[-1]) == (ProgressStatus.COMPLETED, True)
    assert len(h.producer.jobs("pipeline.summary-embed")) == 1


@pytest.mark.asyncio
async def test_unchanged_inputs_complete_without_running() -> None:  # COST-01 at the stage level
    h, job = _Harness(), _job()
    h.seed(job, fingerprint=h.expected_fingerprint(job))
    await h.deliver(job)
    assert h.stage.runs == 0
    assert h.state(job).status is ProgressStatus.COMPLETED
    assert h.headlines.writes[-1][2] is ProgressStatus.COMPLETED


@pytest.mark.asyncio
async def test_changed_config_runs_again() -> None:
    h, job = _Harness(), _job()
    h.seed(job, ProgressStatus.COMPLETED, fingerprint=h.expected_fingerprint(job))
    h.stage.config = "model-b"
    await h.deliver(job)
    assert h.stage.runs == 1
    assert h.state(job).fingerprint == h.expected_fingerprint(job)


@pytest.mark.asyncio
async def test_force_runs_even_when_current() -> None:
    h, job = _Harness(), _job(force=True)
    h.seed(job, fingerprint=h.expected_fingerprint(job))
    await h.deliver(job)
    assert h.stage.runs == 1


@pytest.mark.parametrize("status", [ProgressStatus.FAILED, ProgressStatus.SKIPPED])
@pytest.mark.asyncio
async def test_a_leftover_delivery_of_a_settled_job_does_nothing(status: ProgressStatus) -> None:
    h, job = _Harness(), _job()
    h.seed(job, status)
    assert await h.deliver(job) == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    assert h.stage.runs == 0 and h.state(job).status is status and h.headlines.writes == []


@pytest.mark.asyncio
async def test_a_crashed_run_is_taken_over() -> None:
    h, job = _Harness(), _job()
    h.seed(job, ProgressStatus.IN_PROGRESS, worker_id="dead", attempt=1)
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.COMPLETED and h.state(job).attempt == 2


@pytest.mark.asyncio
async def test_a_missing_state_is_recreated_from_the_job() -> None:
    h, job = _Harness(), _job()
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_superseded_revision_is_settled_without_status_or_successors() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.STALE)
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.SKIPPED
    assert h.state(job).reason == "superseded by a newer revision"
    assert h.headlines.writes == [] and h.producer.sent == []


@pytest.mark.asyncio
async def test_a_skipped_stage_records_its_reason_and_still_dispatches() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.SKIPPED, reason="Extraction skipped: no LLM")
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.SKIPPED
    assert h.headlines.writes[-1][2:] == (ProgressStatus.SKIPPED, "rev-a", "Extraction skipped: no LLM")
    assert len(h.producer.jobs("pipeline.summary-embed")) == 1


@pytest.mark.asyncio
async def test_retry_requeues_and_counts_an_attempt() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.RETRY, reason="LLM returned nothing")
    with pytest.raises(StageRetry):
        await h.deliver(job)
    assert h.state(job).status is ProgressStatus.QUEUED and h.state(job).reason == "LLM returned nothing"
    assert not issubclass(StageRetry, RequeueWithoutAttempt)


@pytest.mark.asyncio
async def test_paused_requeues_without_an_attempt() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.PAUSED, reason="extraction breaker open")
    with pytest.raises(StagePaused) as raised:
        await h.deliver(job)
    assert isinstance(raised.value, RequeueWithoutAttempt)
    assert h.state(job).status is ProgressStatus.PAUSED


@pytest.mark.asyncio
async def test_failed_is_terminal_and_marks_the_record() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.FAILED, reason="document rejected")
    with pytest.raises(StageFailed) as raised:
        await h.deliver(job)
    assert isinstance(raised.value, ProcessingError)
    assert h.state(job).status is ProgressStatus.FAILED
    assert h.headlines.writes[-1][2:] == (ProgressStatus.FAILED, "rev-a", "document rejected")


@pytest.mark.asyncio
async def test_a_run_past_its_budget_is_retried() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.delay = 10
    type(h.stage).budget_s = 0.05
    try:
        with pytest.raises(StageRetry, match="budget"):
            await h.deliver(job)
    finally:
        type(h.stage).budget_s = 5.0
    assert h.state(job).status is ProgressStatus.QUEUED


@pytest.mark.asyncio
async def test_an_unexpected_error_leaves_a_reason_and_propagates() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = ConnectionError("graph unavailable")
    with pytest.raises(ConnectionError):
        await h.deliver(job)
    assert h.state(job).status is ProgressStatus.QUEUED
    assert h.state(job).reason == "ConnectionError: graph unavailable"


@pytest.mark.asyncio
async def test_no_successors_when_every_record_moved_on() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.headlines.moved_on = True
    await h.deliver(job)
    assert h.state(job).status is ProgressStatus.COMPLETED
    assert h.producer.sent == []


@pytest.mark.asyncio
async def test_invalid_payloads_and_unknown_stages_are_terminal() -> None:
    h = _Harness()
    with pytest.raises(StageFailed, match="invalid stage job"):
        _ = [e async for e in h.handler(StreamMessage(eventType="stageJob", payload={"stage": "classify"}))]
    with pytest.raises(StageFailed, match="no stage named 'entities'"):
        await h.deliver(_job(stage="entities"))


@pytest.mark.asyncio
async def test_outcomes_are_counted_per_stage_for_health() -> None:
    h = _Harness()
    done, current, settled, failing = (_job(virtual_record_id=f"vr-{i}") for i in range(4))
    h.seed(done)
    h.seed(current, fingerprint=h.expected_fingerprint(current))
    h.seed(settled, ProgressStatus.SKIPPED)
    h.seed(failing)
    for job in (done, current, settled):
        await h.deliver(job)
    h.stage.result = ConnectionError("graph unavailable")
    with pytest.raises(ConnectionError):
        await h.deliver(failing)
    message = StreamMessage(eventType="stageJob", payload=failing.model_dump(mode="json", by_alias=True))
    await h.handler.on_message_abandoned(message, reason="transient error", attempts=3)

    counted = {k: v for k, v in h.handler.outcomes("classify").items() if v}
    assert counted == {"completed": 1, "unchanged": 1, "duplicate": 1, "error": 1, "abandoned": 1}
    assert h.handler.outcomes("embed") == {}


class TestAbandonment:
    @pytest.mark.asyncio
    async def test_a_dead_lettered_job_is_failed_with_its_record(self) -> None:
        h, job = _Harness(), _job()
        h.seed(job, ProgressStatus.QUEUED)
        message = StreamMessage(eventType="stageJob", payload=job.model_dump(mode="json", by_alias=True))
        await h.handler.on_message_abandoned(message, reason="transient error", attempts=3)
        assert h.state(job).status is ProgressStatus.FAILED
        assert "after 3 attempt(s)" in (h.state(job).reason or "")
        assert h.headlines.writes[-1][2] is ProgressStatus.FAILED

    @pytest.mark.asyncio
    async def test_a_settled_job_is_left_alone(self) -> None:
        h, job = _Harness(), _job()
        h.seed(job, ProgressStatus.COMPLETED)
        message = StreamMessage(eventType="stageJob", payload=job.model_dump(mode="json", by_alias=True))
        await h.handler.on_message_abandoned(message, reason="x", attempts=1)
        assert h.state(job).status is ProgressStatus.COMPLETED and h.headlines.writes == []

    @pytest.mark.asyncio
    async def test_never_raises(self) -> None:
        h, job = _Harness(), _job()
        h.seed(job)
        h.states.fail_cas = True
        message = StreamMessage(eventType="stageJob", payload=job.model_dump(mode="json", by_alias=True))
        await h.handler.on_message_abandoned(message, reason="x", attempts=1)
        await h.handler.on_message_abandoned(None, reason="x", attempts=1)
        await h.handler.on_message_abandoned(StreamMessage(eventType="stageJob", payload={}), reason="x", attempts=1)


@pytest.mark.asyncio
async def test_a_paused_job_waits_longer_each_time_and_never_less_than_asked() -> None:
    from app.services.messaging.config import compute_retry_backoff_seconds

    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.PAUSED, reason="provider down", retry_after_s=40.0)
    delays: list[float | None] = []
    for _ in range(5):
        with pytest.raises(StagePaused) as raised:
            await h.deliver(job)
        assert raised.value.delay_s == max(40.0, compute_retry_backoff_seconds(h.state(job).attempt))
        delays.append(raised.value.delay_s)
    assert delays == sorted(delays) and delays[-1] == 300.0
    # A clean run that found its dependency down: the crash-loop backstop must not count it.
    assert not StagePaused.counts_toward_backstop


@pytest.mark.asyncio
async def test_a_completed_run_keeps_its_output_with_the_state() -> None:
    h, job = _Harness(), _job()
    h.seed(job)
    h.stage.result = StageResult(outcome=StageOutcome.COMPLETED, output='{"summary": "s"}')
    await h.deliver(job)
    assert h.state(job).output == '{"summary": "s"}'


@pytest.mark.asyncio
async def test_an_unchanged_job_whose_output_was_rewritten_away_restores_it_without_running() -> None:
    """A re-index of unchanged content rewrote the stored record without the classification."""
    h, job = _Harness(), _job()
    kept = '{"summary": "s"}'
    h.seed(job, fingerprint=h.expected_fingerprint(job), output=kept)
    restored: list[str] = []

    async def absent(_job: StageJob, _io: StageIO) -> bool:
        return False

    async def restore(_job: StageJob, _io: StageIO, output: str, *, still_current: Callable[[], Awaitable[bool]]) -> bool:
        restored.append(output)
        return await still_current()

    setattr(h.stage, "output_present", absent)
    setattr(h.stage, "restore_output", restore)
    await h.deliver(job)

    assert restored == [kept] and h.stage.runs == 0
    assert h.state(job).status is ProgressStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_kept_output_is_not_restored_over_a_run_that_claimed_the_state_meanwhile() -> None:
    """Where the job lease does not hold (it expired, or none is configured), a forced run can
    claim the state between this delivery's read and its restore."""
    h, job = _Harness(), _job()
    h.seed(job, fingerprint=h.expected_fingerprint(job), output='{"summary": "old"}')

    async def absent(_job: StageJob, _io: StageIO) -> bool:
        return False

    async def restore(_job: StageJob, _io: StageIO, _output: str, *, still_current: Callable[[], Awaitable[bool]]) -> bool:
        assert await h.states.cas(job.state_key, expected=ProgressStatus.QUEUED, new=ProgressStatus.IN_PROGRESS)
        return await still_current()

    setattr(h.stage, "output_present", absent)
    setattr(h.stage, "restore_output", restore)
    with pytest.raises(StageRetry):
        await h.deliver(job)

    assert h.stage.runs == 0 and h.state(job).status is ProgressStatus.IN_PROGRESS
    assert not h.headlines.writes


@pytest.mark.asyncio
async def test_an_unchanged_delivery_does_not_report_a_run_claimed_meanwhile_as_complete() -> None:
    h, job = _Harness(), _job()
    h.seed(job, ProgressStatus.COMPLETED, fingerprint=h.expected_fingerprint(job))

    async def present(_job: StageJob, _io: StageIO) -> bool:
        # A forced run claims the state while this delivery checks the stored record.
        assert await h.states.cas(job.state_key, expected=ProgressStatus.COMPLETED, new=ProgressStatus.IN_PROGRESS)
        return True

    setattr(h.stage, "output_present", present)
    with pytest.raises(StageRetry):
        await h.deliver(job)

    assert not h.headlines.writes and h.producer.jobs("pipeline.summary-embed") == []


@pytest.mark.asyncio
async def test_an_unchanged_job_with_nothing_kept_runs_again() -> None:
    h, job = _Harness(), _job()
    h.seed(job, fingerprint=h.expected_fingerprint(job))

    async def absent(_job: StageJob, _io: StageIO) -> bool:
        return False

    setattr(h.stage, "output_present", absent)
    await h.deliver(job)
    assert h.stage.runs == 1
