"""Knowledge Base API client for integration tests."""

import io
import uuid
from typing import Any
from urllib.parse import quote

import requests

from helper.http.api_client import APIClient
from helper.kb_upload_sse import parse_kb_upload_response


class KBClient(APIClient):
    """Client for /api/v1/knowledgeBase endpoints.

    Provides methods for knowledge base management operations including
    file uploads with SSE response parsing.
    """

    BASE = "/api/v1/knowledgeBase"

    def create_kb(self, name: str | None = None) -> dict[str, Any]:
        """Create a new knowledge base.

        Args:
            name: KB name (auto-generated if not provided)

        Returns:
            Response body from the API
        """
        name = name or f"pipeline-test-kb-{uuid.uuid4().hex[:8]}"
        resp = self.post("/", json={"kbName": name})
        resp.raise_for_status()
        return resp.json()

    def delete_kb(self, kb_id: str) -> dict[str, Any]:
        """Delete a knowledge base.

        Args:
            kb_id: KB ID

        Returns:
            Response body from the API
        """
        resp = self.delete(f"/{kb_id}")
        resp.raise_for_status()
        return resp.json()

    def upload_file(
        self,
        kb_id: str,
        file_name: str,
        file_content: bytes,
        folder_id: str | None = None,
        mimetype: str = "text/plain",
    ) -> dict[str, Any]:
        """Upload a file to a knowledge base.

        Args:
            kb_id: KB ID
            file_name: File name
            file_content: File content bytes
            folder_id: Optional folder ID
            mimetype: MIME type

        Returns:
            Parsed upload response from SSE stream
        """
        files = [("files", (file_name, io.BytesIO(file_content), mimetype))]
        upload_path = f"/{kb_id}/upload"
        if folder_id:
            upload_path = f"{upload_path}?folderId={quote(folder_id, safe='')}"

        url = f"{self._client.base_url}{self.BASE}{upload_path}"
        self._client._ensure_access_token()
        headers = {"Authorization": f"Bearer {self._client._access_token}"}

        with requests.post(
            url,
            headers=headers,
            files=files,
            stream=True,
            timeout=self._client.timeout_seconds,
        ) as resp:
            return parse_kb_upload_response(resp)

    def get_record(
        self,
        record_id: str,
        retries: int = 3,
        delay: float = 2.0,
    ) -> dict[str, Any]:
        """Get a record by ID with retry on transient errors.

        Args:
            record_id: Record ID
            retries: Number of retries
            delay: Delay between retries in seconds

        Returns:
            Response body from the API
        """
        import time

        last_err = None
        for attempt in range(retries):
            resp = self.get(f"/record/{record_id}")
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code >= 500 and attempt < retries - 1:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                time.sleep(delay)
                continue
            resp.raise_for_status()
        raise RuntimeError(f"get_record failed after {retries} retries: {last_err}")

    def list_records(self, kb_id: str, **params: Any) -> dict[str, Any]:
        """List records in a knowledge base.

        Args:
            kb_id: KB ID
            **params: Query params (page, limit, etc.)

        Returns:
            Response body from the API
        """
        resp = self.get(f"/{kb_id}/records", params=params)
        resp.raise_for_status()
        return resp.json()

    def get_folders(self, kb_id: str) -> dict[str, Any]:
        """Get folders in a knowledge base.

        Args:
            kb_id: KB ID

        Returns:
            Response body from the API
        """
        resp = self.get(f"/{kb_id}/folders")
        resp.raise_for_status()
        return resp.json()

    def create_folder(
        self,
        kb_id: str,
        folder_name: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a folder in a knowledge base.

        Args:
            kb_id: KB ID
            folder_name: Folder name
            parent_id: Optional parent folder ID

        Returns:
            Response body from the API
        """
        payload: dict[str, Any] = {"folderName": folder_name}
        if parent_id:
            payload["parentId"] = parent_id
        resp = self.post(f"/{kb_id}/folders", json=payload)
        resp.raise_for_status()
        return resp.json()

    def delete_record(self, record_id: str) -> dict[str, Any]:
        """Delete a record.

        Args:
            record_id: Record ID

        Returns:
            Response body from the API
        """
        resp = self.delete(f"/record/{record_id}")
        resp.raise_for_status()
        return resp.json()
