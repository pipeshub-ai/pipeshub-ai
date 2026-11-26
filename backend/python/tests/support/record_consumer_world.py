"""A deterministic world for the record consumers: a fake broker, an injected clock, and a lockstep driver.

The three consumers (Kafka, Redis Streams, PostgreSQL queue) are driven through their real read, dispatch and
claim code. Nothing runs on a second thread: the consumer's "worker loop" is the test's own loop, the broker
is in memory, time is a counter the driver advances, and each step runs the phases once and then lets every
started task finish. So two runs of the same stream give the same trace, which is what lets a trace recorded
on one commit be compared with the trace of another (``tests/unit/services/messaging/goldens``).

``generate_messages`` builds the stream: several orgs and connectors, one connector with far more queued
messages than its cap (so remembered positions, fetch-back and parked entries all run), heavy and light
records, duplicates of a record, and messages that carry a retry back-off.

The drivers take ``consumer_kwargs`` and pass them to the consumer's constructor, so the same driver serves the
base commit (no extra arguments) and a build that takes more.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import random
import time as real_time
import zlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from tests.support.fake_kafka import FakeKafkaBroker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

LOGGER = logging.getLogger("record-consumer-world")
START = 1_700_000_000.0
TICK = 0.5
KAFKA_TOPIC = "record-events"
GROUP = "records_consumer_group"
LANES = 4


class FakeTime:
    """Stands in for the ``time`` module inside the code under test; time moves only when the driver ticks."""

    def __init__(self) -> None:
        self.now = START

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now - START + 1000.0

    def tick(self, seconds: float = TICK) -> None:
        self.now += seconds

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - sleep, perf_counter and the rest stay real
        return getattr(real_time, name)


@contextlib.contextmanager
def injected_clock(*modules: Any) -> Iterator[FakeTime]:  # noqa: ANN401
    clock = FakeTime()
    patches = [patch.object(module, "time", clock) for module in modules]
    for p in patches:
        p.start()
    try:
        yield clock
    finally:
        for p in reversed(patches):
            p.stop()


def clock_modules() -> list[Any]:
    from app.services.messaging.kafka.consumer import (
        indexing_consumer as kafka_consumer,
    )
    from app.services.messaging.redis_streams import indexing_consumer as redis_consumer
    from app.services.messaging.scheduling import drr_scheduler

    return [drr_scheduler, kafka_consumer, redis_consumer]


@dataclass
class Message:
    record_id: str
    org_id: str
    connector_id: str
    extension: str
    retry_not_before: float | None = None

    def envelope(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "recordId": self.record_id,
            "orgId": self.org_id,
            "connectorId": self.connector_id,
            "extension": self.extension,
            # A real PDF event carries the PDF mime: a .pdf stored as text/plain is read, and admitted, as text.
            "mimeType": "application/pdf" if self.extension == "pdf" else "text/plain",
        }
        if self.retry_not_before is not None:
            payload["_retry_not_before"] = self.retry_not_before
        return {"eventType": "newRecord", "payload": payload, "timestamp": 1}


def generate_messages(seed: int, count: int, *, orgs: int = 4, hot_share: float = 0.3) -> list[Message]:
    """One hot connector (``hot_share`` of the stream), the rest spread over several orgs and connectors."""
    rnd = random.Random(seed)
    connectors = {f"org-{o}": [f"org-{o}-conn-{c}" for c in range(rnd.randint(1, 3))] for o in range(orgs)}
    hot = ("org-0", connectors["org-0"][0])
    out: list[Message] = []
    for i in range(count):
        if rnd.random() < hot_share:
            org, connector = hot
        else:
            org = f"org-{rnd.randrange(orgs)}"
            connector = rnd.choice(connectors[org])
        record_id = f"rec-{i}"
        if out and rnd.random() < 0.03:
            record_id = rnd.choice(out[-50:]).record_id  # a second event for a record already in the stream
        back_off = START + rnd.randint(1, 40) * TICK if rnd.random() < 0.03 else None
        out.append(Message(record_id, org, connector, rnd.choice(["txt", "txt", "pdf"]), back_off))
    return out


def lane_of(connector_id: str, lanes: int = LANES) -> int:
    return zlib.crc32(connector_id.encode()) % lanes


@dataclass
class Trace:
    """What a run did, in an order that a change to the code would disturb."""

    dispatch: list[str] = field(default_factory=list)
    settled: list[Any] = field(default_factory=list)
    counters: list[list[int]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    steps: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "dispatch": self.dispatch,
            "settled": self.settled,
            "counters": self.counters,
            "steps": self.steps,
            **self.extra,
        }


class Handler:
    """Takes a few turns of the loop per record, so records are in flight while the next step reads."""

    def __init__(self, turns: int = 3) -> None:
        self.seen: list[str] = []
        self.turns = turns

    async def __call__(self, message: Any) -> Any:  # noqa: ANN401
        self.seen.append(str(message.payload["recordId"]))
        for _ in range(self.turns):
            await asyncio.sleep(0)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r"))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r"))


async def settle(turns: int = 12) -> None:
    for _ in range(turns):
        await asyncio.sleep(0)


def fair_config(**overrides: Any) -> FairSchedulerConfig:  # noqa: ANN401
    values: dict[str, Any] = {
        "enabled": True,
        "key_fields": ("orgId", "connectorId"),
        "default_quantum": 1,
        "max_buffered_messages": 80,
        "max_per_entity_messages": 20,
        "max_dwell_seconds": 900.0,
        "parallel_partitions": True,
        "max_remembered_positions": 5_000,
    }
    values.update(overrides)
    return FairSchedulerConfig(**values)


# -- Kafka -----------------------------------------------------------------------------------------------------------
@dataclass
class KafkaRun:
    consumer: Any
    broker: FakeKafkaBroker
    handler: Handler
    clock: FakeTime
    fake: Any


@asynccontextmanager
async def kafka_world(
    messages: list[Message],
    *,
    partitions: int = 4,
    fair: FairSchedulerConfig | None = None,
    consumer_kwargs: dict[str, Any] | None = None,
    topics: list[str] | None = None,
    produce: bool = True,
    config_topics: list[str] | None = None,
) -> AsyncIterator[KafkaRun]:
    from app.services.messaging.kafka.consumer import (
        indexing_consumer as kafka_consumer,
    )
    from app.services.messaging.kafka.consumer import remembered as remembered_module
    from app.services.messaging.kafka.consumer.indexing_consumer import (
        IndexingKafkaConsumer,
    )

    broker = FakeKafkaBroker()
    factory = broker.consumer_factory()
    topic_list = topics or [KAFKA_TOPIC]
    if produce:
        for m in messages:
            broker.produce(topic_list[0], m.envelope(), partition=lane_of(m.connector_id, partitions))
    with injected_clock(*clock_modules()) as clock, patch.object(kafka_consumer, "AIOKafkaConsumer", factory), \
            patch.object(remembered_module, "AIOKafkaConsumer", factory):
        config = KafkaConsumerConfig(
            topics=config_topics or topic_list, client_id="world", group_id=GROUP, auto_offset_reset="earliest",
            enable_auto_commit=False, bootstrap_servers=["kafka:9092"],
        )
        consumer = IndexingKafkaConsumer(
            LOGGER, config, fair_scheduler_config=fair or fair_config(), **(consumer_kwargs or {})
        )
        loop = asyncio.get_running_loop()
        handler = Handler()
        consumer.running = True
        consumer.main_loop = loop
        consumer.worker_loop = loop
        consumer._on_worker_loop_started(loop)
        consumer._finished = asyncio.Event()
        consumer.message_handler = handler
        fake = factory(group_id=GROUP, auto_offset_reset="earliest")
        fake.subscribe(list(consumer.kafka_config.topics))
        consumer.consumer = fake
        try:
            yield KafkaRun(consumer, broker, handler, clock, fake)
        finally:
            consumer.running = False


async def kafka_step(run: KafkaRun) -> None:
    """One pass of the consume loop's fair-scheduling branch, then every started task runs to its end."""
    c = run.consumer
    c._IndexingKafkaConsumer__apply_backpressure()
    await c._IndexingKafkaConsumer__sweep_stale_offsets()
    c._IndexingKafkaConsumer__resume_drained_lanes()
    await c._IndexingKafkaConsumer__read_phase()
    await c._IndexingKafkaConsumer__dispatch_phase()
    await settle()
    run.clock.tick()


async def run_kafka(
    messages: list[Message],
    *,
    max_steps: int = 4_000,
    on_step: Callable[[KafkaRun, int], None] | None = None,
    **world: Any,  # noqa: ANN401
) -> Trace:
    trace = Trace()
    async with kafka_world(messages, **world) as run:
        for step in range(max_steps):
            await kafka_step(run)
            remembered = run.consumer._remembered
            trace.counters.append([
                run.consumer._scheduler.pending_count,
                remembered.total if remembered is not None else 0,
                len(run.consumer._deferred_messages),
                len(run.handler.seen),
            ])
            if on_step is not None:
                on_step(run, step)
            if len(run.handler.seen) >= len(messages) and run.broker.consumers:
                done = sum(run.broker.committed[GROUP].values())
                if done >= len(messages):
                    break
        trace.steps = step + 1
        trace.dispatch = list(run.handler.seen)
        trace.settled = [
            sorted((tp.partition, off) for tp, off in call.items()) for call in run.fake.commit_calls
        ]
        trace.extra["committed"] = sorted((tp.partition, off) for tp, off in run.broker.committed[GROUP].items())
    return trace


# -- Redis Streams ---------------------------------------------------------------------------------------------------
@dataclass
class RedisRun:
    consumer: Any
    redis: Any
    handler: Handler
    clock: FakeTime
    topics: list[str]


@asynccontextmanager
async def redis_world(
    messages: list[Message],
    *,
    lanes: int = LANES,
    topics: list[str] | None = None,
    fair: FairSchedulerConfig | None = None,
    consumer_kwargs: dict[str, Any] | None = None,
    stream_of: Callable[[Message], str] | None = None,
    batch_size: int = 10,
    produce: bool = True,
    config_overrides: dict[str, Any] | None = None,
) -> AsyncIterator[RedisRun]:
    import fakeredis.aioredis as fakeredis_aio

    from app.services.messaging.redis_streams.indexing_consumer import (
        IndexingRedisStreamsConsumer,
    )

    stream_names = topics or [f"{KAFKA_TOPIC}.{i}" for i in range(lanes)]
    redis = fakeredis_aio.FakeRedis(decode_responses=True)
    for name in stream_names:
        await redis.xgroup_create(name, GROUP, "0", mkstream=True)
    if produce:
        for i, m in enumerate(messages):
            stream = stream_of(m) if stream_of else stream_names[lane_of(m.connector_id, len(stream_names))]
            await redis.xadd(stream, {"value": json.dumps(m.envelope())}, id=f"{i + 1}-0")
    with injected_clock(*clock_modules()) as clock:
        config = RedisStreamsConfig(
            host="localhost", port=6379, client_id="world", group_id=GROUP, topics=list(stream_names),
            batch_size=batch_size, block_ms=5, **(config_overrides or {}),
        )
        consumer = IndexingRedisStreamsConsumer(
            LOGGER, config, fair_scheduler_config=fair or fair_config(), **(consumer_kwargs or {})
        )
        loop = asyncio.get_running_loop()
        handler = Handler()
        consumer.running = True
        consumer.main_loop = loop
        consumer.worker_loop = loop
        consumer._on_worker_loop_started(loop)
        consumer._finished = asyncio.Event()
        consumer.message_handler = handler
        consumer.redis = redis
        consumer._pending_message_is_owned = AsyncMock(return_value=True)
        acks: list[tuple[str, str]] = []

        async def ack(stream: str, message_id: str, *_a: Any, **_k: Any) -> None:  # noqa: ANN401
            await redis.xack(stream, GROUP, message_id)  # as the real one does, so nothing is handed out twice
            acks.append((stream, message_id))

        consumer._ack_message = ack
        consumer.acks = acks
        try:
            yield RedisRun(consumer, redis, handler, clock, list(stream_names))
        finally:
            consumer.running = False
            await redis.aclose()


async def redis_step(run: RedisRun) -> None:
    c = run.consumer
    await c._IndexingRedisStreamsConsumer__read_phase()
    await c._IndexingRedisStreamsConsumer__dispatch_phase()
    await settle()
    run.clock.tick()


async def run_redis(
    messages: list[Message],
    *,
    max_steps: int = 4_000,
    on_step: Callable[[RedisRun, int], None] | None = None,
    **world: Any,  # noqa: ANN401
) -> Trace:
    trace = Trace()
    async with redis_world(messages, **world) as run:
        for step in range(max_steps):
            await redis_step(run)
            trace.counters.append([
                run.consumer._scheduler.pending_count,
                len(run.consumer._deferred_entries),
                len(run.handler.seen),
            ])
            if on_step is not None:
                on_step(run, step)
            if len(run.handler.seen) >= len(messages) and len(run.consumer.acks) >= len(messages):
                break
        trace.steps = step + 1
        trace.dispatch = list(run.handler.seen)
        trace.settled = [[stream, message_id] for stream, message_id in run.consumer.acks]
    return trace


# -- PostgreSQL queue ------------------------------------------------------------------------------------------------
class FakePgTx:
    def __init__(self, world: FakePgRuntime) -> None:
        self.world = world

    async def fetchval(self, sql: str, *args: Any) -> None:  # noqa: ANN401
        self.world.statements.append(["lock", self.world.sql_name(sql), list(args)])

    async def fetch(self, sql: str, group: str, topics: list[str], limit: int, visibility: float, consumer: str) -> list[dict]:
        self.world.statements.append(["claim", self.world.sql_name(sql), [group, topics, limit, visibility, consumer]])
        rows = self.world.rows
        out: list[dict] = []
        for topic in topics:
            queue = rows.get(topic, [])
            while queue and len(out) < limit:
                out.append(queue.pop(0))
        return out

    async def execute(self, sql: str, *args: Any) -> str:  # noqa: ANN401
        self.world.statements.append(["write", self.world.sql_name(sql), []])
        return "UPDATE 1"

    async def __aenter__(self) -> FakePgTx:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class FakePgRuntime:
    """Serves ``PgQueue`` claims from per-topic lists and records every statement it is asked to run."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}
        self.statements: list[list[Any]] = []

    @staticmethod
    def sql_name(sql: str) -> str:
        return hashlib.sha256(sql.encode()).hexdigest()[:16]

    async def db(self) -> SimpleNamespace:
        return SimpleNamespace(runtime_uow=lambda write=False: FakePgTx(self))


def pg_claim_sql() -> dict[str, str]:
    """The claim statements by name, as the digests the fake runtime records."""
    from app.services.messaging.postgres import queue

    return {
        "claim": FakePgRuntime.sql_name(queue._CLAIM),
        "claim_ordered": FakePgRuntime.sql_name(queue._CLAIM_ORDERED),
        "lock_ordered": FakePgRuntime.sql_name(queue._LOCK_ORDERED),
    }


async def run_pg(
    messages: list[Message],
    *,
    topics: list[str] | None = None,
    ordered_topics: str = "record-events",
    consumer_kwargs: dict[str, Any] | None = None,
    batch_size: int = 6,
    concurrency: int = 4,
    monkeypatch: Any = None,  # noqa: ANN401 - pytest's MonkeyPatch, or None for a plain process
    max_seconds: float = 30.0,
    on_claim: Callable[[Any], None] | None = None,
    handler: Any = None,  # noqa: ANN401 - an async generator function, for a test that needs records to take unequal times
) -> tuple[Trace, Any, FakePgRuntime]:
    import os

    from app.services.messaging.config import messaging_env  # noqa: F401
    from app.services.messaging.postgres import consumer as pg_consumer
    from app.services.messaging.postgres.queue import PgQueue

    topic_list = topics or ["record-events", "aux-events"]
    runtime = FakePgRuntime()
    for i, m in enumerate(messages):
        topic = topic_list[i % len(topic_list)]
        runtime.rows.setdefault(topic, []).append({
            "topic": topic, "message_id": i + 1, "attempts": 1, "msg_key": m.record_id,
            # a delivery no consumer has opened yet: the claim returns its admission columns empty, as the real statement does
            "admission_ms": None, "admission_mac": None,
            "value": json.dumps({"eventType": "newRecord", "payload": m.envelope()["payload"], "requestId": f"req-{i}"}),
        })
    saved_ordered = os.environ.get("PG_QUEUE_ORDERED_TOPICS")
    os.environ["PG_QUEUE_ORDERED_TOPICS"] = ordered_topics
    config = RedisStreamsConfig(
        topics=list(topic_list), group_id=GROUP, client_id="world", batch_size=batch_size, block_ms=5
    )
    queue = PgQueue(runtime)  # type: ignore[arg-type]
    consumer = pg_consumer.PgMessagingConsumer(LOGGER, config, indexing=True, queue=queue, **(consumer_kwargs or {}))
    handler = handler or Handler(turns=1)
    consumer.message_handler = handler
    if on_claim is not None:
        on_claim(consumer)
    consumer.running = True
    consumer._wake = asyncio.Event()
    consumer._ordered = any(
        t in {s.strip() for s in ordered_topics.split(",")} or t.split(".")[0] in ordered_topics.split(",")
        for t in config.topics
    )
    consumer._concurrency = lambda: concurrency  # type: ignore[method-assign]
    task = asyncio.create_task(consumer._consume_loop())
    deadline = real_time.monotonic() + max_seconds
    try:
        while len(handler.seen) < len(messages) and real_time.monotonic() < deadline:  # noqa: ASYNC110 - polls the consumer's own loop
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)
    finally:
        consumer.running = False
        task.cancel()
        deliveries = list(consumer._tasks)
        for delivery in deliveries:
            delivery.cancel()
        await asyncio.gather(task, *deliveries, return_exceptions=True)
        if saved_ordered is None:
            os.environ.pop("PG_QUEUE_ORDERED_TOPICS", None)
        else:
            os.environ["PG_QUEUE_ORDERED_TOPICS"] = saved_ordered
    trace = Trace(dispatch=list(handler.seen))
    trace.extra["statements"] = [s for s in runtime.statements if s[0] in {"claim", "lock"}]
    trace.extra["claim_sql"] = pg_claim_sql()
    return trace, consumer, runtime


def digest(trace: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest()


# -- the seam-off record ---------------------------------------------------------------------------------------------
GOLDEN_DIR = "tests/unit/services/messaging/goldens/record_consumer_seam_off"
GOLDEN_SEED = 7
GOLDEN_SIZES = {"kafka": 2_000, "redis": 5_000, "pg": 600}
GOLDEN_ENV = {"MAX_CONCURRENT_INDEXING": "6", "MESSAGE_BATCH_SIZE_INDEXING": "100", "MESSAGE_TIMEOUT_MS": "5"}


def summarise(trace: dict[str, Any]) -> dict[str, Any]:
    """A trace as digests of its long sequences, the head of each for reading a diff, and its short parts whole."""
    out: dict[str, Any] = {}
    for key, value in trace.items():
        if isinstance(value, list) and len(value) > 50:
            out[key] = {"count": len(value), "sha256": digest({"v": value}), "head": value[:20]}
        else:
            out[key] = value
    return out


async def collect(kind: str, **world: Any) -> dict[str, Any]:  # noqa: ANN401
    """The seam-off trace of one broker's consumer on the golden stream; ``world`` reaches the driver."""
    messages = generate_messages(GOLDEN_SEED, GOLDEN_SIZES[kind])
    if kind == "kafka":
        raw = (await run_kafka(messages, **world)).to_json()
    elif kind == "redis":
        raw = (await run_redis(messages, **world)).to_json()
    else:
        trace, _consumer, _runtime = await run_pg(messages, **world)
        raw = trace.to_json()
    return summarise(json.loads(json.dumps(raw)))


def constructor_state(kind: str) -> dict[str, Any]:
    """What a consumer built with no extras holds, for the attributes the base commit has."""
    from app.services.messaging.postgres.consumer import PgMessagingConsumer

    if kind == "pg":
        consumer = PgMessagingConsumer(LOGGER, RedisStreamsConfig(topics=["a", "b"], group_id="g"), indexing=True)
        return {"topics": list(consumer.config.topics), "ordered": consumer._ordered, "indexing": consumer.indexing}
    if kind == "kafka":
        from app.services.messaging.kafka.consumer.indexing_consumer import (
            IndexingKafkaConsumer,
        )

        consumer = IndexingKafkaConsumer(
            LOGGER,
            KafkaConsumerConfig(
                topics=["t"], client_id="c", group_id="g", auto_offset_reset="earliest", enable_auto_commit=False,
                bootstrap_servers=["k:1"],
            ),
            fair_scheduler_config=fair_config(),
        )
        topics = list(consumer.kafka_config.topics)
    else:
        from app.services.messaging.redis_streams.indexing_consumer import (
            IndexingRedisStreamsConsumer,
        )

        consumer = IndexingRedisStreamsConsumer(
            LOGGER, RedisStreamsConfig(host="h", port=1, client_id="c", group_id="g", topics=["t"]),
            fair_scheduler_config=fair_config(),
        )
        topics = list(consumer.config.topics)
    return {
        "topics": topics,
        "scheduler": type(consumer._scheduler).__name__,
        "planner": type(consumer._slot_share).__name__,
        "extractor": type(consumer.key_extractor).__name__,
        "weights": consumer.weight_provider is None or type(consumer.weight_provider).__name__,
        "observer": consumer.admission_backlog.observer,
        "key_fields": list(consumer.fair_scheduler_config.key_fields),
    }
