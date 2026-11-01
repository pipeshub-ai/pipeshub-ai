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
    )


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
