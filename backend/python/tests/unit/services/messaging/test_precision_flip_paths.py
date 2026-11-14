"""A flip raised under the real ``index_documents``, batch and embedding-retry wrappers is handed back on every path.

The flip comes out of the real vector store (index pinned int8, server answering fp32 on the batch); the real record
handler converts it; each consumer's own outcome handling then runs. One test per path: Kafka, Redis Streams, the
Postgres queue and the stage worker. None counts an attempt, each re-queues with a delay, and no terminal status is
written.
"""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import EventTypes
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.precision_flip import PrecisionFlip, precision_flip_in
from app.services.vector_db.collection_pin import FP32, INT8, CollectionPinStore
from app.services.vector_db.precision_guard import PrecisionGuard
from app.utils.embedding_server_client import EmbeddingServerEmbeddings
from app.utils.precision_witness import PrecisionWitness, set_precision_witness
from tests.unit.modules.transformers.test_vectorstore_point_ids import _make_vectorstore
from tests.unit.services.messaging.test_record_handler_parse_now import (
    Harness,
    _payload,
    _record,
)
from tests.unit.services.vector_db.test_collection_pin import _FakeDb

log = logging.getLogger("flip-paths")


class _Bundled(EmbeddingServerEmbeddings):
    def __init__(self) -> None:
        self.model = "m"
        self.aembed_documents_witnessed = AsyncMock(return_value=([[0.1, 0.2]], FP32, False))

    def bulk_gate(self):  # noqa: ANN202
        return None


async def _flip_through_the_real_wrappers() -> PrecisionFlip:
    """The handler's view of a flip: what the real ``index_documents`` raises when the index holds the other precision."""
    set_precision_witness(PrecisionWitness())
    db = _FakeDb()
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    store = _make_vectorstore()
    store.dense_embeddings = _Bundled()
    store.embedding_provider = "embeddingServer"
    store.vector_db_service = db
    store.graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})
    store._is_local_cpu_embedding = lambda: False
    store._guard = PrecisionGuard(CollectionPinStore(config, log), db, log)
    await store._guard._pins.claim("records", INT8, None)  # the index holds int8; the server answers fp32
    store.get_embedding_model_instance = AsyncMock(return_value=False)
    store._ensure_collection = AsyncMock(return_value="records")
    store._bind_membership = AsyncMock(return_value=None)
    store._compute_sparse_embeddings = AsyncMock(side_effect=lambda texts: [None] * len(texts))
    block = Block(id="b1", index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="alpha beta gamma")
    with pytest.raises(Exception) as caught:
        await store.index_documents(BlocksContainer(blocks=[block], block_groups=[]), "o1", "rec-1", "vr-1")
    flip = precision_flip_in(caught.value)
    assert flip is not None, f"no flip in the chain of {caught.value!r}"
    assert db.points == []  # nothing was stored
    return flip


async def _converted_by_the_handler(under_the_wrappers: BaseException) -> Exception:
    harness = Harness(_record(indexingStatus="IN_PROGRESS"))
    harness.fail_with = under_the_wrappers  # type: ignore[assignment]
    with pytest.raises(Exception) as caught:
        await harness.run(_payload(parseNow=False, is_final_failure=True), EventTypes.NEW_RECORD.value)
    assert all(w.get("indexingStatus") != "FAILED" for w in harness.writes())  # no terminal status on the last delivery
    return caught.value


@pytest.fixture(autouse=True)
def _reset_witness():  # noqa: ANN202
    yield
    set_precision_witness(None)


@pytest.mark.asyncio
async def test_the_flip_survives_the_real_index_documents_and_the_handler() -> None:
    flip = await _flip_through_the_real_wrappers()
    converted = await _converted_by_the_handler(flip)
    assert isinstance(converted, PrecisionFlip) and isinstance(converted, RequeueWithoutAttempt)
    assert converted.counts_toward_backstop is False and converted.pauses_consumer is False


def _kafka_message(value: dict) -> MagicMock:
    msg = MagicMock()
    msg.topic, msg.partition, msg.offset = "record-events", 0, 7
    msg.value = json.dumps(value).encode()
    return msg


@pytest.mark.asyncio
async def test_kafka_a_flip_is_requeued_with_a_delay_and_no_attempt() -> None:
    from tests.unit.services.messaging.governor_test_helpers import make_test_governor

    raised = await _converted_by_the_handler(await _flip_through_the_real_wrappers())
    consumer = IndexingKafkaConsumer(log, KafkaConsumerConfig(
        topics=["record-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
        enable_auto_commit=False, bootstrap_servers=["b:9092"], ssl=False, sasl=None,
    ), retry_manager=None, producer=None, governor=make_test_governor(logger_name="flip_paths_kafka"))
    consumer.running = True
    consumer.main_loop = asyncio.get_running_loop()
    consumer.retry_manager = AsyncMock()
    consumer.retry_manager.record_delivery = AsyncMock(return_value=1)
    consumer._requeue_message = AsyncMock()
    consumer._commit_offset = AsyncMock()

    async def handler(_msg):  # noqa: ANN202
        raise raised
        yield  # an async generator, like every handler

    consumer.message_handler = handler
    result = await consumer._IndexingKafkaConsumer__process_message_wrapper(
        _kafka_message({"eventType": "newRecord", "payload": {"recordId": "rec-1", "orgId": "o1"}}),
    )
    assert result is False
    consumer.retry_manager.increment_and_check.assert_not_awaited()  # no attempt
    assert consumer._requeue_message.await_args.kwargs["retry_count"] == 0
    assert consumer._requeue_message.await_args.kwargs["delay_s"] >= 60.0
    consumer._commit_offset.assert_awaited_once()  # re-queued, so the offset moves on


@pytest.mark.asyncio
async def test_redis_streams_a_flip_is_requeued_with_a_delay_and_no_attempt() -> None:
    from app.services.messaging.config import RedisStreamsConfig
    from app.services.messaging.redis_streams.indexing_consumer import (
        IndexingRedisStreamsConsumer,
    )
    from tests.unit.services.messaging.governor_test_helpers import make_test_governor

    raised = await _converted_by_the_handler(await _flip_through_the_real_wrappers())
    consumer = IndexingRedisStreamsConsumer(
        log,
        RedisStreamsConfig(host="localhost", port=6379, password="x", db=0, max_len=10, block_ms=100, batch_size=5,
                           client_id="c", group_id="g", topics=["a"]),
        retry_manager=None, producer=None, governor=make_test_governor(logger_name="flip_paths_redis"),
    )
    consumer._pending_message_is_owned = AsyncMock(return_value=True)
    consumer.running = True
    consumer.main_loop = asyncio.get_running_loop()
    consumer.retry_manager = AsyncMock()
    consumer._requeue_message = AsyncMock()
    consumer._ack_message = AsyncMock()

    async def handler(_msg):  # noqa: ANN202
        raise raised
        yield  # an async generator, like every handler

    consumer.message_handler = handler
    fields = {"value": json.dumps({"eventType": "newRecord", "payload": {"recordId": "rec-1", "orgId": "o1"}})}
    result = await consumer._process_message_wrapper("stream-a", "1-0", fields)
    assert result is False
    consumer.retry_manager.record_delivery.assert_not_awaited()
    consumer.retry_manager.increment_and_check.assert_not_awaited()
    assert consumer._requeue_message.await_args.kwargs["retry_count"] == 0
    assert consumer._requeue_message.await_args.kwargs["delay_s"] >= 60.0
    consumer._ack_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_postgres_a_flip_is_nacked_with_a_refund_and_never_dead_lettered() -> None:
    raised = await _converted_by_the_handler(await _flip_through_the_real_wrappers())
    consumer = object.__new__(PgMessagingConsumer)
    consumer.logger = log
    consumer.indexing = True
    consumer.precision_filter = None
    consumer.config = SimpleNamespace(group_id="g")
    consumer.stage_admission = None
    consumer._paused_until = 0.0
    consumer._handbacks = {}
    consumer._handback_log = {}
    consumer.queue = MagicMock(nack=AsyncMock(), ack=AsyncMock())
    consumer._process = AsyncMock(side_effect=raised)
    consumer._abandon = AsyncMock()
    await consumer._settle(SimpleNamespace(topic="t", message_id=5, attempts=1))
    consumer._abandon.assert_not_awaited()
    args, kwargs = consumer.queue.nack.await_args
    assert args[2] >= 60.0 and kwargs["refund"] is True
    consumer.queue.ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_stage_worker_pauses_not_fails() -> None:
    from app.modules.pipeline.worker import StageWaitsForPrecision
    from tests.unit.modules.pipeline.test_worker import _Harness, _job

    # The stage sees the chain exactly as the real index_documents leaves it, before any conversion.
    chain = None
    try:
        flip = await _flip_through_the_real_wrappers()
        chain = flip
    finally:
        assert chain is not None
    harness, job = _Harness(), _job()
    harness.seed(job)
    wrapped = RuntimeError("Failed to store documents in vector store")
    wrapped.__cause__ = chain
    harness.handler._handle = AsyncMock(side_effect=wrapped)
    with pytest.raises(StageWaitsForPrecision) as caught:
        await harness.deliver(job)
    assert caught.value.pauses_consumer is False and caught.value.counts_toward_backstop is False
