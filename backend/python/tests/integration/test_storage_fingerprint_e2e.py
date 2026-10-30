"""The storage fingerprint seen from Python, against the real storage routes (MongoDB + local disk / S3 / Azure Blob).

Each write path the Python writers use (``update_record_buffer``, ``upload_next_version``, a presigned overwrite through
``get_direct_upload_url``, the rollback route) must move ``StorageDocuments.info(verify=True).fingerprint`` and leave the document
trusted afterwards; a signed GET sent with ``If-Match`` succeeds for the verified object and answers 412 once it was overwritten.

Environment: as ``test_artifact_store_contract.py`` (``ARTIFACTS_IT_STORAGE_URL``, ``ARTIFACTS_IT_SCOPED_SECRET``,
``ARTIFACTS_IT_STORAGE_TYPE``).
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from app.config.constants.service import config_node_constants
from app.modules.transformers.blob_storage import BlobStorage, close_shared_session
from app.services.storage_documents import StorageDocumentInfo, StorageDocuments
from app.utils.jwt import generate_jwt

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE_URL", "")
SCOPED_SECRET = os.environ.get("ARTIFACTS_IT_SCOPED_SECRET", "")
STORAGE_TYPE = os.environ.get("ARTIFACTS_IT_STORAGE_TYPE", "local")
ORG_ID = os.environ.get("ARTIFACTS_IT_ORG_ID", "64d0000000000000000a4f01")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(300),
    pytest.mark.skipif(not STORAGE_URL, reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]

logger = logging.getLogger("storage-fingerprint-it")
API = f"{STORAGE_URL}/api/v1/document/internal"


def _config_service() -> MagicMock:
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": SCOPED_SECRET},
        config_node_constants.ENDPOINTS.value: {"cm": {"endpoint": STORAGE_URL}, "storage": {"endpoint": STORAGE_URL}},
        config_node_constants.STORAGE.value: {"storageType": STORAGE_TYPE},
    }
    service = MagicMock()
    service.get_config = AsyncMock(side_effect=lambda key, *a, **kw: values[key])
    return service


@pytest.fixture
async def stack() -> AsyncIterator[tuple[BlobStorage, StorageDocuments, MagicMock]]:
    config = _config_service()
    yield BlobStorage(logger=logger, config_service=config), StorageDocuments(config, logger), config
    await close_shared_session()


async def _create_versioned(config: MagicMock, name: str) -> str:
    """A versioned JSON document (the writers below add versions and overwrite it)."""
    token = await generate_jwt(config, {"orgId": ORG_ID, "scopes": ["storage:token"]})
    form = aiohttp.FormData()
    form.add_field("file", b'{"n": 0}', filename=f"{name}.json", content_type="application/json")
    form.add_field("documentName", name)
    form.add_field("documentPath", f"fingerprint-it/{name}")
    form.add_field("isVersionedFile", "true")
    async with aiohttp.ClientSession() as session, session.post(
        f"{API}/upload", data=form, headers={"Authorization": f"Bearer {token}"}, allow_redirects=False,
    ) as resp:
        doc_id = resp.headers.get("x-document-id")
        body = await resp.json() if resp.status == 200 else {}
        if resp.status == 301:
            location = resp.headers["Location"]
            headers = {"x-ms-blob-type": "BlockBlob"} if STORAGE_TYPE == "azureBlob" else {}
            async with session.put(location, data=b'{"n": 0}', headers=headers, skip_auto_headers={"Content-Type"}) as put:
                assert put.status in (200, 201)
        else:
            assert resp.status == 200, resp.status
            doc_id = doc_id or body.get("_id")
    assert doc_id
    return str(doc_id)


async def _verified(sd: StorageDocuments, doc_id: str) -> StorageDocumentInfo:
    info = await sd.info(ORG_ID, doc_id, verify=True)
    assert info is not None
    return info


async def _post_json(config: MagicMock, path: str, body: dict[str, object]) -> int:
    token = await generate_jwt(config, {"orgId": ORG_ID, "scopes": ["storage:token"]})
    async with aiohttp.ClientSession() as session, session.post(
        f"{API}{path}", json=body, headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        return resp.status


@pytest.mark.asyncio
async def test_p13_every_python_write_path_moves_the_verified_fingerprint(
    stack: tuple[BlobStorage, StorageDocuments, MagicMock],
) -> None:
    blob, sd, config = stack
    doc_id = await _create_versioned(config, f"fp-{uuid.uuid4().hex[:8]}")
    seen: list[str] = []

    async def check(step: str) -> StorageDocumentInfo:
        info = await _verified(sd, doc_id)
        assert info.trusted, f"{step}: the document must be trusted once the write is over ({info.reason})"
        assert info.fingerprint and info.fingerprint not in seen, f"{step}: the fingerprint did not move"
        assert info.object_size is not None
        seen.append(info.fingerprint)
        return info

    await check("created")

    await blob.update_record_buffer(ORG_ID, doc_id, {"id": "r1", "n": 1}, "vr-1")
    await check("update_record_buffer")

    await blob.upload_next_version(ORG_ID, "r1", doc_id, {"id": "r1", "n": 2}, "vr-1")
    await check("upload_next_version")

    # on S3 and Azure `upload_next_version` PUTs to the current object and keeps no history, so the version a rollBack goes back to is
    # made by the route that records one (the same route on every vendor)
    await blob.upload_artifact_version(ORG_ID, doc_id, "v.json", b'{"n": 3}', "application/json")
    await check("upload_artifact_version")

    assert await _post_json(config, f"/{doc_id}/rollBack", {"version": 0, "note": "back"}) == 200
    await check("rollBack")

    if STORAGE_TYPE == "local":
        # a local document has no presigned PUT target: the writer's documented answer, not a skip
        with pytest.raises(Exception, match="not supported for local storage"):
            await blob.get_direct_upload_url(ORG_ID, doc_id)
        return

    before = await _verified(sd, doc_id)
    url = await blob.get_direct_upload_url(ORG_ID, doc_id)
    headers = {"x-ms-blob-type": "BlockBlob"} if STORAGE_TYPE == "azureBlob" else {}
    async with aiohttp.ClientSession() as session:
        async with session.put(url, data=b'{"n": 99}', headers=headers, skip_auto_headers={"Content-Type"}) as put:
            assert put.status in (200, 201)
    after = await check("presigned overwrite")
    assert after.object_etag != before.object_etag

    # a signed GET carries the verified identity: 200 for the object it was verified against, 412 once it was overwritten
    token = await generate_jwt(config, {"orgId": ORG_ID, "scopes": ["storage:token"]})
    async with aiohttp.ClientSession() as session:
        async def signed() -> str:
            async with session.get(f"{API}/{doc_id}/download", headers={"Authorization": f"Bearer {token}"}) as resp:
                assert resp.status == 200
                return (await resp.json())["signedUrl"]

        async with session.get(await signed(), headers={"If-Match": after.if_match_header or ""}) as ok:
            assert ok.status == 200
            assert after.matches_download(ok.headers.get("ETag"))
        await blob.update_record_buffer(ORG_ID, doc_id, {"id": "r1", "n": 100}, "vr-1")
        async with session.get(await signed(), headers={"If-Match": after.if_match_header or ""}) as stale:
            assert stale.status == 412
