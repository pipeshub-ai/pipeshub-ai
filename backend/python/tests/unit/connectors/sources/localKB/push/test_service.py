"""The push service's decisions and the record it hands to the processor (T33b md5 control)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push.service import KbPushService
from app.connectors.sources.localKB.push.worker import Validated
from app.models.entities import FileRecord, RecordType


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
