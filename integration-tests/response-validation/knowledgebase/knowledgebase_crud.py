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
