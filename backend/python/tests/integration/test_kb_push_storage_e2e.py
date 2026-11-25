"""``storage://`` images in a push, against the real storage routes (MongoDB + local disk / S3 (MinIO) / Azure Blob (Azurite)).

The push service, ``StorageDocuments`` and the real ``Materialiser`` (a spawned child) run; the graph is the recording
fake of the unit harness, because what is under test here is storage: the upload limit, the verified read, the bound
download, the signed-URL hop and a user's own upload. The graph decisions on real Neo4j and ArangoDB are in
``graph_db/test_kb_push_images_real_backends.py``.

Environment: as ``test_storage_fingerprint_e2e.py`` (``ARTIFACTS_IT_STORAGE_URL``, ``ARTIFACTS_IT_SCOPED_SECRET``,
``ARTIFACTS_IT_STORAGE_TYPE`` = local | s3 | azureBlob, ``ARTIFACTS_IT_S3_VHOST=1`` on the MinIO leg, ``SSL_CERT_FILE`` the
throwaway CA). Without ``ARTIFACTS_IT_STORAGE_URL`` the whole module skips, so the offline integration job (no storage
service) passes over it and only the storage job runs it.
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
from yarl import URL

from app.config.constants.service import config_node_constants
from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push import worker as W
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.connectors.sources.localKB.push.image_admission import ImageAdmission
from app.connectors.sources.localKB.push.materialiser import Materialiser
from app.connectors.sources.localKB.push.service import (
    UNTRUSTED,
    KbPushService,
    push_record_key,
)
from app.connectors.sources.localKB.push.spool import Spool
from app.services.graph_db.interface.graph_db_provider import RefAccess
from app.services.storage_documents import StorageDocuments, StorageUploadError
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

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(600),
    pytest.mark.skipif(not STORAGE_URL, reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]


def _config_service() -> MagicMock:
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": SCOPED_SECRET},
        config_node_constants.ENDPOINTS.value: {"cm": {"endpoint": STORAGE_URL}, "storage": {"endpoint": STORAGE_URL}},
        config_node_constants.STORAGE.value: {"storageType": STORAGE_TYPE},
    }
    service = MagicMock()
    service.get_config = AsyncMock(side_effect=lambda key, default=None, *a, **kw: values.get(key, default))
    return service


async def upload_as(user_id: str | None, data: bytes, *, name: str | None = None, mime: str = "image/png", tags: dict[str, str] | None = None) -> str:
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
    if tags:  # what any uploader can do: the push's own tag keys are not reserved
        from app.modules.transformers.blob_storage import _add_custom_metadata_to_form
        _add_custom_metadata_to_form(form, [{"key": k, "value": v} for k, v in tags.items()])
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
    config = _config_service()
    docs = StorageDocuments(config, logger)
    if resolver_override is None and os.environ.get("ARTIFACTS_IT_S3_VHOST") and os.environ.get("STORAGE_IT_S3_VHOST_ADDR"):
        # The virtual-hosted S3 leg: the signed URL names <bucket>.s3.<region>.amazonaws.com, which only the throwaway MinIO
        # answers. The parent's tests pin that name in-process (vhost_pin.py); the spawned child needs the same through its seam.
        resolver_override = {"*.amazonaws.com": os.environ["STORAGE_IT_S3_VHOST_ADDR"]}
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
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{API}/{doc_id}/download", headers={"Authorization": f"Bearer {token}"}) as r:
            assert r.status == 200
            body = await r.read()
        if STORAGE_TYPE == "local":
            return body
        # S3 and Azure answer ``{"signedUrl": ...}``: the document is what that URL serves (no Authorization header on it).
        async with s.get(URL(json.loads(body)["signedUrl"], encoded=True)) as r:
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
    """The real upload route answers 400 (not 413) over the 30 MiB default and ``create`` maps it to 413: exactly the limit
    is stored, limit + 1 is refused with the limit that was read."""
    limit = await p.docs.upload_limit()
    assert limit == 30 << 20, "this cell assumes the default platform setting"
    tmp = tempfile.mkdtemp(prefix="pb")
    try:
        path = os.path.join(tmp, "big.bin")

        async def create(size: int) -> str:
            with open(path, "wb") as f:
                f.truncate(size)
            return await p.docs.create(org_id=ORG_ID, name=f"big-{uuid.uuid4().hex[:8]}", document_path="push-it/big", file_path=path,
                                       extension="bin", content_type="application/octet-stream", tags={})

        assert await create(limit)
        with pytest.raises(StorageUploadError) as e:
            await create(limit + 1)
        assert (e.value.status, e.value.limit_bytes) == (413, limit)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_images_that_inline_past_the_setting_are_413_resolved_before_any_download(p: Pushes) -> None:
    """A body is at most 16 MiB, so only images take a record past the 30 MiB setting: three 11 MiB documents inline to
    about 44 MiB, and the push refuses from the stored sizes alone."""
    limit = await p.docs.upload_limit()
    docs = [await upload_as(USER_ID, os.urandom(11 << 20)) for _ in range(3)]
    with pytest.raises(PushRefusal) as e:
        await p.push(body_with(docs))
    assert (e.value.status, e.value.extra["limit"], e.value.extra["limitBytes"]) == (413, "resolved", limit)
    assert not p.claims


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
        # Real S3 URLs are https; the Azure leg runs Azurite, which serves plain http.
        assert urlsplit(info.object_url).scheme in {"s3": ("https",), "azureBlob": ("http", "https")}[STORAGE_TYPE]
    assert (await p.push(body_with([doc]))).status == "created"


# ---- heal and R5 on real storage ----------------------------------------------------------------------------------

def record_cleanups(p: Pushes) -> list[list[str]]:
    published: list[list[str]] = []

    async def cleanup(org_id, kb_id, ids) -> None:
        published.append(list(ids))

    p.svc._cleanup = cleanup
    return published


async def test_heal_on_real_storage(p: Pushes) -> None:
    """Tags round-trip through the real storage service, and a leaked document is cleaned by the next content change."""
    published = record_cleanups(p)
    await p.push(body_with([], version=1, text="a"))
    e1 = p.graph.record["externalRecordId"]
    await p.push(body_with([], version=2, text="b"))
    e2 = p.graph.record["externalRecordId"]
    info = await p.docs.info(ORG_ID, e2)
    assert info is not None and info.tags.get("previousDocumentId") == e1 and info.tags.get("connectorId") == KB
    published.clear()  # the event for E1 never reached the consumer; E1 is still stored
    await p.push(body_with([], version=3, text="c"))
    assert published == [[e2, e1]]


async def test_user_written_tags_cannot_steer_a_heal_delete(p: Pushes) -> None:
    """A user uploads documents carrying the push's tag keys; the record names one of them; the heal still never reaches the other record's document."""
    published = record_cleanups(p)
    key = push_record_key(ORG_ID, KB, "it-1")
    victim = await upload_as(USER_ID, b"victim", mime="application/octet-stream", tags={"connectorId": KB, "pushRecordKey": "another-records-key"})
    forged = await upload_as(USER_ID, b"{}", mime="application/octet-stream",
                             tags={"connectorId": KB, "pushRecordKey": key, "previousDocumentId": victim})
    await p.push(body_with([], version=1, text="a"))
    p.graph.record["externalRecordId"] = forged
    published.clear()
    r = await p.push(body_with([], version=2, text="b"))
    assert r.status == "updated"
    assert all(victim not in ids for ids in published) and await p.docs.exists(ORG_ID, victim)


async def test_r5_an_image_replaced_in_flight_is_refused_and_the_next_push_stores_the_new_bytes_with_a_matching_fingerprint() -> None:
    doc = await upload_as(USER_ID, PNG)

    async def replace(_task) -> None:
        await overwrite(doc, PNG + b"\x00replaced")

    tmp = tempfile.mkdtemp(prefix="pb")
    p = await build(tmp, before_task=replace)
    try:
        with pytest.raises(PushRefusal) as e:
            await p.push(body_with([doc]))
        assert (e.value.status, e.value.reason) == (422, "IMAGE_FETCH_FAILED") and not p.claims
        p.mat.before_task = None
        created = await p.push(body_with([doc]))
        assert created.status == "created"
        again = await p.push(body_with([doc]))
        assert again.status == "unchanged", "the stored fingerprint is not newer than the stored bytes"
    finally:
        await p.mat.close()
        shutil.rmtree(tmp, ignore_errors=True)
