"""The write behind a pushed record: the transaction it needs and the status a re-pushed record is left in."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.push_claim import PushClaim, PushExpect, PushSet

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

REVISION = "pb1:" + "c" * 64 + ":" + "d" * 16


def _record(**over) -> FileRecord:
    fields = {
        "id": "rid", "org_id": "org-1", "record_name": "a", "record_type": RecordType.FILE,
        "external_record_id": "doc-new", "external_revision_id": REVISION, "version": 1,
        "origin": OriginTypes.UPLOAD, "connector_name": Connectors.KNOWLEDGE_BASE, "connector_id": "kb",
        "is_file": True, "indexing_status": ProgressStatus.NOT_STARTED.value,
    }
    fields.update(over)
    return FileRecord(**fields)


def _processor(store: AsyncMock) -> DataSourceEntitiesProcessor:
    @asynccontextmanager
    async def transaction(**_: object) -> AsyncIterator[AsyncMock]:
        yield store

    provider = MagicMock()
    provider.transaction = transaction
    proc = DataSourceEntitiesProcessor(MagicMock(), provider, AsyncMock())
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()
    return proc


def _store(*, explicit: bool, stored: FileRecord | None = None) -> AsyncMock:
    store = AsyncMock()
    store.explicit = explicit
    store.claim_push_record = AsyncMock(return_value=PushClaim.CLAIMED)
    store.get_record_by_key = AsyncMock(return_value=stored.to_arango_base_record() if stored else None)
    store.get_record_by_external_id = AsyncMock(return_value=stored)
    store.get_record_group_by_external_id = AsyncMock(return_value=None)
    return store


async def test_a_push_is_refused_when_the_transaction_is_not_a_real_one() -> None:
    store = _store(explicit=False)

    with pytest.raises(RuntimeError, match="real graph transaction"):
        await _processor(store).on_pushed_record(_record(), expected=None, new=PushSet("doc-new", 1), content_changed=True)

    store.claim_push_record.assert_not_awaited()


async def test_an_identical_repush_that_stores_a_new_document_is_queued_again() -> None:
    """The revision is the stored one, so the status the upload branch carried over (FAILED) must be reset."""
    stored = _record(external_record_id="doc-lost", indexing_status=ProgressStatus.FAILED.value)
    store = _store(explicit=True, stored=stored)
    proc = _processor(store)

    outcome = await proc.on_pushed_record(
        _record(), expected=PushExpect("doc-lost", REVISION, 1), new=PushSet("doc-new", 1), content_changed=True,
    )

    written = store.batch_upsert_records.await_args_list[-1].args[0][0]
    assert written.indexing_status == ProgressStatus.NOT_STARTED.value and written.queued_at
    assert (outcome.event, outcome.published) == ("updateRecord", True)


@pytest.mark.parametrize("sent", [False, RuntimeError("down")], ids=["rejected", "raised"])
async def test_an_event_the_broker_did_not_take_leaves_the_record_for_the_sweep(sent: object) -> None:
    """A producer may report a rejection by returning False; the record must not be marked QUEUED for it."""
    store = _store(explicit=True)
    proc = _processor(store)
    proc.messaging_producer.send_message = AsyncMock(**({"side_effect": sent} if isinstance(sent, Exception) else {"return_value": sent}))
    proc._mark_queued_after_publish = AsyncMock()

    outcome = await proc.on_pushed_record(_record(), expected=None, new=PushSet("doc-new", 1), content_changed=True)

    assert (outcome.event, outcome.published) == ("newRecord", False)
    proc._mark_queued_after_publish.assert_not_awaited()
