"""F17: a handler failure reaches the consumer as the exception, so the shared classifier decides terminal vs transient.

The entity, sync and AI-config handlers used to turn every exception (and every permanent condition) into ``False``,
which the Redis, Kafka and Postgres consumers can only read as "retry". Contract after the fix:
``True`` = done, ``False`` = deliberately "not yet, deliver again", raise = failure for ``MessageErrorClassifier``.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from app.exceptions.non_retryable import NonRetryableError
from app.services.distributed.interface import IRetryTracker
from app.services.messaging.config import (
    MessageHandler,
    RedisStreamsConfig,
    StreamMessage,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import consumer as kafka_consumer_module
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.kafka.handlers import entity as entity_module
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.utils.api_call import ApiCallError
from app.utils.logger import log_record_fields
from tests.support.fake_kafka import FakeKafkaBroker
from tests.unit.services.messaging.message_log_helpers import in_delivery

ORG = "org-f17"
ORG_UPDATED = {"eventType": "orgUpdated", "payload": {"orgId": ORG, "registeredName": "Acme"}}


class _Org(BaseModel):
    name: str


def _validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        _Org(name=None)  # type: ignore[arg-type]
    return caught.value


NOT_FOUND = ApiCallError("graph: org document not found", status_code=404)
TERMINAL_ERRORS = [
    pytest.param(lambda: NOT_FOUND, id="http-404"),
    pytest.param(lambda: ApiCallError("rejected", status_code=401), id="http-401"),
    pytest.param(_validation_error, id="pydantic-validation"),
]
TRANSIENT_ERRORS = [
    pytest.param(lambda: ConnectionError("graph unreachable"), id="connection"),
    pytest.param(lambda: ApiCallError("unavailable", status_code=503), id="http-503"),
    pytest.param(lambda: TimeoutError("graph timed out"), id="timeout"),
]


def _classify(exc: BaseException) -> str:
    return MessageErrorClassifier.classify_by_exception(exc)


@pytest.fixture(autouse=True)
def _open_source_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(KafkaUtils.__module__ + ".EntityEventService", entity_module.EntityEventService)


@pytest.fixture
def notify(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(entity_module, "notify_principals_changed", mock)
    return mock


def _container() -> MagicMock:
    container = MagicMock()
    container.logger.return_value = logging.getLogger("test.f17")
    container.messaging_producer.send_message = AsyncMock(return_value=True)
    del container.connectors_map
    return container


def _graph(upsert_error: BaseException | None = None) -> MagicMock:
    graph = MagicMock()
    graph.batch_upsert_nodes = AsyncMock(side_effect=upsert_error, return_value=True)
    graph.get_document = AsyncMock(return_value=None)
    graph.get_user_by_email = AsyncMock(return_value=None)
    return graph


async def _entity_handler(graph: MagicMock) -> MessageHandler:
    return await KafkaUtils.create_entity_message_handler(_container(), graph)


# --- entity wrapper + EntityEventService --------------------------------------------------------------------------


class TestEntityFailuresRaise:
    @pytest.mark.parametrize("make_error", TERMINAL_ERRORS)
    async def test_a_terminal_failure_reaches_the_consumer_as_itself(self, make_error) -> None:
        error = make_error()
        handler = await _entity_handler(_graph(error))
        with pytest.raises(type(error)) as caught:
            await handler(StreamMessage(**ORG_UPDATED))
        assert caught.value is error
        assert _classify(caught.value) == MessageErrorType.TERMINAL

    @pytest.mark.parametrize("make_error", TRANSIENT_ERRORS)
    async def test_a_transient_failure_still_reaches_the_consumer_as_transient(self, make_error) -> None:
        error = make_error()
        handler = await _entity_handler(_graph(error))
        with pytest.raises(type(error)) as caught:
            await handler(StreamMessage(**ORG_UPDATED))
        assert _classify(caught.value) == MessageErrorType.TRANSIENT

    async def test_an_unknown_event_type_is_terminal(self) -> None:
        handler = await _entity_handler(_graph())
        with pytest.raises(NonRetryableError):
            await handler(StreamMessage(eventType="orgRenamed", payload={"orgId": ORG}))

    @pytest.mark.parametrize("message", [
        pytest.param({"eventType": "", "payload": {"orgId": ORG}}, id="no-event-type"),
        pytest.param({"eventType": "orgUpdated", "payload": {}}, id="no-payload"),
    ])
    async def test_a_message_missing_its_event_type_or_payload_is_terminal(self, message) -> None:
        handler = await _entity_handler(_graph())
        with pytest.raises(NonRetryableError):
            await handler(StreamMessage(**message))

    async def test_success_still_returns_true(self) -> None:
        handler = await _entity_handler(_graph())
        assert await handler(StreamMessage(**ORG_UPDATED)) is True

    async def test_a_user_whose_org_is_not_there_yet_is_still_redelivered(self, notify) -> None:
        # Deliberate False: the org event may still be in retry (Redis has no head-of-line), so this must stay a retry.
        graph = _graph()
        handler = await _entity_handler(graph)
        message = StreamMessage(eventType="userAdded", payload={"orgId": ORG, "userId": "u-1", "email": "a@x.example"})
        assert await handler(message) is False

    async def test_a_schema_rejection_is_still_acked_as_handled(self) -> None:
        from app.schema.node_validator import SchemaValidationError

        handler = await _entity_handler(_graph(SchemaValidationError("orgs", "bad node")))
        assert await handler(StreamMessage(**ORG_UPDATED)) is True

    @pytest.mark.parametrize(("event_type", "payload"), [
        ("orgCreated", {"orgId": ORG, "accountType": "enterprise", "registeredName": "Acme", "userId": "u-1"}),
        ("orgUpdated", {"orgId": ORG, "registeredName": "Acme"}),
        ("orgDeleted", {"orgId": ORG}),
        ("userAdded", {"orgId": ORG, "userId": "u-1", "email": "a@x.example"}),
        ("userUpdated", {"orgId": ORG, "userId": "u-1", "email": "a@x.example"}),
        ("userDeleted", {"orgId": ORG, "userId": "u-1", "email": "a@x.example"}),
        ("appEnabled", {"orgId": ORG, "apps": ["DRIVE"], "connectorId": "c1"}),
        ("appDisabled", {"orgId": ORG, "apps": ["DRIVE"], "connectorId": "c1"}),
    ])
    async def test_every_entity_handler_lets_a_terminal_failure_through(self, notify, event_type, payload) -> None:
        graph = MagicMock()
        for name in ("batch_upsert_nodes", "get_document", "get_user_by_email", "get_user_by_user_id",
                     "get_entity_id_by_email"):
            setattr(graph, name, AsyncMock(side_effect=NOT_FOUND))
        handler = await _entity_handler(graph)
        with pytest.raises(ApiCallError) as caught:
            await handler(StreamMessage(eventType=event_type, payload=payload))
        assert caught.value is NOT_FOUND
        assert _classify(caught.value) == MessageErrorType.TERMINAL
        if event_type in entity_module.PRINCIPAL_EVENTS:
            notify.assert_awaited_with(ORG)

    async def test_principals_are_still_notified_when_a_user_event_raises(self, notify) -> None:
        graph = _graph()
        graph.get_user_by_email = AsyncMock(side_effect=ConnectionError("graph unreachable"))
        handler = await _entity_handler(graph)
        message = StreamMessage(eventType="userAdded", payload={"orgId": ORG, "userId": "u-1", "email": "a@x.example"})
        with pytest.raises(ConnectionError):
            await handler(message)
        notify.assert_awaited_once_with(ORG)


# --- sync wrapper + EventService -----------------------------------------------------------------------------------


async def _sync_handler() -> MessageHandler:
    return await KafkaUtils.create_sync_message_handler(_container(), _graph())


class TestSyncFailuresRaise:
    @pytest.mark.parametrize("message", [
        pytest.param({"eventType": "jira.frobnicate", "payload": {"connectorId": "c1"}}, id="unknown-action"),
        pytest.param({"eventType": "jira.start.now", "payload": {"connectorId": "c1"}}, id="bad-format"),
        pytest.param({"eventType": "", "payload": {"connectorId": "c1"}}, id="no-event-type"),
        # A producer that built "<name>.resync" from an empty name: a bug to see, not a routine event to skip.
        pytest.param({"eventType": ".resync", "payload": {"connectorId": "c1"}}, id="empty-connector-before-the-dot"),
    ])
    async def test_a_message_no_handler_can_ever_take_is_terminal(self, message) -> None:
        handler = await _sync_handler()
        with pytest.raises(NonRetryableError):
            await handler(StreamMessage(**message))

    @pytest.mark.parametrize("message", [
        # What Node publishes on sync-events (cm_controller.ts :2554, :1657/:1683/:1974/:2000, :1669/:1986); no Python consumer.
        pytest.param({"eventType": "connectorPublicUrlChanged", "payload": {"url": "https://pub.example", "orgId": ORG}},
                     id="connectorPublicUrlChanged"),
        pytest.param({"eventType": "gmailUpdatesEnabledEvent", "payload": {"orgId": ORG, "topicName": "projects/p/topics/t"}},
                     id="gmailUpdatesEnabledEvent"),
        pytest.param({"eventType": "gmailUpdatesDisabledEvent", "payload": {"orgId": ORG}}, id="gmailUpdatesDisabledEvent"),
    ])
    async def test_a_node_event_with_no_connector_is_acked_as_skipped_at_debug(self, message, caplog) -> None:
        from app.connectors.services import event_service

        with patch.object(event_service.EventService, "process_event", AsyncMock()) as process, \
                caplog.at_level(logging.DEBUG, logger="test.f17"):
            handler = await _sync_handler()
            assert await handler(StreamMessage(**message)) is True
        process.assert_not_awaited()
        assert [r.levelno for r in caplog.records if r.name == "test.f17"] == [logging.DEBUG]

    async def test_an_exception_escaping_a_sync_handler_reaches_the_consumer(self) -> None:
        from app.connectors.services import event_service

        with patch.object(event_service.EventService, "_handle_start_sync", AsyncMock(side_effect=NOT_FOUND)):
            handler = await _sync_handler()
            with pytest.raises(ApiCallError):
                await handler(StreamMessage(eventType="jira.start", payload={"connectorId": "c1"}))

    async def test_a_sync_handler_asking_for_redelivery_still_gets_it(self) -> None:
        from app.connectors.services import event_service

        with patch.object(event_service.EventService, "_handle_start_sync", AsyncMock(return_value=False)):
            handler = await _sync_handler()
            assert await handler(StreamMessage(eventType="jira.start", payload={"connectorId": "c1"})) is False


# --- AI-config wrapper + AiConfigEventService ----------------------------------------------------------------------


async def _aiconfig_handler(llm_error: BaseException | None = None) -> MessageHandler:
    retrieval = SimpleNamespace(get_llm_instance=AsyncMock(side_effect=llm_error),
                                get_embedding_model_instance=AsyncMock(side_effect=llm_error))
    container = _container()
    container.retrieval_service = AsyncMock(return_value=retrieval)
    return await KafkaUtils.create_aiconfig_message_handler(container)


class TestAiConfigFailuresRaise:
    @pytest.mark.parametrize("event_type", ["llmConfigured", "embeddingModelConfigured"])
    async def test_a_rejected_provider_key_is_terminal(self, event_type) -> None:
        handler = await _aiconfig_handler(ApiCallError("rejected", status_code=401))
        with pytest.raises(ApiCallError) as caught:
            await handler(StreamMessage(eventType=event_type, payload={}))
        assert _classify(caught.value) == MessageErrorType.TERMINAL

    async def test_an_unreachable_provider_is_transient(self) -> None:
        handler = await _aiconfig_handler(ConnectionError("provider unreachable"))
        with pytest.raises(ConnectionError):
            await handler(StreamMessage(eventType="llmConfigured", payload={}))

    async def test_a_message_without_an_event_type_is_terminal(self) -> None:
        handler = await _aiconfig_handler()
        with pytest.raises(NonRetryableError):
            await handler(StreamMessage(eventType="", payload={}))

    async def test_other_events_are_still_skipped_as_handled(self) -> None:
        handler = await _aiconfig_handler()
        assert await handler(StreamMessage(eventType="orgUpdated", payload={})) is True


# --- through the consumers -----------------------------------------------------------------------------------------


class TestRedisConsumerDecision:
    @staticmethod
    async def _decide(handler) -> tuple[bool, bool]:
        consumer = RedisStreamsConsumer(logging.getLogger("test.f17"), RedisStreamsConfig(
            host="localhost", port=6379, topics=["entity-events"], client_id="c", group_id="g"))
        consumer.message_handler = handler
        import json
        return await in_delivery(consumer._process_message_with_classification(
            "entity-events", "1-0", {"value": json.dumps(ORG_UPDATED)}))

    async def test_a_permanent_entity_failure_is_acked_as_terminal(self) -> None:
        assert await self._decide(await _entity_handler(_graph(NOT_FOUND))) == (False, True)

    async def test_a_transient_entity_failure_is_left_for_retry(self) -> None:
        assert await self._decide(await _entity_handler(_graph(ConnectionError("down")))) == (False, False)


class TestPostgresConsumerFailureRecord:
    """L1: on PostgreSQL a raised failure is logged once through log_failure, without its text, and only its class and type
    are kept in the delivery row (``last_error``) and handed to the disposition sink."""

    @staticmethod
    async def _settle(handler, caplog) -> MagicMock:
        import json

        from app.services.messaging.postgres.consumer import PgMessagingConsumer
        from app.services.messaging.postgres.queue import Delivery

        consumer = PgMessagingConsumer(logging.getLogger("test.f17"), RedisStreamsConfig(topics=["t"], group_id="g"))
        consumer.message_handler = handler
        consumer.queue = MagicMock(dead=AsyncMock(), ack=AsyncMock(), nack=AsyncMock())
        with caplog.at_level(logging.DEBUG, logger="test.f17"):
            await consumer._settle(Delivery(topic="t", message_id=1, attempts=1, value=json.dumps(ORG_UPDATED), key=None))
        return consumer.queue

    async def test_a_validation_error_is_dead_lettered_with_its_class_only(self, caplog) -> None:
        secret_value = "Ada Lovelace private value"

        async def handler(_m: StreamMessage) -> bool:
            _Org(name={"leak": secret_value})  # type: ignore[arg-type]

        queue = await self._settle(handler, caplog)
        (_group, _d, stored), _ = queue.dead.await_args
        assert stored == "internal: ValidationError"
        assert secret_value not in caplog.text
        assert all(secret_value not in str(c) for c in queue.dead.await_args_list)

    async def test_a_transient_failure_is_retried_with_its_class_only(self, caplog) -> None:
        async def handler(_m: StreamMessage) -> bool:
            raise ConnectionError("graph at 10.0.0.7:7687 refused, token=abc")

        queue = await self._settle(handler, caplog)
        (_group, _d, _delay, stored), _ = queue.nack.await_args
        assert stored == "unavailable: ConnectionError"
        assert "10.0.0.7" not in caplog.text
        assert all("10.0.0.7" not in str(log_record_fields(r)) for r in caplog.records)

    async def test_one_stack_for_an_internal_failure(self, caplog) -> None:
        leak = "payload said " + "Ada Lovelace"  # not on the raise line: the stack keeps source lines

        async def handler(_m: StreamMessage) -> bool:
            raise RuntimeError(leak)

        await self._settle(handler, caplog)
        assert sum(1 for r in caplog.records if r.exc_info or getattr(r, "exc_text", None)) == 1
        [line] = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert line.getMessage() == "message.handler_failed"
        assert "Ada Lovelace" not in caplog.text  # the stack keeps its frames, not the message


class _RetryTracker(IRetryTracker):
    def __init__(self) -> None:
        self.failures: dict[str, int] = {}

    async def initialize(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def increment_and_check(self, message_id: str, max_attempts: int) -> tuple[int, bool]:
        self.failures[message_id] = self.failures.get(message_id, 0) + 1
        return self.failures[message_id], self.failures[message_id] >= max_attempts

    async def get_count(self, message_id: str) -> int:
        return self.failures.get(message_id, 0)

    async def clear(self, message_id: str) -> None:
        self.failures.pop(message_id, None)

    async def clear_batch(self, message_ids: list[str]) -> int:
        return sum(self.failures.pop(m, None) is not None for m in message_ids)

    async def has_pending_retries(self, message_ids: list[str]) -> bool:
        return any(m in self.failures for m in message_ids)


class TestKafkaConsumerAttempts:
    """Real KafkaMessagingConsumer over FakeKafkaBroker, real entity wrapper, a retry tracker, MAX_DELIVERY_ATTEMPTS=3."""

    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")
        monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")

    @staticmethod
    async def _attempts(graph: MagicMock) -> int:
        broker = FakeKafkaBroker()
        with patch.object(kafka_consumer_module, "AIOKafkaConsumer", broker.consumer_factory()):
            broker.produce("entity-events", ORG_UPDATED)
            consumer = KafkaMessagingConsumer(
                logging.getLogger("test.f17"),
                KafkaConsumerConfig(topics=["entity-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
                                    enable_auto_commit=False, bootstrap_servers=["kafka:9092"]),
                retry_manager=_RetryTracker(),
            )
            await consumer.start(await _entity_handler(graph))
            deadline = asyncio.get_running_loop().time() + 5
            while (broker.committed_offset("g", "entity-events") or 0) < 1:
                assert asyncio.get_running_loop().time() < deadline, "the message was never settled"
                await asyncio.sleep(0.01)
            await consumer.stop()
        return graph.batch_upsert_nodes.await_count

    async def test_a_permanent_failure_is_tried_once(self) -> None:
        assert await self._attempts(_graph(NOT_FOUND)) == 1

    async def test_a_transient_failure_is_tried_max_attempts_times(self) -> None:
        assert await self._attempts(_graph(ConnectionError("down"))) == 3

    async def test_a_transient_failure_that_clears_is_delivered(self) -> None:
        graph = _graph()
        graph.batch_upsert_nodes = AsyncMock(side_effect=[ConnectionError("down"), True])
        assert await self._attempts(graph) == 2
