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
