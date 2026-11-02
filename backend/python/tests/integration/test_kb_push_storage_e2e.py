"""``storage://`` images in a push, against the real storage routes (MongoDB + local disk / S3 (MinIO) / Azure Blob (Azurite)).

The push service, ``StorageDocuments`` and the real ``Materialiser`` (a spawned child) run; the graph is the recording
fake of the unit harness, because what is under test here is storage: the upload limit, the verified read, the bound
download, the signed-URL hop and a user's own upload. The graph decisions on real Neo4j and ArangoDB are in
``graph_db/test_kb_push_images_real_backends.py``.

Environment: as ``test_storage_fingerprint_e2e.py`` (``ARTIFACTS_IT_STORAGE_URL``, ``ARTIFACTS_IT_SCOPED_SECRET``,
``ARTIFACTS_IT_STORAGE_TYPE`` = local | s3 | azureBlob, ``ARTIFACTS_IT_S3_VHOST=1`` on the MinIO leg, ``SSL_CERT_FILE`` the
throwaway CA). A missing URL fails the module's fixture: the CI job always sets it.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import tempfile
import uuid
from collections import Counter
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlsplit

import aiohttp
import pytest

from app.config.constants.service import config_node_constants
from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push import worker as W
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.connectors.sources.localKB.push.image_admission import ImageAdmission
from app.connectors.sources.localKB.push.materialiser import Materialiser
from app.connectors.sources.localKB.push.service import UNTRUSTED, KbPushService
from app.connectors.sources.localKB.push.spool import Spool
from app.services.graph_db.interface.graph_db_provider import RefAccess
from app.services.storage_documents import StorageDocuments
from app.utils.jwt import generate_jwt
from tests.unit.connectors.sources.localKB.push.image_harness import FakeGraph

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE_URL", "")
SCOPED_SECRET = os.environ.get("ARTIFACTS_IT_SCOPED_SECRET", "")
STORAGE_TYPE = os.environ.get("ARTIFACTS_IT_STORAGE_TYPE", "local")
ORG_ID = os.environ.get("ARTIFACTS_IT_ORG_ID", "64d0000000000000000a4f01")
USER_ID = "64d0000000000000000a4f10"
OTHER_USER = "64d0000000000000000a4f11"
KB = "kb-it"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
API = f"{STORAGE_URL}/api/v1/document/internal"
logger = logging.getLogger("kb-push-storage-it")

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]


def _config_service() -> MagicMock:
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": SCOPED_SECRET},
        config_node_constants.ENDPOINTS.value: {"cm": {"endpoint": STORAGE_URL}, "storage": {"endpoint": STORAGE_URL}},
        config_node_constants.STORAGE.value: {"storageType": STORAGE_TYPE},
    }
    service = MagicMock()
    service.get_config = AsyncMock(side_effect=lambda key, default=None, *a, **kw: values.get(key, default))
    return service


async def upload_as(user_id: str | None, data: bytes, *, name: str | None = None, mime: str = "image/png") -> str:
    """Upload a document the way a user's session does: a service token that carries ``userId`` sets ``initiatorUserId``
    (``storage/utils/utils.ts:641-649``, ``storage.upload.service.ts:307-313``)."""
    claims = {"orgId": ORG_ID, "scopes": ["storage:token"], **({"userId": user_id} if user_id else {})}
    token = await generate_jwt(_config_service(), claims)
    name = name or f"it-{uuid.uuid4().hex[:8]}"
    form = aiohttp.FormData()
    form.add_field("file", data, filename=f"{name}.png", content_type=mime)
    form.add_field("documentName", name)
    form.add_field("documentPath", f"push-it/{name}")
    form.add_field("isVersionedFile", "false")
    async with aiohttp.ClientSession() as session, session.post(f"{API}/upload", data=form, headers={"Authorization": f"Bearer {token}"}, allow_redirects=False) as resp:
        doc_id = resp.headers.get("x-document-id")
        if resp.status in (301, 302, 307, 308):
            headers = {"x-ms-blob-type": "BlockBlob"} if STORAGE_TYPE == "azureBlob" else {}
            async with session.put(resp.headers["Location"], data=data, headers=headers, skip_auto_headers={"Content-Type"}) as put:
                assert put.status in (200, 201), put.status
        else:
            assert resp.status == 200, resp.status
            doc_id = doc_id or (await resp.json()).get("_id")
    assert doc_id
    return doc_id


async def overwrite(doc_id: str, data: bytes) -> None:
    token = await generate_jwt(_config_service(), {"orgId": ORG_ID, "scopes": ["storage:token"]})
    async with aiohttp.ClientSession() as session:
        form = aiohttp.FormData()
        form.add_field("file", data, filename="x.png", content_type="image/png")
        async with session.put(f"{API}/{doc_id}/buffer", data=form, headers={"Authorization": f"Bearer {token}"}) as resp:
            assert resp.status in (200, 201), resp.status


def body_with(doc_ids: list[str], *, ext: str = "it-1", version: int = 1, text: str = "t") -> bytes:
    blocks = [{"index": 0, "type": "text", "format": "txt", "data": text}]
    blocks += [{"index": i + 1, "type": "image", "format": "base64", "data": {"uri": f"storage://{d}"}} for i, d in enumerate(doc_ids)]
    return json.dumps({"recordName": ext, "mimeType": "application/blocks", "externalRecordId": ext, "version": version, "blocksFormat": 1,
                       "blocks": {"blocks": blocks}}).encode()


class Pushes:
    def __init__(self, svc: KbPushService, graph: FakeGraph, docs: StorageDocuments, mat: Materialiser, claims: list) -> None:
        self.svc, self.graph, self.docs, self.mat, self.claims = svc, graph, docs, mat, claims

    async def push(self, body: bytes, user_id: str = USER_ID) -> object:
        return await self.svc.push(kb_id=KB, user_id=user_id, org_id=ORG_ID, body=body, folder_id=None)


async def build(tmp: str, *, resolver_override: dict[str, str] | None = None, before_task=None) -> Pushes:
    if not STORAGE_URL:
        pytest.fail("ARTIFACTS_IT_STORAGE_URL is not set: this suite needs the storage service (CI starts it)")
    config = _config_service()
    docs = StorageDocuments(config, logger)
    mat = Materialiser(resolver_override=resolver_override, ca_file=os.environ.get("SSL_CERT_FILE") if resolver_override else None,
                       allow_loopback=bool(resolver_override or STORAGE_URL.startswith("http://127.0.0.1")), before_task=before_task)
    graph = FakeGraph()
    claims: list = []
    worker = MagicMock()
    worker.validate = AsyncMock(side_effect=lambda i, o, schema_id, schema: W.validate_body(open(i, "rb").read(), schema_id=schema_id, schema=schema, out_path=o))
    svc = KbPushService(graph_provider=graph, kb_service=MagicMock(), processor_for_kb=AsyncMock(), schema_registry=MagicMock(), storage=docs,
                        worker=worker, spool=Spool(tmp), publish_event=AsyncMock(return_value=True), sleep=AsyncMock(),
                        materialiser=mat, image_admission=ImageAdmission())

    async def claim(processor, existing, action, v, key, kb_id, org_id, user_id, parent, new_doc, revision) -> object:
        claims.append((action, new_doc))
        graph.record = {**(existing or {"id": key, "orgId": org_id, "connectorId": kb_id, "indexingStatus": "COMPLETED"}), "version": v.version,
                        "externalRecordId": new_doc or existing["externalRecordId"],
                        "externalRevisionId": revision or C.revision_id(v.digest, C.parse_revision_id(existing["externalRevisionId"])[2])}
        return MagicMock(event="newRecord", published=True), None, None, None

    svc._claim_and_write = claim
    svc._processor = AsyncMock()
    svc._cleanup = AsyncMock()
    svc._cleanup_unless_referenced = AsyncMock()
    return Pushes(svc, graph, docs, mat, claims)


@pytest.fixture
async def p() -> AsyncIterator[Pushes]:
    tmp = tempfile.mkdtemp(prefix="pb")
    pushes = await build(tmp)
    yield pushes
    await pushes.mat.close()
    shutil.rmtree(tmp, ignore_errors=True)


async def stored_text(p: Pushes, doc_id: str) -> bytes:
    token = await generate_jwt(_config_service(), {"orgId": ORG_ID, "scopes": ["storage:token"]})
    async with aiohttp.ClientSession() as s, s.get(f"{API}/{doc_id}/download", headers={"Authorization": f"Bearer {token}"}) as r:
        assert r.status == 200
        return await r.read()


async def test_materialised_document_on_real_storage(p: Pushes) -> None:
    doc = await upload_as(USER_ID, PNG)
    r = await p.push(body_with([doc]))
    assert r.status == "created"
    stored = await stored_text(p, p.graph.record["externalRecordId"])
    assert stored.count(b"data:image/png;base64," + base64.b64encode(PNG)) == 1 and b"storage://" not in stored
    assert C.content_rev(stored) == r.content_rev


async def test_own_unlinked_upload_accepted(p: Pushes) -> None:
    assert (await p.push(body_with([await upload_as(USER_ID, PNG)]))).status == "created"


async def test_other_users_unlinked_upload_denied(p: Pushes) -> None:
    with pytest.raises(PushRefusal) as e:
        await p.push(body_with([await upload_as(OTHER_USER, PNG)]))
    assert e.value.reason == "IMAGE_REF_INVALID"


async def test_linked_to_unreadable_record_denied_though_uploaded_by_caller(p: Pushes) -> None:
    doc = await upload_as(USER_ID, PNG)
    p.graph.access = RefAccess(frozenset({doc}), frozenset())
    with pytest.raises(PushRefusal) as e:
        await p.push(body_with([doc]))
    assert e.value.reason == "IMAGE_REF_INVALID"


async def test_oversize_upload_status_on_real_storage(p: Pushes) -> None:
    """31 MiB against the 30 MiB default: the real route answers 400 (not 413), and the push surfaces 413 ``resolved``;
    exactly the limit is stored, limit + 1 is refused."""
    limit = await p.docs.upload_limit()
    assert limit == 30 << 20, "this cell assumes the default platform setting"
    filler = {"index": 0, "type": "text", "format": "txt", "data": "x" * (15 << 20)}

    def text_of(size: int) -> bytes:
        blocks = [dict(filler, data="x" * (size // 2)), {"index": 1, "type": "text", "format": "txt", "data": "y" * (size - size // 2)}]
        return json.dumps({"recordName": "big", "mimeType": "application/blocks", "externalRecordId": f"big-{size}", "version": 1, "blocksFormat": 1,
                           "blocks": {"blocks": blocks}}).encode()

    with pytest.raises(PushRefusal) as e:
        await p.push(text_of(31 << 20))
    assert (e.value.status, e.value.extra["limit"], e.value.extra["limitBytes"]) == (413, "resolved", limit)


async def test_image_replaced_between_info_and_download_fails_bound() -> None:
    doc = await upload_as(USER_ID, PNG)

    async def replace(_task) -> None:
        await overwrite(doc, PNG + b"\x00replaced")

    tmp = tempfile.mkdtemp(prefix="pb")
    p = await build(tmp, before_task=replace)
    try:
        with pytest.raises(PushRefusal) as e:
            await p.push(body_with([doc]))
        assert (e.value.status, e.value.reason) == (422, "IMAGE_FETCH_FAILED")
        assert not p.claims
    finally:
        await p.mat.close()
        shutil.rmtree(tmp, ignore_errors=True)


async def test_image_replaced_after_download_heals_on_the_next_push(p: Pushes) -> None:
    doc = await upload_as(USER_ID, PNG)
    await p.push(body_with([doc], version=1))
    await overwrite(doc, PNG + b"\x00new")
    r = await p.push(body_with([doc], version=2))
    assert r.content_changed is True and [a for a, _ in p.claims] == ["create", "content"]


async def test_real_storage_download_failure(p: Pushes) -> None:
    missing = uuid.uuid4().hex[:24]
    with pytest.raises(PushRefusal) as e:
        await p.push(body_with([missing]))
    assert e.value.reason == "IMAGE_REF_INVALID"  # absent: the uniform denial, never "not found"


async def test_untrusted_count_by_reason_is_printed_and_no_unexpected_reason_for_an_idle_document(p: Pushes) -> None:
    UNTRUSTED.clear()
    doc = await upload_as(USER_ID, PNG)
    for v in range(1, 6):
        await p.push(body_with([doc], version=v))
    print(f"untrusted by reason ({STORAGE_TYPE}): {dict(Counter(UNTRUSTED))}")
    assert not UNTRUSTED, f"an idle healthy document must be trusted, saw {dict(UNTRUSTED)}"


async def test_image_replaced_through_each_write_path(p: Pushes) -> None:
    """The leg's own vendor (local, S3 or Azure per ``ARTIFACTS_IT_STORAGE_TYPE``): same bytes rewritten is metadata, new bytes is content."""
    doc = await upload_as(USER_ID, PNG)
    await p.push(body_with([doc], version=1))
    await overwrite(doc, PNG)
    r2 = await p.push(body_with([doc], version=2))
    assert r2.content_changed is False
    await overwrite(doc, PNG + b"\x01")
    r3 = await p.push(body_with([doc], version=3))
    assert r3.content_changed is True


async def test_signed_url_host_set_on_real_s3_and_azure(p: Pushes) -> None:
    """The hop's host is the stored object's host on the S3 and Azure legs (and none on local): the pin accepts the real signed URLs."""
    doc = await upload_as(USER_ID, PNG)
    info = (await p.docs.info_many(ORG_ID, [doc], verify=True))[doc]
    assert info.storage_vendor == {"local": "local", "s3": "s3", "azureBlob": "azureBlob"}[STORAGE_TYPE]
    if STORAGE_TYPE != "local":
        assert urlsplit(info.object_url).scheme == "https" or os.environ.get("ARTIFACTS_IT_S3_VHOST")
    assert (await p.push(body_with([doc]))).status == "created"
