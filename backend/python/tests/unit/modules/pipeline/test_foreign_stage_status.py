"""A stage state whose status this release does not know: a newer release wrote it and owns the row.

This release reads such a row as foreign. It never compares-and-sets it, never sweeps or reclaims it, and
one such row never stops the other stages of its revision from being read. These tests run the real
``GraphStageStateStore`` over a graph that stores documents and compares stored strings, as the three graph
stores do, and records every compare-and-set it is asked for.
"""

import copy
import logging
from typing import Any

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.ingress import StageIngress
from app.modules.pipeline.models import (
    Priority,
    StageJob,
    StageOutcome,
    StageResult,
    StageState,
    StageStateSummary,
)
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.stage import StageIO
from app.modules.pipeline.state import GraphStageStateStore
from app.modules.pipeline.worker import StageJobHandler
from app.services.messaging.config import StreamMessage
from app.services.resource_governor.models import ParseTier
from tests.unit.modules.pipeline.fakes import (
    ClassifyStage,
    Clock,
    RecordingHeadlines,
    RecordingProducer,
    StubIO,
)

FOREIGN = "SPLIT"


class DocumentGraph:
    """The stage-state half of ``IGraphDBProvider``: documents keyed by id, CAS on the stored status."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.cas_calls: list[tuple[str, str, str]] = []

    async def stage_state_get(self, key: str) -> dict[str, Any] | None:
        document = self.documents.get(key)
        return copy.deepcopy(document) if document is not None else None

    async def stage_states_for_revision(self, virtual_record_id: str, rev: str) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(d) for d in self.documents.values()
            if d["virtualRecordId"] == virtual_record_id and d["rev"] == rev
        ]

    async def stage_state_create(self, document: dict[str, Any]) -> bool:
        if document["id"] in self.documents:
            return False
        self.documents[document["id"]] = copy.deepcopy(document)
        return True

    async def stage_state_compare_and_set(
        self, key: str, expected: str, new: str, fields: dict[str, Any], *, expected_attempt: int | None = None
    ) -> bool:
        self.cas_calls.append((key, expected, new))
        document = self.documents.get(key)
        if document is None or document["status"] != expected:
            return False
        if expected_attempt is not None and document.get("attempt", 0) != expected_attempt:
            return False
        document.update(copy.deepcopy(fields))
        document["status"] = new
        return True

    async def stage_states_stale(self, statuses: list[str], updated_before_ms: int, limit: int) -> list[dict[str, Any]]:
        matching = [d for d in self.documents.values() if d["status"] in statuses and d["updatedAtMs"] < updated_before_ms]
        return [copy.deepcopy(d) for d in sorted(matching, key=lambda d: d["updatedAtMs"])[:limit]]

    def cas_on(self, key: str) -> list[tuple[str, str, str]]:
        return [call for call in self.cas_calls if call[0] == key]


def _job(stage: str) -> StageJob:
    return StageJob(
        stage=stage, stage_version=1, org_id="org-1", virtual_record_id="vr-1", rev="rev-a", record_ids=("rec-1",),
        connector_id="conn-1", tier=ParseTier.LIGHT, priority=Priority.BULK, trigger="embed", text_digest="t",
        blocks_digest="b", text_chars=10, has_tables=False, has_images=False,
    )


class _Harness:
    def __init__(self) -> None:
        self.clock = Clock()
        self.graph = DocumentGraph()
        self.states = GraphStageStateStore(self.graph, clock_ms=self.clock)  # type: ignore[arg-type]
        self.headlines = RecordingHeadlines()
        self.producer = RecordingProducer()
        self.stage = ClassifyStage()
        self.registry = StageRegistry()
        self.registry.register_external("embed")
        self.registry.register(self.stage)
        self.coordinator = Coordinator(
            self.registry, self.states, self.headlines, self.producer,  # type: ignore[arg-type]
            policy_for=lambda _org: DEFAULT_POLICY, logger=logging.getLogger("t"), clock_ms=self.clock,
        )
        self.handler = StageJobHandler(
            self.registry, self.states, self.headlines, self.coordinator, StubIO,
            logger=logging.getLogger("t"), worker_id="w1", clock_ms=self.clock,
        )

    async def seed(self, stage: str, status: str, **fields: object) -> StageJob:
        job = _job(stage)
        document = StageState.from_job(job, ProgressStatus.COMPLETED, self.clock()).model_dump(mode="json", by_alias=True)
        document["id"] = document.pop("key")
        document.update(status=status, **fields)
        assert await self.graph.stage_state_create(document)
        return job

    async def seed_foreign(self, stage: str) -> tuple[StageJob, dict[str, Any]]:
        """A row as a newer release leaves it: a status and a field this release does not know."""
        job = await self.seed(stage, FOREIGN, attempt=2, sweepAtMs=7, updatedAtMs=1)
        return job, copy.deepcopy(self.graph.documents[job.state_key])

    def message(self, job: StageJob) -> StreamMessage:
        return StreamMessage(eventType="stageJob", payload=job.model_dump(mode="json", by_alias=True))


def test_a_status_this_release_does_not_know_is_read_as_foreign() -> None:
    from app.modules.pipeline.models import ForeignStatus

    document = {**_job("classify").model_dump(by_alias=True), "status": FOREIGN, "key": "k", "updatedAtMs": 1}

    state = StageState.model_validate(document)

    assert state.status == ForeignStatus(FOREIGN)
    assert state.foreign
    assert not StageState.model_validate({**document, "status": "FAILED"}).foreign


@pytest.mark.asyncio
async def test_an_upstream_completion_never_claims_a_foreign_row() -> None:
    h = _Harness()
    classify, before = await h.seed_foreign("classify")

    dispatched = await h.coordinator.on_external_done("embed", _job("embed").view, priority=Priority.BULK, trigger="t")

    assert dispatched == []
    assert h.graph.cas_on(classify.state_key) == []
    assert h.graph.documents[classify.state_key] == before
    assert h.producer.sent == []


@pytest.mark.asyncio
async def test_a_foreign_external_prerequisite_is_not_overwritten() -> None:
    h = _Harness()
    embed, before = await h.seed_foreign("embed")

    dispatched = await h.coordinator.on_external_done("embed", embed.view, priority=Priority.BULK, trigger="t")

    assert dispatched == []
    assert h.graph.cas_on(embed.state_key) == []
    assert h.graph.documents[embed.state_key] == before


@pytest.mark.asyncio
async def test_a_redrive_never_claims_a_foreign_row() -> None:
    h = _Harness()
    await h.seed("embed", ProgressStatus.COMPLETED.value)
    classify, before = await h.seed_foreign("classify")

    assert await h.coordinator.redrive(classify.view, "classify", priority=Priority.INTERACTIVE, force=True) is None
    assert h.graph.cas_on(classify.state_key) == []
    assert h.graph.documents[classify.state_key] == before


@pytest.mark.asyncio
async def test_a_delivered_job_leaves_a_foreign_row_untouched_and_is_counted() -> None:
    h = _Harness()
    await h.seed("embed", ProgressStatus.COMPLETED.value)
    classify, before = await h.seed_foreign("classify")

    events = [event async for event in h.handler(h.message(classify))]

    assert events, "the delivery ends normally, so the broker drops it"
    assert h.stage.runs == 0
    assert h.graph.cas_on(classify.state_key) == []
    assert h.graph.documents[classify.state_key] == before
    assert h.handler.outcomes("classify")["foreign"] == 1


@pytest.mark.asyncio
async def test_an_abandoned_job_leaves_a_foreign_row_untouched() -> None:
    h = _Harness()
    classify, before = await h.seed_foreign("classify")

    await h.handler.on_message_abandoned(h.message(classify), reason="poison", attempts=5)

    assert h.graph.cas_on(classify.state_key) == []
    assert h.graph.documents[classify.state_key] == before


@pytest.mark.asyncio
async def test_a_run_whose_row_turned_foreign_meanwhile_does_not_settle_over_it() -> None:
    h = _Harness()
    await h.seed("embed", ProgressStatus.COMPLETED.value)
    classify = await h.seed("classify", ProgressStatus.QUEUED.value)

    async def a_newer_release_takes_the_row(job: StageJob, io: StageIO) -> StageResult:
        h.stage.runs += 1
        # Same attempt: only the status says the row moved on, so only the status may stop the settle.
        h.graph.documents[job.state_key].update(status=FOREIGN, sweepAtMs=9)
        return StageResult(outcome=StageOutcome.COMPLETED)

    h.stage.run = a_newer_release_takes_the_row  # type: ignore[method-assign]
    _ = [event async for event in h.handler(h.message(classify))]

    key = classify.state_key
    assert (h.graph.documents[key]["status"], h.graph.documents[key]["sweepAtMs"]) == (FOREIGN, 9)
    # The claim and the settle from IN_PROGRESS, which the store refuses; nothing after it.
    assert h.graph.cas_on(key) == [(key, "QUEUED", "IN_PROGRESS"), (key, "IN_PROGRESS", "COMPLETED")]


@pytest.mark.asyncio
async def test_the_sweep_never_selects_a_foreign_row_and_its_siblings_stay_readable() -> None:
    h = _Harness()
    await h.seed("embed", ProgressStatus.COMPLETED.value)
    classify, before = await h.seed_foreign("classify")
    h.clock.advance(10 * 24 * 3600 * 1000)

    report = await h.coordinator.sweep(queued_older_than_ms=1, limit=10, in_progress_older_than_ms=1)

    assert (report.scanned, report.republished, report.reclaimed) == (0, 0, 0)
    assert h.graph.cas_calls == []
    assert h.graph.documents[classify.state_key] == before
    states = await h.states.get_many("vr-1", "rev-a")
    assert states["embed"].status is ProgressStatus.COMPLETED
    assert not isinstance(states["classify"].status, ProgressStatus)
    assert await h.coordinator.has_seen_revision("vr-1", "rev-a")


@pytest.mark.asyncio
async def test_a_foreign_row_is_reported_as_handled_by_a_newer_release() -> None:
    h = _Harness()
    classify, _ = await h.seed_foreign("classify")
    state = await h.states.get(classify.state_key)
    assert state is not None

    summary = StageStateSummary.of(state).model_dump(mode="json", by_alias=True)

    assert summary["status"] == FOREIGN
    assert summary["reason"] == "handled by a newer release"


@pytest.mark.asyncio
async def test_a_revision_whose_prerequisite_a_newer_release_recorded_counts_as_seen() -> None:
    """``None`` from a redrive, or an unseen revision, makes the record handler reindex from scratch (and delete
    the embeddings): a revision a newer release recorded must never look unseen."""
    h = _Harness()
    embed, before = await h.seed_foreign("embed")
    await h.seed("classify", ProgressStatus.FAILED.value)
    ingress = StageIngress(h.coordinator, h.graph, logging.getLogger("t"))  # type: ignore[arg-type]
    record = {
        "indexingStatus": "COMPLETED", "extractionStatus": "QUEUED", "virtualRecordId": "vr-1", "contentRev": "rev-a",
    }

    assert await h.coordinator.has_seen_revision("vr-1", "rev-a")
    assert not await ingress.handoff_lost(record)
    assert await h.coordinator.redrive_revision("vr-1", "rev-a", ["classify"], priority=Priority.INTERACTIVE, force=True) == []
    assert h.graph.cas_calls == []
    assert h.producer.sent == []
    assert h.graph.documents[embed.state_key] == before
