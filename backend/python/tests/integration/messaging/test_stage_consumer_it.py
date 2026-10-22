"""Pipeline stage consumers on real Redis Streams and Kafka brokers (INT-RACE-01, INT-BP-02, INT-DLQ-01).

What a fake broker cannot show: a duplicated job redelivered to a real consumer, the
stage's own admission limit on a real consumer, and the difference between a paused
job (requeued without an attempt) and a failing one (dead-lettered after
MAX_DELIVERY_ATTEMPTS). Consumers are built by MessagingFactory, as in production.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""

import asyncio
import logging
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.pipeline import worker as pipeline_worker
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.models import (
    HeadlineField,
    Priority,
    RecordView,
    StageFingerprint,
    StageJob,
    StageOutcome,
    StageResult,
    Workload,
    stage_state_key,
)
from app.modules.pipeline.policy import DEFAULT_POLICY, PipelinePolicy
from app.modules.pipeline.publisher import DeferredPublisher
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.stage import Deadline, StageIO
from app.modules.pipeline.worker import StageJobHandler
from app.services.distributed.interface import IRetryTracker
from app.services.messaging.config import (
    ConsumerType,
    MessageBrokerType,
    PipelineEvent,
    RedisConfig,
    RedisStreamsConfig,
    StreamMessage,
    messaging_env,
)
from app.services.messaging.consumer_concurrency import StageAdmission
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.kafka.consumer import (
    indexing_consumer as kafka_indexing_consumer,
)
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.redis_streams import (
    indexing_consumer as redis_indexing_consumer,
)
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from app.services.messaging.worker_loop import WorkerLoop
from app.services.resource_governor.models import ParseTier
from tests.integration.messaging.conftest import (
    DRAIN_TIMEOUT_SECONDS,
    create_kafka_topic,
    delete_kafka_topic,
)
from tests.unit.modules.pipeline.fakes import (
    InMemoryStageStateStore,
    RecordingHeadlines,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_LOGGER = logging.getLogger("stage-consumer-it")
# Requeue delay for these tests; the production schedule starts at 15s.
_BACKOFF_S = 0.2
_REV = "rev-a"


def _now_ms() -> int:
    return int(time.time() * 1000)


class _IO:
    def __init__(self, deadline: Deadline) -> None:
        self._deadline = deadline

    @property
    def policy(self) -> PipelinePolicy:
        return DEFAULT_POLICY

    @property
    def deadline(self) -> Deadline:
        return self._deadline


class _Stage:
    """A classify-shaped stage whose outcomes are scripted; runs on the consumer's worker loop."""

    name: ClassVar[str] = "classify"
    version: ClassVar[int] = 1
    requires: ClassVar[frozenset[str]] = frozenset({"embed"})
    workload: ClassVar[Workload] = Workload.LLM
    headline: ClassVar[HeadlineField | None] = HeadlineField.EXTRACTION
    budget_s: ClassVar[float] = 30.0

    def __init__(self, *, delay: float = 0.0, script: list[StageOutcome] | None = None) -> None:
        self.delay = delay
        self.script = list(script or [])
        self.runs = 0
        self.running = 0
        self.max_running = 0
        self._lock = threading.Lock()

    def applies(self, view: RecordView, policy: PipelinePolicy) -> bool:
        return True

    async def fingerprint(self, job: StageJob, io: StageIO) -> StageFingerprint:
        return StageFingerprint(stage=self.name, stage_version=self.version, input_digest=job.text_digest, config_digest="c")

    async def run(self, job: StageJob, io: StageIO) -> StageResult:
        with self._lock:
            self.runs += 1
            self.running += 1
            self.max_running = max(self.max_running, self.running)
            outcome = self.script.pop(0) if self.script else StageOutcome.COMPLETED
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            with self._lock:
                self.running -= 1
        return StageResult(outcome=outcome, reason=f"scripted {outcome.value}")


@dataclass
class _Rig:
    broker: MessageBrokerType
    topic: str
    producer: Any
    retry_manager: Any
    consumer_config: Any


@dataclass
class _Runtime:
    stage: _Stage
    states: InMemoryStageStateStore
    headlines: RecordingHeadlines
    coordinator: Coordinator
    handler: StageJobHandler
    handled: list[str]
    loops: set[asyncio.AbstractEventLoop] = field(default_factory=set[asyncio.AbstractEventLoop])


async def _retry_manager(host: str, port: int) -> IRetryTracker:
    manager = MessagingFactory.create_retry_manager(_LOGGER, RedisConfig(host=host, port=port))
    await manager.initialize()
    return manager


@pytest.fixture
async def redis_rig(redis_available, unique_suffix, monkeypatch) -> AsyncIterator[_Rig]:
    host, port = redis_available
    monkeypatch.setattr(redis_indexing_consumer, "compute_retry_backoff_seconds", lambda _count: _BACKOFF_S)
    monkeypatch.setattr(pipeline_worker, "compute_retry_backoff_seconds", lambda _count: _BACKOFF_S)
    topic = f"pipeline.classify.{unique_suffix}"
    producer = RedisStreamsProducer(_LOGGER, RedisStreamsConfig(host=host, port=port, client_id=f"stage-it-{unique_suffix}"))
    await producer.initialize()
    retry_manager = await _retry_manager(host, port)
    config = RedisStreamsConfig(
        host=host, port=port, client_id=f"stage-it-{unique_suffix}-c", group_id=f"stage-it-{unique_suffix}",
        topics=[topic], batch_size=10, block_ms=500, claim_min_idle_ms=500,
    )
    yield _Rig(MessageBrokerType.REDIS, topic, producer, retry_manager, config)
    await producer.cleanup()
    await retry_manager.cleanup()


@pytest.fixture
async def kafka_rig(kafka_available, redis_available, unique_suffix, monkeypatch) -> AsyncIterator[_Rig]:
    monkeypatch.setattr(kafka_indexing_consumer, "_compute_retry_backoff_seconds", lambda _count: _BACKOFF_S)
    monkeypatch.setattr(pipeline_worker, "compute_retry_backoff_seconds", lambda _count: _BACKOFF_S)
    topic = f"pipeline.classify.{unique_suffix}"
    await create_kafka_topic(kafka_available, topic, 1)
    producer = KafkaMessagingProducer(
        _LOGGER, KafkaProducerConfig(bootstrap_servers=[kafka_available], client_id=f"stage-it-{unique_suffix}")
    )
    await producer.initialize()
    retry_manager = await _retry_manager(*redis_available)
    config = KafkaConsumerConfig(
        topics=[topic], client_id=f"stage-it-{unique_suffix}-c", group_id=f"stage-it-{unique_suffix}",
        auto_offset_reset="earliest", enable_auto_commit=False, bootstrap_servers=[kafka_available],
    )
    yield _Rig(MessageBrokerType.KAFKA, topic, producer, retry_manager, config)
    await producer.cleanup()
    await retry_manager.cleanup()
    await delete_kafka_topic(kafka_available, topic)


@pytest.fixture(params=["redis", "kafka"])
def rig(request: pytest.FixtureRequest) -> _Rig:
    return request.getfixturevalue(f"{request.param}_rig")


def _runtime(rig: _Rig, stage: _Stage) -> _Runtime:
    registry = StageRegistry()
    registry.register_external("embed")
    registry.register(stage)
    registry.topic_for = lambda _name: rig.topic  # type: ignore[method-assign]
    states = InMemoryStageStateStore(_now_ms)  # type: ignore[arg-type]
    headlines = RecordingHeadlines()
    publisher = DeferredPublisher()
    publisher.bind(rig.producer, asyncio.get_running_loop())
    coordinator = Coordinator(
        registry, states, headlines, publisher, policy_for=lambda _org: DEFAULT_POLICY, logger=_LOGGER, clock_ms=_now_ms
    )
    handler = StageJobHandler(
        registry, states, headlines, coordinator, lambda _job, deadline: _IO(deadline),
        logger=_LOGGER, worker_id="stage-it", clock_ms=_now_ms,
    )
    return _Runtime(stage, states, headlines, coordinator, handler, [])


def _view(vrid: str) -> RecordView:
    return RecordView(
        org_id="org-1", virtual_record_id=vrid, rev=_REV, record_ids=(f"rec-{vrid}",), connector_id="conn-1",
        tier=ParseTier.LIGHT, text_digest=f"t-{vrid}", blocks_digest="b", text_chars=10, has_tables=False,
        has_images=False,
    )


def _counting(runtime: _Runtime) -> Callable[[StreamMessage], AsyncGenerator[PipelineEvent, None]]:
    async def handle(message: StreamMessage) -> AsyncGenerator[PipelineEvent, None]:
        runtime.loops.add(asyncio.get_running_loop())
        async for event in runtime.handler(message):
            yield event
        runtime.handled.append(str(message.payload.get("jobId")))

    return handle


async def _consume(rig: _Rig, runtime: _Runtime, *, limit: int, until: Callable[[], bool]) -> None:
    # As in production: the stage consumer joins the process's one worker loop.
    worker = WorkerLoop(_LOGGER)
    worker_loop = worker.start()
    consumer = MessagingFactory.create_consumer(
        _LOGGER,
        config=rig.consumer_config,
        broker_type=rig.broker,
        consumer_type=ConsumerType.INDEXING,
        retry_manager=rig.retry_manager,
        producer=rig.producer,
        disposition_sink=runtime.handler,
        stage_admission=StageAdmission(stage="classify", limit=limit),
        worker=worker,
    )
    await consumer.start(_counting(runtime))  # type: ignore[arg-type]
    try:
        deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
        while not until():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"stage consumer on {rig.broker.value} did not settle in time")
            await asyncio.sleep(0.2)
        assert runtime.loops == {worker_loop}
    finally:
        await consumer.stop()
        await worker.aclose()


def _status(runtime: _Runtime, vrid: str) -> ProgressStatus | None:
    state = runtime.states.states.get(stage_state_key(vrid, _REV, "classify"))
    return state.status if state else None


async def test_a_duplicated_job_runs_once(rig: _Rig) -> None:  # INT-RACE-01
    runtime = _runtime(rig, _Stage(delay=0.3))
    view = _view("vr-dup")
    [job_id] = await runtime.coordinator.on_external_done("embed", view, priority=Priority.BULK, trigger="it")
    # A second copy of the same job, as a sweeper re-publish would leave on the broker.
    duplicate = StageJob.for_view(view, stage="classify", stage_version=1, priority=Priority.BULK, trigger="embed")
    assert await rig.producer.send_event(rig.topic, "stageJob", duplicate.model_dump(mode="json", by_alias=True), key="conn-1")

    await _consume(rig, runtime, limit=4, until=lambda: runtime.handled.count(job_id) == 2)

    assert runtime.stage.runs == 1
    assert _status(runtime, "vr-dup") is ProgressStatus.COMPLETED


async def test_the_stage_limit_caps_concurrent_jobs(rig: _Rig) -> None:
    runtime = _runtime(rig, _Stage(delay=0.5))
    vrids = [f"vr-cap-{i}" for i in range(6)]
    for vrid in vrids:
        await runtime.coordinator.on_external_done("embed", _view(vrid), priority=Priority.BULK, trigger="it")

    await _consume(rig, runtime, limit=2, until=lambda: all(_status(runtime, v) is ProgressStatus.COMPLETED for v in vrids))

    assert runtime.stage.runs == 6
    # The stage's own limit bounds it: not the record-indexing concurrency, and on Kafka
    # not the topic's single partition.
    assert runtime.stage.max_running == 2


async def test_a_paused_job_is_not_dead_lettered_for_waiting(rig: _Rig) -> None:  # INT-BP-02
    # Past the retry budget and the crash-loop delivery backstop: an outage of any length is waited out.
    pauses = max(messaging_env.max_delivery_attempts, messaging_env.redis_max_deliveries) + 2
    runtime = _runtime(rig, _Stage(script=[StageOutcome.PAUSED] * pauses))
    await runtime.coordinator.on_external_done("embed", _view("vr-pause"), priority=Priority.BULK, trigger="it")

    await _consume(rig, runtime, limit=2, until=lambda: _status(runtime, "vr-pause") is ProgressStatus.COMPLETED)

    assert runtime.stage.runs == pauses + 1


async def test_a_failing_job_is_dead_lettered_and_its_stage_failed(rig: _Rig) -> None:  # INT-DLQ-01
    runtime = _runtime(rig, _Stage(script=[StageOutcome.RETRY] * 20))
    await runtime.coordinator.on_external_done("embed", _view("vr-fail"), priority=Priority.BULK, trigger="it")

    await _consume(rig, runtime, limit=2, until=lambda: _status(runtime, "vr-fail") is ProgressStatus.FAILED)

    assert runtime.stage.runs == messaging_env.max_delivery_attempts
    assert runtime.headlines.writes[-1][2] is ProgressStatus.FAILED
