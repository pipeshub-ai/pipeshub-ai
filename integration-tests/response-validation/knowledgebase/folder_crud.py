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
class TestFolderCrud:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.client._ensure_access_token()
        self.kb_url = f"{self.client.base_url}/api/v1/knowledgeBase/"
        self.headers = {
            "Authorization": f"Bearer {self.client._access_token}",
            "Content-Type": "application/json",
        }

    def _create_root_folder(self, kb_id: str, folder_name: str | None = None) -> str:
        name = folder_name or f"parent-{uuid4().hex[:8]}"
        resp = requests.post(
            f"{self.kb_url}{kb_id}/folder",
            headers=self.headers,
            json={"folderName": name},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    def _folder_url(self, kb_id: str, folder_id: str) -> str:
        return f"{self.kb_url}{kb_id}/folder/{folder_id}"

    def test_create_root_folder_success(
        self, six_kb_records: dict[str, object]
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        folder_name = f"folder-create-{uuid4().hex[:8]}"

        resp = requests.post(
            f"{self.kb_url}{kb_id}/folder",
            headers=self.headers,
            json={"folderName": folder_name},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createFolder")

        assert body["name"] == folder_name
        assert isinstance(body["id"], str) and body["id"]
        assert body["webUrl"] == f"/kb/{kb_id}/folder/{body['id']}"

    def test_create_root_folder_negative(
        self, six_kb_records: dict[str, object]
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        folder_url = f"{self.kb_url}{kb_id}/folder"

        resp = requests.post(
            folder_url,
            headers=self.headers,
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="400"
        )

        resp = requests.post(
            folder_url,
            headers=self.headers,
            json={"folderName": ""},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="400"
        )

        resp = requests.post(
            folder_url,
            headers=self.headers,
            json={"folderName": "x" * 256},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="400"
        )

        resp = requests.post(
            folder_url,
            headers=self.headers,
            json={"folderName": "<script>alert(1)</script>"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="400"
        )

        resp = requests.post(
            folder_url,
            headers={"Content-Type": "application/json"},
            json={"folderName": "should-fail"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="401"
        )

        resp = requests.post(
            folder_url,
            headers={
                "Authorization": "Bearer invalid",
                "Content-Type": "application/json",
            },
            json={"folderName": "should-fail"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="401"
        )

        missing_kb_id = str(uuid4())
        resp = requests.post(
            f"{self.kb_url}{missing_kb_id}/folder",
            headers=self.headers,
            json={"folderName": "missing-kb-folder"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code in (403, 404), resp.text
        assert_response_matches_openapi_operation(
            resp.json(),
            "createFolder",
            status_code=str(resp.status_code),
        )

        duplicate_name = f"dup-folder-{uuid4().hex[:8]}"
        first = requests.post(
            folder_url,
            headers=self.headers,
            json={"folderName": duplicate_name},
            timeout=self.client.timeout_seconds,
        )
        assert first.status_code == 200, first.text

        resp = requests.post(
            folder_url,
            headers=self.headers,
            json={"folderName": duplicate_name},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 409, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="409"
        )

    def test_create_subfolder_success(
        self, six_kb_records: dict[str, object]
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        parent_id = self._create_root_folder(kb_id)
        folder_name = f"subfolder-create-{uuid4().hex[:8]}"

        resp = requests.post(
            f"{self.kb_url}{kb_id}/folder",
            headers=self.headers,
            params={"folderId": parent_id},
            json={"folderName": folder_name},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createFolder")

        assert body["name"] == folder_name
        assert isinstance(body["id"], str) and body["id"]
        assert body["webUrl"] == f"/kb/{kb_id}/folder/{body['id']}"

    def test_create_subfolder_negative(
        self, six_kb_records: dict[str, object]
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        parent_id = self._create_root_folder(kb_id)
        subfolder_url = f"{self.kb_url}{kb_id}/folder"
        subfolder_params = {"folderId": parent_id}

        resp = requests.post(
            subfolder_url,
            headers=self.headers,
            params=subfolder_params,
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="400"
        )

        resp = requests.post(
            subfolder_url,
            headers=self.headers,
            params=subfolder_params,
            json={"folderName": ""},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="400"
        )

        resp = requests.post(
            subfolder_url,
            headers=self.headers,
            params=subfolder_params,
            json={"folderName": "x" * 256},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="400"
        )

        resp = requests.post(
            subfolder_url,
            headers=self.headers,
            params=subfolder_params,
            json={"folderName": "<script>alert(1)</script>"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="400"
        )

        resp = requests.post(
            subfolder_url,
            headers={"Content-Type": "application/json"},
            params=subfolder_params,
            json={"folderName": "should-fail"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="401"
        )

        resp = requests.post(
            subfolder_url,
            headers={
                "Authorization": "Bearer invalid",
                "Content-Type": "application/json",
            },
            params=subfolder_params,
            json={"folderName": "should-fail"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="401"
        )

        missing_kb_id = str(uuid4())
        resp = requests.post(
            f"{self.kb_url}{missing_kb_id}/folder",
            headers=self.headers,
            params={"folderId": parent_id},
            json={"folderName": "missing-kb-subfolder"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code in (403, 404), resp.text
        assert_response_matches_openapi_operation(
            resp.json(),
            "createFolder",
            status_code=str(resp.status_code),
        )

        resp = requests.post(
            f"{self.kb_url}{kb_id}/folder",
            headers=self.headers,
            params={"folderId": str(uuid4())},
            json={"folderName": "missing-parent-subfolder"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="404"
        )

        duplicate_name = f"dup-subfolder-{uuid4().hex[:8]}"
        first = requests.post(
            subfolder_url,
            headers=self.headers,
            params=subfolder_params,
            json={"folderName": duplicate_name},
            timeout=self.client.timeout_seconds,
        )
        assert first.status_code == 200, first.text

        resp = requests.post(
            subfolder_url,
            headers=self.headers,
            params=subfolder_params,
            json={"folderName": duplicate_name},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 409, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "createFolder", status_code="409"
        )

    def test_update_folder_success(
        self, six_kb_records: dict[str, object]
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        folder_id = self._create_root_folder(kb_id)
        new_name = f"folder-updated-{uuid4().hex[:8]}"

        resp = requests.put(
            self._folder_url(kb_id, folder_id),
            headers=self.headers,
            json={"folderName": new_name},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateFolder")

        assert body["success"] is True
        assert isinstance(body["message"], str) and body["message"]

    def test_update_folder_negative(
        self, six_kb_records: dict[str, object]
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        folder_id = self._create_root_folder(kb_id)
        folder_url = self._folder_url(kb_id, folder_id)
        valid_body = {"folderName": f"rename-{uuid4().hex[:8]}"}

        resp = requests.put(
            folder_url,
            headers=self.headers,
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateFolder", status_code="400"
        )

        resp = requests.put(
            folder_url,
            headers=self.headers,
            json={"folderName": ""},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateFolder", status_code="400"
        )

        resp = requests.put(
            folder_url,
            headers=self.headers,
            json={"folderName": "x" * 256},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateFolder", status_code="400"
        )

        resp = requests.put(
            folder_url,
            headers=self.headers,
            json={"folderName": "<script>alert(1)</script>"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateFolder", status_code="400"
        )

        resp = requests.put(
            folder_url,
            headers={"Content-Type": "application/json"},
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateFolder", status_code="401"
        )

        resp = requests.put(
            folder_url,
            headers={
                "Authorization": "Bearer invalid",
                "Content-Type": "application/json",
            },
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateFolder", status_code="401"
        )

        missing_kb_id = str(uuid4())
        resp = requests.put(
            self._folder_url(missing_kb_id, folder_id),
            headers=self.headers,
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code in (403, 404), resp.text
        assert_response_matches_openapi_operation(
            resp.json(),
            "updateFolder",
            status_code=str(resp.status_code),
        )

        resp = requests.put(
            self._folder_url(kb_id, str(uuid4())),
            headers=self.headers,
            json=valid_body,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateFolder", status_code="404"
        )

        name_a = f"dup-a-{uuid4().hex[:8]}"
        name_b = f"dup-b-{uuid4().hex[:8]}"
        id_a = self._create_root_folder(kb_id, name_a)
        id_b = self._create_root_folder(kb_id, name_b)

        resp = requests.put(
            self._folder_url(kb_id, id_b),
            headers=self.headers,
            json={"folderName": name_a},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 409, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "updateFolder", status_code="409"
        )
        assert id_a != id_b

    def test_delete_folder_success(
        self, six_kb_records: dict[str, object]
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        folder_id = self._create_root_folder(kb_id)

        resp = requests.delete(
            self._folder_url(kb_id, folder_id),
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "deleteFolder")

        assert body["success"] is True
        assert body["message"] == "Folder deleted successfully"

        resp = requests.delete(
            self._folder_url(kb_id, folder_id),
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteFolder", status_code="404"
        )

    def test_delete_folder_negative(
        self, six_kb_records: dict[str, object]
    ) -> None:
        kb_id = str(six_kb_records["kb_id"])
        folder_id = self._create_root_folder(kb_id)
        folder_url = self._folder_url(kb_id, folder_id)

        resp = requests.delete(
            folder_url,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteFolder", status_code="401"
        )

        resp = requests.delete(
            folder_url,
            headers={"Authorization": "Bearer invalid"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteFolder", status_code="401"
        )

        missing_kb_id = str(uuid4())
        resp = requests.delete(
            self._folder_url(missing_kb_id, folder_id),
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code in (403, 404), resp.text
        assert_response_matches_openapi_operation(
            resp.json(),
            "deleteFolder",
            status_code=str(resp.status_code),
        )

        resp = requests.delete(
            self._folder_url(kb_id, str(uuid4())),
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteFolder", status_code="404"
        )
