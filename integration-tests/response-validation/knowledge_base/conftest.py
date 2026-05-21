from __future__ import annotations

import pytest
import requests


@pytest.fixture
def create_kb():
    def _create(base_url: str, access_token: str, kb_name: str, timeout: int = 30) -> requests.Response:
        return requests.post(
            f"{base_url}/api/v1/knowledgeBase",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"kbName": kb_name},
            timeout=timeout,
        )
    return _create


@pytest.fixture
def delete_kb():
    def _delete(base_url: str, access_token: str, kb_id: str, timeout: int = 30) -> requests.Response:
        return requests.delete(
            f"{base_url}/api/v1/knowledgeBase/{kb_id}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            timeout=timeout,
        )
    return _delete


@pytest.fixture
def get_kb():
    def _get(base_url: str, access_token: str, kb_id: str, timeout: int = 30) -> requests.Response:
        return requests.get(
            f"{base_url}/api/v1/knowledgeBase/{kb_id}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            timeout=timeout,
        )
    return _get


@pytest.fixture
def update_kb():
    def _update(base_url: str, access_token: str, kb_id: str, kb_name: str, timeout: int = 30) -> requests.Response:
        return requests.put(
            f"{base_url}/api/v1/knowledgeBase/{kb_id}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"kbName": kb_name},
            timeout=timeout,
        )
    return _update


@pytest.fixture
def upload_record():
    def _upload(base_url: str, access_token: str, kb_id: str, timeout: int = 30) -> requests.Response:
        return requests.post(
            f"{base_url}/api/v1/knowledgeBase/{kb_id}/upload",
            headers={"Authorization": f"Bearer {access_token}"},
            files={"files": ("test.txt", b"integration test content", "text/plain")},
            timeout=timeout,
        )
    return _upload


@pytest.fixture
def move_record():
    def _move(
        base_url: str,
        access_token: str,
        kb_id: str,
        record_id: str,
        new_parent_id: str | None,
        timeout: int = 30,
    ) -> requests.Response:
        return requests.put(
            f"{base_url}/api/v1/knowledgeBase/{kb_id}/record/{record_id}/move",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"newParentId": new_parent_id},
            timeout=timeout,
        )
    return _move


@pytest.fixture
def create_folder():
    def _create(base_url: str, access_token: str, kb_id: str, folder_name: str, timeout: int = 30) -> requests.Response:
        return requests.post(
            f"{base_url}/api/v1/knowledgeBase/{kb_id}/folder",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"folderName": folder_name},
            timeout=timeout,
        )
    return _create


@pytest.fixture
def reindex_record():
    def _reindex(
        base_url: str,
        access_token: str,
        record_id: str,
        depth: int = -1,
        force: bool = False,
        timeout: int = 30,
    ) -> requests.Response:
        return requests.post(
            f"{base_url}/api/v1/knowledgeBase/reindex/record/{record_id}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"depth": depth, "force": force},
            timeout=timeout,
        )
    return _reindex


@pytest.fixture
def create_subfolder():
    def _create(
        base_url: str,
        access_token: str,
        kb_id: str,
        folder_id: str,
        folder_name: str,
        timeout: int = 30,
    ) -> requests.Response:
        return requests.post(
            f"{base_url}/api/v1/knowledgeBase/{kb_id}/folder/{folder_id}/subfolder",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"folderName": folder_name},
            timeout=timeout,
        )
    return _create
