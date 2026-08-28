"""End-to-end guard on which indexing event a Jira sync publishes.

Unlike the mock-level tests, these wire the connector's real ``_process_new_records`` to a
real ``DataSourceEntitiesProcessor`` (only the graph store and the broker are faked), so they
assert the event that actually reaches ``record-events`` after ``_process_record`` has applied
its indexing-status rules.

The case that motivated them: on a full sync the unchanged-issue short-circuit is bypassed so
every issue is re-emitted to rebuild the edges the full sync deleted. Routing those through
``on_record_content_update`` publishes an ``updateRecord`` for each one — that method publishes
unconditionally — which re-parses and re-embeds an entire already-indexed project.
"""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.models.entities import RecordGroupType, RecordType, TicketRecord


def _ticket(*, ext_id, version, revision, status=None, is_placeholder=False, parent=None):
    record = TicketRecord(
        org_id="org-1",
        external_record_id=ext_id,
        external_revision_id=revision,
        record_name=f"[PROJ-{ext_id}] ticket",
        record_type=RecordType.TICKET,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA,
        connector_id="conn-1",
        record_group_type=RecordGroupType.PROJECT,
        external_record_group_id="p-1",
        parent_external_record_id=parent,
        parent_record_type=RecordType.TICKET if parent else None,
        version=version,
        mime_type=MimeTypes.BLOCKS.value,
        inherit_permissions=True,
        is_placeholder=is_placeholder,
    )
    if status:
        record.indexing_status = status
    return record


def _connector_with_real_processor(existing_by_external_id):
    """JiraConnector + real DataSourceEntitiesProcessor; graph store and broker faked.

    Returns ``(connector, published)`` where ``published`` collects
    ``(eventType, recordId)`` for everything that reaches the broker.
    """
    published: list[tuple[str, str]] = []
    tx_store = AsyncMock()
    tx_store.get_record_by_external_id = AsyncMock(
        side_effect=lambda connector_id, external_id: existing_by_external_id.get(external_id)
    )

    @asynccontextmanager
    async def _transaction():
        yield tx_store

    store_provider = MagicMock()
    store_provider.transaction = _transaction
    store_provider.get_existing_record_keys = AsyncMock(side_effect=lambda ids: set(ids))
    store_provider.compare_and_set_indexing_status = AsyncMock(
        side_effect=lambda ids, expected, new_status: list(ids)
    )

    processor = DataSourceEntitiesProcessor(
        logging.getLogger("test.jira.routing"), store_provider, AsyncMock()
    )
    processor.org_id = "org-1"
    processor.messaging_producer = AsyncMock()

    async def _send_message(topic, message, key=None):
        published.append((message["eventType"], message["payload"]["recordId"]))
        return True

    async def _send_messages(topic, messages):
        for _key, message in messages:
            published.append((message["eventType"], message["payload"]["recordId"]))
        return [True] * len(messages)

    processor.messaging_producer.send_message = AsyncMock(side_effect=_send_message)
    processor.messaging_producer.send_messages = AsyncMock(side_effect=_send_messages)

    connector = JiraConnector(
        logging.getLogger("test.jira.routing"), processor, MagicMock(), AsyncMock(),
        "conn-1", "team", "u-1",
    )
    return connector, published


async def _route(existing_by_external_id, batch):
    connector, published = _connector_with_real_processor(existing_by_external_id)
    stats = {"new_count": 0, "updated_count": 0}
    await connector._process_new_records(batch, "PROJ", stats)
    return published, stats


class TestIndexingEventRouting:

    @pytest.mark.asyncio
    async def test_first_sync_publishes_new_record(self):
        record = _ticket(ext_id="1", version=0, revision="100")
        published, stats = await _route({}, [(record, [], True)])

        assert published == [("newRecord", record.id)]
        assert stats == {"new_count": 1, "updated_count": 0}

    @pytest.mark.asyncio
    async def test_changed_issue_publishes_update_record(self):
        existing = _ticket(
            ext_id="2", version=1, revision="100", status=ProgressStatus.COMPLETED.value
        )
        record = _ticket(ext_id="2", version=2, revision="200")
        published, stats = await _route({"2": existing}, [(record, [], True)])

        assert published == [("updateRecord", record.id)]
        assert stats == {"new_count": 0, "updated_count": 1}

    @pytest.mark.asyncio
    async def test_full_sync_unchanged_indexed_issue_publishes_nothing(self):
        """The regression: version > 1 and already COMPLETED, re-emitted only to rebuild
        edges. Routing it as a content update re-indexes the whole project.
        """
        existing = _ticket(
            ext_id="3", version=4, revision="100", status=ProgressStatus.COMPLETED.value
        )
        record = _ticket(ext_id="3", version=4, revision="100")
        published, stats = await _route({"3": existing}, [(record, [], False)])

        assert published == []
        assert stats == {"new_count": 0, "updated_count": 0}

    @pytest.mark.asyncio
    async def test_full_sync_unchanged_but_never_indexed_is_requeued(self):
        """A record that failed indexing must still be retried by a full sync."""
        existing = _ticket(
            ext_id="5", version=3, revision="100", status=ProgressStatus.FAILED.value
        )
        record = _ticket(ext_id="5", version=3, revision="100")
        published, _stats = await _route({"5": existing}, [(record, [], False)])

        assert published == [("newRecord", record.id)]

    @pytest.mark.asyncio
    async def test_placeholder_promotion_publishes_new_record(self):
        existing = _ticket(ext_id="9", version=0, revision=None, is_placeholder=True)
        record = _ticket(ext_id="9", version=0, revision="100")
        published, _stats = await _route({"9": existing}, [(record, [], True)])

        assert published == [("newRecord", record.id)]

    @pytest.mark.asyncio
    async def test_parent_is_processed_before_child(self):
        """The batch handed to on_new_records must keep the parent-first sort, otherwise a
        child creates a placeholder for a parent that is in the very same batch.
        """
        parent = _ticket(ext_id="p", version=0, revision="1")
        child = _ticket(ext_id="c", version=0, revision="1", parent="p")
        published, _stats = await _route({}, [(child, [], True), (parent, [], True)])

        assert [record_id for _event, record_id in published] == [parent.id, child.id]


class TestManualIndexingRouting:
    """Issues filter off => records are created AUTO_INDEX_OFF and only indexed on demand.
    No sync path may auto-publish for them.
    """

    @pytest.mark.asyncio
    async def test_new_record_with_auto_index_off_publishes_nothing(self):
        record = _ticket(
            ext_id="6", version=0, revision="100",
            status=ProgressStatus.AUTO_INDEX_OFF.value,
        )
        published, _stats = await _route({}, [(record, [], True)])

        assert published == []

    @pytest.mark.asyncio
    async def test_manually_indexed_then_changed_is_requeued(self):
        """Once the user has manually indexed a record, a source change re-queues it.

        The connector stamps AUTO_INDEX_OFF on every ticket while the issues filter is
        off; honouring that on an already-COMPLETED record downgraded it to
        AUTO_INDEX_OFF and left stale vectors behind with no event to fix them.
        """
        existing = _ticket(
            ext_id="7", version=1, revision="100", status=ProgressStatus.COMPLETED.value
        )
        record = _ticket(
            ext_id="7", version=2, revision="200",
            status=ProgressStatus.AUTO_INDEX_OFF.value,
        )
        published, _stats = await _route({"7": existing}, [(record, [], True)])

        assert published == [("updateRecord", record.id)]
        assert record.indexing_status == ProgressStatus.NOT_STARTED.value

    @pytest.mark.asyncio
    async def test_manually_indexed_survives_full_sync(self):
        """_process_record forces a stored-COMPLETED unchanged record back to COMPLETED,
        which clears the incoming AUTO_INDEX_OFF — so only on_new_records' COMPLETED skip
        keeps this one from being re-indexed.
        """
        existing = _ticket(
            ext_id="8", version=2, revision="100", status=ProgressStatus.COMPLETED.value
        )
        record = _ticket(
            ext_id="8", version=2, revision="100",
            status=ProgressStatus.AUTO_INDEX_OFF.value,
        )
        published, _stats = await _route({"8": existing}, [(record, [], False)])

        assert published == []
