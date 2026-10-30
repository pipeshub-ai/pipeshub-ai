"""Linked-record context decodes only the head of a stored record (head_only)."""

from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import (
    BlobStorage,
    _decode_json,
    _decode_record_head,
)
from app.utils import chat_helpers as ch

HEAD = {
    "id": "r1",
    "org_id": "org-1",
    "record_name": "Design doc",
    "record_type": "TICKET",
    "external_record_id": "EXT-1",
    "parent_external_record_id": "EXT-0",
    "version": 3,
    "origin": "CONNECTOR",
    "connector_name": "JIRA",
    "connector_id": "c1",
    "mime_type": "text/html",
    "source_created_at": 1700000000000,
    "source_updated_at": 1700000001000,
    "source_timestamp_origin": "SOURCE",
    "created_at": 1700000002000,
    "location": "PROJ",
    "weburl": "https://example.com/browse/EXT-1",
    "hide_weburl": False,
    "semantic_metadata": {"summary": "What the doc says.", "topics": ["a"], "categories": ["b"]},
}
RECORD = {
    **HEAD,
    "virtual_record_id": "vr-1",
    "md5_hash": "abc",
    "block_containers": {"blocks": [{"index": 0, "type": "text", "data": "Borgartún " * 50}], "block_groups": []},
}


def _raw(envelope: dict) -> bytes:
    return msgspec.json.encode(envelope)


def test_the_head_is_the_record_without_the_other_fields() -> None:
    data = _decode_record_head(_raw({"record": RECORD, "virtualRecordId": "vr-1"}))
    assert data["record"] == HEAD
    assert not data["isCompressed"]


def test_a_missing_field_stays_missing_and_a_null_stays_null() -> None:
    record = {"id": "r1", "location": None}
    assert _decode_record_head(_raw({"record": record}))["record"] == record


def test_a_compressed_record_is_left_for_the_full_decode() -> None:
    data = _decode_record_head(_raw({"isCompressed": True, "record": "b64", "virtualRecordId": "v"}))
    assert data["record"] == "b64"
    assert data["isCompressed"] is True


def test_a_signed_url_envelope_passes_through() -> None:
    data = _decode_record_head(_raw({"signedUrl": "https://s3/x"}))
    assert data["signedUrl"] == "https://s3/x"
    assert data["record"] is None


@pytest.mark.parametrize("envelope", [{"record": [1, 2]}, {"record": {"other": 1}}, [1]])
def test_anything_else_decodes_as_before(envelope: object) -> None:
    raw = _raw(envelope)
    assert _decode_record_head(raw) == _decode_json(raw)


@pytest.mark.asyncio
async def test_get_record_from_storage_head_only_skips_the_blocks() -> None:
    blob = BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())
    blob._get_auth_and_config = AsyncMock(return_value=({}, "http://storage", ""))
    blob._cached_signed_url = AsyncMock(return_value=None)
    raw = _raw({"record": RECORD})
    seen: list = []

    async def fetch(session, url, headers, vrid, decode=_decode_json) -> dict:
        seen.append(decode)
        return decode(raw)

    blob._fetch_record_envelope = fetch
    lookup = {"record_doc_id": "doc-1", "fileSizeBytes": len(raw)}
    with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()):
        head = await blob.get_record_from_storage("vr-1", "org-1", lookup_result=lookup, head_only=True)
        full = await blob.get_record_from_storage("vr-1", "org-1", lookup_result=lookup)
    assert head == HEAD
    assert full == RECORD
    assert [getattr(d, "__wrapped__", d) for d in seen] == [_decode_record_head, _decode_json]


@pytest.mark.asyncio
@pytest.mark.parametrize("record_type, type_doc", [
    ("TICKET", {"id": "r1", "status": "open", "priority": "P1", "assignee": "a"}),
    ("FILE", {"id": "r1", "extension": "pdf"}),
    ("FILE", None),
])
async def test_linked_context_is_the_same_from_the_head(record_type: str, type_doc: dict | None) -> None:
    record = {**RECORD, "record_type": record_type}
    head = _decode_record_head(_raw({"record": record}))["record"]
    base_doc = {"id": "r1", "recordType": record_type, "recordName": "Graph name", "connectorName": "JIRA"}
    outputs = []
    for stored in (record, head):
        blob_store = MagicMock()
        blob_store.get_record_from_storage = AsyncMock(return_value=stored)
        outputs.append(await ch._build_linked_record_context_metadata(
            "r1", MagicMock(), {"r1": base_doc}, "https://app",
            vrid="vr-1", blob_store=blob_store, org_id="org-1",
            lookup_result={"record_doc_id": "doc-1"}, type_doc=type_doc or {},
        ))
    assert outputs[0]
    assert outputs[0] == outputs[1]
    assert blob_store.get_record_from_storage.await_args.kwargs["head_only"] is True


@pytest.mark.asyncio
async def test_a_head_is_never_handed_out_as_the_whole_record() -> None:
    import asyncio

    from app.utils import turn_memo

    bs_mod._DECODED_RECORDS.clear()
    blob = BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())
    blob._get_auth_and_config = AsyncMock(return_value=({}, "http://storage", ""))
    blob._cached_signed_url = AsyncMock(return_value=None)
    raw = _raw({"record": RECORD})
    downloads: list = []

    async def fetch(session, url, headers, vrid, decode=_decode_json) -> dict:
        downloads.append(decode)
        return decode(raw)

    blob._fetch_record_envelope = fetch
    lookup = {"record_doc_id": "doc-1", "fileSizeBytes": len(raw), "updatedAt": 1}

    async def turn(head_only: bool) -> dict:
        turn_memo.begin_turn()
        return await blob.get_record_from_storage("vr-1", "org-1", lookup_result=dict(lookup), head_only=head_only)

    async def both() -> tuple:
        turn_memo.begin_turn()
        head = await blob.get_record_from_storage("vr-1", "org-1", lookup_result=lookup, head_only=True)
        full = await blob.get_record_from_storage("vr-1", "org-1", lookup_result=lookup)
        return head, full

    with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()):
        head, full = await asyncio.create_task(both())
        assert head == HEAD
        assert full == RECORD
        # The whole record is cached across turns and also answers a head_only call.
        assert await asyncio.create_task(turn(True)) == RECORD
    assert [getattr(d, "__wrapped__", d) for d in downloads] == [_decode_record_head, _decode_json]
    bs_mod._DECODED_RECORDS.clear()
