"""Every record status field accepts every ProgressStatus.

A status a writer can produce but the collection schema rejects fails the
write at runtime, on Arango only.
"""

from typing import Any, cast

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.schema.arango.documents import record_schema


def _allowed(field: str) -> set[str]:
    properties = cast(dict[str, Any], record_schema["rule"])["properties"]
    return set(cast(list[str], properties[field]["enum"]))


@pytest.mark.parametrize("field", ["parsingStatus", "indexingStatus", "extractionStatus"])
def test_status_field_accepts_every_progress_status(field: str) -> None:
    assert {status.value for status in ProgressStatus} <= _allowed(field)


# Values stored records may already hold. Dropping one from the schema makes ArangoDB reject every
# later update to those records, so a removal needs a data migration first.
_STORED_STATUSES = frozenset({
    "NOT_STARTED", "PAUSED", "IN_PROGRESS", "COMPLETED", "FAILED", "FILE_TYPE_NOT_SUPPORTED",
    "AUTO_INDEX_OFF", "EMPTY", "ENABLE_MULTIMODAL_MODELS", "QUEUED", "SKIPPED",
})


@pytest.mark.parametrize("field", ["parsingStatus", "indexingStatus", "extractionStatus"])
def test_no_status_a_stored_record_may_hold_is_dropped(field: str) -> None:
    assert _STORED_STATUSES <= _allowed(field)
    assert _STORED_STATUSES <= {status.value for status in ProgressStatus}


def test_legacy_connector_disabled_is_still_accepted_on_indexing_status() -> None:
    assert "CONNECTOR_DISABLED" in _allowed("indexingStatus")
