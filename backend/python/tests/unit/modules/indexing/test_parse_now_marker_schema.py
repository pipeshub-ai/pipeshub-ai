"""The parse-now marker is written through the graph providers, which validate every update against
the record schema (strict unless GRAPH_SCHEMA_ENFORCEMENT turns it off). Fields the schema does not
declare are refused, and the marker writer only logs that, so a parse-now record would never show as
stored."""
from __future__ import annotations

import pytest

from app.modules.indexing import parse_now_marker as marker
from app.modules.indexing.parse_now_marker import ParseNowStatus, marker_fields
from app.schema.node_validator import NodeSchemaValidator


@pytest.fixture(autouse=True)
def _strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPH_SCHEMA_ENFORCEMENT", raising=False)


@pytest.mark.parametrize("status", list(ParseNowStatus))
def test_every_marker_status_is_a_valid_record_update(status: ParseNowStatus) -> None:
    NodeSchemaValidator().validate_node_update("records", marker_fields(status, "0123456789abcdef", "why"))


def test_a_marker_without_a_revision_or_reason_is_valid() -> None:
    NodeSchemaValidator().validate_node_update("records", marker_fields(ParseNowStatus.QUEUED, None))


@pytest.mark.parametrize("store_only", [False, True])
def test_a_status_write_shaped_during_a_parse_now_run_is_a_valid_record_update(store_only: bool) -> None:
    run = marker.ParseNowRun(content_rev="0123456789abcdef", store_only=store_only)
    marker.begin_run(run)
    try:
        shaped = marker.shape_status_write({"indexingStatus": "FAILED", "reason": "boom", "contentRev": "0123456789abcdef"})
    finally:
        marker.end_run()
    assert shaped[marker.PARSE_NOW_STATUS] == ParseNowStatus.FAILED.value
    NodeSchemaValidator().validate_node_update("records", shaped)
