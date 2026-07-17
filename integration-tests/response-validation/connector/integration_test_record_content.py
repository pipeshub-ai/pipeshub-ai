"""Response-validation ITs for GET /api/v1/connectors/record/{recordId}/content."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.connectors_client import ConnectorsClient  # noqa: E402
from helper.clients.kb_client import KBClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user_auth import second_pipeshub_client  # noqa: E402, F401
from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
)

_OPERATION_ID = "getRecordContent"


@pytest.mark.integration
class TestConnectorRecordContent:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.connectors = ConnectorsClient(pipeshub_client)
        self.kb = KBClient(pipeshub_client)

    # ---------------------------------------------------------------- positive

    def test_get_record_content_success(
        self, indexed_text_record: dict[str, str]
    ) -> None:
        record_id = indexed_text_record["record_id"]

        resp = self.connectors.get_record_content(record_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, _OPERATION_ID)

        record = body["record"]
        assert record["id"] == record_id
        assert record["org_id"] == self.connectors._client.org_id
        assert record["record_type"] == "FILE"
        assert record["virtual_record_id"]
        assert indexed_text_record["record_name_stem"] in record["record_name"]

    def test_get_record_content_returns_parsed_content(
        self, indexed_text_record: dict[str, str]
    ) -> None:
        """The endpoint's whole point: return parsed content, not just metadata."""
        record_id = indexed_text_record["record_id"]
        sentinel = indexed_text_record["sentinel"]

        resp = self.connectors.get_record_content(record_id)
        assert resp.status_code == 200, resp.text
        record = resp.json()["record"]

        # context_metadata is a pre-formatted header plus an LLM-written summary, so
        # assert only the deterministic lines — the prose is paraphrased, never verbatim.
        context_metadata = record["context_metadata"]
        assert f"Record ID       : {record_id}" in context_metadata, context_metadata[:500]
        assert indexed_text_record["record_name_stem"] in context_metadata

        # block_containers carries the parsed text itself, so the uploaded sentinel
        # must survive here verbatim.
        blocks = record["block_containers"]["blocks"]
        assert blocks, "block_containers.blocks should not be empty for a parsed txt record"
        assert sentinel in json.dumps(blocks), (
            f"uploaded sentinel {sentinel!r} missing from parsed blocks: "
            f"{json.dumps(blocks)[:500]}"
        )

    def test_get_record_content_agrees_with_record_metadata(
        self, indexed_text_record: dict[str, str]
    ) -> None:
        """snake_case content response and camelCase metadata response describe one record."""
        record_id = indexed_text_record["record_id"]

        resp = self.connectors.get_record_content(record_id)
        assert resp.status_code == 200, resp.text
        content_record = resp.json()["record"]

        metadata_record = self.kb.get_record(record_id)["record"]

        assert content_record["id"] == metadata_record["id"]
        assert content_record["record_name"] == metadata_record["recordName"]
        assert content_record["record_type"] == metadata_record["recordType"]
        assert "recordName" not in content_record, (
            "content response must stay snake_case — camelCase keys mean the two "
            "record shapes have been mixed up"
        )

    # ---------------------------------------------------------------- negative

    def test_get_record_content_unauthenticated(
        self, indexed_text_record: dict[str, str]
    ) -> None:
        record_id = indexed_text_record["record_id"]

        resp = self.connectors.get_record_content(record_id, auth=False)
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), _OPERATION_ID, status_code="401"
        )

        resp = self.connectors.get_record_content(
            record_id,
            auth=False,
            headers={"Authorization": "Bearer invalid"},
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), _OPERATION_ID, status_code="401"
        )

    def test_get_record_content_unknown_record_is_forbidden(self) -> None:
        """Unknown IDs 403 rather than 404 — the access check can't tell a missing
        record from an inaccessible one, so existence is never leaked."""
        resp = self.connectors.get_record_content(str(uuid4()))
        assert resp.status_code == 403, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), _OPERATION_ID, status_code="403"
        )

    def test_get_record_content_without_connector_read_scope(
        self,
        indexed_text_record: dict[str, str],
        second_pipeshub_client: PipeshubClient,
    ) -> None:
        """A token without `connector:read` is rejected even for a real record."""
        resp = ConnectorsClient(second_pipeshub_client).get_record_content(
            indexed_text_record["record_id"]
        )
        assert resp.status_code == 403, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), _OPERATION_ID, status_code="403"
        )
