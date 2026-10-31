"""Unit tests for the Salesforce connector's sync window, eligibility and deletion modules."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.salesforce.deletions import SalesforceDeletions
from app.connectors.sources.salesforce.eligibility import SalesforceEligibility
from app.connectors.sources.salesforce.sync_window import (
    CHECKPOINT_OVERLAP_MS,
    checkpoint_for,
    soql_datetime,
)
from app.sources.client.salesforce.salesforce import SalesforceResponse

LOG = logging.getLogger("test.salesforce.sync_modules")
NOW = 1_767_225_600_000  # 2026-01-01T00:00:00Z


# ---------------------------------------------------------------- sync window


def test_soql_datetime_is_whole_seconds_in_utc():
    assert soql_datetime(NOW + 123) == "2026-01-01T00:00:00Z"
    assert soql_datetime(NOW + 1999) == "2026-01-01T00:00:01Z"


def test_checkpoint_is_the_start_less_the_overlap():
    assert checkpoint_for(NOW) == NOW - CHECKPOINT_OVERLAP_MS
    assert checkpoint_for(10) == 0


# ---------------------------------------------------------------- eligibility


def _rows(answers):
    async def rows(soql):
        answer = answers[soql]
        if isinstance(answer, Exception):
            raise answer
        yield answer
    return rows


@pytest.mark.asyncio
async def test_product_readers_are_permission_set_readers_and_view_all_data_users():
    from app.connectors.sources.salesforce import eligibility as e
    rows = _rows({e.PRODUCT_READERS_SOQL: [{"AssigneeId": "005A"}],
                  e.VIEW_ALL_DATA_SOQL: [{"AssigneeId": "005B"}]})
    assert await SalesforceEligibility(rows, LOG).product_reader_ids() == {"005A", "005B"}


@pytest.mark.asyncio
async def test_an_unreadable_list_is_unknown_not_empty():
    from app.connectors.sources.salesforce import eligibility as e
    rows = _rows({e.PRODUCT_READERS_SOQL: RuntimeError("HTTP 500"), e.VIEW_ALL_DATA_SOQL: [],
                  e.FROZEN_USERS_SOQL: RuntimeError("HTTP 403")})
    eligibility = SalesforceEligibility(rows, LOG)
    assert await eligibility.product_reader_ids() is None
    assert await eligibility.frozen_user_ids() is None


# ---------------------------------------------------------------- deletions


class _SyncPoint:
    def __init__(self, values=None):
        self.values = dict(values or {})

    async def read_sync_point(self, key):
        return self.values.get(key, {})

    async def update_sync_point(self, key, value):
        self.values[key] = value


def _store(nodes):
    tx = MagicMock()

    async def by_field(collection, field, values, return_fields=None):
        return [n for n in nodes if n.get(field) in values]
    tx.get_nodes_by_field_in = AsyncMock(side_effect=by_field)
    provider = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=tx)
    ctx.__aexit__ = AsyncMock(return_value=False)
    provider.transaction.return_value = ctx
    return provider


def _deletions(source, nodes, sync_point, live=()):
    processor = MagicMock()
    processor.on_record_deleted = AsyncMock()
    processor.on_record_group_deleted = AsyncMock()

    async def list_ids(soql, query_all=False):
        yield [{"Id": i} for i in live]
    d = SalesforceDeletions(source, processor, _store(nodes), sync_point, "conn-1", LOG,
                            now_ms=lambda: NOW + 10 * 60_000, list_ids=list_ids)
    return d, processor


DEAL = {"id": "r1", "externalRecordId": "006000000000001AAA", "recordType": "DEAL", "connectorId": "conn-1"}


def _source(response):
    source = MagicMock()
    source.s_object_get_deleted = AsyncMock(return_value=response)
    return source


@pytest.mark.asyncio
async def test_first_run_only_starts_the_window():
    source = _source(SalesforceResponse(success=True, data={}))
    sync_point = _SyncPoint()
    d, processor = _deletions(source, [DEAL], sync_point)
    await d.run("59.0", NOW, full_run=True)
    source.s_object_get_deleted.assert_not_awaited()
    processor.on_record_deleted.assert_not_awaited()
    assert sync_point.values["deleted_Opportunity"] == {"lastSyncTimestamp": checkpoint_for(NOW)}


@pytest.mark.asyncio
async def test_an_upgraded_install_lists_once_what_was_deleted_before():
    """No deletion checkpoint on an incremental run: the old version never removed anything."""
    source = _source(SalesforceResponse(success=True, data={}))
    other = {**DEAL, "id": "r2", "externalRecordId": "006000000000002AAA"}
    d, processor = _deletions(source, [DEAL, other], _SyncPoint(), live=["006000000000002AAA"])
    await d._run_one("59.0", "Opportunity", NOW)
    processor.on_record_deleted.assert_awaited_once_with(record_id="r1")
    source.s_object_get_deleted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_listed_deleted_record_is_removed():
    source = _source(SalesforceResponse(success=True, data={
        "deletedRecords": [{"id": "006000000000001AAA", "deletedDate": "2026-01-01T00:05:00.000+0000"}],
        "earliestDateAvailable": "2025-12-20T00:00:00.000+0000",
        "latestDateCovered": "2026-01-01T00:09:00.000+0000"}))
    sync_point = _SyncPoint({f"deleted_{o}": {"lastSyncTimestamp": NOW} for o in
                             ("Opportunity", "Case", "Task", "Product2", "ContentDocument", "Account")})
    d, processor = _deletions(source, [DEAL], sync_point)
    await d.run("59.0", NOW)
    processor.on_record_deleted.assert_awaited_once_with(record_id="r1")


@pytest.mark.asyncio
async def test_a_record_restored_since_its_delete_is_kept():
    source = _source(SalesforceResponse(success=True, data={
        "deletedRecords": [{"id": "006000000000001AAA", "deletedDate": "2026-01-01T00:05:00.000+0000"}],
        "earliestDateAvailable": "2025-12-20T00:00:00.000+0000",
        "latestDateCovered": "2026-01-01T00:09:00.000+0000"}))
    sync_point = _SyncPoint({"deleted_Opportunity": {"lastSyncTimestamp": NOW}})
    d, processor = _deletions(source, [DEAL], sync_point, live=["006000000000001AAA"])
    await d._run_one("59.0", "Opportunity", NOW)
    processor.on_record_deleted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_read_removes_nothing_and_keeps_the_checkpoint():
    source = _source(SalesforceResponse(success=False, error="HTTP 500", status_code=500))
    sync_point = _SyncPoint({"deleted_Opportunity": {"lastSyncTimestamp": NOW}})
    d, processor = _deletions(source, [DEAL], sync_point)
    with pytest.raises(Exception):
        await d._run_one("59.0", "Opportunity", NOW)
    processor.on_record_deleted.assert_not_awaited()
    assert sync_point.values["deleted_Opportunity"] == {"lastSyncTimestamp": NOW}


@pytest.mark.asyncio
async def test_a_purged_log_lists_every_live_id_instead():
    source = _source(SalesforceResponse(success=False, error="HTTP 400", status_code=400,
                                        message='[{"errorCode":"INVALID_REPLICATION_DATE"}]'))
    other = {**DEAL, "id": "r2", "externalRecordId": "006000000000002AAA"}
    sync_point = _SyncPoint({"deleted_Opportunity": {"lastSyncTimestamp": NOW}})
    d, processor = _deletions(source, [DEAL, other], sync_point, live=["006000000000002AAA"])
    await d._run_one("59.0", "Opportunity", NOW)
    processor.on_record_deleted.assert_awaited_once_with(record_id="r1")


@pytest.mark.asyncio
async def test_a_listing_that_fails_removes_nothing():
    source = _source(SalesforceResponse(success=False, status_code=400,
                                        message='[{"errorCode":"INVALID_REPLICATION_DATE"}]'))
    sync_point = _SyncPoint({"deleted_Opportunity": {"lastSyncTimestamp": NOW}})
    processor = MagicMock()
    processor.on_record_deleted = AsyncMock()

    async def failing(soql, query_all=False):
        yield [{"Id": "006000000000009AAA"}]
        raise RuntimeError("page two failed")
    d = SalesforceDeletions(source, processor, _store([DEAL]), sync_point, "conn-1", LOG,
                            now_ms=lambda: NOW + 10 * 60_000, list_ids=failing)
    with pytest.raises(RuntimeError):
        await d._run_one("59.0", "Opportunity", NOW)
    processor.on_record_deleted.assert_not_awaited()


# ---------------------------------------------------------------- file links

from app.connectors.sources.salesforce.file_links import SalesforceFileLinks  # noqa: E402

DOC, DEAL_ID, TASK_ID, EMAIL_ID = "069000000000001AAA", "006000000000001AAA", "00T000000000001AAA", "02s000000000001AAA"


def _file_links(rows_by_prefix, nodes):
    processor = MagicMock()
    processor.on_record_deleted = AsyncMock()

    async def rows(soql):
        for prefix, answer in rows_by_prefix.items():
            if prefix in soql:
                if isinstance(answer, Exception):
                    raise answer
                yield answer
                return
        yield []
    return SalesforceFileLinks(rows, processor, _store(nodes), "conn-1", LOG), processor


def _file(record_id, entity):
    return {"id": record_id, "externalRecordId": f"{DOC}-{entity}", "recordType": "FILE", "connectorId": "conn-1"}


@pytest.mark.asyncio
async def test_a_copy_whose_link_is_gone_is_removed_and_the_others_stay():
    links, processor = _file_links(
        {"FROM ContentDocumentLink": [{"ContentDocumentId": DOC, "LinkedEntityId": DEAL_ID,
                                       "LinkedEntity": {"Type": "Opportunity"}}]},
        [_file("f-deal", DEAL_ID), _file("f-case", "500000000000001AAA")])
    await links.run()
    processor.on_record_deleted.assert_awaited_once_with(record_id="f-case")


@pytest.mark.asyncio
async def test_an_email_attachment_counts_under_its_task():
    links, processor = _file_links(
        {"FROM ContentDocumentLink": [{"ContentDocumentId": DOC, "LinkedEntityId": EMAIL_ID,
                                       "LinkedEntity": {"Type": "EmailMessage"}}],
         "FROM EmailMessage": [{"Id": EMAIL_ID, "ActivityId": TASK_ID}]},
        [_file("f-task", TASK_ID)])
    await links.run()
    processor.on_record_deleted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_link_listing_removes_nothing():
    links, processor = _file_links({"FROM ContentDocumentLink": RuntimeError("HTTP 503")},
                                   [_file("f-deal", DEAL_ID)])
    with pytest.raises(RuntimeError):
        await links.run()
    processor.on_record_deleted.assert_not_awaited()


# --------------------------------------------------------------------------- notices and errors
from types import SimpleNamespace  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from app.connectors.sources.salesforce.api_errors import SalesforceRequestFailed, salesforce_error  # noqa: E402
from app.connectors.sources.salesforce.connector import SalesforceConnector  # noqa: E402
from app.connectors.sources.salesforce.sync_notices import SyncProblems, stopped_sync_notice  # noqa: E402
from app.services.notification.types import NotificationSeverity, NotificationType  # noqa: E402


def _failed(status, code, message="refused"):
    response = SimpleNamespace(status_code=status, error=f"{code}", message=[{"errorCode": code, "message": message}])
    fake = SimpleNamespace(logger=logging.getLogger("t"), display_name="Salesforce")
    with pytest.raises(HTTPException) as raised:
        SalesforceConnector._raise_failed_request(fake, response, "query", "here")
    return raised.value


def test_salesforce_error_reads_rest_and_oauth_answers():
    rest = SalesforceRequestFailed(400, [{"errorCode": "INVALID_FIELD", "message": "No such column"}])
    assert (rest.error_code, rest.detail) == ("INVALID_FIELD", "No such column")
    oauth = SalesforceRequestFailed(400, {"error": "invalid_grant", "error_description": "expired"})
    assert (oauth.error_code, oauth.detail) == ("invalid_grant", "expired")
    try:
        try:
            raise rest
        except SalesforceRequestFailed as e:
            raise RuntimeError("wrapped") from e
    except RuntimeError as outer:
        assert salesforce_error(outer) is rest


def test_chatter_hourly_limit_is_a_rate_limit_with_an_hour_to_wait():
    error = _failed(503, "REQUEST_LIMIT_EXCEEDED", "You have reached the Chatter API's hourly request limit")
    assert error.status_code == 429
    assert error.headers.get("Retry-After") == "3600"
    assert salesforce_error(error).status == 503


def test_daily_api_limit_is_a_rate_limit_and_a_plain_403_is_a_permission():
    assert _failed(403, "REQUEST_LIMIT_EXCEEDED", "TotalRequests Limit exceeded.").status_code == 429
    assert _failed(403, "INSUFFICIENT_ACCESS").status_code == 403


@pytest.mark.parametrize("status,code,event", [
    (401, "INVALID_SESSION_ID", "needs to be reconnected"),
    (400, "invalid_grant", "needs to be reconnected"),
    (403, "REQUEST_LIMIT_EXCEEDED", "sync paused, Salesforce API limit reached"),
    (503, "REQUEST_LIMIT_EXCEEDED", "sync paused, Salesforce Chatter limit reached"),
    (403, "API_DISABLED_FOR_ORG", "cannot read Salesforce"),
    (500, "UNKNOWN_EXCEPTION", "sync failed"),
])
def test_a_stopped_sync_says_why(status, code, event):
    try:
        raise RuntimeError("mapped") from SalesforceRequestFailed(status, [{"errorCode": code, "message": "m"}])
    except RuntimeError as e:
        notice = stopped_sync_notice(e)
    assert notice.event == event
    assert "nothing it had not read was removed" in notice.message


def test_a_clean_sync_sends_nothing_and_problems_are_listed_once():
    problems = SyncProblems()
    assert problems.notice() is None
    problems.note("A")
    problems.note("A")
    problems.skipped_records = 2
    notice = problems.notice()
    assert notice.type == NotificationType.CONNECTOR_WARNING and notice.severity == NotificationSeverity.WARNING
    assert notice.message.count("- A") == 1 and "2 Salesforce record(s)" in notice.message
