from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict
from uuid import uuid4

import pytest
import requests

from pipeshub_client import PipeshubClient

_KB_COUNT = 10


class TenKnowledgeBases(TypedDict):
    prefix: str
    ids: list[str]
    names: list[str]


@pytest.fixture
def ten_knowledge_bases(pipeshub_client: PipeshubClient) -> Generator[TenKnowledgeBases, None, None]:
    client = pipeshub_client
    client._ensure_access_token()
    url = f"{client.base_url}/api/v1/knowledgeBase/"
    headers = {
        "Authorization": f"Bearer {client._access_token}",
        "Content-Type": "application/json",
    }
    timeout = client.timeout_seconds
    prefix = f"rv-list-{uuid4().hex[:8]}"
    names = [f"{prefix}-{i:02d}" for i in range(_KB_COUNT)]
    kb_ids: list[str] = []

    def _create_one(name: str) -> str:
        resp = requests.post(
            url,
            headers=headers,
            json={"kbName": name},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["id"]

    with ThreadPoolExecutor(max_workers=_KB_COUNT) as pool:
        kb_ids = list(pool.map(_create_one, names))

    payload: TenKnowledgeBases = {"prefix": prefix, "ids": kb_ids, "names": names}
    try:
        yield payload
    finally:

        def _delete_one(kb_id: str) -> None:
            requests.delete(f"{url}{kb_id}", headers=headers, timeout=timeout)

        with ThreadPoolExecutor(max_workers=_KB_COUNT) as pool:
            list(pool.map(_delete_one, kb_ids))
