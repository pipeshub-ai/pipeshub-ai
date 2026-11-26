"""
The validator the record stream route answers with follows the stored bytes.

The route's ``ETag`` is derived from the storage service's verified fingerprint of the stored object
(``app.utils.content_tag``), so a 304 can only be wrong if some write moves the bytes and not the
fingerprint. Each write path below changes the bytes of a stored document and must change the tag; a
read in between must not. The writes run on every configured backend (local, and object stores where
configured): a backend-handled write, the storage service's next-version route used with a scoped storage
token and no update of the record (the record update fails in that state, and the old tag must then get
the new bytes, never a 304), a rollback, and the presigned upload an object store uses for every upload.
"""

from __future__ import annotations

import pytest
import requests

from app.services.storage_documents import StorageDocumentInfo, parse_document_info
from app.utils.content_tag import content_tag
from storage_client import StorageClient

RECORD_ID = "tag-probe-record"


def _doc_id(body: dict) -> str:
    return str(body.get("_id") or body.get("id") or body.get("documentId"))


def _info(sc: StorageClient, doc_id: str) -> StorageDocumentInfo:
    resp = requests.get(
        sc._url(f"/{doc_id}"),
        params={"verify": "1"},
        headers=sc._json_headers(),
        timeout=sc._c.timeout_seconds,
    )
    assert resp.status_code == 200, f"info failed ({resp.status_code}): {resp.text}"
    info = parse_document_info(resp.json(), verify=True)
    assert info is not None
    # An untrusted answer gives no tag and so no 304 at all: the probe would pass for the wrong reason.
    assert info.trusted, f"the storage service gave no trusted fingerprint: {info.untrusted_reason}"
    return info


def _tag(sc: StorageClient, doc_id: str) -> str:
    tag = content_tag(RECORD_ID, _info(sc, doc_id), "original")
    assert tag, "no tag for a trusted fingerprint"
    return tag


@pytest.mark.integration
@pytest.mark.storage
def test_a_replace_of_equal_size_changes_the_tag_and_a_read_does_not(sc: StorageClient):
    resp = sc.upload(file_content=b"A" * 64, file_name="tag.txt", document_name="Tag probe", is_versioned=False)
    assert resp.status_code == 200, resp.text
    doc_id = _doc_id(resp.json())
    try:
        first = _tag(sc, doc_id)
        assert _tag(sc, doc_id) == first, "a read moved the tag"
        assert sc.update_buffer(doc_id, b"B" * 64, "tag.txt").status_code == 200  # the same size
        second = _tag(sc, doc_id)
        assert second != first, "equal-size replacement kept the tag: a client would be told 304 for new bytes"
        assert _tag(sc, doc_id) == second
    finally:
        sc.delete_document(doc_id)


@pytest.mark.integration
@pytest.mark.storage
def test_the_next_version_route_without_a_record_update_changes_the_tag(sc: StorageClient):
    resp = sc.upload(file_content=b"v0-bytes", file_name="tag.txt", document_name="Tag probe versioned", is_versioned=True)
    assert resp.status_code == 200, resp.text
    doc_id = _doc_id(resp.json())
    try:
        before = _tag(sc, doc_id)
        # What the knowledge base replace does first; the Python record update that should follow is never made.
        assert sc.upload_next_version(doc_id, b"v1-bytes", "tag.txt").status_code == 200
        after = _tag(sc, doc_id)
        assert after != before, "a new stored version kept the old tag: the stream would answer 304 with stale bytes"
        buf = sc.get_document_buffer(doc_id)
        assert buf.status_code == 200 and buf.content == b"v1-bytes"
    finally:
        sc.delete_document(doc_id)


@pytest.mark.integration
@pytest.mark.storage
def test_a_rollback_changes_the_tag(sc: StorageClient):
    resp = sc.upload(file_content=b"first-bytes", file_name="tag.txt", document_name="Tag probe rollback", is_versioned=True)
    assert resp.status_code == 200, resp.text
    doc_id = _doc_id(resp.json())
    try:
        assert sc.upload_next_version(doc_id, b"second-bytes", "tag.txt").status_code == 200
        second = _tag(sc, doc_id)
        assert sc.rollback(doc_id, 0, "back").status_code == 200
        assert _tag(sc, doc_id) != second
    finally:
        sc.delete_document(doc_id)


@pytest.mark.integration
@pytest.mark.storage
def test_a_presigned_put_over_an_existing_object_changes_the_tag(sc: StorageClient, storage_backend: str):
    if storage_backend == "local":
        pytest.skip("a presigned PUT exists only on object stores; the local provider has no such write path")
    resp = sc.upload(file_content=b"C" * 64, file_name="tag.txt", document_name="Tag probe presigned", is_versioned=False)
    assert resp.status_code == 200, resp.text
    doc_id = _doc_id(resp.json())
    try:
        first = _tag(sc, doc_id)
        direct = sc.direct_upload(doc_id)
        assert direct.status_code == 200, direct.text
        url = direct.json().get("signedUrl") or direct.json().get("url")
        assert url, direct.text
        put = requests.put(url, data=b"D" * 64, timeout=sc._c.timeout_seconds)
        assert put.status_code in (200, 201), put.text
        assert _tag(sc, doc_id) != first
    finally:
        sc.delete_document(doc_id)
