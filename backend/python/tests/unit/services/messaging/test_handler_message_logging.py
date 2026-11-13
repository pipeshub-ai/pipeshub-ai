"""Message handlers (entity, AI config, record): one failure line each, no start-line duplicates, no payloads.

DEBUG is captured throughout, so a payload logged at DEBUG fails too.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jsonschema import Draft7Validator

from app.core import org_context
from app.exceptions.indexing_exceptions import ProcessingError
from app.schema import node_validator
from app.schema.node_validator import SchemaValidationError
from app.services.messaging.config import StreamMessage
from app.services.messaging.kafka.handlers import entity as entity_module
from app.services.messaging.kafka.handlers.ai_config import AiConfigEventService
from app.services.messaging.kafka.handlers.entity import EntityEventService
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.utils.logger import log_record_fields
from app.utils.structured_logging import DefaultLoggingProvider
from tests.unit.services.messaging.test_record_handler import (
    _collect_events,
    _make_handler,
)

LOGGER = "test.p8.handlers"
log = logging.getLogger(LOGGER)
SEEDED_NAME = "Ada SEEDED Lovelace"
SEEDED_ROUTE = "https://cm.internal/api/v1/SEEDED-credentials-route"
SEEDED_URL = "https://storage.example/SEEDED-signed?sig=abc"
SEEDS = (SEEDED_NAME, SEEDED_ROUTE, SEEDED_URL)


@pytest.fixture(autouse=True)
def _capture(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    caplog.set_level(logging.DEBUG, logger=LOGGER)


def _ours(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER]


def _assert_no_seeds(caplog) -> None:
    for record in _ours(caplog):
        text = " ".join([record.getMessage(), str(log_record_fields(record)), record.exc_text or ""])
        for seed in SEEDS:
            assert seed not in text, (seed, record.getMessage())


def _at(caplog, level: int) -> list[str]:
    return [r.getMessage() for r in _ours(caplog) if r.levelno == level]


def _service(graph_provider: AsyncMock | None = None) -> EntityEventService:
    return EntityEventService(logger=log, graph_provider=graph_provider or AsyncMock(), app_container=MagicMock())


ORG_UPDATED = {"orgId": "org_a", "registeredName": SEEDED_NAME}


class TestEntityEvents:
    async def test_success_is_one_info_line_without_the_payload(self, caplog) -> None:
        container = MagicMock()
        container.logger.return_value = log
        handle = await KafkaUtils.create_entity_message_handler(container, AsyncMock())
        assert await handle(StreamMessage(eventType="orgUpdated", payload=dict(ORG_UPDATED))) is True
        assert len(_at(caplog, logging.INFO)) == 1, _at(caplog, logging.INFO)
        assert not any("Processing entity event" in m for m in _at(caplog, logging.INFO))
        _assert_no_seeds(caplog)

    async def test_failure_is_one_line_with_its_class_and_no_payload(self, caplog) -> None:
        graph = AsyncMock()
        graph.batch_upsert_nodes = AsyncMock(side_effect=ConnectionError("graph unavailable"))
        with pytest.raises(ConnectionError):
            await _service(graph).process_event("orgUpdated", dict(ORG_UPDATED))
        failures = [r for r in _ours(caplog) if r.levelno >= logging.WARNING]
        assert [r.getMessage() for r in failures] == ["entity_event.failed"]
        fields = log_record_fields(failures[0])
        assert (fields["error_class"], fields["error_type"]) == ("unavailable", "ConnectionError")
        _assert_no_seeds(caplog)

    async def test_a_user_event_failure_names_the_user_it_was_about(self, caplog) -> None:
        graph = AsyncMock()
        graph.get_user_by_email = AsyncMock(side_effect=ConnectionError("graph unavailable"))
        payload = {"orgId": "org_a", "userId": "u-1", "email": "ada@example.com"}
        with pytest.raises(ConnectionError):
            await _service(graph).process_event("userAdded", payload)
        failures = [r for r in _ours(caplog) if r.levelno >= logging.WARNING]
        assert [r.getMessage() for r in failures] == ["entity_event.failed"]
        assert log_record_fields(failures[0])["target_user_id"] == "u-1"
        assert not any("ada@example.com" in r.getMessage() for r in _ours(caplog))

    @pytest.mark.parametrize(("event_type", "payload"), [
        ("orgDeleted", {"orgId": "org_a", "credentialsRoute": SEEDED_ROUTE}),
        ("appEnabled", {"orgId": "org_a", "apps": ["DRIVE"], "credentialsRoute": SEEDED_ROUTE}),
        ("appDisabled", {"orgId": "org_a", "apps": ["DRIVE"], "connectorId": "c1", "refreshTokenRoute": SEEDED_ROUTE}),
    ])
    async def test_org_and_app_events_never_log_their_payload(self, caplog, event_type, payload) -> None:
        svc = _service()
        svc._remove_mcp_data = AsyncMock()
        svc._remove_personal_toolsets = AsyncMock()
        svc._remove_api_toolset_data = AsyncMock()
        svc._wait_for_sync_to_stop = AsyncMock(return_value=True)
        await svc.process_event(event_type, payload)
        _assert_no_seeds(caplog)

    @pytest.mark.parametrize(("event_type", "payload"), [
        ("orgCreated", {"orgId": "org_a", "accountType": "enterprise", "registeredName": SEEDED_NAME}),
        ("orgUpdated", dict(ORG_UPDATED)),
        ("orgDeleted", {"orgId": "org_a"}),
        ("appEnabled", {"orgId": "org_a", "apps": ["DRIVE"], "connectorId": "c1"}),
        ("appDisabled", {"orgId": "org_a", "apps": ["DRIVE"], "connectorId": "c1"}),
    ])
    @pytest.mark.parametrize("fails", [False, True])
    async def test_no_log_line_has_an_emoji(self, caplog, event_type, payload, fails) -> None:
        graph = AsyncMock()
        if fails:
            graph.batch_upsert_nodes = AsyncMock(side_effect=ConnectionError("down"))
            graph.get_document = AsyncMock(side_effect=ConnectionError("down"))
        svc = _service(graph)
        svc._remove_mcp_data = AsyncMock()
        svc._remove_personal_toolsets = AsyncMock()
        svc._remove_api_toolset_data = AsyncMock()
        svc._start_requeue_of_parked_records = AsyncMock()
        svc._wait_for_sync_to_stop = AsyncMock(return_value=True)
        if fails:
            with pytest.raises(ConnectionError):
                await svc.process_event(event_type, payload)
        else:
            await svc.process_event(event_type, payload)
        assert _ours(caplog)
        assert all(m.isascii() for m in (r.getMessage() for r in _ours(caplog)))

    async def test_a_failure_escaping_the_service_reaches_the_consumer_and_the_wrapper_adds_no_line(self, caplog) -> None:
        container = MagicMock()
        container.logger.return_value = log
        service = MagicMock()
        error = ConnectionError(f"graph said {SEEDED_NAME}")
        service.process_event = AsyncMock(side_effect=error)
        with patch("app.services.messaging.kafka.utils.utils.EntityEventService", return_value=service):
            handle = await KafkaUtils.create_entity_message_handler(container, AsyncMock())
        with pytest.raises(ConnectionError) as caught:
            await handle(StreamMessage(eventType="orgUpdated", payload=dict(ORG_UPDATED)))
        assert caught.value is error  # the consumer's message.end classifies and logs it
        assert _ours(caplog) == []


class TestSchemaRejection:
    @pytest.mark.parametrize(("field", "value", "rule", "validator"), [
        ("name", SEEDED_NAME, {"type": "integer"}, "type"),
        ("email", "ada.seeded@example.com", {"type": "string", "pattern": "^nobody$"}, "pattern"),
    ])
    async def test_a_rejected_write_logs_where_and_which_rule_never_the_value(
        self, caplog, field, value, rule, validator,
    ) -> None:
        error = next(Draft7Validator({"type": "object", "properties": {field: rule}}).iter_errors({field: value}))
        with pytest.raises(SchemaValidationError) as raised:
            node_validator._raise("organizations", error)
        assert value in str(raised.value.original_error)  # control: the value still rides on the exception
        graph = AsyncMock()
        graph.batch_upsert_nodes = AsyncMock(side_effect=raised.value)
        assert await _service(graph).process_event("orgUpdated", {"orgId": "org_a", "registeredName": SEEDED_NAME}) is True
        [record] = [r for r in _ours(caplog) if r.levelno >= logging.WARNING]
        fields = log_record_fields(record)
        assert (fields["schema_path"], fields["validator"]) == (f"$.{field}", validator)
        assert "organizations" in record.getMessage()
        for r in _ours(caplog):
            assert value not in r.getMessage() + str(log_record_fields(r)), r.getMessage()


class TestConnectorLinkPasses:
    async def test_link_passes_run_as_an_org_job_without_the_message_ids(self, caplog, monkeypatch) -> None:
        # A new module: imported here so the rest of this file runs on main.
        from app.services.messaging import message_log as ML

        monkeypatch.setattr(entity_module, "_LINK_DEBOUNCE_SEC", 0)
        svc = _service()
        connector = MagicMock()
        connector.user_link_steps = ("users",)
        connector.data_entities_processor.org_id = "org_a"
        svc.app_container.connectors_map = {"conn-1": connector}

        async def link(connector_id: str) -> bool:
            log.info("link pass")
            return True

        svc._link_connector = link

        async def leaked() -> None:
            log.info("plain task")

        with ML.delivery(log, topic="entity-events", message_id="1-0"):
            ML.bind_opened(org_context.OrgContext("org_a", "admin", "user", "envelope", "r"), {"eventType": "userAdded"})
            svc._schedule_connector_link("org_a")
            link_task = svc._link_state["conn-1"]["task"]
            control = asyncio.create_task(leaked())
            ML.end("ack")
        await asyncio.gather(link_task, control)

        def fields(message: str) -> dict[str, object]:
            return log_record_fields(next(r for r in _ours(caplog) if r.getMessage() == message))

        assert fields("plain task")["message_id"] == "1-0" and fields("plain task")["user_id"] == "admin"  # control
        link_line = fields("link pass")
        assert (link_line["connector_id"], link_line["org_id"], link_line["ctx_kind"]) == ("conn-1", "org_a", "job")
        assert "message_id" not in link_line and "user_id" not in link_line and "event_type" not in link_line


class TestAiConfigEvents:
    @pytest.mark.parametrize("event_type", ["llmConfigured", "embeddingModelConfigured"])
    async def test_config_events_never_log_their_payload(self, caplog, event_type) -> None:
        retrieval = MagicMock()
        retrieval.get_llm_instance = AsyncMock()
        retrieval.get_embedding_model_instance = AsyncMock()
        svc = AiConfigEventService(logger=log, retrieval_service=retrieval)
        assert await svc.process_event(event_type, {"credentialsRoute": SEEDED_ROUTE}) is True
        _assert_no_seeds(caplog)
        assert all(m.isascii() for m in (r.getMessage() for r in _ours(caplog)))

    async def test_failure_is_one_line_with_its_class(self, caplog) -> None:
        retrieval = MagicMock()
        retrieval.get_llm_instance = AsyncMock(side_effect=ConnectionError("down"))
        svc = AiConfigEventService(logger=log, retrieval_service=retrieval)
        with pytest.raises(ConnectionError):
            await svc.process_event("llmConfigured", {"credentialsRoute": SEEDED_ROUTE})
        failures = [r for r in _ours(caplog) if r.levelno >= logging.WARNING]
        assert len(failures) == 1 and log_record_fields(failures[0])["error_class"] == "unavailable"


class TestMalformedRecordEvents:
    @pytest.mark.parametrize(("event_type", "payload"), [
        ("", {"recordId": "r1", "signedUrl": SEEDED_URL, "recordName": SEEDED_NAME}),
        ("newRecord", {"signedUrl": SEEDED_URL, "recordName": SEEDED_NAME}),
    ])
    async def test_a_malformed_record_event_never_logs_its_payload(self, caplog, event_type, payload) -> None:
        handler = _make_handler(logger=log)
        with pytest.raises(ProcessingError):
            await _collect_events(handler, event_type, payload)
        _assert_no_seeds(caplog)
