"""A sync re-indexes a stored record only when that can change the outcome.

Every unchanged record that was not COMPLETED used to be re-queued on every sync:
empty and unsupported files were paid for again, records mid-pipeline flipped
back to QUEUED, and a broken file failed again on every sync for ever. A failed
record now gets MAX_SYNC_RETRIES sync-driven retries, which start over when its
content changes; everything else keeps its state and publishes nothing.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors as ConnectorsEnum
from app.config.constants.arangodb import OriginTypes, ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    MAX_SYNC_RETRIES,
    DataSourceEntitiesProcessor,
)
from app.models.entities import FileRecord, RecordType


def _record(status: str = ProgressStatus.QUEUED.value, revision: str = "rev-1", retries: int | None = None) -> FileRecord:
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
        auto_retry_count=retries,
    )


def _settle(incoming: FileRecord, stored: FileRecord, *, publishes_event: bool = True, content_changed: bool = False) -> FileRecord:
    processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
    processor._settle_unfinished_record(
        incoming, stored, publishes_event=publishes_event, content_changed=content_changed
    )
    return incoming


def test_a_failed_record_is_retried_by_syncs_until_its_budget_is_spent() -> None:
    for used in range(MAX_SYNC_RETRIES):
        out = _settle(_record(), _record(ProgressStatus.FAILED.value, retries=used or None))
        assert out.indexing_status == ProgressStatus.NOT_STARTED.value
        assert out.auto_retry_count == used + 1
        assert not out.keeps_indexing_state
    spent = _settle(_record(), _record(ProgressStatus.FAILED.value, retries=MAX_SYNC_RETRIES))
    assert spent.indexing_status == ProgressStatus.FAILED.value
    assert spent.keeps_indexing_state
    assert spent.auto_retry_count == MAX_SYNC_RETRIES


def test_changed_content_is_indexed_and_its_budget_starts_over() -> None:
    out = _settle(_record(revision="rev-2"), _record(ProgressStatus.FAILED.value, retries=MAX_SYNC_RETRIES))
    assert out.indexing_status == ProgressStatus.NOT_STARTED.value
    assert out.auto_retry_count == 0


@pytest.mark.parametrize(
    "stored",
    [
        ProgressStatus.EMPTY.value,
        ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
        ProgressStatus.ENABLE_MULTIMODAL_MODELS.value,
        ProgressStatus.IN_PROGRESS.value,
        ProgressStatus.QUEUED.value,
    ],
)
def test_outcomes_the_same_bytes_cannot_change_are_kept(stored: str) -> None:
    out = _settle(_record(), _record(stored))
    assert out.indexing_status == stored
    assert out.keeps_indexing_state


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
    out = _settle(_record(), _record(ProgressStatus.FAILED.value, retries=MAX_SYNC_RETRIES), content_changed=True)
    assert out.indexing_status == ProgressStatus.QUEUED.value
    assert out.auto_retry_count == 0
    assert not out.keeps_indexing_state


@pytest.mark.parametrize("used, sent", [(MAX_SYNC_RETRIES, 0), (0, 1)])
async def test_a_failure_is_sent_to_indexing_only_while_its_budget_lasts(used: int, sent: int) -> None:
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
    stored = _record(ProgressStatus.FAILED.value, retries=used)
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
    assert len(published) == sent
