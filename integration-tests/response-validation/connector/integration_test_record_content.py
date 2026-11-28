"""Response-validation ITs for GET /api/v1/connectors/record/{recordId}/content."""

from __future__ import annotations

import re
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
# Any read-budget parameter sends the read to the query service's record reader
# instead of the connector service's whole-record route.
_BUDGETED = {"maxChars": 2000}


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
        # paraphrased, never verbatim. Spacing around the colon is not part of the
        # contract (Prompt tuning #2848 dropped column padding).
        assert re.search(
            rf"^Record ID[^\S\r\n]*:[^\S\r\n]*{re.escape(record_id)}[^\S\r\n]*$",
            content,
            re.MULTILINE,
        ), content[:500]
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

    @pytest.mark.parametrize("params", [{}, _BUDGETED], ids=["whole", "budgeted"])
    def test_get_record_content_unknown_record_is_not_found(self, params: dict[str, int]) -> None:
        """An id that names no record is a 404 "Record not found", in both read
        modes (integration_test_record_reads_hide_existence.py pins that a record
        the caller can't open gets the same answer)."""
        resp = self.connectors.get_record_content(str(uuid4()), params=params)
        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert body["error"]["message"] == "Record not found", body
        assert_response_matches_openapi_operation(body, _OPERATION_ID, status_code="404")

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
        # Pin the scope check by its message: the 403 alone would also pass for a
        # token refused for some other reason.
        assert "scope" in body["error"]["message"].lower(), body
