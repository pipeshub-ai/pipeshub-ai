"""One record per Zammad attachment, whichever of the sync and the stream creates it.

The sync stores a ticket's (or knowledge-base answer's) attachments, and
``stream_record`` of that ticket or answer (indexing reads it while the sync goes
on) creates any attachment it finds unstored. Both can look before either has
stored it. With a random id each, the graph held two records for one attachment,
and the next full sync rewrote the one its lookup found and unlinked the other,
which lost its parent and the access it inherits.
"""

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from zammad_behaviour_fakes import (
    FakeConfigService,
    FakeRecordsDb,
    FakeStore,
    FakeZammad,
    epoch_ms,
    iso,
)

from app.connectors.core.registry.filters import IndexingFilterKey
from app.connectors.sources.zammad.connector import ZammadConnector
from app.models.entities import Record, RecordType, WebpageRecord
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.zammad.zammad import ZammadResponse

CONNECTOR_ID = "zm-children"
ATTACHMENT = "11_1_1"
KB_ATTACHMENT = "kb_answer_5_attachment_7"


class RacingRecordsDb(FakeRecordsDb):
    """The graph as two writers meet it: neither sees the other's unstored child, and
    nothing keeps one record per external id, so a write lands under its own record id."""

    def __init__(self, racing: set[str]) -> None:
        super().__init__()
        self.racing = racing
        self.by_id: dict[str, Any] = {}

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        if external_record_id in self.racing:
            return None
        return await super().get_record_by_external_id(connector_id, external_record_id)

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        for record, _ in records_with_permissions:
            self.by_id[record.id] = record.model_copy(deep=True)
        await super().on_new_records(records_with_permissions)

    def stored_as(self, external_record_id: str) -> list[str]:
        return sorted(r.id for r in self.by_id.values() if r.external_record_id == external_record_id)


def _connector(db: FakeRecordsDb, datasource: object) -> ZammadConnector:
    with patch("app.connectors.sources.zammad.connector.ZammadApp"):
        connector = ZammadConnector(
            logger=logging.getLogger("test.zammad.children"),
            data_entities_processor=db,
            data_store_provider=FakeStore(),
            config_service=FakeConfigService(),
            connector_id=CONNECTOR_ID,
            scope="team",
            created_by="admin",
        )
    connector.external_client = object()
    connector.base_url = "https://zammad.test"
    connector._get_fresh_datasource = AsyncMock(return_value=datasource)
    return connector


def _zammad() -> FakeZammad:
    zammad = FakeZammad(groups={1: "Support"})
    zammad.add_ticket(11, 1, day=2, attachments=1)
    return zammad


def _children(streamed: bytes) -> list[str]:
    container = json.loads(streamed)
    return [c["child_id"] for g in container["block_groups"] for c in (g.get("children_records") or [])]


async def _stream(connector: ZammadConnector, record: Record) -> bytes:
    response = await connector.stream_record(record)
    return b"".join([chunk async for chunk in response.body_iterator])


async def _ticket_writers(db: FakeRecordsDb) -> tuple[list[str], str]:
    """The sync builds ticket 11's records, the stream of ticket 11 runs, then the sync stores its batch.
    Returns the stream's child ids and the sync's attachment id."""
    zammad = _zammad()
    connector = _connector(db, zammad)
    batch = await connector._ticket_records(zammad._public(zammad.tickets[11]))
    ticket = next(r for r, _ in batch if r.external_record_id == "11")
    await db.on_new_records([(ticket, [])])

    children = _children(await _stream(connector, ticket))
    await db.on_new_records(batch)
    return children, next(r.id for r, _ in batch if r.external_record_id == ATTACHMENT)


class TestTicketAttachmentMetByTwoWriters:
    async def test_the_sync_and_the_stream_store_one_record(self) -> None:
        db = RacingRecordsDb(racing={ATTACHMENT})

        children, _ = await _ticket_writers(db)

        stored = db.stored_as(ATTACHMENT)
        assert len(stored) == 1, f"one attachment stored as {len(stored)} records: {stored}"
        assert children == stored

    async def test_a_stored_attachment_keeps_its_id(self) -> None:
        db = FakeRecordsDb()
        zammad = _zammad()
        connector = _connector(db, zammad)
        await db.on_new_records(await connector._ticket_records(zammad._public(zammad.tickets[11])))
        db.records[ATTACHMENT].id = "stored-node"

        children, synced = await _ticket_writers(db)

        assert synced == "stored-node"
        assert children == ["stored-node"]
        assert db.records[ATTACHMENT].id == "stored-node"


def _answer_data() -> dict[str, Any]:
    return {
        "id": 5,
        "category_id": 1,
        "translation_ids": [],
        "attachments": [{"id": 7, "filename": "guide.pdf", "size": 3,
                         "preferences": {"Content-Type": "application/pdf"}}],
        "published_at": iso(epoch_ms(1)),
        "internal_at": None,
        "archived_at": None,
        "created_at": iso(epoch_ms(1)),
        "updated_at": iso(epoch_ms(1)),
    }


def _answer_record() -> WebpageRecord:
    return WebpageRecord(
        id="answer-node",
        org_id="org-1",
        record_name="KB answer 5",
        record_type=RecordType.WEBPAGE,
        external_record_id="kb_answer_5",
        external_record_group_id="cat_1",
        version=0,
        origin="CONNECTOR",
        connector_name="ZAMMAD",
        connector_id=CONNECTOR_ID,
        weburl="https://zammad.test/help/kb_answer_5",
        source_created_at=epoch_ms(1),
        source_updated_at=epoch_ms(1),
        inherit_permissions=False,
    )


def _kb_datasource() -> MagicMock:
    datasource = MagicMock()
    datasource.init_knowledge_base = AsyncMock(return_value=ZammadResponse(success=True, data={}))
    datasource.get_kb_answer = AsyncMock(
        side_effect=lambda **_: ZammadResponse(success=True, data=_answer_data(), status_code=200))
    return datasource


async def _kb_writers(db: FakeRecordsDb) -> tuple[list[str], str]:
    connector = _connector(db, _kb_datasource())
    answer = _answer_record()
    await db.on_new_records([(answer, [])])
    org_read = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
    batch = await connector._fetch_kb_answer_attachments(_answer_data(), answer, org_read)

    children = _children(await _stream(connector, answer))
    await db.on_new_records(batch)
    return children, batch[0][0].id


class TestKbAnswerAttachmentMetByTwoWriters:
    async def test_the_sync_and_the_stream_store_one_record(self) -> None:
        db = RacingRecordsDb(racing={KB_ATTACHMENT})

        children, _ = await _kb_writers(db)

        stored = db.stored_as(KB_ATTACHMENT)
        assert len(stored) == 1, f"one attachment stored as {len(stored)} records: {stored}"
        assert children == stored

    async def test_a_stored_attachment_keeps_its_id(self) -> None:
        db = FakeRecordsDb()
        connector = _connector(db, _kb_datasource())
        answer = _answer_record()
        await db.on_new_records(await connector._fetch_kb_answer_attachments(_answer_data(), answer, []))
        db.records[KB_ATTACHMENT].id = "stored-node"

        children, synced = await _kb_writers(db)

        assert synced == "stored-node"
        assert children == ["stored-node"]


@pytest.mark.parametrize("external_record_id", [ATTACHMENT, KB_ATTACHMENT])
async def test_a_new_attachment_gets_the_same_id_from_every_writer(external_record_id: str) -> None:
    parent = _answer_record()

    async def new_id() -> str:
        record = await _connector(FakeRecordsDb(), object())._transform_attachment_to_file_record(
            attachment_data={"id": 7, "filename": "guide.pdf"},
            external_record_id=external_record_id,
            parent_record=parent,
            parent_record_type=RecordType.WEBPAGE,
            indexing_filter_key=IndexingFilterKey.KNOWLEDGE_BASE,
        )
        return record.id

    assert await new_id() == await new_id()
