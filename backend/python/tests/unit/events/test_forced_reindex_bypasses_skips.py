"""D-180: a forced reindex takes no same-bytes skip and no embed-resume checkpoint."""
from __future__ import annotations

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.events.events import EventProcessor
from app.modules.indexing.embed_resume import (
    EMBED_DELIVERY_ID,
    EMBED_DIGEST,
    EMBED_REV,
    EMBED_VRID,
    may_resume,
)

RECORD = {
    "indexingStatus": ProgressStatus.COMPLETED.value, "hasIndexedCopy": True, "virtualRecordId": "vr", "contentRev": "rev-1",
}


def _skips(event_data: dict) -> bool:
    return EventProcessor._is_same_bytes_update(
        EventTypes.UPDATE_RECORD.value, event_data, RECORD, "rev-1", store_only=False, is_code=False, is_sql=False,
    )


def test_a_plain_update_of_the_same_bytes_is_skipped_and_a_forced_one_is_not() -> None:
    assert _skips({"recordId": "r"}) is True
    assert _skips({"recordId": "r", "forceReindex": True}) is False


def test_a_forced_reindex_takes_no_embed_resume_checkpoint() -> None:
    marker = {EMBED_DELIVERY_ID: "d1", EMBED_REV: "rev-1", EMBED_VRID: "vr", EMBED_DIGEST: "x"}
    common = {"delivery_id": "d1", "rev": "rev-1", "node_vrid": "vr"}
    assert may_resume(marker, force_reindex=False, **common) is True
    assert may_resume(marker, force_reindex=True, **common) is False
