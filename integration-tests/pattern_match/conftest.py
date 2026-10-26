"""Fixtures for the pattern-match storage suite.

Indexed content is filed under ``records/<connectorId>/...`` so pattern-match
search can grep it by place. These tests check that the files land where the
graph puts each record, follow it through renames and moves, and leave nothing
behind; ``helper/storage_layout.py`` does the looking.
"""

from __future__ import annotations

import uuid
from typing import Any, AsyncGenerator, Iterator

import pytest
import pytest_asyncio

from helper.kb_tree import KbTree
from helper.storage_layout import StorageLayoutProbe, connector_root
from storage.storage_client import StorageClient, scoped_jwt_secret


@pytest.fixture(scope="session")
def storage_client(pipeshub_client) -> StorageClient:
    if not scoped_jwt_secret():
        pytest.skip("SCOPED_JWT_SECRET is not set, so the storage service's internal routes cannot be called")
    return StorageClient(pipeshub_client)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def layout(mongo_store, blob_store, test_org_id: str) -> AsyncGenerator[StorageLayoutProbe, None]:
    yield StorageLayoutProbe(mongo_store, blob_store, test_org_id)


@pytest.fixture
def stored_records(storage_client: StorageClient) -> Iterator[Any]:
    """Stores processed-record documents directly, the way the indexer does.

    ``put(connector, path, vrid)`` writes ``record_<vrid>`` and ``metadata_<vrid>``
    at ``records/<connector>/<path>``. Every connector folder used is deleted
    afterwards.
    """
    connectors: set[str] = set()

    def put(connector: str, path: str, vrid: str, *, kinds: tuple[str, ...] = ("record", "metadata")) -> dict[str, str]:
        connectors.add(connector)
        folder = f"{connector_root(connector)}/{path}" if path else connector_root(connector)
        ids: dict[str, str] = {}
        for kind in kinds:
            name = f"{kind}_{vrid}"
            resp = storage_client.upload(
                f'{{"virtualRecordId": "{vrid}", "kind": "{kind}"}}'.encode(),
                f"{name}.json",
                name,
                is_versioned=False,
                document_path=folder,
            )
            assert resp.status_code == 200, f"storing {name} at {folder!r}: {resp.status_code} {resp.text[:300]}"
            body = resp.json()
            ids[kind] = str(body.get("document", body)["_id"])
        return ids

    put.connectors = connectors  # type: ignore[attr-defined]
    yield put
    for connector in connectors:
        storage_client.delete_connector_storage(connector)


@pytest.fixture
def connector_id() -> str:
    return f"it-pm-{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="session", autouse=True)
def _models_for_indexing(request) -> None:
    """Seed the org's models before anything here uploads, but only if something does.

    The internal-route tests store documents directly and need no model.
    """
    if any({"kb_tree", "second_kb"} & set(item.fixturenames) for item in request.session.items):
        request.getfixturevalue("ai_models_configured")


@pytest.fixture
def kb_tree(kb_client, vector_store, storage_client, layout) -> Iterator[KbTree]:
    tree = KbTree(kb_client, vector_store, storage_client, layout)
    yield tree
    tree.close()


@pytest.fixture
def second_kb(kb_client, vector_store, storage_client, layout) -> Iterator[KbTree]:
    tree = KbTree(kb_client, vector_store, storage_client, layout)
    yield tree
    tree.close()
