"""Every record status field accepts every ProgressStatus.

A status a writer can produce but the collection schema rejects fails the
write at runtime, on Arango only.
"""

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

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
    "AUTO_INDEX_OFF", "EMPTY", "ENABLE_MULTIMODAL_MODELS", "QUEUED", "SKIPPED", "PASSWORD_PROTECTED",
})


@pytest.mark.parametrize("field", ["parsingStatus", "indexingStatus", "extractionStatus"])
def test_no_status_a_stored_record_may_hold_is_dropped(field: str) -> None:
    assert _STORED_STATUSES <= _allowed(field)
    assert _STORED_STATUSES <= {status.value for status in ProgressStatus}


def test_legacy_connector_disabled_is_still_accepted_on_indexing_status() -> None:
    assert "CONNECTOR_DISABLED" in _allowed("indexingStatus")


# The Node API and its OpenAPI spec list the statuses by hand; a status the indexer writes that they
# omit is rejected by typed clients and by the UI's filters.
_NODE_APPS = Path(__file__).resolve().parents[5] / "backend" / "nodejs" / "apps"


def test_node_indexing_status_constants_match() -> None:
    source = (_NODE_APPS / "src/modules/knowledge_base/constants/record.constants.ts").read_text()
    block = re.search(r"export const INDEXING_STATUS = \{(.*?)\} as const", source, re.S)
    assert block, "INDEXING_STATUS not found"
    assert set(re.findall(r"^\s+(\w+):", block.group(1), re.M)) == {s.value for s in ProgressStatus}


def test_node_indexing_status_type_matches() -> None:
    source = (_NODE_APPS / "src/modules/knowledge_base/types/record.ts").read_text()
    block = re.search(r"export type IndexingStatus =(.*?);", source, re.S)
    assert block, "IndexingStatus not found"
    assert set(re.findall(r"'(\w+)'", block.group(1))) == {s.value for s in ProgressStatus}


def test_openapi_record_indexing_status_matches() -> None:
    spec = yaml.safe_load((_NODE_APPS / "src/modules/api-docs/pipeshub-openapi.yaml").read_text())
    properties = spec["components"]["schemas"]["Record"]["properties"]
    assert set(properties["indexingStatus"]["enum"]) == {s.value for s in ProgressStatus}
    assert set(properties["parsingStatus"]["enum"]) <= {s.value for s in ProgressStatus}
