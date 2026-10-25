"""A ticket's properties.json through the storage service's real artifact routes.

The real ``ArtifactWriter`` stores a ticket's revision; ``properties.json`` is
read back through the signed read URLs and checked against the model, the
reader still rebuilds the stored record from the set, and the storage service
refuses every path that is only like ``properties.json``.

Same environment as ``test_artifact_store_contract.py``:
``ARTIFACTS_IT_STORAGE_URL``, ``ARTIFACTS_IT_SCOPED_SECRET``,
``ARTIFACTS_IT_STORAGE_TYPE``, ``ARTIFACTS_IT_ORG_ID``. Skipped without the URL.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from app.config.constants.arangodb import Connectors
from app.config.constants.service import Routes, config_node_constants
from app.exceptions.storage_exceptions import StorageRequestRejectedError
from app.models.blocks import Block, BlocksContainer, BlockType
from app.models.entities import ItemType, Priority, RecordType, Status, TicketRecord
from app.models.record_metadata import RecordMetadata
from app.modules.artifacts import store as store_module
from app.modules.artifacts.models import MANIFEST_PATH, PROPERTIES_PATH
from app.modules.artifacts.record_backend import ArtifactRecordBackend
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.artifacts.writer import ArtifactWriter, build_revision, canonical_json
from app.modules.record_metadata.builtin import ticket_metadata
from app.modules.transformers.blob_storage import (
    BlobStorage,
    clean_stored_record,
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

logger = logging.getLogger("record-properties-e2e")


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


def _ticket(vrid: str, **fields: Any) -> TicketRecord:  # noqa: ANN401
    blocks = [Block(index=i, type=BlockType.TEXT, data=f"Comment {i}: login fails on Safari ü€😀") for i in range(5)]
    return TicketRecord(
        id=f"rec-{vrid}", org_id=ORG_ID, virtual_record_id=vrid, record_name="PA-1 Login fails",
        record_type=RecordType.TICKET, external_record_id="PA-1", version=1, origin="CONNECTOR",
        connector_name=Connectors.JIRA, connector_id="conn", mime_type="text/blocks",
        block_containers=BlocksContainer(blocks=blocks, block_groups=[]),
        **fields,
    )


TICKET_FIELDS: dict[str, Any] = {
    "status": Status.IN_PROGRESS, "source_status": "In Review — ünïcode", "priority": Priority.HIGHEST,
    "type": ItemType.BUG, "assignee": "Ada Lovelace", "assignee_email": "ada@example.com",
    "reporter_name": "Grace 😀", "reporter_email": "grace@example.com",
    "labels": ["auth", "web", "é" * 512], "resolved_at": 1_760_000_000_123, "due_date": 1_767_225_600_000,
}


async def _write(blob: BlobStorage, record: TicketRecord) -> tuple[NodeArtifactStore, Any]:
    store = NodeArtifactStore(blob, logger)
    rev = uuid.uuid4().hex[:16]
    written = await ArtifactWriter(store, logger).write_revision(
        record, ORG_ID, record.virtual_record_id, rev, record.block_containers,
    )
    assert written is not None
    return store, written


async def test_a_tickets_properties_json_round_trips(blob: BlobStorage) -> None:
    vrid = str(uuid.uuid4())
    record = _ticket(vrid, **TICKET_FIELDS)
    store, written = await _write(blob, record)
    try:
        entry = written.manifest.artifacts["properties"]
        assert entry.path == PROPERTIES_PATH and entry.count == 11

        urls = await store.read_urls(ORG_ID, written.manifest_ref.document_id, [MANIFEST_PATH, PROPERTIES_PATH])
        data = await store.get(urls[PROPERTIES_PATH])
        assert (len(data), hashlib.sha256(data).hexdigest()) == (entry.size, entry.sha256)
        assert data == build_revision(record, record.block_containers).files[PROPERTIES_PATH].data
        assert data == canonical_json(json.loads(data))
        stored_manifest = json.loads(await store.get(urls[MANIFEST_PATH]))
        assert stored_manifest["artifacts"]["properties"]["sha256"] == entry.sha256

        parsed = RecordMetadata.model_validate_json(data)
        assert parsed == ticket_metadata(record)
        assert parsed.values["labels"].value == ["auth", "web", "é" * 512]
        assert parsed.values["reporterName"].value == "Grace 😀"
        assert set(json.loads(data)) == {"schemaId", "values"}
    finally:
        await store.delete_revision(ORG_ID, written.manifest_ref.document_id)


async def test_the_reader_still_rebuilds_the_stored_record(blob: BlobStorage) -> None:
    vrid = str(uuid.uuid4())
    record = _ticket(vrid, **TICKET_FIELDS)
    store, written = await _write(blob, record)
    try:
        signed: list[str] = []
        read_urls = store.read_urls

        async def spy(org_id: str, document_id: str, paths: list[str]) -> Any:  # noqa: ANN401
            signed.extend(paths)
            return await read_urls(org_id, document_id, paths)

        store.read_urls = spy  # type: ignore[method-assign]
        mappings = MagicMock()
        backend = ArtifactRecordBackend(mappings, store)
        lookup = {"record_doc_id": "doc", "currentRev": written.manifest.rev, "storedRev": written.manifest.rev,
                  "manifestDocId": written.manifest_ref.document_id}

        read = await backend.read_record(vrid, ORG_ID, lookup=lookup)

        expected = json.loads(json.dumps(clean_stored_record(record.model_dump(mode="json", exclude_none=True))))
        assert read == expected
        assert PROPERTIES_PATH not in signed
    finally:
        await store.delete_revision(ORG_ID, written.manifest_ref.document_id)


async def test_a_ticket_without_values_and_a_plain_record_store_no_properties(blob: BlobStorage) -> None:
    vrid = str(uuid.uuid4())
    record = _ticket(vrid, status=Status.UNKNOWN, priority=Priority.UNKNOWN)
    store, written = await _write(blob, record)
    try:
        assert "properties" not in written.manifest.artifacts
        with pytest.raises(aiohttp.ClientError, match="404"):
            urls = await store.read_urls(ORG_ID, written.manifest_ref.document_id, [PROPERTIES_PATH])
            await store.get(urls[PROPERTIES_PATH])
    finally:
        await store.delete_revision(ORG_ID, written.manifest_ref.document_id)


LOOKALIKES = [
    "Properties.json", "properties.JSON", "properties.json/", "/properties.json", "./properties.json",
    "../properties.json", "x/../properties.json", "properties.json/..", "properties.json.", "properties.jsonl",
    "properties.json ", " properties.json", "properties.json\n", "properties.json\\", "properties\\.json",
    "properties.json\x00", "enrich/properties.json", "blocks/properties.json", "propertiesXjson",
    "properties%2ejson", "propertieſ.json", "properties.ĵson", "ｐroperties.json",
]


@pytest.mark.parametrize("path", LOOKALIKES, ids=[repr(p) for p in LOOKALIKES])
async def test_presign_refuses_every_lookalike(blob: BlobStorage, path: str) -> None:
    store = NodeArtifactStore(blob, logger)
    with pytest.raises(StorageRequestRejectedError) as info:
        await store.presign_revision(ORG_ID, str(uuid.uuid4()), uuid.uuid4().hex[:16], [(path, "application/json")])
    assert "400" in str(info.value)


async def test_read_urls_and_file_routes_refuse_lookalikes(blob: BlobStorage) -> None:
    vrid = str(uuid.uuid4())
    record = _ticket(vrid, **TICKET_FIELDS)
    store, written = await _write(blob, record)
    document_id = written.manifest_ref.document_id
    try:
        for path in LOOKALIKES:
            with pytest.raises(StorageRequestRejectedError, match="400"):
                await store.read_urls(ORG_ID, document_id, [path])
        headers, endpoint, _ = await blob._get_auth_and_config(ORG_ID)
        session = get_shared_session()
        base = f"{endpoint}{Routes.STORAGE_ARTIFACT_SET.value.format(documentId=document_id)}/files/"
        from yarl import URL

        # Express decodes the wildcard before the check: %2e is the exact name, never a way around it.
        async with session.get(URL(base + "properties%2ejson", encoded=True), headers=headers) as response:
            assert "VALIDATION_ERROR" not in await response.text()
        for raw in ["..%2fproperties.json", "%2e%2e/properties.json", "Properties.json",
                    "properties.json%00", "properties.json%2f", "x%2f..%2fproperties.json"]:
            async with session.get(URL(base + raw, encoded=True), headers=headers) as response:
                body = await response.text()
                # The path check runs before anything else; a decoded lookalike never names a file.
                assert (response.status, "VALIDATION_ERROR" in body) == (400, True) or response.status == 404, \
                    (raw, response.status, body[:200])
        if STORAGE_TYPE == "local":
            # The files route serves local storage only; on S3/Azure the read URLs are the vendor's.
            async with session.get(URL(base + "properties.json", encoded=True), headers=headers) as response:
                assert response.status == 200
                assert await response.read() == build_revision(record, record.block_containers).files[
                    PROPERTIES_PATH].data
    finally:
        await store.delete_revision(ORG_ID, document_id)


async def test_the_exact_name_is_accepted_beside_the_rest(blob: BlobStorage) -> None:
    store = NodeArtifactStore(blob, logger)
    plan = await store.presign_revision(
        ORG_ID, str(uuid.uuid4()), uuid.uuid4().hex[:16],
        [(PROPERTIES_PATH, "application/json"), ("record.json", "application/json")],
    )
    assert set(plan.files) == {PROPERTIES_PATH, "record.json"}
    await store.delete_revision(ORG_ID, plan.document_id)


async def test_a_storage_service_that_does_not_know_the_file_gets_the_set_without_it(
    blob: BlobStorage, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real router's 400 for a path it does not hold is what a storage service older
    # than properties.json answers for properties.json: stand it in with a lookalike name.
    from app.modules.artifacts import writer as writer_module

    monkeypatch.setattr(writer_module, "PROPERTIES_PATH", "Properties.json")
    store_module.PROPERTIES_FILE.reset()
    vrid = str(uuid.uuid4())
    record = _ticket(vrid, **TICKET_FIELDS)
    try:
        store, written = await _write(blob, record)
        try:
            assert not store_module.PROPERTIES_FILE.available
            assert "properties" not in written.manifest.artifacts
            urls = await store.read_urls(ORG_ID, written.manifest_ref.document_id, [MANIFEST_PATH, "record.json"])
            assert set(urls) == {MANIFEST_PATH, "record.json"}
        finally:
            await store.delete_revision(ORG_ID, written.manifest_ref.document_id)
    finally:
        store_module.PROPERTIES_FILE.reset()

