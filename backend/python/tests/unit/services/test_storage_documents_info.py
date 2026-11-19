"""The verified document read of the storage service (``StorageDocuments.info``) and its parser."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.services.storage_documents import (
    StorageDocumentInfo,
    StorageDocuments,
    StorageUploadError,
    parse_document_info,
)

DOC = {"_id": "d1", "isDeleted": False, "sizeInBytes": 10, "storageVendor": "s3"}


def fingerprint(**over: object) -> dict[str, Any]:
    fp = {
        "version": 1,
        "value": "a" * 32,
        "settled": True,
        "verified": True,
        "object": {"size": 5, "etag": "abc123", "lastModified": "2026-01-01T00:00:00.000Z"},
        "objectMissing": False,
        "objectUnverifiable": False,
        "trusted": True,
    }
    fp.update(over)
    return fp


def body(doc: dict[str, Any] | None = None, fp: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(doc or DOC)
    if fp is not None:
        out["storageFingerprint"] = fp
    return out


def test_p1_full_parse_of_a_verified_answer() -> None:
    info = parse_document_info(body(fp=fingerprint()), verify=True)
    assert info == StorageDocumentInfo(
        document_id="d1", is_deleted=False, awaiting_direct_upload=False, declared_size=10,
        fingerprint="a" * 32, trusted=True, object_size=5, object_etag="abc123",
        object_unverifiable=False, reason=None, is_artifact_set=False, storage_vendor="s3",
        settled=True, untrusted_reason=None,
    )


S3_DOC = {**DOC, "initiatorUserId": "u1", "mimeType": "image/png", "retiredAt": "2026-01-01", "s3": {"url": "https://b.s3.eu-west-1.amazonaws.com/k"}}


def test_u2_new_fields_are_parsed() -> None:
    """Mongo returns ``initiatorUserId`` as a string and Postgres maps the same names (``mongo.ts:46,270-299``, ``pg.ts:123-150``)."""
    info = parse_document_info(body(S3_DOC, fingerprint()), verify=True)
    assert (info.initiator_user_id, info.mime_type, info.retired, info.object_url) == ("u1", "image/png", True, "https://b.s3.eu-west-1.amazonaws.com/k")
    plain = parse_document_info(body(), verify=True)
    assert (plain.initiator_user_id, plain.mime_type, plain.retired, plain.object_url) == (None, None, False, None)


def test_object_url_follows_the_vendor() -> None:
    azure = {**DOC, "storageVendor": "azureBlob", "azureBlob": {"url": "https://a.blob.core.windows.net/c/k"}, "s3": {"url": "https://wrong"}}
    assert parse_document_info(body(azure), verify=False).object_url == "https://a.blob.core.windows.net/c/k"
    local = {**DOC, "storageVendor": "local", "local": {"url": "file:///x"}}
    assert parse_document_info(body(local), verify=False).object_url is None


@pytest.mark.parametrize("fp,reason", [
    (None, "no_fingerprint"), (fingerprint(version=99), "version"), (fingerprint(objectUnverifiable=True, trusted=False), "unverifiable"),
    (fingerprint(reason="clock_unavailable", trusted=False), "clock"), (fingerprint(settled=False, trusted=False), "open_writer"),
    (fingerprint(), None),
])
def test_untrusted_reason(fp, reason) -> None:
    assert parse_document_info(body(fp=fp), verify=True).untrusted_reason == reason


def test_p2_missing_storage_fingerprint_is_untrusted() -> None:
    info = parse_document_info(body(), verify=True)
    assert info is not None and info.trusted is False and info.fingerprint is None and info.object_size is None


def test_p3_an_unverified_answer_or_a_server_that_says_untrusted_is_never_trusted() -> None:
    assert parse_document_info(body(fp=fingerprint()), verify=False).trusted is False  # type: ignore[union-attr]
    assert parse_document_info(body(fp=fingerprint(trusted=False)), verify=True).trusted is False  # type: ignore[union-attr]
    assert parse_document_info(body(fp=fingerprint(verified=False)), verify=True).trusted is False  # type: ignore[union-attr]
    assert parse_document_info(body(fp=fingerprint(object=None)), verify=True).trusted is False  # type: ignore[union-attr]


def test_p4_a_fingerprint_version_other_than_1_is_untrusted() -> None:
    info = parse_document_info(body(fp=fingerprint(version=2)), verify=True)
    assert info is not None and info.trusted is False and info.fingerprint is None


def test_p5_awaiting_direct_upload_and_artifact_set_are_reported() -> None:
    info = parse_document_info(body({**DOC, "awaitingDirectUpload": True, "artifactSet": {"rev": "r"}}, fingerprint(trusted=False)), verify=True)
    assert info is not None and info.awaiting_direct_upload is True and info.is_artifact_set is True and info.trusted is False


def test_p6_object_unverifiable_is_reported_with_its_reason() -> None:
    fp = fingerprint(trusted=False, object=None, objectUnverifiable=True, reason="unparseable_storage_url")
    info = parse_document_info(body(fp=fp), verify=True)
    assert info is not None and info.object_unverifiable is True and info.reason == "unparseable_storage_url" and info.trusted is False


def test_p9_a_document_wrapped_body_is_accepted() -> None:
    info = parse_document_info({"document": DOC, "storageFingerprint": fingerprint()}, verify=True)
    assert info is not None and info.document_id == "d1" and info.trusted is True


def test_p9b_a_body_without_a_document_is_none() -> None:
    assert parse_document_info({"nothing": 1}, verify=False) is None
    assert parse_document_info([], verify=False) is None


def test_p10_the_size_comes_from_the_object_not_the_declared_size() -> None:
    info = parse_document_info(body(fp=fingerprint(object={"size": 5, "etag": "x", "lastModified": None})), verify=True)
    assert info is not None and info.declared_size == 10 and info.object_size == 5


def test_p11_if_match_header_is_one_quoted_etag_for_object_stores_and_none_for_local() -> None:
    s3 = parse_document_info(body(fp=fingerprint(object={"size": 1, "etag": '"abc"', "lastModified": None})), verify=True)
    assert s3 is not None and s3.object_etag == "abc" and s3.if_match_header == '"abc"'
    local = parse_document_info(body({**DOC, "storageVendor": "local"}, fingerprint(object={"size": 1, "etag": "d" * 40, "lastModified": None})), verify=True)
    assert local is not None and local.if_match_header is None and local.object_etag == "d" * 40
    unverified = parse_document_info(body(fp=fingerprint(object=None, trusted=False)), verify=True)
    assert unverified is not None and unverified.if_match_header is None


def test_p11b_matches_download_compares_the_normalised_etag_and_never_a_weak_one() -> None:
    info = parse_document_info(body(fp=fingerprint()), verify=True)
    assert info is not None
    assert info.matches_download('"abc123"') is True
    assert info.matches_download("abc123") is True
    assert info.matches_download('"other"') is False
    assert info.matches_download('W/"abc123"') is False
    assert info.matches_download(None) is False


class _Storage:
    """A real local HTTP server standing in for the storage service."""

    def __init__(self, status: int = 200, payload: object = None) -> None:
        self.status = status
        self.payload = payload
        self.queries: list[dict[str, str]] = []

    async def handler(self, request: web.Request) -> web.Response:
        self.queries.append(dict(request.query))
        return web.json_response(self.payload, status=self.status)


async def _info(storage: _Storage, *, verify: bool) -> StorageDocumentInfo | None:
    app = web.Application()
    app.router.add_get("/api/v1/document/internal/{doc_id}", storage.handler)
    async with TestServer(app) as server:
        sd = StorageDocuments(AsyncMock(), logging.getLogger("t"))
        with patch.object(StorageDocuments, "_storage_url", AsyncMock(return_value=str(server.make_url("")).rstrip("/"))), \
             patch("app.services.storage_documents.generate_jwt", AsyncMock(return_value="tok")):
            return await sd.info("org1", "d1", verify=verify)


async def test_p7_verify_adds_the_query_parameter_and_only_then() -> None:
    storage = _Storage(payload=body(fp=fingerprint()))
    info = await _info(storage, verify=True)
    assert info is not None and info.trusted is True
    assert storage.queries == [{"verify": "1"}]
    storage2 = _Storage(payload=body(fp=fingerprint()))
    info2 = await _info(storage2, verify=False)
    assert info2 is not None and info2.trusted is False
    assert storage2.queries == [{}]


@pytest.mark.parametrize("status", [404, 410])
async def test_p8_a_missing_or_gone_document_is_none(status: int) -> None:
    assert await _info(_Storage(status=status, payload={}), verify=True) is None


@pytest.mark.parametrize("status", [500, 503, 401])
async def test_p8b_any_other_status_raises(status: int) -> None:
    with pytest.raises(StorageUploadError):
        await _info(_Storage(status=status, payload={}), verify=True)


async def test_p8c_a_transport_failure_raises() -> None:
    sd = StorageDocuments(AsyncMock(), logging.getLogger("t"))
    with patch.object(StorageDocuments, "_storage_url", AsyncMock(return_value="http://127.0.0.1:9")), \
         patch("app.services.storage_documents.generate_jwt", AsyncMock(return_value="tok")), \
         pytest.raises(StorageUploadError):
        await sd.info("org1", "d1", verify=True)


async def test_p12_exists_is_unchanged() -> None:
    storage = _Storage(payload={"document": {"_id": "d1", "isDeleted": True}})
    app = web.Application()
    app.router.add_get("/api/v1/document/internal/{doc_id}", storage.handler)
    async with TestServer(app) as server:
        sd = StorageDocuments(AsyncMock(), logging.getLogger("t"))
        with patch.object(StorageDocuments, "_storage_url", AsyncMock(return_value=str(server.make_url("")).rstrip("/"))), \
             patch("app.services.storage_documents.generate_jwt", AsyncMock(return_value="tok")):
            assert await sd.exists("org1", "d1") is False
            storage.payload = {"_id": "d1", "isDeleted": False}
            assert await sd.exists("org1", "d1") is True


# The document row Node writes for an upload (``storage.controller.ts:473-484``): the type is the extension, with a
# leading dot; ``mimeType`` exists in the schema (``document.schema.ts:114``) but no writer sets it.
@pytest.mark.parametrize(("extension", "mime"), [
    (".png", "image/png"), (".PNG", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".gif", "image/gif"),
    (".webp", "image/webp"), (".svg", "image/svg+xml"), (".pdf", None), (".bin", None), ("", None), (None, None),
])
def test_the_mime_type_comes_from_the_extension_like_nodes_upload_rows(extension: str | None, mime: str | None) -> None:
    doc = {**DOC, **({} if extension is None else {"extension": extension})}
    info = parse_document_info({"document": doc}, verify=False)
    assert info is not None and info.mime_type == mime


def test_a_stored_mime_type_is_used_only_when_there_is_no_extension() -> None:
    assert parse_document_info({"document": {**DOC, "mimeType": "image/gif"}}, verify=False).mime_type == "image/gif"
    assert parse_document_info({"document": {**DOC, "extension": ".png", "mimeType": "application/pdf"}}, verify=False).mime_type == "image/png"


async def test_info_many_stops_its_sibling_lookups_when_one_fails() -> None:
    """The first failure leaves no lookup running behind it (``gather`` alone would let them outlive the session)."""
    import asyncio

    started = asyncio.Event()

    async def handler(request: web.Request) -> web.Response:
        if request.match_info["doc_id"] == "bad":
            await started.wait()  # the slow lookup is in flight when this one fails
            return web.json_response({}, status=500)
        started.set()
        await asyncio.sleep(30)
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/api/v1/document/internal/{doc_id}", handler)
    async with TestServer(app) as server:
        sd = StorageDocuments(AsyncMock(), logging.getLogger("t"))
        with patch.object(StorageDocuments, "_storage_url", AsyncMock(return_value=str(server.make_url("")).rstrip("/"))), \
             patch("app.services.storage_documents.generate_jwt", AsyncMock(return_value="tok")), \
             pytest.raises(StorageUploadError):
            await sd.info_many("org1", ["bad", "slow"], verify=True)
        lookups = [t for t in asyncio.all_tasks() if t.get_coro().__qualname__.endswith("info_many.<locals>.one")]
        assert [t for t in lookups if not t.done()] == []
