"""Restoring the records of a connector turned back on never writes None into a status the schema calls non-null.

A legacy record without extractionStatus used to make the whole restore batch fail validation.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import CollectionNames, OriginTypes, ProgressStatus
from app.modules.indexing.connector_off_events import GraphConnectorOffFilter
from app.schema.node_validator import NodeSchemaValidator


async def _restored_rows(records: dict[str, dict]) -> list[tuple[str, dict, dict]]:
    graph = MagicMock()
    graph.get_nodes_by_field_in = AsyncMock(return_value=[{"id": "conn-1", "isActive": True}])
    graph.update_nodes_fields_if_match = AsyncMock()
    gate = GraphConnectorOffFilter(graph, logging.getLogger("test.connector_off"), refresh_seconds=60)

    still_off = await gate._still_off_after_write(set(records), records)

    assert still_off == set()  # the connector is on again: every event takes the normal path
    graph.update_nodes_fields_if_match.assert_awaited_once()
    collection, rows = graph.update_nodes_fields_if_match.await_args.args
    assert collection == CollectionNames.RECORDS.value
    return rows


def _record(**fields: object) -> dict:
    return {"connectorId": "conn-1", "origin": OriginTypes.CONNECTOR.value, **fields}


async def test_a_legacy_record_without_extraction_status_restores_without_that_key() -> None:
    (_, restored, _), = await _restored_rows({"r1": _record(indexingStatus=ProgressStatus.QUEUED.value)})

    assert "extractionStatus" not in restored
    assert restored["indexingStatus"] == ProgressStatus.QUEUED.value
    NodeSchemaValidator().validate_node_update(CollectionNames.RECORDS.value, restored)


async def test_nullable_fields_are_still_restored_to_none() -> None:
    (_, restored, _), = await _restored_rows({"r1": _record(
        indexingStatus=ProgressStatus.QUEUED.value,
        extractionStatus=ProgressStatus.NOT_STARTED.value,
        processingStartedAt=None,
        reason=None,
    )})

    assert restored["processingStartedAt"] is None and restored["reason"] is None
    assert restored["extractionStatus"] == ProgressStatus.NOT_STARTED.value
    NodeSchemaValidator().validate_node_update(CollectionNames.RECORDS.value, restored)


def test_connector_off_updates_never_writes_a_null_status() -> None:
    from app.modules.indexing.connector_off_events import connector_off_updates

    updates = connector_off_updates({"extractionStatus": None, "parsingStatus": None})

    assert updates["extractionStatus"] == ProgressStatus.NOT_STARTED.value
    assert updates["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
