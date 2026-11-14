"""F27: a failure inside EventService reaches the consumer as the exception, so the shared classifier decides
terminal vs transient.

``_build_init_connector`` and ``_handle_reindex`` ended in ``except Exception: return False``, and the lazy-init chain
(``ConnectorFactory.initialize_connector`` -> ``_auto_initialize_connector``) turned the same failures into ``None``
and then ``False``. The consumers can only read ``False`` as "retry", so a source 401 was retried until the attempts
ran out. Contract (F17): ``True`` = done, ``False`` = deliberately "deliver again", raise = failure for the classifier.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.sync.sync_coordinator import Admission, SyncLease
from app.connectors.services import event_service as es_module
from app.connectors.services.event_service import EventService
from app.exceptions.non_retryable import NonRetryableError
from app.services.distributed.interface import IRetryTracker
from app.services.messaging.config import RedisStreamsConfig, StreamMessage
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import consumer as kafka_consumer_module
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.utils.api_call import ApiCallError
from tests.support.fake_kafka import FakeKafkaBroker
from tests.unit.services.messaging.message_log_helpers import in_delivery

ORG = "65f0a1b2c3d4e5f6a7b8aaaa"
CID = "65f0a1b2c3d4e5f6a7b8cccc"
APP_DOC = {"_key": CID, "orgId": ORG, "isActive": True, "type": "Jira", "scope": "team", "createdBy": "u1", "name": "J"}
INIT = {"eventType": "jira.init", "payload": {"orgId": ORG, "connectorId": CID}}
REINDEX = {"eventType": "jira.reindex", "payload": {"orgId": ORG, "connectorId": CID}}
RESYNC = {"eventType": "jira.resync", "payload": {"orgId": ORG, "connectorId": CID}}


def _wrapped(cause: Exception) -> ConnectorInitError:
    """What jira_cloud.init() raises: ``raise ConnectorInitError(str(e)) from e``."""
    try:
        raise ConnectorInitError(str(cause)) from cause
    except ConnectorInitError as e:
        return e


class _Model(BaseModel):
    name: str


def _validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        _Model(name=None)  # type: ignore[arg-type]
    return caught.value


TERMINAL = [
    pytest.param(lambda: _wrapped(ApiCallError("token revoked", status_code=401)), id="init-wraps-401"),
    pytest.param(lambda: ApiCallError("site gone", status_code=404), id="http-404"),
    pytest.param(_validation_error, id="pydantic-validation"),
]
TRANSIENT = [
    pytest.param(lambda: ConnectionError("source unreachable"), id="connection"),
    pytest.param(lambda: _wrapped(ApiCallError("unavailable", status_code=503)), id="init-wraps-503"),
    pytest.param(lambda: TimeoutError("source timed out"), id="timeout"),
]


def _classify(exc: BaseException) -> str:
    return MessageErrorClassifier.classify_by_exception(exc)


class _Connector:
    def __init__(self, error: Exception | None = None, *, ok: bool = True, cleanup_error: Exception | None = None) -> None:
        self.error, self.ok = error, ok
        self.cleanup = AsyncMock(side_effect=cleanup_error)
        self.on_background_init_failed = AsyncMock()
        self.data_entities_processor = MagicMock()
        self.app = MagicMock()
        self.app.get_app_name.return_value.name = "JIRA"

    async def init(self) -> bool:
        if self.error is not None:
            raise self.error
        return self.ok


class _Built(list):
    """Every connector the factory handed out; ``fail_next`` picks the next one's init outcome."""

    def __init__(self) -> None:
        super().__init__()
        self.error: Exception | None = None
        self.ok = True
        self.cleanup_error: Exception | None = None

    async def create_connector(self, **_: object) -> _Connector:
        self.append(_Connector(self.error, ok=self.ok, cleanup_error=self.cleanup_error))
        return self[-1]


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> _Built:
    made = _Built()
    monkeypatch.setattr(ConnectorFactory, "create_connector", staticmethod(made.create_connector))
    monkeypatch.setattr(es_module, "sync_executor_enabled", lambda: False)

    async def start_if_idle(_key: str, coro) -> object:
        coro.close()
        return MagicMock(name="reindex-task")
    monkeypatch.setattr(es_module.reindex_task_manager, "start_if_idle", start_if_idle)
    return made


def _fails_with(built: _Built, error: Exception | None = None, *, ok: bool = True) -> None:
    built.error, built.ok = error, ok


def _container() -> SimpleNamespace:
    return SimpleNamespace(logger=lambda: logging.getLogger("test.f27"), config_service=lambda: MagicMock(),
                           connector_notification_service=lambda: None, connectors_map=OrderedDict())


def _graph(doc: dict | None = APP_DOC) -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=dict(doc) if doc else None)
    graph.update_node = AsyncMock()
    graph.batch_upsert_nodes = AsyncMock()
    return graph


def _service(graph: MagicMock | None = None) -> EventService:
    return EventService(logging.getLogger("test.f27"), _container(), graph or _graph())


async def _handle(message: dict, graph: MagicMock | None = None) -> bool:
    handler = await KafkaUtils.create_sync_message_handler(_container(), graph or _graph())
    return await handler(StreamMessage(**message))


# --- init event: the _build_init_connector catch-all ---------------------------------------------------------------


class TestInitFailuresRaise:
    @pytest.mark.parametrize("make_error", TERMINAL)
    async def test_a_terminal_init_failure_reaches_the_consumer_as_itself(self, built, make_error) -> None:
        error = make_error()
        _fails_with(built, error)
        with pytest.raises(type(error)) as caught:
            await _handle(INIT)
        assert caught.value is error
        assert _classify(caught.value) == MessageErrorType.TERMINAL

    @pytest.mark.parametrize("make_error", TRANSIENT)
    async def test_a_transient_init_failure_still_reaches_the_consumer_as_transient(self, built, make_error) -> None:
        _fails_with(built, make_error())
        with pytest.raises(Exception) as caught:
            await _handle(INIT)
        assert _classify(caught.value) == MessageErrorType.TRANSIENT

    async def test_a_connector_whose_init_raised_is_still_disposed_and_not_cached(self, built) -> None:
        """#4774: the factory handed it an open processor; a redelivery would build another."""
        _fails_with(built, ConnectionError("down"))
        svc = _service()
        with pytest.raises(ConnectionError):
            await svc.process_event(INIT["eventType"], INIT["payload"])
        built[0].cleanup.assert_awaited_once()
        assert svc.app_container.connectors_map == {}

    async def test_a_failing_cleanup_does_not_replace_the_init_error(self, built) -> None:
        """message.end and the PG row report the raised error; a cleanup failure must not become it."""
        error = _wrapped(ApiCallError("token revoked", status_code=401))
        _fails_with(built, error)
        built.cleanup_error = RuntimeError("producer close failed")
        with pytest.raises(ConnectorInitError) as caught:
            await _handle(INIT)
        assert caught.value is error
        built[0].cleanup.assert_awaited_once()

    async def test_a_connector_already_cached_when_the_failure_hit_is_not_disposed(self, built, monkeypatch) -> None:
        """Closing a live cached connector breaks every task using it (the _is_cached guard)."""
        async def store_then_fail(self, connector_id, connector) -> None:
            self.app_container.connectors_map[connector_id] = connector
            raise RuntimeError("eviction failed")
        monkeypatch.setattr(EventService, "_store_connector", store_then_fail)
        svc = _service()
        with pytest.raises(RuntimeError):
            await svc.process_event(INIT["eventType"], INIT["payload"])
        built[0].cleanup.assert_not_awaited()

    async def test_init_returning_false_is_still_a_redelivery(self, built) -> None:
        _fails_with(built, ok=False)
        assert await _handle(INIT) is False
        built[0].cleanup.assert_awaited_once()

    async def test_a_successful_init_still_returns_true_and_caches(self, built) -> None:
        svc = _service()
        assert await svc.process_event(INIT["eventType"], INIT["payload"]) is True
        assert svc.app_container.connectors_map[CID] is built[0]


# --- reindex event: the _handle_reindex catch-all ------------------------------------------------------------------


class TestReindexFailuresRaise:
    async def test_a_reindex_without_org_is_terminal(self, built) -> None:
        with pytest.raises(NonRetryableError):
            await _handle({"eventType": "jira.reindex", "payload": {"connectorId": CID}})

    async def test_a_failure_scheduling_the_reindex_reaches_the_consumer(self, built, monkeypatch) -> None:
        """A guard that the catch-all is gone: the 403 is synthetic (start_if_idle is in-process and makes no call)."""
        async def start_if_idle(_key, coro) -> object:
            coro.close()
            raise ApiCallError("forbidden", status_code=403)
        monkeypatch.setattr(es_module.reindex_task_manager, "start_if_idle", start_if_idle)
        with pytest.raises(ApiCallError) as caught:
            await _handle(REINDEX)
        assert _classify(caught.value) == MessageErrorType.TERMINAL

    @pytest.mark.parametrize("stages", [[], ["not-a-stage"], ["classify", "entities"]],
                             ids=["empty", "unknown", "mixed-selectors"])
    async def test_an_invalid_stage_selection_is_terminal(self, built, stages) -> None:
        with pytest.raises(NonRetryableError) as caught:
            await _handle({"eventType": "jira.reindex", "payload": {"orgId": ORG, "connectorId": CID, "stages": stages}})
        assert _classify(caught.value) == MessageErrorType.TERMINAL
        assert not built  # rejected before any connector is built

    async def test_a_reindex_without_connector_id_is_still_a_redelivery(self, built) -> None:
        assert await _handle({"eventType": "jira.reindex", "payload": {"orgId": ORG}}) is False

    async def test_a_reindex_still_returns_true_once_scheduled(self, built) -> None:
        assert await _handle(REINDEX) is True


# --- lazy init (reindex, start, resync): ConnectorFactory.initialize_connector -> _auto_initialize_connector --------


class TestLazyInitFailuresRaise:
    @pytest.mark.parametrize("make_error", TERMINAL)
    async def test_a_terminal_lazy_init_failure_on_reindex_reaches_the_consumer(self, built, make_error) -> None:
        error = make_error()
        _fails_with(built, error)
        with pytest.raises(type(error)) as caught:
            await _handle(REINDEX)
        assert caught.value is error
        assert _classify(caught.value) == MessageErrorType.TERMINAL

    async def test_the_failed_connector_is_still_reported_and_disposed(self, built) -> None:
        error = _wrapped(ApiCallError("token revoked", status_code=401))
        _fails_with(built, error)
        with pytest.raises(ConnectorInitError):
            await _handle(REINDEX)
        built[0].on_background_init_failed.assert_awaited_once_with(error)
        built[0].cleanup.assert_awaited_once()

    async def test_a_failing_cleanup_does_not_replace_the_lazy_init_error(self, built) -> None:
        error = _wrapped(ApiCallError("token revoked", status_code=401))
        _fails_with(built, error)
        built.cleanup_error = RuntimeError("producer close failed")
        with pytest.raises(ConnectorInitError) as caught:
            await _handle(REINDEX)
        assert caught.value is error
        built[0].cleanup.assert_awaited_once()

    async def test_a_resync_whose_lazy_init_fails_raises_and_releases_its_lease(self, built) -> None:
        coordinator = MagicMock()
        lease = SyncLease(CID, "tok", 1)
        coordinator.begin = AsyncMock(return_value=(Admission.GRANTED, lease))
        coordinator.end = AsyncMock()
        drain = MagicMock()
        _fails_with(built, _wrapped(ApiCallError("token revoked", status_code=401)))
        with patch.object(es_module, "get_coordinator", return_value=coordinator), \
             patch.object(EventService, "_schedule_drain", drain):
            with pytest.raises(ConnectorInitError) as caught:
                await _handle(RESYNC)
        assert _classify(caught.value) == MessageErrorType.TERMINAL
        coordinator.end.assert_awaited_once_with(lease)
        drain.assert_called_once()  # a slot came free without a sync ending

    async def test_a_connector_missing_from_the_graph_is_still_a_redelivery(self, built) -> None:
        assert await _handle(REINDEX, _graph(doc=None)) is False

    async def test_init_returning_false_is_still_none_from_the_factory(self, built) -> None:
        _fails_with(built, ok=False)
        assert await _handle(REINDEX) is False
        built[0].on_background_init_failed.assert_awaited_once_with(None)

    async def test_create_and_start_sync_raises_to_its_catching_callers(self, built) -> None:
        """Its callers (connectors_main boot, KB registration) catch and log; this pins only that the failure reaches them."""
        _fails_with(built, ConnectionError("down"))
        with pytest.raises(ConnectionError):
            await ConnectorFactory.create_and_start_sync(
                name="jira", logger=logging.getLogger("test.f27"), data_store_provider=MagicMock(),
                config_service=MagicMock(get_config=AsyncMock(return_value={})), connector_id=CID, scope="team",
                created_by="u1")


    async def test_the_kb_http_lookup_raises_the_init_failure_instead_of_none(self, built) -> None:
        """kb_router's processor_for_kb no longer turns it into LookupError (push: 404 KB_NOT_FOUND -> 500, PLAN M1)."""
        error = _wrapped(ApiCallError("token revoked", status_code=401))
        _fails_with(built, error)
        with pytest.raises(ConnectorInitError) as caught:
            await _service().get_or_init_connector("jira", CID)
        assert caught.value is error


# --- logged once: the consumer's line is the only one (M2) ------------------------------------------------------


def _lines_from_the_service(caplog: pytest.LogCaptureFixture) -> list[str]:
    """WARNING+ records from event_service.py / connector_factory.py, except lazy init's own 'not in memory' notice."""
    return [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.WARNING
        and r.pathname.endswith(("event_service.py", "connector_factory.py"))
        and "attempting auto-initialization" not in r.getMessage()
    ]


class TestLoggedOnce:
    @pytest.mark.parametrize("message", [pytest.param(INIT, id="init"), pytest.param(REINDEX, id="lazy-init")])
    async def test_a_raised_init_failure_adds_no_line_of_its_own(self, built, message, caplog) -> None:
        caplog.set_level(logging.DEBUG)
        _fails_with(built, _wrapped(ApiCallError("token revoked", status_code=401)))
        with pytest.raises(ConnectorInitError):
            await _handle(message)
        assert _lines_from_the_service(caplog) == []


# --- the real consumers -------------------------------------------------------------------------------------------


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
    """Real KafkaMessagingConsumer over FakeKafkaBroker, real sync wrapper and EventService, MAX_DELIVERY_ATTEMPTS=3."""

    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")
        monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")

    @staticmethod
    async def _attempts(message: dict, built: _Built) -> int:
        broker = FakeKafkaBroker()
        with patch.object(kafka_consumer_module, "AIOKafkaConsumer", broker.consumer_factory()):
            broker.produce("sync-events", message)
            consumer = KafkaMessagingConsumer(
                logging.getLogger("test.f27"),
                KafkaConsumerConfig(topics=["sync-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
                                    enable_auto_commit=False, bootstrap_servers=["kafka:9092"]),
                retry_manager=_RetryTracker(),
            )
            await consumer.start(await KafkaUtils.create_sync_message_handler(_container(), _graph()))
            deadline = asyncio.get_running_loop().time() + 5
            while (broker.committed_offset("g", "sync-events") or 0) < 1:
                assert asyncio.get_running_loop().time() < deadline, "the message was never settled"
                await asyncio.sleep(0.01)
            await consumer.stop()
        return len(built)

    @pytest.mark.parametrize("message", [pytest.param(INIT, id="init"), pytest.param(REINDEX, id="lazy-init")])
    async def test_a_source_401_is_tried_once(self, built, message) -> None:
        _fails_with(built, _wrapped(ApiCallError("token revoked", status_code=401)))
        assert await self._attempts(message, built) == 1

    async def test_an_unreachable_source_is_still_tried_max_attempts_times(self, built) -> None:
        _fails_with(built, ConnectionError("down"))
        assert await self._attempts(INIT, built) == 3


class TestRedisConsumerDecision:
    @staticmethod
    async def _decide(message: dict) -> tuple[bool, bool]:
        consumer = RedisStreamsConsumer(logging.getLogger("test.f27"), RedisStreamsConfig(
            host="localhost", port=6379, topics=["sync-events"], client_id="c", group_id="g"))
        consumer.message_handler = await KafkaUtils.create_sync_message_handler(_container(), _graph())
        return await in_delivery(consumer._process_message_with_classification(
            "sync-events", "1-0", {"value": json.dumps(message)}))

    async def test_a_source_401_is_acked_as_terminal(self, built) -> None:
        _fails_with(built, _wrapped(ApiCallError("token revoked", status_code=401)))
        assert await self._decide(INIT) == (False, True)

    async def test_an_unreachable_source_is_left_for_retry(self, built) -> None:
        _fails_with(built, ConnectionError("down"))
        assert await self._decide(INIT) == (False, False)
