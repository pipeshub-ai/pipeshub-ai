"""Which waiting records are re-sent as a re-embed from stored content."""

import pytest

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.modules.indexing.record_republish import record_event, reembeds_from_storage


@pytest.mark.parametrize("record, expected", [
    ({"virtualRecordId": "vr-1", "indexingStatus": ProgressStatus.COMPLETED.value}, True),
    ({"virtualRecordId": "vr-1", "indexingStatus": ProgressStatus.NOT_STARTED.value, "hasIndexedCopy": True}, True),
    ({"virtualRecordId": "vr-1", "indexingStatus": ProgressStatus.FAILED.value}, False),
    ({"indexingStatus": ProgressStatus.COMPLETED.value}, False),
    ({"virtualRecordId": "vr-1", "hasIndexedCopy": True, "indexingStatus": ProgressStatus.COMPLETED.value,
      "externalRevisionId": "2000", "lastIndexTimestamp": 1000}, False),
    ({"virtualRecordId": "vr-1", "hasIndexedCopy": True, "indexingStatus": ProgressStatus.FAILED.value,
      "externalRevisionId": "2000", "lastIndexTimestamp": 1000}, False),
    ({"virtualRecordId": "vr-1", "hasIndexedCopy": True, "externalRevisionId": "1000", "lastIndexTimestamp": 1000}, True),
    ({"virtualRecordId": "vr-1", "hasIndexedCopy": True, "externalRevisionId": None, "lastIndexTimestamp": 1000}, True),
    ({"virtualRecordId": "vr-1", "hasIndexedCopy": True, "externalRevisionId": "etag-7", "lastIndexTimestamp": 1000}, False),
    ({"virtualRecordId": "vr-1", "hasIndexedCopy": True, "externalRevisionId": "2000"}, False),
], ids=[
    "indexed", "restored with an indexed copy", "never indexed", "no stored content",
    "replaced since, completed", "replaced since, failed", "same revision", "no revision",
    "revision that cannot be compared", "never indexed under a revision",
])
def test_only_a_file_with_an_indexed_copy_re_embeds(record: dict, expected: bool) -> None:
    assert reembeds_from_storage(record) is expected


@pytest.mark.parametrize("restored, expected", [(True, True), (False, None)], ids=["restored", "not restored"])
def test_only_a_restored_upload_is_re_sent_as_a_re_embed(restored: bool, expected: bool | None) -> None:
    record = {"virtualRecordId": "vr-1", "version": 2, "hasIndexedCopy": True}
    event_type, payload = record_event(record, record_key="r1", connector_id="kb-1", restored_upload=restored)
    assert event_type == EventTypes.REINDEX_RECORD.value
    assert payload.get("vectorDbOnly") is expected


# ---- pushed records (P3-08) -------------------------------------------------------------------

def _row(**over: object) -> dict[str, object]:
    row = {"origin": "UPLOAD", "externalRevisionId": "pb1:" + "a" * 64 + ":" + "b" * 16, "version": 1, "mimeType": "application/blocks",
           "recordName": "n", "orgId": "o", "connectorName": "KB", "isDeleted": False}
    row.update(over)
    return row


def test_pushed_records_are_eligible() -> None:
    from app.modules.indexing.record_republish import (
        is_parked_duplicate,
        record_event,
        republish_eligible,
    )

    # (row, status, eligible)
    rows = [
        ("create, lost newRecord", _row(version=0), "NOT_STARTED", True),
        ("update, md5 cleared", _row(version=2, virtualRecordId="vr"), "NOT_STARTED", True),
        ("queued push", _row(), "QUEUED", True),
        ("ordinary upload", _row(externalRevisionId="1700000000000"), "NOT_STARTED", False),
        ("not a push tag", _row(externalRevisionId="pbx"), "NOT_STARTED", False),
        ("pushed but trashed", _row(isDeleted=True), "NOT_STARTED", False),
        ("indexing", _row(), "IN_PROGRESS", False),
        ("connector record", {"origin": "CONNECTOR"}, "QUEUED", True),
    ]
    for label, row, status, expected in rows:
        assert republish_eligible(row, status) == (expected, False), label
    # what is re-sent: a create is new, an update with a stored vrid is a reindex, and neither is a parked twin
    assert record_event(_row(version=0), record_key="k", connector_id="c")[0] == "newRecord"
    assert record_event(_row(version=2, virtualRecordId="vr"), record_key="k", connector_id="c")[0] == "reindexRecord"
    assert not is_parked_duplicate(_row(version=2, virtualRecordId="vr"))
    # an update whose md5 was kept looks like a parked twin and would never be re-sent: the push clears it
    assert is_parked_duplicate(_row(version=2, virtualRecordId="vr", md5Checksum="abc"))


def test_the_predicate_without_the_pushed_rule_finds_none(monkeypatch) -> None:
    """Negative control: with the pushed rule off, no pushed row is eligible (the behaviour before P3-08)."""
    from app.modules.indexing import record_republish

    monkeypatch.setattr(record_republish, "is_pushed_revision", lambda value: False)
    for row in (_row(version=0), _row(version=2, virtualRecordId="vr")):
        assert record_republish.republish_eligible(row, "NOT_STARTED") == (False, False)
