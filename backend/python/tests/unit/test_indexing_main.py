"""Comprehensive unit tests for app.indexing_main module."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.responses import JSONResponse

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.services.messaging.config import MessageBrokerType


@pytest.fixture(autouse=True)
def disable_distributed_concurrency_by_default(monkeypatch):
    monkeypatch.setenv("DISTRIBUTED_INDEXING_CONCURRENCY", "false")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_container():
    """Build a mock IndexingAppContainer with common providers.

    Includes a default kafka_consumers entry with a mock retry producer so
    recover_in_progress_records() tests have something to publish to
    without each test having to wire it up individually. Tests that need to
    assert against send_event should read it back via
    ``mock_container.kafka_consumers[0][2]``.
    """
    container = MagicMock()
    container.logger.return_value = MagicMock()
    mock_config_service = MagicMock()
    mock_config_service.get_config = AsyncMock(return_value={})
    mock_config_service.close = AsyncMock()
    container.config_service.return_value = mock_config_service
    container.graph_provider = AsyncMock()
    mock_producer = AsyncMock()
    mock_producer.send_event = AsyncMock(return_value=True)
    mock_consumer = MagicMock()
    mock_consumer.concurrency_manager = None
    mock_consumer._run_on_main_loop = None
    container.kafka_consumers = [("record", mock_consumer, mock_producer)]
    return container


def _lookup_record_by_key(gp, doc_id):
    """Echo back whatever get_nodes_by_filters was configured to return for
    this _key (forcing indexingStatus to IN_PROGRESS), so the recovery
    status recheck (see fix-recovery-staleness) sees the same fields
    (version, virtualRecordId, connectorId, origin, ...) the initial scan
    did, instead of a bare stub that would silently drop them."""
    records = gp.get_nodes_by_filters.return_value
    if isinstance(records, list):
        for candidate in records:
            if candidate.get("_key") == doc_id:
                return {**candidate, "indexingStatus": ProgressStatus.IN_PROGRESS.value}
    return {"_key": doc_id, "indexingStatus": ProgressStatus.IN_PROGRESS.value}


def _document_lookup(gp, connector=None):
    """Build a get_document side_effect that answers the RECORDS status
    recheck the same way the default does, but overrides the APPS
    (connector-active check) lookup — for tests exercising the connector
    path without having to duplicate the record echo logic."""
    async def _lookup(doc_id, collection):
        if collection == CollectionNames.RECORDS.value:
            return _lookup_record_by_key(gp, doc_id)
        return connector
    return _lookup


def _make_graph_provider():
    """Build a mock graph_provider.

    get_document defaults to answering the RECORDS status recheck by
    echoing back the matching get_nodes_by_filters record (recovery always
    re-fetches before resetting a record — see fix-recovery-staleness), and
    the connector (APPS) lookup with None. Tests exercising the
    connector-check path override get_document via _document_lookup().
    """
    gp = MagicMock()
    gp.get_nodes_by_filters = AsyncMock(return_value=[])
    gp.batch_update_nodes = AsyncMock(return_value=True)

    async def default_get_document(doc_id, collection):
        if collection == CollectionNames.RECORDS.value:
            return _lookup_record_by_key(gp, doc_id)
        return None

    gp.get_document = AsyncMock(side_effect=default_get_document)
    gp.update_node = AsyncMock(return_value=True)

    async def get_documents_paginated(*_args, **_kwargs):
        records = await gp.get_nodes_by_filters()
        filters = _kwargs.get("filters") or {}
        if "parsingStatus" in filters:
            return [
                record
                for record in records
                if record.get("parsingStatus") == filters["parsingStatus"]
            ]
        return records

    gp.get_documents_paginated = AsyncMock(side_effect=get_documents_paginated)
    return gp


class _FakeConcurrencyManager:
    def __init__(self, owners=None):
        self.owners = dict(owners or {})

    async def try_acquire(self, pool, owner, _limit, _lease_seconds):
        if pool in self.owners and self.owners[pool] != owner:
            return False
        self.owners[pool] = owner
        return True

    async def renew(self, pool, owner, _lease_seconds):
        return self.owners.get(pool) == owner

    async def release(self, pool, owner):
        if self.owners.get(pool) == owner:
            self.owners.pop(pool)


# ---------------------------------------------------------------------------
# get_initialized_container
# ---------------------------------------------------------------------------
class TestGetInitializedContainer:
    """Tests for get_initialized_container()."""

    async def test_first_call_initializes(self):
        """First call runs initialize_container and wires."""
        mock_container = _make_container()

        with (
            patch("app.indexing_main.container", mock_container),
            patch("app.indexing_main.initialize_container", new_callable=AsyncMock) as mock_init,
            patch("app.indexing_main.container_lock", asyncio.Lock()),
        ):
            func = self._get_fresh_function()
            if hasattr(func, "initialized"):
                delattr(func, "initialized")

            result = await func()
            mock_init.assert_awaited_once_with(mock_container)
            mock_container.wire.assert_called_once()
            assert result is mock_container

    async def test_subsequent_calls_skip_initialization(self):
        """Second call does not re-initialize."""
        mock_container = _make_container()

        with (
            patch("app.indexing_main.container", mock_container),
            patch("app.indexing_main.initialize_container", new_callable=AsyncMock) as mock_init,
            patch("app.indexing_main.container_lock", asyncio.Lock()),
        ):
            func = self._get_fresh_function()
            if hasattr(func, "initialized"):
                delattr(func, "initialized")

            await func()
            await func()
            mock_init.assert_awaited_once()

    async def test_double_check_inside_lock_skips_if_already_initialized(self):
        """When 'initialized' is set between outer and inner hasattr check, inner check skips init."""
        mock_container = _make_container()

        func = self._get_fresh_function()

        # Create a custom lock that sets 'initialized' before releasing to the inner check.
        # This simulates: outer hasattr returns False, we acquire lock, but another coroutine
        # already finished init (set the flag) before we do the inner check.
        class RiggedLock:
            """A lock that sets func.initialized=True during __aenter__,
            simulating that another coroutine finished init while we waited."""
            async def __aenter__(self):
                func.initialized = True
                return self
            async def __aexit__(self, *args):
                pass

        with (
            patch("app.indexing_main.container", mock_container),
            patch("app.indexing_main.initialize_container", new_callable=AsyncMock) as mock_init,
            patch("app.indexing_main.container_lock", RiggedLock()),
        ):
            # Clear the flag INSIDE the patch context, right before calling
            if hasattr(func, "initialized"):
                delattr(func, "initialized")

            # The outer check sees no 'initialized', enters lock context.
            # RiggedLock sets 'initialized' in __aenter__.
            # Inner hasattr check sees 'initialized' => skips init.
            result = await func()
            mock_init.assert_not_awaited()
            assert result is mock_container

    def _get_fresh_function(self):
        """Import the function fresh."""
        from app.indexing_main import get_initialized_container
        return get_initialized_container


# ---------------------------------------------------------------------------
# recover_in_progress_records
# ---------------------------------------------------------------------------
class TestRecoverInProgressRecords:
    """Tests for recover_in_progress_records().

    Recovery is lightweight: it resets a stuck record to QUEUED and
    republishes an event to Kafka via the retry producer, rather than
    running the indexing pipeline inline. These tests assert against
    graph_provider.update_node (the reset) and the mock producer's
    send_event (the republish), not against a pipeline handler.
    """

    async def test_no_records_to_recover(self):
        """No records returns immediately."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()
        gp.get_nodes_by_filters = AsyncMock(return_value=[])

        await recover_in_progress_records(mock_container, gp)

        producer = mock_container.kafka_consumers[0][2]
        producer.send_event.assert_not_awaited()

    async def test_fresh_in_progress_record_is_not_recovered(self):
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()
        gp.get_nodes_by_filters = AsyncMock(
            return_value=[
                {
                    "_key": "r1",
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                    "processingStartedAt": int(time.time() * 1000),
                }
            ]
        )

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_not_awaited()
        mock_container.kafka_consumers[0][2].send_event.assert_not_awaited()

    async def test_active_record_lease_is_not_recovered(self):
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        mock_container.kafka_consumers[0][1].concurrency_manager = (
            _FakeConcurrencyManager({"record:r1": "active-worker"})
        )
        gp = _make_graph_provider()
        gp.get_nodes_by_filters = AsyncMock(
            return_value=[
                {
                    "_key": "r1",
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                    "processingStartedAt": 0,
                }
            ]
        )

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_not_awaited()
        mock_container.kafka_consumers[0][2].send_event.assert_not_awaited()

    async def test_unowned_fresh_record_is_not_recovered_within_lease_window(self):
        """A Redis flush can wipe every lease while a worker is still
        genuinely mid-processing — an unowned lease alone must not be enough
        to recover a record whose processingStartedAt is still within one
        lease interval (see fix-recovery-staleness)."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        mock_container.kafka_consumers[0][1].concurrency_manager = (
            _FakeConcurrencyManager()
        )
        gp = _make_graph_provider()
        gp.get_nodes_by_filters = AsyncMock(
            return_value=[
                {
                    "_key": "r1",
                    "recordName": "active.pdf",
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                    "processingStartedAt": int(time.time() * 1000),
                }
            ]
        )

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_not_awaited()
        mock_container.kafka_consumers[0][2].send_event.assert_not_awaited()

    async def test_recovery_skips_record_that_completed_mid_scan(self):
        """The status recheck before reset is unconditional (not just in
        distributed mode) — a record that finishes between the initial
        stale scan and the recheck a few records later must not be reset
        back to QUEUED and reindexed (see fix-recovery-staleness)."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()
        old_started_at = int((time.time() - 200) * 1000)
        gp.get_nodes_by_filters = AsyncMock(
            return_value=[
                {
                    "_key": "r1",
                    "recordName": "finished.pdf",
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                    "processingStartedAt": old_started_at,
                }
            ]
        )

        async def get_document_race(doc_id, collection):
            if collection == CollectionNames.RECORDS.value:
                # By the time recovery re-fetches it, the record has already
                # completed on its own — the stale scan above is now stale.
                return {
                    "_key": doc_id,
                    "recordName": "finished.pdf",
                    "indexingStatus": ProgressStatus.COMPLETED.value,
                }
            return None

        gp.get_document = AsyncMock(side_effect=get_document_race)

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_not_awaited()
        mock_container.kafka_consumers[0][2].send_event.assert_not_awaited()

    async def test_unowned_old_record_is_recovered_with_distributed_lock(self):
        """Once processingStartedAt is older than one lease interval, an
        unowned record is recovered even in distributed mode."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        mock_container.kafka_consumers[0][1].concurrency_manager = (
            _FakeConcurrencyManager()
        )
        gp = _make_graph_provider()
        old_started_at = int((time.time() - 200) * 1000)
        gp.get_nodes_by_filters = AsyncMock(
            return_value=[
                {
                    "_key": "r1",
                    "recordName": "orphaned.pdf",
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                    "processingStartedAt": old_started_at,
                }
            ]
        )
        # get_document defaults to echoing back the matching
        # get_nodes_by_filters record (see _lookup_record_by_key).

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_awaited_once()
        mock_container.kafka_consumers[0][2].send_event.assert_awaited_once()

    async def test_large_stale_backlog_leaves_only_active_leases_in_progress(self):
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        records = {
            f"r{index:03d}": {
                "_key": f"r{index:03d}",
                "recordName": f"record-{index}.pdf",
                "origin": "UPLOAD",
                "indexingStatus": ProgressStatus.IN_PROGRESS.value,
            }
            for index in range(170)
        }
        active_ids = set(list(records)[:10])
        manager = _FakeConcurrencyManager(
            {f"record:{record_id}": "active-worker" for record_id in active_ids}
        )
        mock_container.kafka_consumers[0][1].concurrency_manager = manager

        gp = _make_graph_provider()

        async def get_page(
            _collection,
            *,
            skip,
            limit,
            filters,
            **_kwargs,
        ):
            field, value = next(iter(filters.items()))
            matching = sorted(
                (
                    record
                    for record in records.values()
                    if record.get(field) == value
                ),
                key=lambda record: record["_key"],
            )
            return matching[skip : skip + limit]

        async def update_record(record_id, _collection, fields):
            records[record_id].update(fields)
            return True

        gp.get_documents_paginated = AsyncMock(side_effect=get_page)
        gp.get_document = AsyncMock(
            side_effect=lambda record_id, _collection: records.get(record_id)
        )
        gp.update_node = AsyncMock(side_effect=update_record)

        await recover_in_progress_records(mock_container, gp)

        remaining = {
            record_id
            for record_id, record in records.items()
            if record["indexingStatus"] == ProgressStatus.IN_PROGRESS.value
        }
        assert remaining == active_ids
        assert mock_container.kafka_consumers[0][2].send_event.await_count == 160

    async def test_expired_processing_lease_is_recovered(self, monkeypatch):
        from app.indexing_main import recover_in_progress_records

        monkeypatch.setenv("STALE_INDEXING_RECOVERY_AFTER_SECONDS", "10")
        mock_container = _make_container()
        gp = _make_graph_provider()
        gp.get_nodes_by_filters = AsyncMock(
            return_value=[
                {
                    "_key": "r1",
                    "recordName": "expired.pdf",
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                    "processingStartedAt": int((time.time() - 11) * 1000),
                }
            ]
        )

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_awaited_once()
        mock_container.kafka_consumers[0][2].send_event.assert_awaited_once()

    async def test_no_producer_available_leaves_records_in_progress(self):
        """Without a retry producer, recovery skips re-queueing entirely."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        mock_container.kafka_consumers = []
        gp = _make_graph_provider()

        in_progress = [{"_key": "r1", "recordName": "test.pdf", "version": 0, "orgId": "org1"}]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_not_awaited()

    async def test_in_progress_record_requeued_successfully(self):
        """A stuck record is reset to QUEUED and republished."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()

        in_progress = [{"_key": "r1", "recordName": "test.pdf", "version": 0, "orgId": "org1"}]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_awaited_once()
        reset_args = gp.update_node.await_args
        assert reset_args.args[0] == "r1"
        assert reset_args.args[2]["indexingStatus"] == ProgressStatus.QUEUED.value
        assert reset_args.args[2]["extractionStatus"] == ProgressStatus.NOT_STARTED.value

        producer = mock_container.kafka_consumers[0][2]
        producer.send_event.assert_awaited_once()
        send_kwargs = producer.send_event.await_args.kwargs
        assert send_kwargs["topic"] == "record-events"
        assert send_kwargs["payload"]["recordId"] == "r1"
        assert send_kwargs["key"] == "r1"

    async def test_in_progress_record_reindex_when_version_gt_zero_and_virtual_record_id(self):
        """Record with version > 0 and virtualRecordId is treated as REINDEX_RECORD."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()

        in_progress = [{
            "_key": "r1",
            "recordName": "test.pdf",
            "version": 2,
            "orgId": "org1",
            "virtualRecordId": "vr1",
        }]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)

        await recover_in_progress_records(mock_container, gp)

        producer = mock_container.kafka_consumers[0][2]
        assert producer.send_event.await_args.kwargs["event_type"] == "reindexRecord"

    async def test_in_progress_record_new_record_when_version_zero(self):
        """Record with version 0 is treated as NEW_RECORD."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()

        in_progress = [{
            "_key": "r1",
            "recordName": "test.pdf",
            "version": 0,
            "orgId": "org1",
            "virtualRecordId": "vr1",
        }]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)

        await recover_in_progress_records(mock_container, gp)

        producer = mock_container.kafka_consumers[0][2]
        assert producer.send_event.await_args.kwargs["event_type"] == "newRecord"

    async def test_in_progress_record_new_record_when_no_virtual_record_id(self):
        """Record with version > 0 but no virtualRecordId is treated as NEW_RECORD."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()

        in_progress = [{
            "_key": "r1",
            "recordName": "test.pdf",
            "version": 3,
            "orgId": "org1",
        }]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)

        await recover_in_progress_records(mock_container, gp)

        producer = mock_container.kafka_consumers[0][2]
        assert producer.send_event.await_args.kwargs["event_type"] == "newRecord"

    async def test_connector_not_found_skips_record(self):
        """A deleted connector leaves no stale IN_PROGRESS marker."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()

        in_progress = [{
            "_key": "r1",
            "recordName": "test.pdf",
            "connectorId": "c1",
            "origin": "CONNECTOR",
        }]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)
        # RECORDS lookup (status recheck) still finds it IN_PROGRESS by
        # default; only the APPS (connector) lookup returns None here.
        gp.get_document = AsyncMock(side_effect=_document_lookup(gp, connector=None))

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_awaited_once()
        updates = gp.update_node.await_args.args[2]
        assert updates["parsingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
        assert updates["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
        assert updates["extractionStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
        producer = mock_container.kafka_consumers[0][2]
        producer.send_event.assert_not_awaited()

    async def test_inactive_connector_skips_and_updates_record(self):
        """Record with inactive connector is skipped and status set to AUTO_INDEX_OFF."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()

        in_progress = [{
            "_key": "r1",
            "recordName": "test.pdf",
            "connectorId": "c1",
            "origin": "CONNECTOR",
        }]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)
        gp.get_document = AsyncMock(
            side_effect=_document_lookup(gp, connector={"isActive": False})
        )

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_awaited_once()
        updates = gp.update_node.await_args.args[2]
        assert updates["parsingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
        assert updates["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
        assert updates["extractionStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
        producer = mock_container.kafka_consumers[0][2]
        producer.send_event.assert_not_awaited()

    async def test_failed_status_reset_does_not_republish(self):
        """Single-instance fallback resets before publishing."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()
        gp.get_nodes_by_filters = AsyncMock(
            side_effect=[
                [{"_key": "r1", "recordName": "test.pdf", "origin": "UPLOAD"}],
                [],
            ]
        )
        gp.update_node = AsyncMock(return_value=False)

        await recover_in_progress_records(mock_container, gp)

        producer = mock_container.kafka_consumers[0][2]
        producer.send_event.assert_not_awaited()

    async def test_distributed_recovery_publishes_before_status_reset(self):
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        mock_container.kafka_consumers[0][1].concurrency_manager = (
            _FakeConcurrencyManager()
        )
        gp = _make_graph_provider()
        gp.get_nodes_by_filters = AsyncMock(
            return_value=[
                {
                    "_key": "r1",
                    "recordName": "test.pdf",
                    "origin": "UPLOAD",
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                }
            ]
        )
        gp.get_document = AsyncMock(
            return_value={
                "_key": "r1",
                "recordName": "test.pdf",
                "origin": "UPLOAD",
                "indexingStatus": ProgressStatus.IN_PROGRESS.value,
            }
        )
        gp.update_node = AsyncMock(return_value=False)

        await recover_in_progress_records(mock_container, gp)

        mock_container.kafka_consumers[0][2].send_event.assert_awaited_once()

    async def test_recovery_rechecks_status_after_acquiring_record_lease(
        self,
    ) -> None:
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        mock_container.kafka_consumers[0][1].concurrency_manager = (
            _FakeConcurrencyManager()
        )
        gp = _make_graph_provider()
        gp.get_nodes_by_filters = AsyncMock(
            return_value=[
                {
                    "_key": "r1",
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                }
            ]
        )
        gp.get_document = AsyncMock(
            return_value={
                "_key": "r1",
                "indexingStatus": ProgressStatus.COMPLETED.value,
            }
        )

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_not_awaited()
        mock_container.kafka_consumers[0][2].send_event.assert_not_awaited()

    async def test_recovery_publishes_on_coordination_loop(self) -> None:
        from contextvars import ContextVar

        from app.indexing_main import recover_in_progress_records

        on_coordination_loop = ContextVar(
            "on_coordination_loop",
            default=False,
        )

        async def run_coordinated(coro) -> object:
            token = on_coordination_loop.set(True)
            try:
                return await coro
            finally:
                on_coordination_loop.reset(token)

        class LoopBoundProducer:
            def __init__(self) -> None:
                self.send_count = 0

            async def send_event(self, **_kwargs) -> None:
                assert on_coordination_loop.get()
                self.send_count += 1

        mock_container = _make_container()
        consumer = mock_container.kafka_consumers[0][1]
        consumer.concurrency_manager = _FakeConcurrencyManager()
        consumer._run_on_main_loop = run_coordinated
        producer = LoopBoundProducer()
        mock_container.kafka_consumers = [("record", consumer, producer)]
        gp = _make_graph_provider()
        gp.get_nodes_by_filters = AsyncMock(
            return_value=[
                {
                    "_key": "r1",
                    "recordName": "test.pdf",
                    "origin": "UPLOAD",
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                }
            ]
        )
        gp.get_document = AsyncMock(
            return_value={
                "_key": "r1",
                "recordName": "test.pdf",
                "origin": "UPLOAD",
                "indexingStatus": ProgressStatus.IN_PROGRESS.value,
            }
        )

        await recover_in_progress_records(mock_container, gp)

        assert producer.send_count == 1

    async def test_active_connector_processes_record(self):
        """Record with active connector is reset and republished normally."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()

        in_progress = [{
            "_key": "r1",
            "recordName": "test.pdf",
            "connectorId": "c1",
            "origin": "CONNECTOR",
            "version": 0,
            "orgId": "org1",
        }]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)
        gp.get_document = AsyncMock(
            side_effect=_document_lookup(gp, connector={"isActive": True})
        )

        await recover_in_progress_records(mock_container, gp)

        gp.update_node.assert_awaited_once()
        producer = mock_container.kafka_consumers[0][2]
        producer.send_event.assert_awaited_once()

    async def test_record_processing_exception(self):
        """Exception processing a single record (e.g. republish failure) is caught."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()
        producer = mock_container.kafka_consumers[0][2]
        producer.send_event = AsyncMock(side_effect=RuntimeError("kafka publish error"))

        in_progress = [{"_key": "r1", "recordName": "test.pdf", "version": 0, "orgId": "org1"}]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)

        # Should not raise
        await recover_in_progress_records(mock_container, gp)

        assert gp.update_node.await_count == 2
        restored = gp.update_node.await_args_list[-1].args[2]
        assert restored["indexingStatus"] == ProgressStatus.IN_PROGRESS.value
        assert restored["processingStartedAt"] == 0

    async def test_top_level_exception_caught(self):
        """Top-level exception during recovery is caught and logged."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()
        gp.get_nodes_by_filters = AsyncMock(side_effect=RuntimeError("db connection error"))

        # Should not raise
        await recover_in_progress_records(mock_container, gp)

    async def test_record_without_connector_origin_processes_directly(self):
        """Record with origin != CONNECTOR skips connector check."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()

        in_progress = [{
            "_key": "r1",
            "recordName": "test.pdf",
            "connectorId": "c1",
            "origin": "UPLOAD",
            "version": 0,
            "orgId": "org1",
        }]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)

        await recover_in_progress_records(mock_container, gp)

        # get_document is called once for the unconditional status recheck,
        # but the connector-existence check is skipped since origin is
        # UPLOAD, not CONNECTOR.
        gp.get_document.assert_awaited_once()
        producer = mock_container.kafka_consumers[0][2]
        producer.send_event.assert_awaited_once()

    async def test_record_without_connector_id_processes_directly(self):
        """Record without connectorId skips connector check."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()

        in_progress = [{
            "_key": "r1",
            "recordName": "test.pdf",
            "origin": "CONNECTOR",
            "version": 0,
            "orgId": "org1",
        }]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)

        await recover_in_progress_records(mock_container, gp)

        # get_document is called once for the unconditional status recheck,
        # but the connector-existence check is skipped since connectorId is
        # missing.
        gp.get_document.assert_awaited_once()
        producer = mock_container.kafka_consumers[0][2]
        producer.send_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# start_kafka_consumers (indexing)
# ---------------------------------------------------------------------------
class TestStartKafkaConsumers:
    """Tests for start_kafka_consumers()."""

    async def test_success_non_neo4j(self):
        """Record consumer is started successfully for non-neo4j data store."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_consumer = MagicMock()
        mock_consumer.start = AsyncMock()
        
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.KafkaUtils.create_record_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "arangodb"}),
        ):
            consumers = await start_kafka_consumers(mock_container)

        assert len(consumers) == 1
        assert consumers[0][0] == "record"
        assert consumers[0][1] == mock_consumer
        assert consumers[0][2] == mock_producer

    async def test_success_neo4j_with_reconnect(self):
        """Neo4j data store triggers graph provider reconnection."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_driver = MagicMock()
        mock_driver.close = AsyncMock()
        mock_client = MagicMock()
        mock_client.driver = mock_driver
        mock_client.connect = AsyncMock()
        mock_gp = MagicMock()
        mock_gp.client = mock_client
        mock_container._graph_provider = mock_gp

        mock_worker_loop = MagicMock()
        mock_worker_loop.is_running.return_value = True

        mock_consumer = MagicMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.initialize = AsyncMock()
        mock_consumer.worker_loop = mock_worker_loop
        
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()

        def discard_reconnect(coro, _loop):
            coro.close()
            future = asyncio.get_event_loop().create_future()
            future.set_result(None)
            return future

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.KafkaUtils.create_record_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "neo4j"}),
            patch(
                "app.indexing_main.asyncio.run_coroutine_threadsafe",
                side_effect=discard_reconnect,
            ),
            patch("app.indexing_main.asyncio.wrap_future", new_callable=AsyncMock),
        ):
            consumers = await start_kafka_consumers(mock_container)

        assert len(consumers) == 1
        assert consumers[0][0] == "record"
        assert consumers[0][1] == mock_consumer
        assert consumers[0][2] == mock_producer
        mock_consumer.initialize.assert_awaited_once()

    async def test_neo4j_no_graph_provider_raises(self):
        """Neo4j without graph provider raises."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_container._graph_provider = None

        mock_consumer = MagicMock()
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "neo4j"}),
        ):
            with pytest.raises(Exception, match="Neo4j Graph provider not initialized"):
                await start_kafka_consumers(mock_container)

    async def test_neo4j_no_client_raises(self):
        """Neo4j with graph provider but no client raises."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_gp = MagicMock(spec=[])  # no 'client' attribute
        mock_container._graph_provider = mock_gp

        mock_consumer = MagicMock()
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "neo4j"}),
        ):
            with pytest.raises(Exception, match="Neo4j Graph provider not initialized"):
                await start_kafka_consumers(mock_container)

    async def test_neo4j_worker_loop_not_running_raises(self):
        """Neo4j with non-running worker loop raises."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_gp = MagicMock()
        mock_gp.client = MagicMock()
        mock_container._graph_provider = mock_gp

        mock_consumer = MagicMock()
        mock_consumer.initialize = AsyncMock()
        mock_consumer.worker_loop = MagicMock()
        mock_consumer.worker_loop.is_running.return_value = False
        
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "neo4j"}),
        ):
            with pytest.raises(Exception, match="Worker loop not initialized"):
                await start_kafka_consumers(mock_container)

    async def test_neo4j_no_worker_loop_raises(self):
        """Neo4j with no worker loop attribute raises."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_gp = MagicMock()
        mock_gp.client = MagicMock()
        mock_container._graph_provider = mock_gp

        mock_consumer = MagicMock()
        mock_consumer.initialize = AsyncMock()
        mock_consumer.worker_loop = None  # no worker loop
        
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "neo4j"}),
        ):
            with pytest.raises(Exception, match="Worker loop not initialized"):
                await start_kafka_consumers(mock_container)

    async def test_error_cleans_up_started_consumers(self):
        """Error starting consumers cleans up any already started."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_consumer = MagicMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()
        
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()
        mock_producer.cleanup = AsyncMock()

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.KafkaUtils.create_record_message_handler", new_callable=AsyncMock, side_effect=RuntimeError("handler fail")),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "arangodb"}),
        ):
            with pytest.raises(RuntimeError, match="handler fail"):
                await start_kafka_consumers(mock_container)

    async def test_cleanup_error_during_consumer_cleanup(self):
        """Cleanup error is logged but original error still propagated."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_consumer = MagicMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock(side_effect=RuntimeError("cleanup fail"))
        
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()
        mock_producer.cleanup = AsyncMock()

        call_count = 0

        async def start_side_effect(handler):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("start fail")

        mock_consumer.start = AsyncMock(side_effect=start_side_effect)

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.KafkaUtils.create_record_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "arangodb"}),
        ):
            with pytest.raises(RuntimeError, match="start fail"):
                await start_kafka_consumers(mock_container)

    async def test_neo4j_reconnect_with_existing_driver(self):
        """Neo4j reconnect closes existing driver before reconnecting."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_driver = MagicMock()
        mock_driver.close = AsyncMock()
        mock_client = MagicMock()
        mock_client.driver = mock_driver
        mock_client.connect = AsyncMock()
        mock_gp = MagicMock()
        mock_gp.client = mock_client
        mock_container._graph_provider = mock_gp

        mock_worker_loop = MagicMock()
        mock_worker_loop.is_running.return_value = True

        mock_consumer = MagicMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.initialize = AsyncMock()
        mock_consumer.worker_loop = mock_worker_loop
        
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()

        # To test the _reconnect function body, we need run_coroutine_threadsafe to
        # actually execute the coroutine. We'll capture the coroutine and run it.
        captured_coro = None

        def capture_coro(coro, _loop):
            nonlocal captured_coro
            captured_coro = coro
            future = asyncio.get_event_loop().create_future()
            future.set_result(None)
            return future

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.KafkaUtils.create_record_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "neo4j"}),
            patch(
                "app.indexing_main.asyncio.run_coroutine_threadsafe",
                side_effect=capture_coro,
            ),
            patch("app.indexing_main.asyncio.wrap_future", new_callable=AsyncMock),
        ):
            await start_kafka_consumers(mock_container)

        # Now run the captured coroutine to exercise _reconnect
        assert captured_coro is not None
        await captured_coro
        mock_driver.close.assert_awaited_once()
        mock_client.connect.assert_awaited_once()
        assert mock_client.driver is None  # driver was set to None

    async def test_neo4j_reconnect_driver_close_fails(self):
        """Neo4j reconnect handles driver close failure gracefully."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_driver = MagicMock()
        mock_driver.close = AsyncMock(side_effect=RuntimeError("close fail"))
        mock_client = MagicMock()
        mock_client.driver = mock_driver
        mock_client.connect = AsyncMock()
        mock_gp = MagicMock()
        mock_gp.client = mock_client
        mock_container._graph_provider = mock_gp

        mock_worker_loop = MagicMock()
        mock_worker_loop.is_running.return_value = True

        mock_consumer = MagicMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.initialize = AsyncMock()
        mock_consumer.worker_loop = mock_worker_loop
        
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()

        captured_coro = None

        def capture_coro(coro, _loop):
            nonlocal captured_coro
            captured_coro = coro
            future = asyncio.get_event_loop().create_future()
            future.set_result(None)
            return future

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.KafkaUtils.create_record_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "neo4j"}),
            patch(
                "app.indexing_main.asyncio.run_coroutine_threadsafe",
                side_effect=capture_coro,
            ),
            patch("app.indexing_main.asyncio.wrap_future", new_callable=AsyncMock),
        ):
            await start_kafka_consumers(mock_container)

        # Run the captured coroutine - close fails but connect still called
        assert captured_coro is not None
        await captured_coro
        mock_client.connect.assert_awaited_once()

    async def test_neo4j_reconnect_no_driver(self):
        """Neo4j reconnect when driver is None (falsy) skips close."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_client = MagicMock()
        mock_client.driver = None  # No existing driver
        mock_client.connect = AsyncMock()
        mock_gp = MagicMock()
        mock_gp.client = mock_client
        mock_container._graph_provider = mock_gp

        mock_worker_loop = MagicMock()
        mock_worker_loop.is_running.return_value = True

        mock_consumer = MagicMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.initialize = AsyncMock()
        mock_consumer.worker_loop = mock_worker_loop
        
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()

        captured_coro = None

        def capture_coro(coro, _loop):
            nonlocal captured_coro
            captured_coro = coro
            future = asyncio.get_event_loop().create_future()
            future.set_result(None)
            return future

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.KafkaUtils.create_record_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "neo4j"}),
            patch(
                "app.indexing_main.asyncio.run_coroutine_threadsafe",
                side_effect=capture_coro,
            ),
            patch("app.indexing_main.asyncio.wrap_future", new_callable=AsyncMock),
        ):
            await start_kafka_consumers(mock_container)

        # Run the captured coroutine - no driver to close, just connect
        assert captured_coro is not None
        await captured_coro
        mock_client.connect.assert_awaited_once()


# ---------------------------------------------------------------------------
# stop_kafka_consumers
# ---------------------------------------------------------------------------
class TestStopKafkaConsumers:
    """Tests for stop_kafka_consumers()."""

    async def test_stops_all_consumers(self):
        """All consumers are stopped and list is cleared."""
        from app.indexing_main import stop_kafka_consumers

        mock_container = _make_container()
        c1 = MagicMock()
        c1.stop = AsyncMock()
        mock_container.kafka_consumers = [("record", c1)]

        await stop_kafka_consumers(mock_container)

        c1.stop.assert_awaited_once()
        assert mock_container.kafka_consumers == []

    async def test_empty_consumers_list(self):
        """No error when consumers list is empty."""
        from app.indexing_main import stop_kafka_consumers

        mock_container = _make_container()
        mock_container.kafka_consumers = []

        await stop_kafka_consumers(mock_container)

    async def test_no_kafka_consumers_attr(self):
        """No error when kafka_consumers attribute does not exist."""
        from app.indexing_main import stop_kafka_consumers

        class Container:
            pass
        c = Container()
        c.logger = MagicMock(return_value=MagicMock())

        await stop_kafka_consumers(c)

    async def test_error_stopping_consumer_continues(self):
        """Error stopping one consumer does not prevent stopping others."""
        from app.indexing_main import stop_kafka_consumers

        mock_container = _make_container()
        c1 = MagicMock()
        c1.stop = AsyncMock(side_effect=RuntimeError("stop fail"))
        c2 = MagicMock()
        c2.stop = AsyncMock()
        mock_container.kafka_consumers = [("record", c1), ("entity", c2)]

        await stop_kafka_consumers(mock_container)
        c2.stop.assert_awaited_once()
        assert mock_container.kafka_consumers == []


# ---------------------------------------------------------------------------
# lifespan
# ---------------------------------------------------------------------------
class TestLifespan:
    """Tests for lifespan() context manager."""

    async def test_startup_and_shutdown(self):
        """Full lifespan cycle."""
        from app.indexing_main import lifespan

        mock_container = _make_container()
        mock_gp = _make_graph_provider()
        mock_container._graph_provider = mock_gp

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("app.indexing_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.indexing_main.recover_in_progress_records", new_callable=AsyncMock),
            patch("app.indexing_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[("record", MagicMock())]),
            patch("app.indexing_main.stop_kafka_consumers", new_callable=AsyncMock) as mock_stop,
        ):
            async with lifespan(mock_app):
                assert mock_app.container is mock_container
                assert mock_app.state.graph_provider is mock_gp

            mock_stop.assert_awaited_once()
            mock_container.config_service().close.assert_awaited()

    async def test_graph_provider_fallback(self):
        """When _graph_provider is not set, it falls back to graph_provider()."""
        from app.indexing_main import lifespan

        mock_container = _make_container()
        mock_container._graph_provider = None
        mock_gp = _make_graph_provider()
        mock_container.graph_provider = AsyncMock(return_value=mock_gp)

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("app.indexing_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.indexing_main.recover_in_progress_records", new_callable=AsyncMock),
            patch("app.indexing_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.indexing_main.stop_kafka_consumers", new_callable=AsyncMock),
        ):
            async with lifespan(mock_app):
                assert mock_app.state.graph_provider is mock_gp

    async def test_recovery_failure_does_not_raise(self):
        """Recovery failure does not prevent startup."""
        from app.indexing_main import lifespan

        mock_container = _make_container()
        mock_container._graph_provider = _make_graph_provider()

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("app.indexing_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.indexing_main.recover_in_progress_records", new_callable=AsyncMock, side_effect=RuntimeError("recovery fail")),
            patch("app.indexing_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.indexing_main.stop_kafka_consumers", new_callable=AsyncMock),
        ):
            async with lifespan(mock_app):
                pass  # Should not raise

    async def test_kafka_consumer_failure_raises(self):
        """If Kafka consumers fail to start, the lifespan raises."""
        from app.indexing_main import lifespan

        mock_container = _make_container()
        mock_container._graph_provider = _make_graph_provider()

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("app.indexing_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.indexing_main.recover_in_progress_records", new_callable=AsyncMock),
            patch("app.indexing_main.start_kafka_consumers", new_callable=AsyncMock, side_effect=RuntimeError("kafka fail")),
        ):
            with pytest.raises(RuntimeError, match="kafka fail"):
                async with lifespan(mock_app):
                    pass

    async def test_shutdown_stop_consumers_error_caught(self):
        """Error stopping consumers during shutdown is caught."""
        from app.indexing_main import lifespan

        mock_container = _make_container()
        mock_container._graph_provider = _make_graph_provider()

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("app.indexing_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.indexing_main.recover_in_progress_records", new_callable=AsyncMock),
            patch("app.indexing_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.indexing_main.stop_kafka_consumers", new_callable=AsyncMock, side_effect=RuntimeError("stop fail")),
        ):
            async with lifespan(mock_app):
                pass  # Shutdown should not raise

    async def test_shutdown_config_service_close_error_caught(self):
        """Error closing config service during shutdown is caught."""
        from app.indexing_main import lifespan

        mock_container = _make_container()
        mock_container._graph_provider = _make_graph_provider()
        mock_container.config_service.return_value.close = AsyncMock(side_effect=RuntimeError("close fail"))

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch("app.indexing_main.get_initialized_container", new_callable=AsyncMock, return_value=mock_container),
            patch("app.indexing_main.recover_in_progress_records", new_callable=AsyncMock),
            patch("app.indexing_main.start_kafka_consumers", new_callable=AsyncMock, return_value=[]),
            patch("app.indexing_main.stop_kafka_consumers", new_callable=AsyncMock),
        ):
            async with lifespan(mock_app):
                pass  # Shutdown should not raise


# ---------------------------------------------------------------------------
# health_check (indexing)
# ---------------------------------------------------------------------------
class TestIndexingHealthCheck:
    """Tests for health_check() endpoint."""

    async def test_health_check_success(self):
        """Health check returns healthy status."""
        from app.indexing_main import health_check

        with patch("app.indexing_main.get_epoch_timestamp_in_ms", return_value=1234567890):
            result = await health_check()

        assert result.status_code == 200
        assert result.body is not None

    async def test_health_check_includes_timestamp(self):
        """Health check response includes timestamp."""
        import json
        from app.indexing_main import health_check

        with patch("app.indexing_main.get_epoch_timestamp_in_ms", return_value=1234567890):
            result = await health_check()

        body = json.loads(result.body)
        assert body["status"] == "healthy"
        assert body["timestamp"] == 1234567890

    async def test_health_check_general_exception(self):
        """Health check returns 500 when get_epoch_timestamp_in_ms raises on first call."""
        from app.indexing_main import health_check

        mock_ts = MagicMock(side_effect=[RuntimeError("timestamp error"), 9999999])
        with patch("app.indexing_main.get_epoch_timestamp_in_ms", mock_ts):
            result = await health_check()

        assert result.status_code == 500


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
class TestRun:
    """Tests for run() function."""

    def test_run_default_args(self):
        """run() invokes uvicorn with default arguments."""
        from app.indexing_main import run

        with patch("app.indexing_main.uvicorn.run") as mock_uvicorn:
            run()

        mock_uvicorn.assert_called_once_with(
            "app.indexing_main:app",
            host="0.0.0.0",
            port=8091,
            log_level="info",
            reload=True,
            workers=1,
        )

    def test_run_custom_args(self):
        """run() passes custom arguments to uvicorn."""
        from app.indexing_main import run

        with patch("app.indexing_main.uvicorn.run") as mock_uvicorn:
            run(host="127.0.0.1", port=9000, reload=False)

        mock_uvicorn.assert_called_once_with(
            "app.indexing_main:app",
            host="127.0.0.1",
            port=9000,
            log_level="info",
            reload=False,
            workers=1,
        )


# ---------------------------------------------------------------------------
# Module-level code
# ---------------------------------------------------------------------------
class TestModuleLevelCode:
    """Tests for module-level attributes."""

    def test_app_is_fastapi_instance(self):
        """The module-level app is a FastAPI instance."""
        from app.indexing_main import app
        from fastapi import FastAPI
        assert isinstance(app, FastAPI)

    def test_container_lock_is_asyncio_lock(self):
        """The module-level container_lock is an asyncio.Lock."""
        from app.indexing_main import container_lock
        assert isinstance(container_lock, asyncio.Lock)


# ---------------------------------------------------------------------------
# Additional tests to cover missing branches
# ---------------------------------------------------------------------------

class TestStartKafkaConsumersCleanupPath:
    """Cover the consumer cleanup loop (lines 291-296) which runs when
    consumers have been appended to the list before an error occurs."""

    async def test_cleanup_stops_appended_consumer_on_later_error(self):
        """Manually inject a consumer into the list, then force error in the cleanup path."""
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_consumer = MagicMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        # We need the consumer to be appended (line 284) before an error.
        # In the current code, append happens after start() succeeds.
        # The error at line 288 catches and iterates consumers.
        # For a single-consumer flow, the error must happen after append but
        # before return. One way: make create_record_message_handler
        # succeed first (so start is called) but make start raise AFTER
        # consumers.append is reached.
        # Actually: record_kafka_consumer.start is at line 283, append at 284.
        # If start succeeds, append happens, then return at 287 is reached.
        # We need an error between 284 and 287 - there is none in the
        # normal flow. However, we can test the cleanup path by monkey-patching.

        # Approach: patch the consumers list to already contain an item,
        # then trigger the error. We'll make the consumer creation succeed
        # but message_handler fail, which happens before start/append.
        # So we need to inject directly.

        # Direct approach: create a scenario where consumers has items and error occurs.
        # We'll achieve this by patching to use neo4j path which has more steps.
        mock_gp = MagicMock()
        mock_gp.client = MagicMock()
        mock_gp.client.driver = None
        mock_gp.client.connect = AsyncMock()
        mock_container._graph_provider = mock_gp

        mock_worker_loop = MagicMock()
        mock_worker_loop.is_running.return_value = True

        mock_consumer.initialize = AsyncMock()
        mock_consumer.worker_loop = mock_worker_loop
        
        mock_producer = MagicMock()
        mock_producer.initialize = AsyncMock()
        mock_producer.cleanup = AsyncMock()

        # Make start succeed (line 283) so consumer is appended (line 284)
        # Then make the second handler call fail - but there's only one consumer.
        # We actually need to cause an error INSIDE the try block after append.
        # Since append is immediately followed by logger.info and then return,
        # we make logger.info raise.
        call_count = 0
        original_info = mock_container.logger().info

        def info_side_effect(msg, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if "Record message consumer started" in str(msg):
                raise RuntimeError("post-append error")
            return original_info(msg, *args, **kwargs)

        mock_container.logger().info = MagicMock(side_effect=info_side_effect)

        def discard_reconnect(coro, _loop):
            coro.close()
            future = asyncio.get_event_loop().create_future()
            future.set_result(None)
            return future

        with (
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_producer_config_from_service", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.MessagingFactory.create_producer", return_value=mock_producer),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.KafkaUtils.create_record_message_handler", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_consumer", return_value=mock_consumer),
            patch.dict("os.environ", {"DATA_STORE": "neo4j"}),
            patch(
                "app.indexing_main.asyncio.run_coroutine_threadsafe",
                side_effect=discard_reconnect,
            ),
            patch("app.indexing_main.asyncio.wrap_future", new_callable=AsyncMock),
        ):
            with pytest.raises(RuntimeError, match="post-append error"):
                await start_kafka_consumers(mock_container)

        # Consumer should have been stopped during cleanup (lines 292-296)
        mock_consumer.stop.assert_awaited()


class TestRunWorkersWarning:
    """Cover the workers>1 + reload warning path (lines 424-430)."""

    def test_workers_gt_one_with_reload_warns(self):
        """When reload=True and workers>1, a RuntimeWarning is issued and workers resets to 1."""
        import warnings
        from app.indexing_main import run

        with (
            patch("app.indexing_main.uvicorn.run") as mock_uvicorn,
            patch.dict("os.environ", {"INDEXING_UVICORN_WORKERS": "4"}),
        ):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                run(reload=True)

            # Check that the warning was issued
            runtime_warnings = [x for x in w if issubclass(x.category, RuntimeWarning)]
            assert len(runtime_warnings) >= 1
            assert "not compatible with reload=True" in str(runtime_warnings[0].message)

            # Workers should have been reset to 1
            mock_uvicorn.assert_called_once_with(
                "app.indexing_main:app",
                host="0.0.0.0",
                port=8091,
                log_level="info",
                reload=True,
                workers=1,
            )

    def test_workers_gt_one_without_reload(self):
        """When reload=False and workers>1, no warning and workers is used as-is."""
        from app.indexing_main import run

        with (
            patch("app.indexing_main.uvicorn.run") as mock_uvicorn,
            patch.dict("os.environ", {"INDEXING_UVICORN_WORKERS": "4"}),
        ):
            run(reload=False)

        mock_uvicorn.assert_called_once_with(
            "app.indexing_main:app",
            host="0.0.0.0",
            port=8091,
            log_level="info",
            reload=False,
            workers=4,
        )

    def test_workers_from_env_default(self):
        """When INDEXING_UVICORN_WORKERS env is not set, defaults to 1."""
        from app.indexing_main import run
        import os as _os

        env = _os.environ.copy()
        env.pop("INDEXING_UVICORN_WORKERS", None)

        with (
            patch("app.indexing_main.uvicorn.run") as mock_uvicorn,
            patch.dict("os.environ", env, clear=True),
        ):
            run(reload=False)

        mock_uvicorn.assert_called_once()
        assert mock_uvicorn.call_args.kwargs.get("workers", mock_uvicorn.call_args[1].get("workers")) == 1


class TestRecoverInProgressRecordsAdditional:
    """Additional tests targeting the inner branch at line 136->145
    (record_id None check when connector is inactive)."""

    async def test_inactive_connector_record_id_none(self):
        """When connector is inactive and record has _key=None, update_node is skipped (line 136->145)."""
        from app.indexing_main import recover_in_progress_records

        mock_container = _make_container()
        gp = _make_graph_provider()

        # Record with _key=None
        in_progress = [{
            "_key": None,
            "recordName": "test.pdf",
            "connectorId": "c1",
            "origin": "CONNECTOR",
        }]
        gp.get_nodes_by_filters = AsyncMock(return_value=in_progress)
        gp.get_document = AsyncMock(return_value={"isActive": False})

        await recover_in_progress_records(mock_container, gp)

        # update_node should NOT be called because record_id is None
        gp.update_node.assert_not_awaited()
        producer = mock_container.kafka_consumers[0][2]
        producer.send_event.assert_not_awaited()
