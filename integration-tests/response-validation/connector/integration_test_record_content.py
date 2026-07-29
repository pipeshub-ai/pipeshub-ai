"""Response-validation ITs for GET /api/v1/connectors/record/{recordId}/content."""

from __future__ import annotations

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

        assert set(body.keys()) == {"content"}, body
        content = body["content"]
        assert isinstance(content, str) and content, "content should be a non-empty string"
        assert record_id in content
        assert indexed_text_record["record_name_stem"] in content

    def test_get_record_content_returns_parsed_content(
        self, indexed_text_record: dict[str, str]
    ) -> None:
        """The endpoint's whole point: return parsed content, not just metadata."""
        record_id = indexed_text_record["record_id"]
        sentinel = indexed_text_record["sentinel"]

        resp = self.connectors.get_record_content(record_id)
        assert resp.status_code == 200, resp.text
        content = resp.json()["content"]

        # content leads with the pre-formatted metadata header plus an LLM-written
        # summary, so assert only the deterministic Record ID line — the prose is
        # paraphrased, never verbatim.
        assert f"Record ID       : {record_id}" in content, content[:500]
        assert indexed_text_record["record_name_stem"] in content

        # the parsed block text is flattened into the same string, so the uploaded
        # sentinel must survive here verbatim.
        assert sentinel in content, (
            f"uploaded sentinel {sentinel!r} missing from content: {content[:500]}"
        )

        # the per-block Citation ID / Block Index scaffolding is stripped from the string.
        for marker in ("Block Index", "Citation ID"):
            assert marker not in content, f"unexpected scaffolding {marker!r} in content"

    def test_get_record_content_agrees_with_record_metadata(
        self, indexed_text_record: dict[str, str]
    ) -> None:
        """The content string and the camelCase metadata response describe one record."""
        record_id = indexed_text_record["record_id"]

        resp = self.connectors.get_record_content(record_id)
        assert resp.status_code == 200, resp.text
        content = resp.json()["content"]

        metadata_record = self.kb.get_record(record_id)["record"]

        assert metadata_record["id"] == record_id
        assert indexed_text_record["record_name_stem"] in metadata_record["recordName"]
        assert indexed_text_record["record_name_stem"] in content

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
        token_without_connector_read: str,
    ) -> None:
        """A token without `connector:read` is rejected even for a real record."""
        resp = self.connectors.get_record_content(
            indexed_text_record["record_id"],
            auth=False,
            headers={"Authorization": f"Bearer {token_without_connector_read}"},
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, _OPERATION_ID, status_code="403"
        )
        # A record-access denial is also a 403, so pin the scope check specifically —
        # otherwise this still passes if the token turns out to be fine.
        assert "scope" in body["error"]["message"].lower(), body
