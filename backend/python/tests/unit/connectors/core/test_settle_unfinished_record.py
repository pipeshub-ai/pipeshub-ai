"""A sync re-indexes a stored record only when that can change the outcome.

An unchanged FAILED record is not sent again: it stays failed until its content
changes or someone asks for a retry. Changed content and records the filters now
allow are indexed.
"""

from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import Connectors as ConnectorsEnum
from app.config.constants.arangodb import OriginTypes, ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import FileRecord, RecordType


def _record(status: str = ProgressStatus.QUEUED.value, revision: str = "rev-1") -> FileRecord:
    return FileRecord(
        org_id="org-1",
        external_record_id="ext-1",
        external_revision_id=revision,
        record_name="a.txt",
        origin=OriginTypes.CONNECTOR.value,
        connector_name=ConnectorsEnum.GOOGLE_MAIL,
        connector_id="conn-1",
        record_type=RecordType.FILE,
        version=1,
        mime_type="text/plain",
        is_file=True,
        extension="txt",
        size_in_bytes=1,
        indexing_status=status,
    )


def _settle(incoming: FileRecord, stored: FileRecord, *, publishes_event: bool = True, content_changed: bool = False) -> FileRecord:
    processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
    processor._settle_unfinished_record(
        incoming, stored, publishes_event=publishes_event, content_changed=content_changed
    )
    return incoming


def test_changed_content_is_indexed() -> None:
    out = _settle(_record(revision="rev-2"), _record(ProgressStatus.FAILED.value))
    assert out.indexing_status == ProgressStatus.NOT_STARTED.value


def test_a_status_the_connector_chose_stands() -> None:
    out = _settle(_record(ProgressStatus.AUTO_INDEX_OFF.value), _record(ProgressStatus.FAILED.value))
    assert out.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
    assert not out.keeps_indexing_state


def test_a_record_the_filters_now_allow_is_indexed() -> None:
    out = _settle(_record(), _record(ProgressStatus.AUTO_INDEX_OFF.value))
    assert out.indexing_status == ProgressStatus.NOT_STARTED.value


def test_a_write_that_publishes_nothing_changes_no_state() -> None:
    out = _settle(_record(), _record(ProgressStatus.FAILED.value), publishes_event=False)
    assert out.indexing_status == ProgressStatus.FAILED.value
    assert out.keeps_indexing_state


def test_a_content_update_is_indexed_whatever_the_revision_says() -> None:
    out = _settle(_record(), _record(ProgressStatus.FAILED.value), content_changed=True)
    assert out.indexing_status == ProgressStatus.QUEUED.value
    assert not out.keeps_indexing_state


async def test_an_unchanged_failed_record_is_not_sent_to_indexing() -> None:
    from unittest.mock import patch

    from app.connectors.core.sync.sync_progress import (
        SyncProgress,
        reset_current_sync_progress,
        set_current_sync_progress,
    )
    from tests.unit.connectors.core.test_data_source_entities_processor import (
        _make_ctx,
        _make_processor,
        _make_tx_store,
    )

    proc = _make_processor()
    tx_store = _make_tx_store()
    stored = _record(ProgressStatus.FAILED.value)
    stored.id = "rec-1"
    tx_store.get_record_by_external_id = AsyncMock(return_value=stored)
    proc.data_store_provider.transaction = MagicMock(return_value=_make_ctx(tx_store))
    progress = SyncProgress(connector_id="conn-1", run_id="r1", full=False)
    token = set_current_sync_progress(progress)
    try:
        with patch.object(proc, "_handle_record_group", AsyncMock(return_value=None)):
            await proc.on_new_records([(_record(), [])])
    finally:
        reset_current_sync_progress(token)

    assert progress.counts()["unchanged"] == 1
    published = [
        msg
        for call in proc.messaging_producer.send_messages.await_args_list
        for msg in call.args[1]
    ]
    assert len(published) == 0
