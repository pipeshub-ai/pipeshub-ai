"""The stage worker with a fake classify stage, shared by the worker's unit tests and the spend-safety integration tests."""

import logging
from typing import ClassVar

from app.config.constants.arangodb import ProgressStatus
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.models import (
    HeadlineField,
    Priority,
    StageFingerprint,
    StageJob,
    StageOutcome,
    StageResult,
    StageState,
)
from app.modules.pipeline.pause_budget import PauseLimits, PauseState
from app.modules.pipeline.pause_judge import PauseJudge
from app.modules.pipeline.pause_store import PauseStore
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.worker import StageJobHandler
from app.services.llm_gateway.fleet_redis import FleetRedis
from app.services.messaging.config import IndexingEvent, StreamMessage
from app.services.resource_governor.models import ParseTier
from tests.unit.modules.pipeline.fakes import (
    ClassifyStage,
    Clock,
    InMemoryStageStateStore,
    RecordingHeadlines,
    RecordingProducer,
    StubIO,
)


class FakeSummaryEmbed(ClassifyStage):
    name: ClassVar[str] = "summary-embed"
    requires: ClassVar[frozenset[str]] = frozenset({"classify"})
    headline: ClassVar[HeadlineField | None] = None


def make_job(**overrides: object) -> StageJob:
    fields: dict[str, object] = {
        "stage": "classify", "stage_version": 1, "org_id": "org-1", "virtual_record_id": "vr-1",
        "rev": "rev-a", "record_ids": ("rec-1",), "connector_id": "conn-1", "tier": ParseTier.LIGHT,
        "priority": Priority.BULK, "trigger": "embed", "text_digest": "t", "blocks_digest": "b",
        "text_chars": 10, "has_tables": False, "has_images": False,
    }
    fields.update(overrides)
    return StageJob.model_validate(fields)


class WorkerHarness:
    def __init__(self) -> None:
        self.clock = Clock()
        self.states = InMemoryStageStateStore(self.clock)
        self.headlines = RecordingHeadlines()
        self.producer = RecordingProducer()
        self.stage = ClassifyStage()
        self.registry = StageRegistry()
        self.registry.register_external("embed")
        self.registry.register(self.stage)
        self.registry.register(FakeSummaryEmbed())
        self.coordinator = Coordinator(
            self.registry, self.states, self.headlines, self.producer,  # type: ignore[arg-type]
            policy_for=lambda _org: DEFAULT_POLICY, logger=logging.getLogger("t"), clock_ms=self.clock,
        )
        self.handler = StageJobHandler(
            self.registry, self.states, self.headlines, self.coordinator, StubIO,
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


POISON_PK, POISON_CLS = "pkpoison", "l"


def charged_pause(**overrides: object) -> StageResult:
    """What the classify stage returns for a failed send the key's 5-minute success rate says is the document's."""
    fields: dict[str, object] = {
        "outcome": StageOutcome.PAUSED, "reason": "Model provider unavailable: boom", "sent": True,
        "chargeable": True, "provider_pk": POISON_PK, "size_class": POISON_CLS, "provider_label": "ChatX:m@host",
    }
    fields.update(overrides)
    return StageResult.model_validate(fields)


class BudgetHarness(WorkerHarness):
    """The worker with a pause budget on the fleet Redis, and a coordinator that shares its store."""

    def __init__(self, fleet: FleetRedis) -> None:
        super().__init__()
        fleet.clock_ms = self.clock
        self.fleet = fleet
        self.store = PauseStore()
        self.judge = PauseJudge(self.store, clock_ms=self.clock, limits=PauseLimits())
        self.coordinator = Coordinator(
            self.registry, self.states, self.headlines, self.producer,  # type: ignore[arg-type]
            policy_for=lambda _org: DEFAULT_POLICY, logger=logging.getLogger("t"), clock_ms=self.clock,
            pause_store=self.store,
        )
        self.handler = StageJobHandler(
            self.registry, self.states, self.headlines, self.coordinator, StubIO,
            logger=logging.getLogger("t"), worker_id="w1", clock_ms=self.clock, pause_judge=self.judge,
        )

    async def pause_state(self, job: StageJob) -> PauseState | None:
        return await self.store.get(job.state_key)
