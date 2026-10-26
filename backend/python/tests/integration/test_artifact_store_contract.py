"""The Python artifact writer and store against the storage service's real artifact routes.

Needs a running storage service (the Node API's ``/api/v1/document`` routes)
backed by the storage under test; ``tests/integration/compose/storage.yml``
starts MongoDB, MinIO and Azurite for it. Environment:

- ``ARTIFACTS_IT_STORAGE_URL``: the storage service origin, e.g. http://127.0.0.1:3000
- ``ARTIFACTS_IT_SCOPED_SECRET``: the scoped JWT secret it verifies storage tokens with
- ``ARTIFACTS_IT_STORAGE_TYPE``: the storage it is configured with: local, s3 or azureBlob
- ``ARTIFACTS_IT_ORG_ID`` (optional): the organization id to write under

Skipped when ``ARTIFACTS_IT_STORAGE_URL`` is not set. The retention test stands a
fake graph in front of the real store: the graph side has its own test against
real engines (``test_revision_retention_e2e.py``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.config.constants.service import Routes, config_node_constants
from app.models.blocks import Block, BlocksContainer, BlockType, ImageMetadata
from app.models.entities import Record, RecordType
from app.modules.artifacts import sidecars as sidecars_module
from app.modules.artifacts import store as store_module
from app.modules.artifacts.models import MANIFEST_PATH
from app.modules.artifacts.retention import RevisionRetentionSweep
from app.modules.artifacts.sidecars import ArtifactSidecars, sidecar_path
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.artifacts.writer import ArtifactWriter, canonical_json
from app.modules.transformers.blob_storage import (
    BlobStorage,
    close_shared_session,
    get_shared_session,
)

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

logger = logging.getLogger("artifact-contract-it")
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(255, -1, -1)) * 4


def _config_service() -> MagicMock:
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": SCOPED_SECRET},
        config_node_constants.ENDPOINTS.value: {"cm": {"endpoint": STORAGE_URL}},
        config_node_constants.STORAGE.value: {"storageType": STORAGE_TYPE},
    }
    service = MagicMock()
    service.get_config = AsyncMock(side_effect=lambda key, *a, **kw: values[key])
    return service


@pytest.fixture
async def blob() -> AsyncIterator[BlobStorage]:
    store_module.ARTIFACT_ROUTES.reset()
    yield BlobStorage(logger=logger, config_service=_config_service())
    await close_shared_session()


def _uri(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _record(vrid: str, blocks: list[Block]) -> Record:
    return Record(
        id=f"rec-{vrid}", org_id=ORG_ID, virtual_record_id=vrid, record_name="contract.pdf",
        record_type=RecordType.FILE, external_record_id="ext", version=1, origin="UPLOAD",
        connector_name=Connectors.KNOWLEDGE_BASE, connector_id="conn",
        block_containers=BlocksContainer(blocks=blocks, block_groups=[]),
    )


def _files_in(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    listed: dict[str, dict[str, Any]] = {}
    for entry in manifest["artifacts"].values():
        for file in entry.get("parts") or entry.get("files") or []:
            listed[file["path"]] = file
        if entry.get("path"):
            listed[entry["path"]] = entry
    return listed


async def test_a_revision_round_trips_through_the_storage_service(blob: BlobStorage) -> None:
    vrid = str(uuid.uuid4())
    rev = uuid.uuid4().hex[:16]
    blocks = [Block(index=i, type=BlockType.TEXT, data=f"paragraph {i} " + "word " * 200) for i in range(12)]
    blocks += [
        Block(index=12, type=BlockType.IMAGE, data={"uri": _uri("image/png", PNG)},
              image_metadata=ImageMetadata(description="a chart")),
        Block(index=13, type=BlockType.IMAGE, data={"uri": _uri("image/jpeg", JPEG)}),
    ]
    record = _record(vrid, blocks)
    store = NodeArtifactStore(blob, logger)

    written = await ArtifactWriter(store, logger, part_max_bytes=4096).write_revision(
        record, ORG_ID, vrid, rev, record.block_containers,
        reconciliation_metadata={"hash_to_block_ids": {"h": [blocks[0].id]}, "block_id_to_index": {blocks[0].id: 0}},
    )

    assert written is not None
    assert written.manifest_ref.root == f"{ORG_ID}/PipesHub/record-revisions/{vrid}/{rev}"
    document_id = written.manifest_ref.document_id
    listed = _files_in(written.manifest.model_dump(mode="json", exclude_none=True))
    assert len([p for p in listed if p.startswith("blocks/")]) > 1
    assert {f"images/{hashlib.sha256(PNG).hexdigest()}.png", f"images/{hashlib.sha256(JPEG).hexdigest()}.jpg"} <= set(listed)

    urls = await store.read_urls(ORG_ID, document_id, [MANIFEST_PATH, *listed])
    stored_manifest = json.loads(await store.get(urls[MANIFEST_PATH]))
    assert stored_manifest["rev"] == rev and _files_in(stored_manifest) == listed
    for path, entry in listed.items():
        data = await store.get(urls[path])
        assert (len(data), hashlib.sha256(data).hexdigest()) == (entry["size"], entry["sha256"]), path
    part = urls["blocks/part-0000.jsonl"]
    assert await store.get(part, byte_range=(0, 9)) == (await store.get(part))[:10]

    again = await store.presign_revision(ORG_ID, vrid, rev, [(MANIFEST_PATH, "application/json")])
    assert again.document_id == document_id
    assert [r["rev"] for r in await store.list_revisions(ORG_ID, vrid)] == [rev]

    assert await store.delete_revision(ORG_ID, document_id) == 1
    assert await store.list_revisions(ORG_ID, vrid) == []


async def test_more_files_than_one_presign_takes_land_in_one_revision(blob: BlobStorage) -> None:
    vrid = str(uuid.uuid4())
    rev = uuid.uuid4().hex[:16]
    blocks = [Block(index=i, type=BlockType.IMAGE, data={"uri": _uri("image/png", PNG + i.to_bytes(2, "big"))})
              for i in range(1001)]
    record = _record(vrid, blocks)
    store = NodeArtifactStore(blob, logger)

    written = await ArtifactWriter(store, logger, upload_concurrency=32).write_revision(
        record, ORG_ID, vrid, rev, record.block_containers,
    )

    assert written is not None
    assert written.manifest.artifacts["images"].count == 1001
    revisions = await store.list_revisions(ORG_ID, vrid)
    assert [r["documentId"] for r in revisions] == [written.manifest_ref.document_id]
    assert await blob.purge_virtual_record_documents(ORG_ID, vrid) == 1
    assert await store.list_revisions(ORG_ID, vrid) == []


async def test_the_stored_record_uploads_through_its_signed_url(blob: BlobStorage) -> None:
    """On S3 and Azure the stored record goes up through a direct-upload signed URL;
    Azure accepts it only with the blob type named, and answers 201."""
    vrid = str(uuid.uuid4())
    record = {"record_name": "contract", "virtual_record_id": vrid, "block_containers": {"blocks": []}}

    document_id, size = await blob.save_record_to_storage(ORG_ID, f"rec-{vrid}", vrid, record)

    assert document_id and size
    if STORAGE_TYPE == "s3":
        # The S3 adapter reads and deletes a stored record by parsing its
        # AWS-style object URL, which an S3-compatible test server does not
        # hand out; the upload is what is checked here.
        return
    headers, endpoint, _ = await blob._get_auth_and_config(ORG_ID)
    session = get_shared_session()
    async with session.get(
        f"{endpoint}{Routes.STORAGE_DOWNLOAD.value.format(documentId=document_id)}", headers=headers
    ) as response:
        assert response.status == 200
        body = await response.read()
    if STORAGE_TYPE != "local":
        from yarl import URL

        async with session.get(URL(json.loads(body)["signedUrl"], encoded=True)) as response:
            assert response.status == 200
            body = await response.read()
    assert json.loads(body)["record"]["record_name"] == "contract"
    assert await blob.purge_virtual_record_documents(ORG_ID, vrid) == 1


async def test_a_sidecar_lands_in_its_revisions_document(blob: BlobStorage) -> None:
    vrid = str(uuid.uuid4())
    rev = uuid.uuid4().hex[:16]
    record = _record(vrid, [Block(index=0, type=BlockType.TEXT, data="one paragraph")])
    store = NodeArtifactStore(blob, logger)
    written = await ArtifactWriter(store, logger).write_revision(record, ORG_ID, vrid, rev, record.block_containers)
    assert written is not None
    payload = {"summary": "a contract", "topics": ["Renewal", "Ünïcode"]}

    with patch.object(sidecars_module, "is_artifact_write_enabled", AsyncMock(return_value=True)):
        assert await ArtifactSidecars(store, MagicMock(), logger).write(
            ORG_ID, vrid, rev, "classification", 1, payload,
        ) is True

    path = sidecar_path("classification", 1)
    document_id = written.manifest_ref.document_id
    urls = await store.read_urls(ORG_ID, document_id, [path, MANIFEST_PATH])
    assert await store.get(urls[path]) == canonical_json(payload)
    # Beside the revision, not in it: the manifest is unchanged.
    assert json.loads(await store.get(urls[MANIFEST_PATH]))["artifacts"].keys() == written.manifest.artifacts.keys()
    assert [r["documentId"] for r in await store.list_revisions(ORG_ID, vrid)] == [document_id]
    assert await store.delete_revision(ORG_ID, document_id) == 1


async def test_retention_retires_old_revisions_through_the_storage_service(blob: BlobStorage) -> None:
    """Revisions neither current nor previous go, tree and document; the two kept still read."""
    vrid = str(uuid.uuid4())
    revs = [uuid.uuid4().hex[:16] for _ in range(4)]
    store = NodeArtifactStore(blob, logger)
    writer = ArtifactWriter(store, logger)
    written = {}
    for i, rev in enumerate(revs):
        record = _record(vrid, [Block(index=0, type=BlockType.TEXT, data=f"version {i}")])
        written[rev] = await writer.write_revision(record, ORG_ID, vrid, rev, record.block_containers)
    graph = MagicMock()
    graph.get_revision_pointers = AsyncMock(return_value=[
        {"virtualRecordId": vrid, "orgId": ORG_ID, "currentRev": revs[3], "previousRev": revs[2]},
    ])
    graph.get_virtual_record_ids_in_use = AsyncMock(return_value=set())
    graph.stage_states_unheld_revisions = AsyncMock(return_value=[])
    graph.get_document = AsyncMock(return_value={"currentRev": revs[3], "previousRev": revs[2]})
    graph.get_virtual_record_holders = AsyncMock(return_value={vrid: [{"contentRev": revs[3], "isDeleted": False}]})
    # Two days on, so the revisions just written are past the grace.
    sweep = RevisionRetentionSweep(graph, store, logger, now_ms=lambda: int(time.time() * 1000) + 48 * 3600 * 1000)

    try:
        _, report = await sweep.run_page(None)

        assert report.revisions_deleted == 2 and report.failed == 0
        assert sorted(r["rev"] for r in await store.list_revisions(ORG_ID, vrid)) == sorted(revs[2:])
        current = written[revs[3]].manifest_ref.document_id
        urls = await store.read_urls(ORG_ID, current, [MANIFEST_PATH])
        assert json.loads(await store.get(urls[MANIFEST_PATH]))["rev"] == revs[3]
    finally:
        await blob.purge_virtual_record_documents(ORG_ID, vrid)
