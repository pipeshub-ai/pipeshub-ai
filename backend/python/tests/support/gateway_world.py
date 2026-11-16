"""Stage workers over the real gateway and fleet breaker, against a fake provider, on a simulated clock.

What is real: ``StageJobHandler`` (claim, verdict, redrive), ``PauseJudge`` and the pause store, ``LLMGateway`` (admit, wire
judgement, settle, trial lease) and the breaker's Lua on Redis. What is faked: the provider (``FakeProvider``), the stage state
store (in memory) and the clock, which the provider advances by each request's latency and the driver by each pause's delay.

``W`` workers are separate gateways and handlers sharing one Redis, like the processes of a pod or several pods.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import random
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import httpx

from app.config.constants.arangodb import ProgressStatus
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
from app.modules.pipeline.pause_budget import PauseLimits
from app.modules.pipeline.pause_judge import PauseJudge
from app.modules.pipeline.pause_store import PauseStore
from app.modules.pipeline.policy import DEFAULT_POLICY, PipelinePolicy
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.worker import StageFailed, StageJobHandler, StagePaused
from app.services.llm_gateway import gateway as gateway_module
from app.services.llm_gateway import wire
from app.services.llm_gateway.gateway import LLMGateway
from app.services.messaging.config import IndexingEvent, StreamMessage
from app.services.resource_governor.models import ParseTier
from app.utils.llm import LLMUnavailableError
from tests.unit.modules.pipeline.fakes import (
    InMemoryStageStateStore,
    RecordingHeadlines,
    RecordingProducer,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.modules.pipeline.stage import Deadline, StageIO
    from app.services.llm_gateway.fleet_redis import FleetRedis
    from tests.support.spend_world import SimClock

OUTAGE_KINDS = ("503", "529", "hang", "reset", "blackhole")
LATENCY_S = {"503": 2.0, "529": 2.0, "hang": 600.0, "reset": 3.0, "blackhole": 10.0}


class FakeProvider:
    """A model the stage calls through the gateway. ``fails(doc)`` says whether a request for ``doc`` fails now."""

    def __init__(self, clock: SimClock) -> None:
        self.clock = clock
        self.kind = "503"
        # seconds after the world started
        self.outage_until_s: float = 0.0
        self.t0 = clock()
        self.poison: set[str] = set()
        self.requests: Counter[str] = Counter()
        self.model_name = "fake-model"

    def fails(self, doc: str) -> bool:
        if doc.startswith("bg-"):  # other traffic on the same key keeps being served
            return False
        return doc in self.poison or (self.clock() - self.t0) / 1000.0 < self.outage_until_s

    async def ainvoke(self, value, /, **kwargs) -> str:
        doc = str(value)
        self.requests[doc] += 1
        wire.note_request_sent()
        if not self.fails(doc):
            self.clock.advance_s(0.5)
            wire.note_response(200)
            return "answer"
        kind = "hang" if doc in self.poison else self.kind
        self.clock.advance_s(LATENCY_S[kind])
        if kind == "503":
            wire.note_response(503)
            raise RuntimeError("a wrapper's own exception type")
        if kind == "529":
            wire.note_response(529)
            raise ValueError("overloaded")
        if kind == "reset":
            raise ConnectionResetError("reset after the write")
        if kind == "blackhole":
            raise httpx.ConnectTimeout("connect capped at 10 s")
        await asyncio.sleep(3600)  # a hang: the gateway's ceiling ends it
        raise AssertionError("unreachable")


class _IO:
    def __init__(self, job: StageJob, deadline: Deadline) -> None:
        self._deadline = deadline

    @property
    def policy(self) -> PipelinePolicy:
        return DEFAULT_POLICY

    @property
    def deadline(self) -> Deadline:
        return self._deadline


class ModelStage:
    """A stage like classification: one model call per run, a provider outage is a pause."""

    name: ClassVar[str] = "classify"
    version: ClassVar[int] = 1
    requires: ClassVar[frozenset[str]] = frozenset({"embed"})
    workload: ClassVar[Workload] = Workload.LLM
    headline: ClassVar[HeadlineField | None] = HeadlineField.EXTRACTION
    budget_s: ClassVar[float] = 5.0

    def __init__(self, gateway: LLMGateway, provider: FakeProvider, *, est_chars: int = 0) -> None:
        self.gateway = gateway
        self.provider = provider
        self.est_chars = est_chars

    def applies(self, view: RecordView, policy: PipelinePolicy) -> bool:
        return True

    async def fingerprint(self, job: StageJob, io: StageIO) -> StageFingerprint:
        return StageFingerprint(stage=self.name, stage_version=1, input_digest=job.text_digest, config_digest="cfg")

    async def run(self, job: StageJob, io: StageIO) -> StageResult:
        doc = job.virtual_record_id
        text = doc + " " * self.est_chars  # the size class follows the input size
        try:
            await self.gateway.invoke(_Doc(self.provider, doc, text), text, provider="FakeChat:fake-model@host")
        except LLMUnavailableError as exc:
            return StageResult.paused_for(exc, f"Model provider unavailable: {exc}")
        return StageResult(outcome=StageOutcome.COMPLETED)


class _Doc:
    """Binds the document to the provider call (the fake keys its requests by document)."""

    def __init__(self, provider: FakeProvider, doc: str, text: str) -> None:
        self._provider, self._doc = provider, doc

    async def ainvoke(self, value, /, **kwargs):  # noqa: ANN202
        return await self._provider.ainvoke(self._doc, **kwargs)


@dataclass
class Outcome:
    status: str = "pending"  # OK | FAILED
    reason: str | None = None
    deliveries: int = 0
    finished_s: float | None = None


class GatewayWorld:
    def __init__(
        self, fleet: FleetRedis, clock: SimClock, *, workers: int = 1, seed: int = 1, est_chars: int = 0,
        stage_factory: Callable[[LLMGateway], Any] | None = None,
    ) -> None:
        self.fleet = fleet
        self.clock = clock
        fleet.clock_ms = clock
        self.provider = FakeProvider(clock)
        self.states = InMemoryStageStateStore(self.clock)  # type: ignore[arg-type]
        self.headlines = RecordingHeadlines()
        self.producer = RecordingProducer()
        self.store = PauseStore()
        self.t0 = clock()
        self.provider.t0 = self.t0
        self.rng = random.Random(seed)
        self.handlers: list[StageJobHandler] = []
        self.gateways: list[LLMGateway] = []
        self.coordinators: list[Coordinator] = []
        for _ in range(workers):
            gateway = LLMGateway(8)
            registry = StageRegistry()
            registry.register_external("embed")
            registry.register(stage_factory(gateway) if stage_factory else ModelStage(gateway, self.provider, est_chars=est_chars))
            judge = PauseJudge(self.store, clock_ms=clock, limits=PauseLimits(), rng=random.Random(self.rng.random()))
            coordinator = Coordinator(
                registry, self.states, self.headlines, self.producer,  # type: ignore[arg-type]
                policy_for=lambda _org: DEFAULT_POLICY, logger=logging.getLogger("gw"), clock_ms=clock, pause_store=self.store,
            )
            self.gateways.append(gateway)
            self.coordinators.append(coordinator)
            self.handlers.append(StageJobHandler(
                registry, self.states, self.headlines, coordinator, _IO, logger=logging.getLogger("gw"),
                worker_id=f"w{len(self.handlers)}", clock_ms=clock, pause_judge=judge,
            ))
        self._published_seen = 0
        self.jobs: dict[str, StageJob] = {}
        self.outcomes: dict[str, Outcome] = {}
        self.queue: list[tuple[float, int, str]] = []
        self.seq = 0

    def add_job(self, doc: str, *, arrive_s: float = 0.0) -> None:
        job = StageJob.model_validate({
            "stage": "classify", "stage_version": 1, "org_id": "org-1", "virtual_record_id": doc, "rev": "rev-a",
            "record_ids": (f"rec-{doc}",), "connector_id": "conn-1", "tier": ParseTier.LIGHT, "priority": Priority.BULK,
            "trigger": "embed", "text_digest": "t", "blocks_digest": "b", "text_chars": 10, "has_tables": False,
            "has_images": False,
        })
        self.jobs[doc] = job
        self.outcomes[doc] = Outcome()
        self.states.put(StageState.from_job(job, ProgressStatus.QUEUED, self.clock()))
        self._schedule(arrive_s, doc)

    def _schedule(self, at_s: float, doc: str) -> None:
        self.seq += 1
        heapq.heappush(self.queue, (at_s, self.seq, doc))

    def add_background(self, every_s: float, until_s: float, *, start_s: float = 0.0) -> None:
        """Requests of other call sites on the same provider key and size class, which the provider keeps serving."""
        n = 0
        t = start_s
        while t < until_s:
            self._schedule(t, f"bg-{n}")
            self.outcomes[f"bg-{n}"] = Outcome()
            n += 1
            t += every_s

    @property
    def now_s(self) -> float:
        return (self.clock() - self.t0) / 1000.0

    async def deliver(self, doc: str, worker: int) -> None:
        job = self.jobs[doc]
        outcome = self.outcomes[doc]
        outcome.deliveries += 1
        message = StreamMessage(eventType="stageJob", payload=job.model_dump(mode="json", by_alias=True))
        try:
            events = [e.event async for e in self.handlers[worker](message)]
            assert events and events[-1] is IndexingEvent.INDEXING_COMPLETE
            outcome.status, outcome.finished_s = "OK", self.now_s
        except StagePaused as paused:
            self._schedule(self.now_s + float(paused.delay_s or 0.0), doc)
        except StageFailed as failed:
            outcome.status, outcome.reason, outcome.finished_s = "FAILED", str(failed), self.now_s
        self._consume_published()

    def _consume_published(self) -> None:
        """A job the coordinator published (a redrive after a provider hold) is delivered to a worker again."""
        for _topic, _event, payload, _key in self.producer.sent[self._published_seen :]:
            doc = str(payload["virtualRecordId"])
            if doc in self.outcomes and self.outcomes[doc].status == "FAILED":
                self.outcomes[doc] = Outcome(deliveries=self.outcomes[doc].deliveries)
                self._schedule(self.now_s, doc)
        self._published_seen = len(self.producer.sent)

    async def _background(self, doc: str) -> None:
        try:
            await self.gateways[0].invoke(_Doc(self.provider, doc, doc), doc, provider="FakeChat:fake-model@host")
            self.outcomes[doc].status = "OK"
        except LLMUnavailableError:
            self.outcomes[doc].status = "FAILED"

    async def run(self, horizon_s: float = 3 * 86400.0) -> dict[str, Outcome]:
        while self.queue:
            at_s, _, doc = heapq.heappop(self.queue)
            if at_s > horizon_s:
                break
            if self.now_s < at_s:
                self.clock.advance_s(at_s - self.now_s)
            if self.outcomes[doc].status != "pending":
                continue
            if doc.startswith("bg-"):
                await self._background(doc)
                continue
            await self.deliver(doc, (list(self.jobs).index(doc)) % len(self.handlers))
        return self.outcomes


def patch_gateway_ceiling(monkeypatch, seconds: float = 0.02) -> None:
    """The fake advances the simulated clock by a hang's 600 s itself; the real ceiling just has to end it."""
    monkeypatch.setattr(gateway_module, "CALL_TIMEOUT_S", seconds)
