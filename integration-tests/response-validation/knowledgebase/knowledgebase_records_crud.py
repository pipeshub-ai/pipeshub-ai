from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from openapi_schema_validator import assert_response_matches_openapi_operation  # noqa: E402


@pytest.mark.integration
class TestKnowledgeBaseRecordsCrud:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.client._ensure_access_token()
        self.record_url = f"{self.client.base_url}/api/v1/knowledgeBase/record/"
        self.reindex_url = f"{self.client.base_url}/api/v1/knowledgeBase/reindex/record/"
        self.reindex_group_url = (
            f"{self.client.base_url}/api/v1/knowledgeBase/reindex/record-group/"
        )
        self.headers = {
            "Authorization": f"Bearer {self.client._access_token}",
            "Content-Type": "application/json",
        }

    def test_get_record_by_id_success(self, six_kb_records: dict[str, object]) -> None:
        record_id = str(six_kb_records["record_ids"][0])  # type: ignore[index]
        kb_id = str(six_kb_records["kb_id"])

        resp = requests.get(
            f"{self.record_url}{record_id}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getRecordById")

        record = body["record"]
        assert record["id"] == record_id
        assert body["knowledgeBase"]["id"] == kb_id
        assert record["recordType"] == "FILE"
        assert record["origin"] == "UPLOAD"
        assert isinstance(record["fileRecord"], dict)
        assert record["mailRecord"] is None
        assert record["ticketRecord"] is None
        assert body["folder"] is None
        assert body["permissions"]
        assert any(p.get("relationship") == "OWNER" for p in body["permissions"])

    def test_get_record_by_id_success_with_convert_to_query(
        self, six_kb_records: dict[str, object]
    ) -> None:
        record_id = str(six_kb_records["record_ids"][0])  # type: ignore[index]

        resp = requests.get(
            f"{self.record_url}{record_id}",
            headers=self.headers,
            params={"convertTo": "txt"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getRecordById")

    def test_get_record_by_id_negative(self) -> None:
        missing_id = str(uuid4())
        record_url = f"{self.record_url}{missing_id}"

        resp = requests.get(record_url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "getRecordById", status_code="401"
        )

        resp = requests.get(
            record_url,
            headers={"Authorization": "Bearer invalid"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "getRecordById", status_code="401"
        )

        resp = requests.get(
            f"{self.record_url}{uuid4()}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 500, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "getRecordById", status_code="500"
        )

    def test_delete_record_by_id_success(
        self, six_kb_records: dict[str, object]
    ) -> None:
        record_id = str(six_kb_records["record_ids"][-1])  # type: ignore[index]

        resp = requests.delete(
            f"{self.record_url}{record_id}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "deleteRecord")

        assert body["success"] is True
        assert body["recordId"] == record_id

        get_resp = requests.get(
            f"{self.record_url}{record_id}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert get_resp.status_code in (404, 500), get_resp.text

    def test_delete_record_by_id_negative(
        self, six_kb_records: dict[str, object]
    ) -> None:
        missing_id = str(uuid4())
        record_url = f"{self.record_url}{missing_id}"

        resp = requests.delete(record_url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteRecord", status_code="401"
        )

        resp = requests.delete(
            record_url,
            headers={"Authorization": "Bearer invalid"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteRecord", status_code="401"
        )

        resp = requests.delete(
            f"{self.record_url}{uuid4()}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code in (404, 500), resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteRecord", status_code=str(resp.status_code)
        )

        record_id = str(six_kb_records["record_ids"][-2])  # type: ignore[index]

        resp = requests.delete(
            f"{self.record_url}{record_id}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text

        resp = requests.delete(
            f"{self.record_url}{record_id}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code in (404, 500), resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteRecord", status_code=str(resp.status_code)
        )

    def test_reindex_record_by_id_success(
        self, six_kb_records: dict[str, object]
    ) -> None:
        record_id = str(six_kb_records["record_ids"][1])  # type: ignore[index]

        resp = requests.post(
            f"{self.reindex_url}{record_id}",
            headers=self.headers,
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "reindexRecord")

        assert body["success"] is True
        assert body["recordId"] == record_id
        assert body["depth"] == 0
        assert "message" in body

    def test_reindex_record_by_id_negative(
        self, six_kb_records: dict[str, object]
    ) -> None:
        missing_id = str(uuid4())
        reindex_url = f"{self.reindex_url}{missing_id}"

        resp = requests.post(reindex_url, json={}, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "reindexRecord", status_code="401"
        )

        resp = requests.post(
            reindex_url,
            headers={"Authorization": "Bearer invalid", "Content-Type": "application/json"},
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "reindexRecord", status_code="401"
        )

        resp = requests.post(
            f"{self.reindex_url}{uuid4()}",
            headers=self.headers,
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "reindexRecord", status_code="404"
        )

        record_id = str(six_kb_records["record_ids"][1])  # type: ignore[index]
        record_reindex_url = f"{self.reindex_url}{record_id}"

        resp = requests.post(
            record_reindex_url,
            headers=self.headers,
            json={"depth": 101},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text

        resp = requests.post(
            record_reindex_url,
            headers=self.headers,
            json={"depth": -2},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text

        resp = requests.post(
            record_reindex_url,
            headers=self.headers,
            json={"statusFilters": [1]},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text

    def test_reindex_record_group_success(
        self, six_kb_records: dict[str, object]
    ) -> None:
        record_group_id = str(six_kb_records["kb_id"])

        resp = requests.post(
            f"{self.reindex_group_url}{record_group_id}",
            headers=self.headers,
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "reindexRecordGroup")

        assert body["success"] is True
        assert body["recordGroupId"] == record_group_id
        assert body["depth"] == 0
        assert "message" in body

    def test_reindex_record_group_negative(
        self, six_kb_records: dict[str, object]
    ) -> None:
        missing_id = str(uuid4())
        reindex_group_url = f"{self.reindex_group_url}{missing_id}"

        resp = requests.post(
            reindex_group_url, json={}, timeout=self.client.timeout_seconds
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "reindexRecordGroup", status_code="401"
        )

        resp = requests.post(
            reindex_group_url,
            headers={"Authorization": "Bearer invalid", "Content-Type": "application/json"},
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "reindexRecordGroup", status_code="401"
        )

        resp = requests.post(
            f"{self.reindex_group_url}{uuid4()}",
            headers=self.headers,
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "reindexRecordGroup", status_code="404"
        )

        record_group_id = str(six_kb_records["kb_id"])
        group_reindex_url = f"{self.reindex_group_url}{record_group_id}"

        resp = requests.post(
            group_reindex_url,
            headers=self.headers,
            json={"depth": 101},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text

        resp = requests.post(
            group_reindex_url,
            headers=self.headers,
            json={"depth": -2},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text

        resp = requests.post(
            group_reindex_url,
            headers=self.headers,
            json={"statusFilters": [1]},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
