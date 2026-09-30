"""Connector deletes publish the vector cleanup for the record they remove.

``GraphTransactionStore.get_record_by_key`` returns the stored document, the
way both providers' ``get_document`` does, not a ``Record``. Reading Record
attributes off it found no ``virtualRecordId``, so a connector's per-record
delete never asked indexing to remove the record's vectors. The fakes here
return the document, as the real store does.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)


def _processor(store: AsyncMock) -> DataSourceEntitiesProcessor:
    proc = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    proc.data_store_provider.transaction.return_value = ctx
    return proc


def _published(proc: DataSourceEntitiesProcessor) -> list[dict]:
    return [c.args[1] for c in proc.messaging_producer.send_message.await_args_list]


async def test_a_per_record_delete_publishes_the_cleanup_for_its_vectors() -> None:
    store = AsyncMock()
    store.get_record_by_key = AsyncMock(return_value={
        "_key": "rec-1", "orgId": "org-1", "version": 2, "virtualRecordId": "vr-1", "connectorId": "c1",
    })
    proc = _processor(store)

    await proc.on_record_deleted("rec-1")

    store.delete_record_by_key.assert_awaited_once_with("rec-1")
    (event,) = _published(proc)
    assert event["eventType"] == "deleteRecord"
    assert event["payload"] == {
        "orgId": "org-1", "recordId": "rec-1", "version": 2, "virtualRecordId": "vr-1", "connectorId": "c1",
    }


async def test_a_record_that_never_indexed_publishes_nothing() -> None:
    store = AsyncMock()
    store.get_record_by_key = AsyncMock(return_value={"_key": "rec-1", "orgId": "org-1"})
    proc = _processor(store)
    await proc.on_record_deleted("rec-1")
    assert _published(proc) == []


async def test_a_record_already_gone_is_still_removed_quietly() -> None:
    store = AsyncMock()
    store.get_record_by_key = AsyncMock(return_value=None)
    proc = _processor(store)
    await proc.on_record_deleted("rec-1")
    store.delete_record_by_key.assert_awaited_once_with("rec-1")
    assert _published(proc) == []


async def test_a_delete_by_external_id_publishes_the_providers_cleanup_event() -> None:
    """Outlook deletes go this way; the provider's event used to be dropped."""
    payload = {"orgId": "org-1", "recordId": "rec-1", "virtualRecordId": "vr-1", "connectorId": "c1"}
    store = AsyncMock()
    store.delete_record_by_external_id = AsyncMock(return_value={
        "success": True,
        "eventData": {"eventType": "deleteRecord", "topic": "record-events", "payload": payload},
    })
    proc = _processor(store)

    await proc.delete_record_by_external_id("c1", "ext-1", "u1")

    (event,) = _published(proc)
    assert (event["eventType"], event["payload"]) == ("deleteRecord", payload)


async def test_a_delete_by_external_id_of_nothing_publishes_nothing() -> None:
    store = AsyncMock()
    store.delete_record_by_external_id = AsyncMock(return_value=None)
    proc = _processor(store)
    await proc.delete_record_by_external_id("c1", "missing", "u1")
    assert _published(proc) == []


async def test_an_arango_outlook_delete_returns_cleanup_for_the_email_and_its_attachments() -> None:
    """It returned no event at all, so neither the email's nor its attachments' vectors went."""
    import logging

    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql = AsyncMock(return_value=["att-1"])
    provider.http_client.get_document = AsyncMock(return_value={
        "_key": "att-1", "orgId": "org-1", "virtualRecordId": "vr-att", "connectorId": "c1",
        "connectorName": "OUTLOOK", "origin": "CONNECTOR",
    })
    provider.get_document = AsyncMock(return_value=None)
    for step in ("_delete_outlook_edges", "_delete_file_record", "_delete_main_record", "_delete_mail_record"):
        setattr(provider, step, AsyncMock())
    email = {"_key": "mail-1", "orgId": "org-1", "virtualRecordId": "vr-mail", "connectorId": "c1"}

    result = await provider._execute_outlook_record_deletion("mail-1", email)

    event = result["eventData"]
    assert event["payload"]["virtualRecordId"] == "vr-mail"
    assert [p["virtualRecordId"] for p in event["payloads"]] == ["vr-mail", "vr-att"]


async def test_the_delete_route_publishes_every_payload() -> None:
    from app.connectors.api.router import delete_record
    from tests.unit.connectors.api.test_router_part1 import _mock_request

    graph = AsyncMock()
    graph.check_record_access_with_details = AsyncMock(return_value={"record": {}})
    graph.delete_record = AsyncMock(return_value={
        "success": True,
        "eventData": {
            "eventType": "deleteRecord", "topic": "record-events",
            "payload": {"recordId": "mail-1", "virtualRecordId": "vr-mail"},
            "payloads": [{"recordId": "mail-1", "virtualRecordId": "vr-mail"},
                         {"recordId": "att-1", "virtualRecordId": "vr-att"}],
        },
    })
    kafka = AsyncMock()
    container = MagicMock()
    container.logger = MagicMock(return_value=MagicMock())

    await delete_record("mail-1", _mock_request(container=container), graph, kafka)

    published = [c.args[1]["payload"]["virtualRecordId"] for c in kafka.publish_event.await_args_list]
    assert published == ["vr-mail", "vr-att"]


async def test_the_delete_route_reports_the_record_whose_cleanup_did_not_go_out() -> None:
    from unittest.mock import patch

    from app.connectors.api.router import delete_record
    from tests.unit.connectors.api.test_router_part1 import _mock_request

    graph = AsyncMock()
    graph.check_record_access_with_details = AsyncMock(return_value={"record": {}})
    graph.delete_record = AsyncMock(return_value={
        "success": True,
        "eventData": {
            "eventType": "deleteRecord", "topic": "record-events",
            "payload": {"recordId": "mail-1", "virtualRecordId": "vr-mail"},
            "payloads": [{"recordId": "mail-1", "virtualRecordId": "vr-mail"},
                         {"recordId": "att-1", "virtualRecordId": "vr-att"}],
        },
    })

    async def publish(thunk, **_kwargs):
        event = thunk.__defaults__[0]
        if event["payload"]["recordId"] == "att-1":
            raise RuntimeError("broker down")
        return await thunk()

    container = MagicMock()
    container.logger = MagicMock(return_value=MagicMock())
    with patch("app.connectors.api.router.retry_async", side_effect=publish):
        response = await delete_record("mail-1", _mock_request(container=container), graph, AsyncMock())

    assert response["vectorCleanupPending"] is True
    assert response["vectorCleanupFailedRecordIds"] == ["att-1"]
