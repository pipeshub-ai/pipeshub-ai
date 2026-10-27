"""The push service's decisions and the record it hands to the processor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    PushClaimRefused,
    PushOutcome,
)
from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.connectors.sources.localKB.push.service import KbPushService
from app.connectors.sources.localKB.push.worker import Validated
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.push_claim import PushClaim


def validated() -> Validated:
    return Validated(
        record_name="n", external_record_id="k", version=2, source_last_modified=None, schema_id=None, properties=None,
        blocks_sha="0" * 64, digest="a" * 64, stored_path="/nope", size=10, content_rev="b" * 16, warnings=0,
        block_count=1, group_count=0,
    )


def stored_file_record(md5: str | None) -> FileRecord:
    from app.config.constants.arangodb import Connectors, OriginTypes

    return FileRecord(
        id="rid", org_id="o", record_name="n", record_type=RecordType.FILE, external_record_id="doc-old",
        external_revision_id="pb1:" + "c" * 64 + ":" + "d" * 16, version=1, origin=OriginTypes.UPLOAD,
        connector_name=Connectors.KNOWLEDGE_BASE, connector_id="kb", is_file=True, md5_hash=md5,
    )


async def run_update(action: str, md5: str | None) -> FileRecord:
    graph = MagicMock()
    graph.get_file_record_by_id = AsyncMock(return_value=stored_file_record(md5))
    storage = MagicMock()
    storage._storage_url = AsyncMock(return_value="http://storage.test")
    svc = KbPushService(
        graph_provider=graph, kb_service=MagicMock(), processor_for_kb=AsyncMock(), schema_registry=MagicMock(),
        storage=storage, worker=MagicMock(), spool=MagicMock(), publish_event=AsyncMock(),
    )
    processor = MagicMock()
    processor.on_pushed_record = AsyncMock(return_value=MagicMock(event="updateRecord", published=True))
    existing = {"externalRecordId": "doc-old", "externalRevisionId": "pb1:" + "c" * 64 + ":" + "d" * 16, "version": 1}
    await svc._claim_and_write(
        processor, existing, action, validated(), "rid", "kb", "o", "u", None, "doc-new", C.revision_id("a" * 64, "b" * 16),
    )
    return processor.on_pushed_record.await_args.args[0]


async def test_a_content_change_hands_over_a_record_without_the_checksum() -> None:
    """A kept md5Checksum makes the NOT_STARTED row look like a parked duplicate, so the sweep never re-sends it."""
    record = await run_update("content", md5="abc123")
    assert record.md5_hash is None
    assert record.indexing_status == "NOT_STARTED" and record.external_record_id == "doc-new"


async def test_a_metadata_only_change_keeps_the_stored_document_and_revision() -> None:
    record = await run_update("metadata", md5="abc123")
    assert record.external_record_id == "doc-old" and record.external_revision_id.startswith("pb1:" + "c")


def _service(graph: MagicMock) -> KbPushService:
    return KbPushService(
        graph_provider=graph, kb_service=MagicMock(), processor_for_kb=AsyncMock(), schema_registry=MagicMock(),
        storage=MagicMock(), worker=MagicMock(), spool=MagicMock(), publish_event=AsyncMock(), sleep=AsyncMock(),
    )


def _stored(tag: str) -> dict:
    return {
        "id": "rid", "orgId": "o", "connectorId": "kb", "version": 2, "indexingStatus": "COMPLETED",
        "externalRecordId": "doc", "externalRevisionId": f"{tag}:{'a' * 64}:{'b' * 16}",
    }


async def test_a_retag_that_lost_to_a_concurrent_push_decides_again() -> None:
    """The retag matches nothing when the revision moved; answering ``unchanged`` then reports a stale revision."""
    graph = MagicMock()
    graph.get_document = AsyncMock(side_effect=[_stored("pb0"), _stored("pb1")])
    svc = _service(graph)
    svc._retag = AsyncMock(return_value=False)

    result = await svc._write(kb_id="kb", user_id="u", org_id="o", v=validated(), folder_id=None)

    assert svc._retag.await_count == 1 and graph.get_document.await_count == 2
    assert (result.status, result.content_rev) == ("unchanged", "b" * 16)


async def test_a_retag_that_applied_answers_unchanged_at_once() -> None:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=_stored("pb0"))
    svc = _service(graph)
    svc._retag = AsyncMock(return_value=True)

    result = await svc._write(kb_id="kb", user_id="u", org_id="o", v=validated(), folder_id=None)

    assert graph.get_document.await_count == 1 and result.status == "unchanged"


async def test_a_document_stored_for_a_lost_attempt_is_cleaned_up_when_the_retry_keeps_the_stored_one() -> None:
    """Attempt 1 stores a document and loses the claim; attempt 2 sees the same content at a lower version
    (a metadata change), which names no new document, so the stored one is the push's to remove."""
    changed = {**_stored("pb1"), "version": 1, "externalRevisionId": f"pb1:{'e' * 64}:{'f' * 16}"}
    same_content = {**_stored("pb1"), "version": 1}
    graph = MagicMock()
    graph.get_document = AsyncMock(side_effect=[changed, same_content])
    svc = _service(graph)
    svc._placement = AsyncMock(return_value=None)
    svc._check_name = AsyncMock()
    svc._processor = AsyncMock()
    svc._store = AsyncMock(return_value="doc-new")
    svc._cleanup_unless_referenced = AsyncMock()
    svc._claim_and_write = AsyncMock(side_effect=[PushClaimRefused(PushClaim.LOST), (PushOutcome(None, False), None, None, None)])

    result = await svc._write(kb_id="kb", user_id="u", org_id="o", v=validated(), folder_id=None)

    assert result.status == "updated"
    svc._cleanup_unless_referenced.assert_awaited_once()
    assert svc._cleanup_unless_referenced.await_args.args[1:] == ("o", "kb", "doc-new")


@pytest.mark.parametrize("version", ["²", "١", "1²"])
async def test_a_schema_version_with_non_ascii_digits_is_a_schema_mismatch(version: str) -> None:
    """str.isdigit() accepts them but int() does not: the push must answer 422, not 500."""
    svc = _service(MagicMock())
    body = ('{"metadata": {"schemaId": "o:kb:' + version + '"}}').encode()

    with pytest.raises(PushRefusal) as raised:
        await svc._load_schema("o", "kb", body)

    assert (raised.value.status, raised.value.reason) == (422, "SCHEMA_MISMATCH")
    svc.schemas.get.assert_not_called()
