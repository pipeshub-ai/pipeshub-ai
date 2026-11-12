"""An entity event whose write the graph schema rejects is final: logged once, counted, not redelivered.

The handlers returned False, so the consumer delivered the event three times and then dropped it.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schema.node_validator import SchemaValidationError
from app.services.messaging.kafka.handlers import entity as entity_module
from app.services.messaging.kafka.handlers.entity import EntityEventService

REJECTION = SchemaValidationError("organizations", "at 'name': None is not of type 'string'")


def _service(write_error: Exception) -> tuple[EntityEventService, AsyncMock]:
    graph = AsyncMock()
    graph.batch_upsert_nodes = AsyncMock(side_effect=write_error)
    container = MagicMock()
    container.messaging_producer = AsyncMock()
    logger = logging.getLogger("entity-schema-rejection-test")
    return EntityEventService(logger, graph, container), graph.batch_upsert_nodes


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        ("orgUpdated", {"orgId": "org-1", "registeredName": "Acme"}),
        ("orgDeleted", {"orgId": "org-1"}),
    ],
)
async def test_a_rejected_write_settles_the_event_once_and_is_counted(
    event: str, payload: dict, caplog: pytest.LogCaptureFixture
) -> None:
    svc, write = _service(REJECTION)
    with (
        caplog.at_level(logging.ERROR, logger="entity-schema-rejection-test"),
        patch.object(entity_module, "record_schema_rejection") as counted,
        patch.object(entity_module, "notify_principals_changed", new=AsyncMock()),
    ):
        assert await svc.process_event(event, payload) is True

    write.assert_awaited_once()
    counted.assert_called_once_with("organizations")
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "organizations" in errors[0].getMessage()


async def test_any_other_write_failure_is_still_a_failed_event() -> None:
    svc, _ = _service(RuntimeError("db down"))
    with patch.object(entity_module, "record_schema_rejection") as counted, pytest.raises(RuntimeError, match="db down"):
        await svc.process_event("orgUpdated", {"orgId": "org-1", "registeredName": "Acme"})
    counted.assert_not_called()
