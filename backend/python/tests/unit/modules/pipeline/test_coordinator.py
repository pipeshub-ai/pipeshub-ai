"""Coordinator: dispatch exactly once (UNIT-CO-01..06), release on publish failure, sweeper (UNIT-SWEEP-01..02)."""

import asyncio
import logging
from typing import ClassVar

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.pipeline.coordinator import (
    STAGE_JOB_EVENT,
    Coordinator,
    StagePublishError,
)
from app.modules.pipeline.models import (
    HeadlineField,
    Priority,
    RecordView,
    StageJob,
    StageOutcome,
    StageResult,
    StageState,
    Workload,
    stage_state_key,
)
from app.modules.pipeline.policy import DEFAULT_POLICY, PipelinePolicy
from app.modules.pipeline.publisher import DeferredPublisher
from app.modules.pipeline.registry import StageRegistry
from app.services.resource_governor.models import ParseTier
from tests.unit.modules.pipeline.fakes import (
    Clock,
    InMemoryStageStateStore,
    RecordingHeadlines,
    RecordingProducer,
)

VRID, REV = "vr-1", "rev-a"


class _Stage:
    name: ClassVar[str] = ""
    version: ClassVar[int] = 1
    requires: ClassVar[frozenset[str]] = frozenset()
    workload: ClassVar[Workload] = Workload.LLM
    headline: ClassVar[HeadlineField | None] = None
    budget_s: ClassVar[float] = 60.0
    applicable: ClassVar[bool] = True

    def applies(self, view: RecordView, policy: PipelinePolicy) -> bool:
        return self.applicable


def _stage(name: str, *requires: str, applicable: bool = True, headline: HeadlineField | None = None) -> _Stage:
    attrs = {"name": name, "requires": frozenset(requires), "applicable": applicable, "headline": headline}
    return type(f"Stage_{name}", (_Stage,), attrs)()


def _view(**overrides: object) -> RecordView:
    fields: dict[str, object] = {
        "org_id": "org-1",
        "virtual_record_id": VRID,
        "rev": REV,
        "record_ids": ("rec-1",),
        "connector_id": "conn-1",
        "tier": ParseTier.LIGHT,
        "text_digest": "t",
        "blocks_digest": "b",
        "text_chars": 10,
        "has_tables": False,
        "has_images": False,
    }
    fields.update(overrides)
    return RecordView.model_validate(fields)


class _Harness:
    def __init__(self, registry: StageRegistry, *, reject_next: int = 0) -> None:
        self.clock = Clock()
        self.states = InMemoryStageStateStore(self.clock)
        self.headlines = RecordingHeadlines()
        self.producer = RecordingProducer(reject_next=reject_next)
        self.coordinator = Coordinator(
            registry,
            self.states,
            self.headlines,
            self.producer,  # type: ignore[arg-type]
            policy_for=lambda _org: DEFAULT_POLICY,
            logger=logging.getLogger("test"),
            clock_ms=self.clock,
        )

    def status(self, stage: str) -> ProgressStatus | None:
        state = self.states.states.get(stage_state_key(VRID, REV, stage))
        return state.status if state else None

    def complete(self, stage: str, version: int = 1) -> StageJob:
        job = StageJob(
            stage=stage, stage_version=version, org_id="org-1", virtual_record_id=VRID, rev=REV,
            record_ids=("rec-1",), connector_id="conn-1", tier=ParseTier.LIGHT, priority=Priority.BULK,
            trigger="test", text_digest="t", blocks_digest="b", text_chars=10, has_tables=False, has_images=False,
        )
        self.states.put(StageState.from_job(job, ProgressStatus.COMPLETED, self.clock()))
        return job


def _classify_after_embed() -> StageRegistry:
    registry = StageRegistry()
    registry.register_external("embed")
    registry.register(_stage("classify", "embed", headline=HeadlineField.EXTRACTION))
    return registry


@pytest.mark.asyncio
async def test_a_linear_successor_is_dispatched_once() -> None:  # UNIT-CO-01
    h = _Harness(_classify_after_embed())
    first = await h.coordinator.on_external_done("embed", _view(), priority=Priority.INTERACTIVE, trigger="newRecord")
    second = await h.coordinator.on_external_done("embed", _view(), priority=Priority.INTERACTIVE, trigger="newRecord")
    assert first == [f"{VRID}:{REV}:classify@1"]
    assert second == []
    assert h.status("embed") is ProgressStatus.COMPLETED
    assert h.status("classify") is ProgressStatus.QUEUED
    [(topic, event_type, payload, key)] = h.producer.sent
    assert (topic, event_type, key) == ("pipeline.classify", STAGE_JOB_EVENT, "conn-1")
    # The record shows the stage as queued once its job is on the broker.
    assert [(w[1], w[2]) for w in h.headlines.writes] == [(HeadlineField.EXTRACTION, ProgressStatus.QUEUED)]
    assert StageJob.model_validate(payload).trigger == "embed"


@pytest.mark.asyncio
async def test_fan_in_is_dispatched_exactly_once_under_concurrency() -> None:  # UNIT-CO-02 / RACE-02
    registry = StageRegistry()
    registry.register_external("embed")
    registry.register(_stage("classify", "embed"))
    registry.register(_stage("entities", "embed"))
    registry.register(_stage("project-metadata", "classify", "entities"))
    h = _Harness(registry)
    classify, entities = h.complete("classify"), h.complete("entities")
    done = StageResult(outcome=StageOutcome.COMPLETED, record_view=_view())
    await asyncio.gather(*(h.coordinator.on_stage_done(classify if i % 2 else entities, done) for i in range(100)))
    assert len(h.producer.jobs("pipeline.project-metadata")) == 1
    assert h.status("project-metadata") is ProgressStatus.QUEUED


@pytest.mark.asyncio
async def test_a_stage_waits_for_its_last_prerequisite() -> None:
    registry = StageRegistry()
    registry.register_external("embed")
    registry.register(_stage("classify", "embed"))
    registry.register(_stage("entities", "embed"))
    registry.register(_stage("project-metadata", "classify", "entities"))
    h = _Harness(registry)
    classify = h.complete("classify")
    assert await h.coordinator.on_stage_done(classify, StageResult(outcome=StageOutcome.COMPLETED, record_view=_view())) == []
    assert h.status("project-metadata") is None


@pytest.mark.asyncio
async def test_an_inapplicable_stage_is_skipped_and_its_successors_still_run() -> None:  # UNIT-CO-03
    registry = StageRegistry()
    registry.register_external("embed")
    registry.register(_stage("classify", "embed", applicable=False, headline=HeadlineField.EXTRACTION))
    registry.register(_stage("summary-embed", "classify"))
    h = _Harness(registry)
    published = await h.coordinator.on_external_done("embed", _view(), priority=Priority.BULK, trigger="newRecord")
    assert h.status("classify") is ProgressStatus.SKIPPED
    assert published == [f"{VRID}:{REV}:summary-embed@1"]
    [(record_ids, field, status, rev, _reason)] = h.headlines.writes
    assert (record_ids, field, status, rev) == (("rec-1",), HeadlineField.EXTRACTION, ProgressStatus.SKIPPED, REV)


@pytest.mark.asyncio
async def test_re_running_an_upstream_re_claims_a_finished_successor_and_carries_force() -> None:  # UNIT-CO-04
    h = _Harness(_classify_after_embed())
    await h.coordinator.on_external_done("embed", _view(), priority=Priority.BULK, trigger="newRecord")
    classify_key = stage_state_key(VRID, REV, "classify")
    h.states.states[classify_key] = h.states.states[classify_key].model_copy(update={"status": ProgressStatus.COMPLETED})
    await h.coordinator.on_external_done(
        "embed", _view(), priority=Priority.INTERACTIVE, trigger="reindexRecord", force=True
    )
    assert h.status("classify") is ProgressStatus.QUEUED
    jobs = [StageJob.model_validate(p) for p in h.producer.jobs("pipeline.classify")]
    assert [job.force for job in jobs] == [False, True]
    assert jobs[-1].priority is Priority.INTERACTIVE


@pytest.mark.parametrize("outcome", [StageOutcome.STALE, StageOutcome.RETRY, StageOutcome.PAUSED, StageOutcome.FAILED])
@pytest.mark.asyncio
async def test_only_a_finished_stage_dispatches_successors(outcome: StageOutcome) -> None:  # UNIT-CO-05
    registry = StageRegistry()
    registry.register_external("embed")
    registry.register(_stage("classify", "embed"))
    registry.register(_stage("summary-embed", "classify"))
    h = _Harness(registry)
    job = h.complete("classify")
    assert await h.coordinator.on_stage_done(job, StageResult(outcome=outcome, record_view=_view())) == []
    assert h.producer.sent == []


@pytest.mark.asyncio
async def test_a_finished_stage_without_a_record_view_is_a_bug() -> None:
    h = _Harness(_classify_after_embed())
    with pytest.raises(ValueError, match="record view"):
        await h.coordinator.on_stage_done(h.complete("embed"), StageResult(outcome=StageOutcome.COMPLETED))


@pytest.mark.asyncio
async def test_a_rejected_publish_releases_the_claim_so_a_retry_dispatches() -> None:  # UNIT-CO-06
    h = _Harness(_classify_after_embed(), reject_next=1)
    with pytest.raises(StagePublishError):
        await h.coordinator.on_external_done("embed", _view(), priority=Priority.BULK, trigger="newRecord")
    assert h.status("classify") is ProgressStatus.NOT_STARTED
    assert await h.coordinator.on_external_done("embed", _view(), priority=Priority.BULK, trigger="newRecord") == [
        f"{VRID}:{REV}:classify@1"
    ]
    assert h.status("classify") is ProgressStatus.QUEUED


@pytest.mark.asyncio
async def test_an_unreleasable_claim_stays_queued_and_the_sweeper_publishes_it() -> None:  # UNIT-SWEEP-01
    h = _Harness(_classify_after_embed(), reject_next=1)
    original_cas = h.states.cas

    async def failing_release(key: str, **kwargs: object) -> bool:
        if kwargs.get("expected") is ProgressStatus.QUEUED:
            raise ConnectionError("graph unavailable")
        return await original_cas(key, **kwargs)  # type: ignore[arg-type]

    h.states.cas = failing_release  # type: ignore[method-assign]
    with pytest.raises(StagePublishError):
        await h.coordinator.on_external_done("embed", _view(), priority=Priority.BULK, trigger="newRecord")
    h.states.cas = original_cas  # type: ignore[method-assign]
    assert h.status("classify") is ProgressStatus.QUEUED and h.producer.sent == []

    assert (await h.coordinator.sweep(queued_older_than_ms=60_000, limit=10)).republished == 0
    h.clock.advance(61_000)
    assert (await h.coordinator.sweep(queued_older_than_ms=60_000, limit=10)).republished == 1
    assert len(h.producer.jobs("pipeline.classify")) == 1
    # The touch makes the next sweep wait a full interval.
    assert (await h.coordinator.sweep(queued_older_than_ms=60_000, limit=10)).republished == 0


@pytest.mark.asyncio
async def test_a_claim_made_before_the_stage_topics_exist_waits_for_the_sweeper(caplog: pytest.LogCaptureFixture) -> None:
    # The indexing service binds the publisher only once the stage topics exist.
    h = _Harness(_classify_after_embed())
    coordinator = Coordinator(
        _classify_after_embed(),
        h.states,
        h.headlines,
        DeferredPublisher(),
        policy_for=lambda _org: DEFAULT_POLICY,
        logger=logging.getLogger("test"),
        clock_ms=h.clock,
    )
    with caplog.at_level(logging.ERROR):
        await coordinator.on_external_done(
            "embed", _view(), priority=Priority.BULK, trigger="newRecord", keep_claim_on_publish_failure=True
        )
        h.clock.advance(61_000)
        assert (await coordinator.sweep(queued_older_than_ms=60_000, limit=10)).republished == 0
    assert h.status("classify") is ProgressStatus.QUEUED
    assert not caplog.records


@pytest.mark.asyncio
async def test_the_sweeper_reclaims_a_running_job_only_when_its_lease_is_free() -> None:  # UNIT-SWEEP-02 / RACE-11
    h = _Harness(_classify_after_embed())
    await h.coordinator.on_external_done("embed", _view(), priority=Priority.BULK, trigger="newRecord")
    key = stage_state_key(VRID, REV, "classify")
    h.states.states[key] = h.states.states[key].model_copy(
        update={"status": ProgressStatus.IN_PROGRESS, "worker_id": "w1"}
    )
    h.clock.advance(3_600_000)
    active: set[str] = {f"{VRID}:{REV}:classify@1"}

    async def is_job_active(job_id: str) -> bool:
        return job_id in active

    report = await h.coordinator.sweep(
        queued_older_than_ms=60_000, limit=10, in_progress_older_than_ms=1_800_000, is_job_active=is_job_active
    )
    assert report.reclaimed == 0 and h.status("classify") is ProgressStatus.IN_PROGRESS
    active.clear()
    report = await h.coordinator.sweep(
        queued_older_than_ms=60_000, limit=10, in_progress_older_than_ms=1_800_000, is_job_active=is_job_active
    )
    assert report.reclaimed == 1
    assert h.status("classify") is ProgressStatus.QUEUED
    assert h.states.states[key].worker_id is None
    assert len(h.producer.jobs("pipeline.classify")) == 2


@pytest.mark.asyncio
async def test_redrive_re_queues_a_failed_stage_but_not_a_running_one() -> None:
    h = _Harness(_classify_after_embed())
    await h.coordinator.on_external_done("embed", _view(), priority=Priority.BULK, trigger="newRecord")
    key = stage_state_key(VRID, REV, "classify")
    h.states.states[key] = h.states.states[key].model_copy(update={"status": ProgressStatus.IN_PROGRESS})
    assert await h.coordinator.redrive(_view(), "classify", priority=Priority.INTERACTIVE, force=True) is None
    h.states.states[key] = h.states.states[key].model_copy(
        update={"status": ProgressStatus.FAILED, "reason": "LLM quota"}
    )
    assert await h.coordinator.redrive(_view(), "classify", priority=Priority.INTERACTIVE, force=True) == (
        f"{VRID}:{REV}:classify@1"
    )
    state = h.states.states[key]
    assert state.status is ProgressStatus.QUEUED and state.reason is None and state.force is True


@pytest.mark.asyncio
async def test_a_published_job_records_when_it_reached_the_broker() -> None:
    h = _Harness(_classify_after_embed())
    await h.coordinator.on_external_done("embed", _view(), priority=Priority.BULK, trigger="newRecord")
    state = h.states.states[stage_state_key(VRID, REV, "classify")]
    assert state.published_at_ms == h.clock()


@pytest.mark.asyncio
async def test_a_job_waiting_on_the_broker_is_not_republished_until_the_backstop() -> None:
    h = _Harness(_classify_after_embed())
    await h.coordinator.on_external_done("embed", _view(), priority=Priority.BULK, trigger="newRecord")
    h.clock.advance(3_600_000)
    assert (await h.coordinator.sweep(queued_older_than_ms=60_000, limit=10)).republished == 0
    h.clock.advance(6 * 3_600_000)
    assert (await h.coordinator.sweep(queued_older_than_ms=60_000, limit=10)).republished == 1
    assert len(h.producer.jobs("pipeline.classify")) == 2


@pytest.mark.asyncio
async def test_an_ingress_keeps_its_claim_when_the_broker_refuses() -> None:
    h = _Harness(_classify_after_embed(), reject_next=1)
    published = await h.coordinator.on_external_done(
        "embed", _view(), priority=Priority.BULK, trigger="newRecord", keep_claim_on_publish_failure=True
    )
    state = h.states.states[stage_state_key(VRID, REV, "classify")]
    assert published == [f"{VRID}:{REV}:classify@1"]
    assert state.status is ProgressStatus.QUEUED and state.published_at_ms is None
    h.clock.advance(61_000)
    assert (await h.coordinator.sweep(queued_older_than_ms=60_000, limit=10)).republished == 1


@pytest.mark.asyncio
async def test_a_redrive_marks_the_stage_queued_on_the_record() -> None:
    h = _Harness(_classify_after_embed())
    await h.coordinator.on_external_done("embed", _view(), priority=Priority.BULK, trigger="newRecord")
    key = stage_state_key(VRID, REV, "classify")
    h.states.states[key] = h.states.states[key].model_copy(update={"status": ProgressStatus.FAILED})
    h.headlines.writes.clear()
    await h.coordinator.redrive(_view(), "classify", priority=Priority.INTERACTIVE, force=True)
    assert h.headlines.writes == [(("rec-1",), HeadlineField.EXTRACTION, ProgressStatus.QUEUED, REV, None)]


@pytest.mark.asyncio
async def test_redrive_revision_rebuilds_the_job_from_the_external_prerequisite() -> None:
    h = _Harness(_classify_after_embed())
    await h.coordinator.on_external_done("embed", _view(text_digest="t-9"), priority=Priority.BULK, trigger="newRecord")
    key = stage_state_key(VRID, REV, "classify")
    h.states.states[key] = h.states.states[key].model_copy(update={"status": ProgressStatus.FAILED})
    dispatched = await h.coordinator.redrive_revision(
        VRID, REV, ["classify", "entities"], priority=Priority.INTERACTIVE, force=True
    )
    assert dispatched == [f"{VRID}:{REV}:classify@1"]
    job = StageJob.model_validate(h.producer.jobs("pipeline.classify")[-1])
    assert (job.text_digest, job.force, job.priority) == ("t-9", True, Priority.INTERACTIVE)


@pytest.mark.asyncio
async def test_redrive_revision_of_a_revision_never_seen_asks_for_a_reindex() -> None:
    h = _Harness(_classify_after_embed())
    assert await h.coordinator.redrive_revision(VRID, REV, ["classify"], priority=Priority.INTERACTIVE, force=True) is None
    assert h.producer.sent == []


@pytest.mark.asyncio
async def test_the_sweeper_republishes_a_paused_job_whose_message_was_lost() -> None:
    from app.modules.pipeline.coordinator import REPUBLISH_BACKSTOP_MS

    h = _Harness(_classify_after_embed())
    await h.coordinator.on_external_done("embed", _view(), priority=Priority.BULK, trigger="newRecord")
    key = stage_state_key(VRID, REV, "classify")
    h.states.states[key] = h.states.states[key].model_copy(update={"status": ProgressStatus.PAUSED})
    h.clock.advance(REPUBLISH_BACKSTOP_MS - 1)
    # Still cycling through the broker as far as anyone can tell.
    assert (await h.coordinator.sweep(queued_older_than_ms=60_000, limit=10)).republished == 0
    h.clock.advance(2)
    assert (await h.coordinator.sweep(queued_older_than_ms=60_000, limit=10)).republished == 1
    assert h.status("classify") is ProgressStatus.QUEUED
    assert len(h.producer.jobs("pipeline.classify")) == 2
