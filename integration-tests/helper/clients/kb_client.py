"""Knowledge Base API client for integration tests."""

import io
import json
import time
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
        file_path: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file to a knowledge base.

        Args:
            kb_id: KB ID
            file_name: File name
            file_content: File content bytes
            folder_id: Optional folder ID
            mimetype: MIME type
            file_path: Send the file's path the way the knowledge-base page
                does. A path containing folders creates them, as a folder
                upload does. Without it the server reads the name from the
                multipart header instead.

        Returns:
            Parsed upload response from SSE stream
        """
        files = [("files", (file_name, io.BytesIO(file_content), mimetype))]
        data = None
        if file_path is not None:
            data = {
                "files_metadata": json.dumps(
                    [{"file_path": file_path, "last_modified": int(time.time() * 1000)}]
                )
            }
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
            data=data,
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

    def list_records(self, kb_id: str, page: int = 1, limit: int = 200) -> dict[str, Any]:
        """List every record in a knowledge base, including those in nested folders.

        The gateway has no ``/{kb_id}/records`` route (a GET there falls through
        to the web app and answers 200 with HTML), so this reads the Knowledge
        Hub browse API, flattened to records only.

        Args:
            kb_id: KB ID
            page: 1-indexed page
            limit: Items per page (the API allows up to 200)

        Returns:
            ``{"items": [...], "pagination": {"totalPages": ..., ...}}``; each
            item carries ``id``, ``name``, ``indexingStatus`` and ``reason``.
        """
        resp = self.get(
            f"/knowledge-hub/nodes/app/{kb_id}",
            params={"flattened": "true", "nodeTypes": "record", "page": page, "limit": limit},
        )
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
        use_subfolder_route: bool = True,
    ) -> dict[str, Any]:
        """Create a folder in a knowledge base.

        Args:
            kb_id: KB ID
            folder_name: Folder name
            parent_id: Optional parent folder ID. None creates at root.
            use_subfolder_route: Whether to use the dedicated subfolder route
                (default: True). Set False to exercise the legacy ?folderId= query.

        Returns:
            Response body from the API

        Raises:
            ValueError: If parent_id is an empty string.
        """
        payload: dict[str, Any] = {"folderName": folder_name}
        if parent_id is None:
            resp = self.post(f"/{kb_id}/folder", json=payload)
        elif not parent_id.strip():
            raise ValueError("parent_id cannot be an empty string")
        elif use_subfolder_route:
            resp = self.post(
                f"/{kb_id}/folder/{quote(parent_id, safe='')}/subfolder",
                json=payload,
            )
        else:
            resp = self.post(
                f"/{kb_id}/folder",
                json=payload,
                params={"folderId": parent_id},
            )
        resp.raise_for_status()
        return resp.json()

    def delete_folder(self, kb_id: str, folder_id: str) -> dict[str, Any]:
        """Delete a folder and everything beneath it.

        Args:
            kb_id: KB ID
            folder_id: Folder ID

        Returns:
            Response body from the API
        """
        resp = self.delete(f"/{kb_id}/folder/{folder_id}")
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

    def restore_record(self, record_id: str) -> dict[str, Any]:
        """Bring a record back from the trash, with everything deleted along with it.

        Args:
            record_id: Record ID (any record of the delete action)

        Returns:
            Response body from the API
        """
        resp = self.post(f"/record/{record_id}/restore")
        resp.raise_for_status()
        return resp.json()

    def rename_kb(self, kb_id: str, name: str) -> dict[str, Any]:
        resp = self.put(f"/{kb_id}", json={"kbName": name})
        resp.raise_for_status()
        return resp.json()

    def rename_folder(self, kb_id: str, folder_id: str, name: str) -> dict[str, Any]:
        resp = self.put(f"/{kb_id}/folder/{folder_id}", json={"folderName": name})
        resp.raise_for_status()
        return resp.json()

    def rename_record(self, record_id: str, name: str) -> dict[str, Any]:
        """Rename a file record. The route takes multipart form data, as for a new version."""
        self._client._ensure_access_token()
        resp = requests.put(
            f"{self._client.base_url}{self.BASE}/record/{record_id}",
            headers={"Authorization": f"Bearer {self._client._access_token}"},
            files={"recordName": (None, name)},
            timeout=self._client.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def move_record(self, kb_id: str, record_id: str, new_parent_id: str | None) -> dict[str, Any]:
        """Move a file or folder to another folder, or to the root with ``None``."""
        resp = self.put(f"/{kb_id}/record/{record_id}/move", json={"newParentId": new_parent_id})
        resp.raise_for_status()
        return resp.json()
