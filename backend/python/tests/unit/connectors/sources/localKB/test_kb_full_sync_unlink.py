"""A full sync of a Collection reads nothing from a source, so the sweep after it must unlink
none of the Collection's files, even when an upload lands while it runs."""

import logging
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.sync import sync_runner
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.core.sync.sync_runner import run_sync_task
from app.connectors.sources.localKB.connector import KnowledgeBaseConnector

KB_ID = "kb-1"
STORED = {"file-1", "file-2", "folder-1"}


def org_processor() -> DataSourceEntitiesProcessor:
    """The org's shared processor, with only its full-sync bookkeeping in use."""
    processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
    processor.org_id = "org-1"
    processor.finish_full_sync_memberships = AsyncMock()
    return processor


def kb_connector(processor: DataSourceEntitiesProcessor) -> KnowledgeBaseConnector:
    return KnowledgeBaseConnector(
        logger=logging.getLogger("test.kb"), data_entities_processor=processor,
        data_store_provider=MagicMock(), config_service=AsyncMock(),
        connector_id=KB_ID, scope="team", created_by="user-1",
    )


async def full_sync_graph(*, upload_during_run: bool) -> AsyncMock:
    """Run a full sync of the Collection as the runner does and return the graph it used.
    An upload, if any, is written while the run holds the Collection's full sync open
    (between its start and its sweep)."""
    processor = org_processor()
    connector = kb_connector(processor)
    graph = AsyncMock()
    graph.unlink_nodes_missed_by_full_sync.return_value = (0, True)

    async def app_doc(**_: object) -> dict:
        if upload_during_run:
            # What the upload route's on_new_records notes for the new file.
            processor._note_full_sync_seen(KB_ID, CollectionNames.RECORDS.value, "uploaded-1")
        return {"id": KB_ID, "status": AppStatus.FULL_SYNCING.value}

    graph.get_document.side_effect = app_doc
    await run_sync_task(connector, KB_ID, graph, logging.getLogger("test.full_sync"),
                        start_status=AppStatus.FULL_SYNCING.value)
    return graph


async def unlinked_by_full_sync(*, upload_during_run: bool) -> set[str]:
    """The stored records the sweep after a full sync of the Collection unlinks."""
    graph = await full_sync_graph(upload_during_run=upload_during_run)
    if not graph.unlink_nodes_missed_by_full_sync.await_count:
        return set()
    seen = graph.unlink_nodes_missed_by_full_sync.await_args.args[1]
    return STORED - set().union(*seen.values())


async def test_an_upload_during_a_full_sync_of_a_collection_unlinks_none_of_its_files() -> None:
    unlinked = await unlinked_by_full_sync(upload_during_run=True)

    wrongly = unlinked & STORED
    assert sorted(wrongly) == [], f"wrongly unlinked: {len(wrongly)}"


async def test_control_a_full_sync_of_a_collection_with_no_upload_unlinks_nothing() -> None:
    # Nothing in a Collection is gone "at the source": files leave it only through its own
    # delete route, so the really-gone count of a Collection's full sync is always 0.
    assert await unlinked_by_full_sync(upload_during_run=False) == set()


async def test_kb_full_sync_owes_no_retry() -> None:
    for _ in range(2):
        graph = await full_sync_graph(upload_during_run=True)

        graph.unlink_nodes_missed_by_full_sync.assert_not_awaited()
        assert KB_ID not in sync_runner._INCOMPLETE_FULL_SYNC_RETRYING


def test_kb_coverage_reports_it_lists_nothing() -> None:
    coverage = kb_connector(org_processor()).full_sync_coverage()

    assert coverage == FullSyncCoverage(complete=True, lists_everything=False)
