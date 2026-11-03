"""Start-up and shutdown of the indexing consumers' worker loop when the worker
thread is slow to reach run_forever().

The worker creates its loop, signals readiness, then calls run_forever(). If
readiness is signalled before the loop runs, initialize() can see a loop that
is not running yet and fail; the stop that follows must still end the worker.
These tests hold the worker in that gap on purpose instead of hoping the
scheduler lands there. Kafka and Redis Streams share the pattern, so each test
runs against both.
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import indexing_consumer as kafka_module
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams import indexing_consumer as redis_module
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)

# A healthy start or stop takes milliseconds; this only bounds a hang so the
# test fails with a message instead of tripping pytest's global timeout.
_HANG_TIMEOUT = 10.0

_LOGGER = logging.getLogger("test_startup_race")

Consumer = IndexingKafkaConsumer | IndexingRedisStreamsConsumer


@dataclass(frozen=True)
class _Case:
    module: ModuleType
    make: Callable[[], Consumer]
    start_worker: Callable[[Consumer], None]
    stop_worker: Callable[[Consumer], None]
    # Stands in for the broker so initialize() can run to completion.
    broker: Callable[[Consumer], AbstractContextManager[None]]


def _make_kafka() -> IndexingKafkaConsumer:
    config = KafkaConsumerConfig(
        topics=["idx-topic"],
        client_id="idx-consumer",
        group_id="idx-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=False,
        sasl=None,
    )
    return IndexingKafkaConsumer(_LOGGER, config)


@contextmanager
def _kafka_broker(consumer: Consumer) -> Iterator[None]:
    with patch.object(kafka_module, "AIOKafkaConsumer") as kafka_cls:
        kafka_cls.return_value.start = AsyncMock()
        kafka_cls.return_value.stop = AsyncMock()
        yield


def _make_redis() -> IndexingRedisStreamsConsumer:
    config = RedisStreamsConfig(
        host="localhost",
        port=6379,
        password="secret",
        db=0,
        max_len=10000,
        block_ms=100,
        batch_size=5,
        client_id="idx-consumer",
        group_id="idx-group",
        topics=["idx-topic"],
    )
    return IndexingRedisStreamsConsumer(_LOGGER, config)


@contextmanager
def _redis_broker(consumer: Consumer) -> Iterator[None]:
    with (
        patch.object(consumer._provider, "create_client", return_value=AsyncMock()),
        patch.object(
            consumer, "_IndexingRedisStreamsConsumer__adopt_existing_lane_streams", AsyncMock()
        ),
        patch.object(consumer, "_cleanup_empty_consumers", AsyncMock()),
    ):
        yield


_CASES = {
    "kafka": _Case(
        module=kafka_module,
        make=_make_kafka,
        start_worker=lambda c: c._IndexingKafkaConsumer__start_worker_thread(),
        stop_worker=lambda c: c._IndexingKafkaConsumer__stop_worker_thread(),
        broker=_kafka_broker,
    ),
    "redis_streams": _Case(
        module=redis_module,
        make=_make_redis,
        start_worker=lambda c: c._start_worker_thread(),
        stop_worker=lambda c: c._stop_worker_thread(),
        broker=_redis_broker,
    ),
}


@pytest.fixture(params=list(_CASES), ids=list(_CASES))
def case(request: pytest.FixtureRequest) -> _Case:
    return _CASES[request.param]


@pytest.fixture
def consumer(case: _Case) -> Consumer:
    return case.make()


class _WorkerGate:
    """Parks the worker thread until the consumer begins shutting its executor
    down, by which point stop has already decided whether to ask the loop to
    stop. It parks just before run_forever(), or with before_publish just
    before the worker stores its new loop on the consumer."""

    def __init__(
        self, should_park: Callable[[], bool] = lambda: False, *, before_publish: bool = False
    ) -> None:
        self.should_park = should_park
        self.before_publish = before_publish
        self.parked = threading.Event()
        self.release = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None

    @contextmanager
    def installed(self, module: ModuleType) -> Iterator["_WorkerGate"]:
        gate = self
        real_new_event_loop = asyncio.new_event_loop

        class GatedLoop(asyncio.SelectorEventLoop):
            def run_forever(self) -> None:
                if gate.should_park():
                    gate.parked.set()
                    gate.release.wait(_HANG_TIMEOUT)
                super().run_forever()

        def new_event_loop() -> asyncio.AbstractEventLoop:
            if threading.current_thread().name.startswith("indexing-worker"):
                gate.loop = GatedLoop()
                if gate.before_publish:
                    gate.parked.set()
                    gate.release.wait(_HANG_TIMEOUT)
                return gate.loop
            return real_new_event_loop()

        class ReleasingExecutor(ThreadPoolExecutor):
            def shutdown(self, wait: bool = True, **kwargs: object) -> None:
                gate.release.set()
                super().shutdown(wait=wait, **kwargs)

        with (
            patch.object(asyncio, "new_event_loop", new_event_loop),
            patch.object(module, "ThreadPoolExecutor", ReleasingExecutor),
        ):
            yield self

    def force_stop(self) -> None:
        """Unwind a hung worker so a failing test does not leak its thread."""
        self.release.set()
        loop = self.loop
        if loop is not None and not loop.is_closed():
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(loop.stop)


def _run_in_thread(target: Callable[[], None], gate: _WorkerGate) -> bool:
    """Run target in a thread; return True if it hung (it is unwound after)."""
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(_HANG_TIMEOUT)
    hung = thread.is_alive()
    if hung:
        gate.force_stop()
        thread.join(_HANG_TIMEOUT)
    return hung


def test_ready_is_signalled_only_once_the_worker_loop_is_running(
    case: _Case, consumer: Consumer
) -> None:
    running_when_set: list[bool] = []

    class RecordingEvent(threading.Event):
        def set(self) -> None:
            loop = consumer.worker_loop
            running_when_set.append(loop is not None and loop.is_running())
            super().set()

    consumer.worker_loop_ready = RecordingEvent()
    case.start_worker(consumer)
    try:
        assert consumer.worker_loop_ready.wait(timeout=_HANG_TIMEOUT)
        assert running_when_set == [True]
    finally:
        case.stop_worker(consumer)


def test_initialize_succeeds_when_worker_is_slow_to_start_its_loop(
    case: _Case, consumer: Consumer
) -> None:
    # Parks only if readiness was signalled before run_forever(), which is
    # exactly the window initialize() used to fall into.
    gate = _WorkerGate(should_park=consumer.worker_loop_ready.is_set)
    outcome: dict[str, BaseException] = {}

    def run_initialize() -> None:
        try:
            asyncio.run(consumer.initialize())
        except BaseException as exc:
            outcome["error"] = exc

    with gate.installed(case.module), case.broker(consumer):
        hung = _run_in_thread(run_initialize, gate)
        try:
            assert not hung, "initialize() hung shutting down a worker loop nobody asked to stop"
            assert "error" not in outcome, f"initialize() failed: {outcome.get('error')!r}"
            assert consumer.worker_loop is not None
            assert consumer.worker_loop.is_running()
        finally:
            asyncio.run(consumer.stop())


def test_stop_ends_a_worker_whose_loop_is_not_running_yet(
    case: _Case, consumer: Consumer
) -> None:
    gate = _WorkerGate(should_park=lambda: True)
    with gate.installed(case.module):
        case.start_worker(consumer)
        assert gate.parked.wait(_HANG_TIMEOUT)
        loop = gate.loop
        assert loop is not None and not loop.is_running()

        hung = _run_in_thread(lambda: case.stop_worker(consumer), gate)

    assert not hung, "stop hung waiting on a worker loop it never asked to stop"
    assert loop.is_closed()
    assert consumer.worker_executor is None
    assert consumer.worker_loop is None


def test_stop_before_the_worker_publishes_its_loop_still_ends_it(
    case: _Case, consumer: Consumer
) -> None:
    gate = _WorkerGate(before_publish=True)
    with gate.installed(case.module):
        case.start_worker(consumer)
        assert gate.parked.wait(_HANG_TIMEOUT)
        assert consumer.worker_loop is None

        hung = _run_in_thread(lambda: case.stop_worker(consumer), gate)

    assert not hung, "stop hung: its request was lost before the worker published its loop"
    assert gate.loop is not None and gate.loop.is_closed()
    assert consumer.worker_executor is None
    assert consumer.worker_loop is None
