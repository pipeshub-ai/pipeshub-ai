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
class TestKnowledgeBaseCrud:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.client._ensure_access_token()
        self.url = f"{self.client.base_url}/api/v1/knowledgeBase/"
        self.headers = {
            "Authorization": f"Bearer {self.client._access_token}",
            "Content-Type": "application/json",
        }

    def test_create_knowledge_base_success(self) -> None:
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"kbName": f"rv-kb-{uuid4()}"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createKnowledgeBase")
        requests.delete(
            f"{self.url}{body['id']}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )

    def test_create_knowledge_base_negative(self) -> None:
        resp = requests.post(
            self.url, headers=self.headers, json={}, timeout=self.client.timeout_seconds
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKnowledgeBase", status_code="400"
        )

        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"kbName": ""},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKnowledgeBase", status_code="400"
        )

        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"kbName": "x" * 256},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKnowledgeBase", status_code="400"
        )

        resp = requests.post(
            self.url,
            headers={"Content-Type": "application/json"},
            json={"kbName": "rv-kb-should-fail"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKnowledgeBase", status_code="401"
        )

        resp = requests.post(
            self.url,
            headers={
                "Authorization": "Bearer invalid",
                "Content-Type": "application/json",
            },
            json={"kbName": "rv-kb-should-fail"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createKnowledgeBase", status_code="401"
        )

    def test_list_knowledge_bases_success(
        self, ten_knowledge_bases: dict[str, object]
    ) -> None:
        prefix = str(ten_knowledge_bases["prefix"])
        params = {
            "page": "1",
            "limit": "5",
            "search": prefix,
            "permissions": "OWNER",
            "sortBy": "createdAtTimestamp",
            "sortOrder": "desc",
        }
        resp = requests.get(
            self.url,
            headers=self.headers,
            params=params,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listKnowledgeBases")

        assert len(body["knowledgeBases"]) == 5
        assert body["pagination"]["totalCount"] >= 10
        assert body["pagination"]["hasNext"] is True
        for kb in body["knowledgeBases"]:
            assert prefix in kb["name"]

    def test_list_knowledge_bases_negative(self) -> None:
        resp = requests.get(
            self.url,
            headers=self.headers,
            params={"page": "0"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKnowledgeBases", status_code="400"
        )

        resp = requests.get(
            self.url,
            headers=self.headers,
            params={"limit": "101"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKnowledgeBases", status_code="400"
        )

        resp = requests.get(
            self.url,
            headers=self.headers,
            params={"foo": "bar"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKnowledgeBases", status_code="400"
        )

        resp = requests.get(
            self.url,
            headers=self.headers,
            params={"sortBy": "notAField"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKnowledgeBases", status_code="400"
        )

        resp = requests.get(
            self.url,
            headers=self.headers,
            params={"sortOrder": "invalid"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKnowledgeBases", status_code="400"
        )

        resp = requests.get(
            self.url,
            headers=self.headers,
            params={"permissions": "NOT_A_ROLE"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKnowledgeBases", status_code="400"
        )

        resp = requests.get(
            self.url,
            headers=self.headers,
            params={"search": "<script>alert(1)</script>"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKnowledgeBases", status_code="400"
        )

        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKnowledgeBases", status_code="401"
        )

        resp = requests.get(
            self.url,
            headers={"Authorization": "Bearer invalid"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listKnowledgeBases", status_code="401"
        )

    def test_get_knowledge_base_success(self) -> None:
        create_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"kbName": f"rv-get-{uuid4()}"},
            timeout=self.client.timeout_seconds,
        )
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            folder_resp = requests.post(
                f"{self.url}{kb_id}/folder",
                headers=self.headers,
                json={"folderName": "test folder"},
                timeout=self.client.timeout_seconds,
            )
            assert folder_resp.status_code == 200, folder_resp.text

            resp = requests.get(
                f"{self.url}{kb_id}",
                headers=self.headers,
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert_response_matches_openapi_operation(body, "getKnowledgeBase")

            assert body["id"] == kb_id
            assert body["userRole"] == "OWNER"
            assert body["connectorId"] is None
            assert len(body["folders"]) >= 1
            folder = body["folders"][0]
            for key in ("id", "name", "createdAtTimestamp", "webUrl"):
                assert key in folder
        finally:
            requests.delete(
                f"{self.url}{kb_id}",
                headers=self.headers,
                timeout=self.client.timeout_seconds,
            )

    def test_get_knowledge_base_negative(self) -> None:
        missing_id = str(uuid4())
        kb_url = f"{self.url}{missing_id}"

        resp = requests.get(kb_url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "getKnowledgeBase", status_code="401"
        )

        resp = requests.get(
            kb_url,
            headers={"Authorization": "Bearer invalid"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "getKnowledgeBase", status_code="401"
        )

        resp = requests.get(
            f"{self.url}{uuid4()}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "getKnowledgeBase", status_code="404"
        )

    def test_update_knowledge_base_success(self) -> None:
        create_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"kbName": f"rv-update-{uuid4()}"},
            timeout=self.client.timeout_seconds,
        )
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]
        new_name = f"rv-updated-{uuid4()}"

        try:
            resp = requests.put(
                f"{self.url}{kb_id}",
                headers=self.headers,
                json={"kbName": new_name},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert_response_matches_openapi_operation(body, "updateKnowledgeBase")

            assert body["success"] is True
            assert body["message"] == "Knowledge base updated successfully"

            get_resp = requests.get(
                f"{self.url}{kb_id}",
                headers=self.headers,
                timeout=self.client.timeout_seconds,
            )
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.json()["name"] == new_name
        finally:
            requests.delete(
                f"{self.url}{kb_id}",
                headers=self.headers,
                timeout=self.client.timeout_seconds,
            )

    def test_update_knowledge_base_negative(self) -> None:
        create_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"kbName": f"rv-update-neg-{uuid4()}"},
            timeout=self.client.timeout_seconds,
        )
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            resp = requests.put(
                f"{self.url}not-a-valid-uuid",
                headers=self.headers,
                json={"kbName": "should-fail"},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 400, resp.text
            assert_response_matches_openapi_operation(
                resp.json(), "updateKnowledgeBase", status_code="400"
            )

            resp = requests.put(
                f"{self.url}{kb_id}",
                headers=self.headers,
                json={"kbName": ""},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 400, resp.text
            assert_response_matches_openapi_operation(
                resp.json(), "updateKnowledgeBase", status_code="400"
            )

            resp = requests.put(
                f"{self.url}{kb_id}",
                headers=self.headers,
                json={"kbName": "x" * 256},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 400, resp.text
            assert_response_matches_openapi_operation(
                resp.json(), "updateKnowledgeBase", status_code="400"
            )

            resp = requests.put(
                f"{self.url}{kb_id}",
                headers=self.headers,
                json={"kbName": "<script>alert(1)</script>"},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 400, resp.text
            assert_response_matches_openapi_operation(
                resp.json(), "updateKnowledgeBase", status_code="400"
            )

            resp = requests.put(
                f"{self.url}{uuid4()}",
                headers=self.headers,
                json={"kbName": "missing-kb"},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 404, resp.text
            assert_response_matches_openapi_operation(
                resp.json(), "updateKnowledgeBase", status_code="404"
            )

            resp = requests.put(
                f"{self.url}{kb_id}",
                headers={"Content-Type": "application/json"},
                json={"kbName": "should-fail"},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 401, resp.text
            assert_response_matches_openapi_operation(
                resp.json(), "updateKnowledgeBase", status_code="401"
            )

            resp = requests.put(
                f"{self.url}{kb_id}",
                headers={
                    "Authorization": "Bearer invalid",
                    "Content-Type": "application/json",
                },
                json={"kbName": "should-fail"},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 401, resp.text
            assert_response_matches_openapi_operation(
                resp.json(), "updateKnowledgeBase", status_code="401"
            )
        finally:
            requests.delete(
                f"{self.url}{kb_id}",
                headers=self.headers,
                timeout=self.client.timeout_seconds,
            )

    def test_delete_knowledge_base_success(self) -> None:
        create_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"kbName": f"rv-delete-{uuid4()}"},
            timeout=self.client.timeout_seconds,
        )
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        resp = requests.delete(
            f"{self.url}{kb_id}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "deleteKnowledgeBase")

        assert body["success"] is True
        assert body["message"] == "Knowledge base deleted successfully"

        get_resp = requests.get(
            f"{self.url}{kb_id}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert get_resp.status_code == 404, get_resp.text

    def test_delete_knowledge_base_negative(self) -> None:
        missing_id = str(uuid4())
        kb_url = f"{self.url}{missing_id}"

        resp = requests.delete(kb_url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKnowledgeBase", status_code="401"
        )

        resp = requests.delete(
            kb_url,
            headers={"Authorization": "Bearer invalid"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKnowledgeBase", status_code="401"
        )

        resp = requests.delete(
            f"{self.url}{uuid4()}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKnowledgeBase", status_code="404"
        )

        create_resp = requests.post(
            self.url,
            headers=self.headers,
            json={"kbName": f"rv-delete-neg-{uuid4()}"},
            timeout=self.client.timeout_seconds,
        )
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        resp = requests.delete(
            f"{self.url}{kb_id}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text

        resp = requests.delete(
            f"{self.url}{kb_id}",
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteKnowledgeBase", status_code="404"
        )


@pytest.mark.integration
class TestGetAllRecords:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.client._ensure_access_token()
        self.records_url = f"{self.client.base_url}/api/v1/knowledgeBase/records"
        self.headers = {
            "Authorization": f"Bearer {self.client._access_token}",
            "Content-Type": "application/json",
        }

    def _get(self, params: dict[str, str]) -> dict:
        resp = requests.get(
            self.records_url,
            headers=self.headers,
            params=params,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getAllRecords")
        return body

    def test_get_all_records_pagination(self, six_kb_records: dict[str, object]) -> None:
        prefix = str(six_kb_records["prefix"])
        body = self._get(
            {"search": prefix, "page": "2", "limit": "2"},
        )

        assert len(body["records"]) == 2
        assert body["pagination"]["page"] == 2
        assert body["pagination"]["limit"] == 2
        assert body["pagination"]["totalCount"] == 6
        assert body["pagination"]["totalPages"] == 3
        for record in body["records"]:
            assert prefix in record["recordName"]

    def test_get_all_records_search(self, six_kb_records: dict[str, object]) -> None:
        prefix = str(six_kb_records["prefix"])
        body = self._get({"search": f"{prefix}-03"})

        assert body["pagination"]["totalCount"] == 1
        assert len(body["records"]) == 1
        assert body["records"][0]["recordName"] == f"{prefix}-03"

    def test_get_all_records_record_types(self, six_kb_records: dict[str, object]) -> None:
        prefix = str(six_kb_records["prefix"])
        body = self._get({"search": prefix, "recordTypes": "FILE"})

        assert body["pagination"]["totalCount"] == 6
        assert all(record["recordType"] == "FILE" for record in body["records"])

    def test_get_all_records_origins(self, six_kb_records: dict[str, object]) -> None:
        prefix = str(six_kb_records["prefix"])
        body = self._get({"search": prefix, "origins": "UPLOAD"})

        assert body["pagination"]["totalCount"] == 6
        assert all(record["origin"] == "UPLOAD" for record in body["records"])

    def test_get_all_records_sort_by_and_order(
        self, six_kb_records: dict[str, object]
    ) -> None:
        prefix = str(six_kb_records["prefix"])
        expected_names = list(six_kb_records["record_display_names"])

        asc_body = self._get(
            {"search": prefix, "sortBy": "recordName", "sortOrder": "asc", "limit": "100"},
        )
        asc_names = [record["recordName"] for record in asc_body["records"]]
        assert asc_names == sorted(expected_names)

        desc_body = self._get(
            {"search": prefix, "sortBy": "recordName", "sortOrder": "desc", "limit": "100"},
        )
        desc_names = [record["recordName"] for record in desc_body["records"]]
        assert desc_names == sorted(expected_names, reverse=True)

    def test_get_all_records_connectors(self, six_kb_records: dict[str, object]) -> None:
        prefix = str(six_kb_records["prefix"])
        body = self._get({"search": prefix, "connectors": "KB"})

        assert body["pagination"]["totalCount"] == 6

    def test_get_all_records_indexing_status(
        self, six_kb_records: dict[str, object]
    ) -> None:
        prefix = str(six_kb_records["prefix"])
        baseline = self._get({"search": prefix, "limit": "100"})
        assert baseline["pagination"]["totalCount"] == 6
        status_counts: dict[str, int] = {}
        for record in baseline["records"]:
            status = str(record["indexingStatus"])
            status_counts[status] = status_counts.get(status, 0) + 1

        for indexing_status, expected_count in status_counts.items():
            body = self._get({"search": prefix, "indexingStatus": indexing_status})
            assert body["pagination"]["totalCount"] == expected_count

    def test_get_all_records_permissions(self, six_kb_records: dict[str, object]) -> None:
        prefix = str(six_kb_records["prefix"])
        body = self._get({"search": prefix, "permissions": "OWNER"})

        assert body["pagination"]["totalCount"] == 6

    def test_get_all_records_date_range(self, six_kb_records: dict[str, object]) -> None:
        prefix = str(six_kb_records["prefix"])
        body = self._get(
            {
                "search": prefix,
                "dateFrom": str(six_kb_records["date_from_ms"]),
                "dateTo": str(six_kb_records["date_to_ms"]),
            },
        )

        assert body["pagination"]["totalCount"] == 6

    def test_get_all_records_source(self, six_kb_records: dict[str, object]) -> None:
        prefix = str(six_kb_records["prefix"])

        local_body = self._get({"search": prefix, "source": "local"})
        assert local_body["pagination"]["totalCount"] == 6

        connector_body = self._get({"search": prefix, "source": "connector"})
        assert connector_body["pagination"]["totalCount"] == 0
