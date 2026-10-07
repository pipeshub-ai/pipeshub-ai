"""Comprehensive unit tests for app.indexing_main module."""

import asyncio
import os
import time
from unittest.mock import ANY, AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.responses import JSONResponse

from app.config.constants.arangodb import (
    CollectionNames,
    EventTypes,
    OriginTypes,
    ProgressStatus,
)
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.messaging.config import MessageBrokerType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

@pytest.fixture(autouse=True)
def _no_inherited_worker_healthcheck_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UVICORN_WORKER_HEALTHCHECK_TIMEOUT_SECONDS", raising=False)



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
    async def _lookup(doc_id, collection, **_kwargs):
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

    async def default_get_document(doc_id, collection, **_kwargs):
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

        async def get_document_race(doc_id, collection, **_kwargs):
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

        # The stranded-record sweep runs in the same pass and re-sees this row,
        # because the stub returns a static page rather than reflecting the
        # update. Assert the recovery write itself rather than a call count.
        updates = gp.update_node.await_args_list[0].args[2]
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

        # The stranded-record sweep runs in the same pass and re-sees this row,
        # because the stub returns a static page rather than reflecting the
        # update. Assert the recovery write itself rather than a call count.
        updates = gp.update_node.await_args_list[0].args[2]
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
class TestRecoverCutShortEnrichment:
    """An indexed record whose enrichment never ended holds its duplicates QUEUED.

    Its start time is kept through enrichment, so it ages like any stale record;
    it stays searchable and is republished so the handler finishes it.
    """

    @staticmethod
    def _graph(records, connector=None, connector_read_fails=False) -> tuple[MagicMock, dict]:
        gp = MagicMock()
        by_key = {r["_key"]: r for r in records}

        async def paginated(*_a, filters=None, **_k) -> list[dict]:
            (field, value), = (filters or {}).items()
            return [dict(r) for r in by_key.values() if r.get(field) == value]

        async def get_document(doc_id, collection, *, raise_on_error=False, **_k) -> dict | None:
            if collection == CollectionNames.APPS.value:
                if connector_read_fails:
                    # Both providers log a failed read and answer None unless asked to raise.
                    if raise_on_error:
                        raise ConnectionError("graph unavailable")
                    return None
                return connector
            row = by_key.get(doc_id)
            return dict(row) if row else None

        async def update_node(doc_id, _collection, fields) -> bool:
            by_key[doc_id].update(fields)
            return True

        gp.get_documents_paginated = AsyncMock(side_effect=paginated)
        gp.get_document = AsyncMock(side_effect=get_document)
        gp.update_node = AsyncMock(side_effect=update_node)
        gp.get_nodes_by_filters = AsyncMock(return_value=[])
        return gp, by_key

    @staticmethod
    def _twin(started_at) -> dict:
        return {
            "_key": "twin", "recordName": "q3.pdf", "orgId": "org-1", "origin": "UPLOAD",
            "virtualRecordId": "vr-1", "version": 0,
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "parsingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.IN_PROGRESS.value,
            "processingStartedAt": started_at,
        }

    async def test_a_stale_one_is_republished_and_stays_searchable(self) -> None:
        from app.indexing_main import recover_in_progress_records

        container = _make_container()
        gp, rows = self._graph([self._twin(0)])

        await recover_in_progress_records(container, gp)

        producer = container.kafka_consumers[0][2]
        producer.send_event.assert_awaited_once()
        assert producer.send_event.await_args.kwargs["payload"]["recordId"] == "twin"
        assert rows["twin"]["indexingStatus"] == ProgressStatus.COMPLETED.value
        assert rows["twin"]["processingStartedAt"] > 0, "kept off the next scans while the event waits"

    async def test_a_failed_connector_read_never_turns_it_auto_index_off(self) -> None:
        from app.indexing_main import recover_in_progress_records

        container = _make_container()
        twin = {**self._twin(0), "origin": OriginTypes.CONNECTOR.value, "connectorId": "conn-1"}
        gp, rows = self._graph([twin], connector_read_fails=True)

        await recover_in_progress_records(container, gp)

        assert rows["twin"]["indexingStatus"] == ProgressStatus.COMPLETED.value
        assert rows["twin"]["extractionStatus"] == ProgressStatus.IN_PROGRESS.value
        container.kafka_consumers[0][2].send_event.assert_awaited_once()

    async def test_a_removed_connector_is_left_to_the_handler_to_release_the_copies(self) -> None:
        from app.indexing_main import recover_in_progress_records

        container = _make_container()
        twin = {**self._twin(0), "origin": OriginTypes.CONNECTOR.value, "connectorId": "conn-1"}
        gp, rows = self._graph([twin], connector=None)

        await recover_in_progress_records(container, gp)

        assert rows["twin"]["indexingStatus"] == ProgressStatus.COMPLETED.value, "still searchable"
        container.kafka_consumers[0][2].send_event.assert_awaited_once()

    async def test_a_failed_connector_read_leaves_a_stale_record_for_the_next_pass(self) -> None:
        from app.indexing_main import recover_in_progress_records

        container = _make_container()
        stale = {
            "_key": "r1", "recordName": "a.pdf", "orgId": "org-1", "version": 0,
            "origin": OriginTypes.CONNECTOR.value, "connectorId": "conn-1",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value, "processingStartedAt": 0,
        }
        gp, rows = self._graph([stale], connector_read_fails=True)

        await recover_in_progress_records(container, gp)

        assert rows["r1"]["indexingStatus"] == ProgressStatus.IN_PROGRESS.value, "not AUTO_INDEX_OFF on a blip"
        container.kafka_consumers[0][2].send_event.assert_not_awaited()

    async def test_a_live_enrichment_is_left_alone(self) -> None:
        from app.indexing_main import recover_in_progress_records

        container = _make_container()
        gp, rows = self._graph([self._twin(get_epoch_timestamp_in_ms())])

        await recover_in_progress_records(container, gp)

        container.kafka_consumers[0][2].send_event.assert_not_awaited()
        assert rows["twin"]["extractionStatus"] == ProgressStatus.IN_PROGRESS.value


class TestStartKafkaConsumers:
    """Tests for start_kafka_consumers()."""

    async def test_success_non_neo4j(self) -> None:
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

    async def test_success_neo4j(self) -> None:
        """Startup under Neo4j is now ordinary.

        This used to close the graph driver on the main loop and reconnect it
        onto the consumer's worker loop, with four guard clauses around it,
        because one driver cannot serve two loops. `Neo4jClient` keys its
        driver by loop now, so there is nothing here to special-case.
        """
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_container._graph_provider = MagicMock()

        mock_consumer = MagicMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.initialize = AsyncMock()

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
            patch.dict("os.environ", {"DATA_STORE": "neo4j"}),
        ):
            consumers = await start_kafka_consumers(mock_container)

        assert len(consumers) == 1
        assert consumers[0][0] == "record"
        assert consumers[0][1] == mock_consumer
        assert consumers[0][2] == mock_producer

    async def test_distributed_concurrency_failure_aborts_startup(self) -> None:
        """Redis is a startup requirement: an unreachable one fails the boot.

        The RetryManager ping just above needs the same Redis, so degrading to
        node-local limits here could never actually keep the service running.
        """
        from app.indexing_main import start_kafka_consumers

        mock_container = _make_container()
        mock_manager = MagicMock()
        mock_manager.initialize = AsyncMock(side_effect=RuntimeError("redis down"))
        mock_manager.cleanup = AsyncMock()

        with (
            patch.dict("os.environ", {"DISTRIBUTED_INDEXING_CONCURRENCY": "true", "DATA_STORE": "arangodb"}),
            patch("app.indexing_main.get_message_broker_type", return_value=MessageBrokerType.KAFKA),
            patch("app.indexing_main.MessagingUtils._get_redis_config", new_callable=AsyncMock, return_value=MagicMock()),
            patch("app.indexing_main.MessagingFactory.create_retry_manager", return_value=MagicMock(initialize=AsyncMock(), cleanup=AsyncMock())),
            patch("app.indexing_main.MessagingUtils.create_record_consumer_config", new_callable=AsyncMock, return_value={}),
            patch("app.indexing_main.DistributedConcurrencyManager", return_value=mock_manager),
        ):
            with pytest.raises(RuntimeError, match="redis down"):
                await start_kafka_consumers(mock_container)

    async def test_error_cleans_up_started_consumers(self) -> None:
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

    async def test_cleanup_error_during_consumer_cleanup(self) -> None:
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
def _make_health_request(governor=None):
    """Build a minimal mock Request exposing app.state.governor, since
    health_check reads the governor off request.app.state (see
    app/indexing_main.py's /health route)."""
    request = MagicMock()
    request.app.state = MagicMock()
    if governor is None:
        del request.app.state.governor
    else:
        request.app.state.governor = governor
    return request


class TestIndexingHealthCheck:
    """Tests for health_check() endpoint."""

    async def test_health_check_success(self):
        """Health check returns healthy status."""
        from app.indexing_main import health_check

        with patch("app.indexing_main.get_epoch_timestamp_in_ms", return_value=1234567890):
            result = await health_check(_make_health_request())

        assert result.status_code == 200
        assert result.body is not None

    async def test_health_check_includes_timestamp(self):
        """Health check response includes timestamp."""
        import json
        from app.indexing_main import health_check

        with patch("app.indexing_main.get_epoch_timestamp_in_ms", return_value=1234567890):
            result = await health_check(_make_health_request())

        body = json.loads(result.body)
        assert body["status"] == "healthy"
        assert body["timestamp"] == 1234567890

    async def test_health_check_includes_governor_stats(self):
        """When a governor is present on app.state, its stats are surfaced
        (see Phase 1/6 of the adaptive-concurrency plan)."""
        import json
        from app.indexing_main import health_check

        mock_governor = MagicMock()
        mock_governor.stats.return_value = {"ceilings": {"index": 5}}

        with patch("app.indexing_main.get_epoch_timestamp_in_ms", return_value=1234567890):
            result = await health_check(_make_health_request(governor=mock_governor))

        body = json.loads(result.body)
        assert body["resource_governor"] == {"ceilings": {"index": 5}}

    async def test_stats_failures_are_not_echoed(self):
        """Still healthy; the exceptions go to the log and the payload says only that stats are unavailable."""
        import json
        from app.indexing_main import health_check

        governor_error = RuntimeError("SENTINEL /sys/fs/cgroup/memory.max")
        dispatch_error = RuntimeError("SENTINEL redis://:hunter2@10.0.0.5:6379")
        mock_governor = MagicMock()
        mock_governor.stats.side_effect = governor_error
        consumer = MagicMock()
        consumer.dispatch_stats.side_effect = dispatch_error

        with patch("app.indexing_main.get_epoch_timestamp_in_ms", return_value=1234567890), patch(
            "app.indexing_main.container"
        ) as mock_container:
            mock_container.kafka_consumers = [("record", consumer)]
            result = await health_check(_make_health_request(governor=mock_governor))

        assert result.status_code == 200
        assert json.loads(result.body) == {
            "status": "healthy",
            "timestamp": 1234567890,
            "resource_governor": {"error": "unavailable"},
            "dispatch": {"record": {"error": "unavailable"}},
        }
        assert [c.args for c in mock_container.logger.return_value.warning.call_args_list] == [
            ("Resource governor stats failed: %s", governor_error),
            ("Dispatch stats failed for %s: %s", "record", dispatch_error),
        ]

    async def test_health_check_general_exception(self):
        """Health check returns 500 when get_epoch_timestamp_in_ms raises on first call."""
        from app.indexing_main import health_check

        mock_ts = MagicMock(side_effect=[RuntimeError("timestamp error"), 9999999])
        with patch("app.indexing_main.get_epoch_timestamp_in_ms", mock_ts):
            result = await health_check(_make_health_request())

        assert result.status_code == 500

    async def test_health_check_exception_is_not_echoed(self):
        """The exception goes to the log; the unauthenticated caller gets fixed text."""
        import json
        from app.indexing_main import health_check

        mock_ts = MagicMock(side_effect=[RuntimeError("SENTINEL timestamp error"), 9999999])
        with patch("app.indexing_main.get_epoch_timestamp_in_ms", mock_ts), patch(
            "app.indexing_main.container"
        ) as mock_container:
            result = await health_check(_make_health_request())

        assert result.status_code == 500
        assert json.loads(result.body) == {
            "status": "unhealthy",
            "error": "Health check failed",
            "timestamp": 9999999,
        }
        mock_container.logger.return_value.exception.assert_called_once_with("Health check failed")


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
            timeout_worker_healthcheck=60,
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
            timeout_worker_healthcheck=60,
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

        mock_gp = MagicMock()
        mock_gp.client = MagicMock()
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
                timeout_worker_healthcheck=60,
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
            timeout_worker_healthcheck=60,
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


# ---------------------------------------------------------------------------
# Queued/stranded records on disabled connectors
# ---------------------------------------------------------------------------


def _sweep_graph(pages, active_ids):
    """Graph stub paging by indexingStatus, with a fixed set of live connectors.

    The pagination signature is spelled out rather than absorbed by **kwargs so a
    caller using the wrong keyword fails here. AsyncMock accepts anything, which
    is how `offset=` shipped against providers whose parameter is `skip=`.
    """
    graph = AsyncMock()

    async def _paged(
        collection,
        skip=0,
        limit=50,
        filters=None,
        sort_field=None,
        transaction=None,
        raise_on_error=False,
    ):
        if skip:
            return []
        return pages.get((filters or {}).get("indexingStatus"), [])

    graph.get_documents_paginated = AsyncMock(side_effect=_paged)
    graph.get_document = AsyncMock(
        side_effect=lambda key, collection, **_kwargs: {"isActive": key in active_ids}
    )
    graph.update_node = AsyncMock()
    return graph


class TestSweepStrandedRecordsOnInactiveConnectors:
    @pytest.mark.asyncio
    async def test_a_connector_that_cannot_be_read_is_not_treated_as_deleted(self):
        """The sweep parks records as AUTO_INDEX_OFF, which nothing undoes.

        A deleted connector and an unreadable graph used to give the same
        answer, so a graph restart could park healthy records permanently --
        under a status that reads as somebody's deliberate setting rather than
        a failure, so nobody would go looking.
        """
        from app.indexing_main import _sweep_queued_records_for_inactive_connectors

        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [{"_key": "r1", "connectorId": "live", "origin": "CONNECTOR"}]},
            active_ids=set(),
        )
        async def unreadable_graph(*_args, raise_on_error: bool = False, **_kwargs):
            # What the providers do: swallow and answer None unless asked not
            # to. A double that raised either way would pass whether or not the
            # sweep asks for the truth, which is the whole change.
            if raise_on_error:
                raise RuntimeError("graph is restarting")
            return None

        graph.get_document = AsyncMock(side_effect=unreadable_graph)

        swept = await _sweep_queued_records_for_inactive_connectors(
            graph_provider=graph, logger=MagicMock(), page_size=100
        )

        assert swept == 0
        graph.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unreadable_connector_is_asked_about_once_per_pass(self):
        """Not once per record.

        On Neo4j a restart surfaces only after the 30s connection-acquisition
        timeout, so re-asking for each of a hundred records on a page would hold
        recovery for the best part of an hour and keep hammering the database
        that is trying to come back. The verdict is remembered for the pass and
        rebuilt on the next tick.
        """
        from app.indexing_main import _sweep_queued_records_for_inactive_connectors

        graph = _sweep_graph(
            {
                ProgressStatus.QUEUED.value: [
                    {"_key": f"r{n}", "connectorId": "live", "origin": "CONNECTOR"}
                    for n in range(5)
                ]
            },
            active_ids=set(),
        )

        async def unreadable_graph(*_args, raise_on_error: bool = False, **_kwargs):
            if raise_on_error:
                raise RuntimeError("graph is restarting")
            return None

        graph.get_document = AsyncMock(side_effect=unreadable_graph)

        swept = await _sweep_queued_records_for_inactive_connectors(
            graph_provider=graph, logger=MagicMock(), page_size=100
        )

        assert swept == 0
        assert graph.get_document.await_count == 1, (
            "the connector was read once per record rather than once per pass"
        )

    @pytest.mark.asyncio
    async def test_queued_records_on_disabled_connector_are_moved(self):
        """QUEUED rows are invisible to the main stale scan.

        It filters on IN_PROGRESS, and a QUEUED row has no processingStartedAt
        to age out, so without this sweep they sit in QUEUED for ever.
        """
        from app.indexing_main import _sweep_queued_records_for_inactive_connectors

        graph = _sweep_graph(
            {
                ProgressStatus.QUEUED.value: [
                    {"_key": "q1", "connectorId": "dead", "origin": "CONNECTOR"}
                ]
            },
            active_ids=set(),
        )

        swept = await _sweep_queued_records_for_inactive_connectors(
            graph_provider=graph, logger=MagicMock(), page_size=100
        )

        assert swept == 1
        key, collection, fields = graph.update_node.await_args.args
        assert key == "q1"
        assert collection == CollectionNames.RECORDS.value
        assert fields["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
        assert fields["processingStartedAt"] is None

    @pytest.mark.asyncio
    async def test_live_connector_records_are_left_alone(self):
        """A queued row on a live connector may still have a message in the
        broker; re-marking it would fight the pipeline."""
        from app.indexing_main import _sweep_queued_records_for_inactive_connectors

        graph = _sweep_graph(
            {
                ProgressStatus.QUEUED.value: [
                    {"_key": "q1", "connectorId": "live", "origin": "CONNECTOR"}
                ]
            },
            active_ids={"live"},
        )

        swept = await _sweep_queued_records_for_inactive_connectors(
            graph_provider=graph, logger=MagicMock(), page_size=100
        )

        assert swept == 0
        graph.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recently_started_in_progress_is_not_yanked(self):
        """A row inside the lease window may still be owned by a worker."""
        from app.indexing_main import _sweep_queued_records_for_inactive_connectors

        future_ms = 9_999_999_999_999
        graph = _sweep_graph(
            {
                ProgressStatus.IN_PROGRESS.value: [
                    {
                        "_key": "p1",
                        "connectorId": "dead",
                        "origin": "CONNECTOR",
                        "processingStartedAt": future_ms,
                    }
                ]
            },
            active_ids=set(),
        )

        swept = await _sweep_queued_records_for_inactive_connectors(
            graph_provider=graph, logger=MagicMock(), page_size=100
        )

        assert swept == 0
        graph.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_aged_in_progress_on_dead_connector_is_moved(self):
        """Past the lease window nothing in flight can succeed: the connector is
        already out of connectors_map, so waiting the full ~32 min stale window
        just leaves it looking stuck."""
        from app.indexing_main import _sweep_queued_records_for_inactive_connectors

        graph = _sweep_graph(
            {
                ProgressStatus.IN_PROGRESS.value: [
                    {
                        "_key": "p1",
                        "connectorId": "dead",
                        "origin": "CONNECTOR",
                        "processingStartedAt": 1,
                    }
                ]
            },
            active_ids=set(),
        )

        swept = await _sweep_queued_records_for_inactive_connectors(
            graph_provider=graph, logger=MagicMock(), page_size=100
        )

        assert swept == 1

    @pytest.mark.asyncio
    async def test_missing_connector_instance_counts_as_inactive(self):
        from app.indexing_main import _sweep_queued_records_for_inactive_connectors

        graph = _sweep_graph(
            {
                ProgressStatus.QUEUED.value: [
                    {"_key": "q1", "connectorId": "gone", "origin": "CONNECTOR"}
                ]
            },
            active_ids=set(),
        )
        graph.get_document = AsyncMock(return_value=None)

        swept = await _sweep_queued_records_for_inactive_connectors(
            graph_provider=graph, logger=MagicMock(), page_size=100
        )

        assert swept == 1

    @pytest.mark.asyncio
    async def test_non_connector_records_are_ignored(self):
        """KB/upload records have no connector to be disabled."""
        from app.indexing_main import _sweep_queued_records_for_inactive_connectors

        graph = _sweep_graph(
            {
                ProgressStatus.QUEUED.value: [
                    {"_key": "kb1", "connectorId": "kb-1", "origin": "UPLOAD"}
                ]
            },
            active_ids=set(),
        )

        swept = await _sweep_queued_records_for_inactive_connectors(
            graph_provider=graph, logger=MagicMock(), page_size=100
        )

        assert swept == 0
        graph.update_node.assert_not_awaited()


# ---------------------------------------------------------------------------
# Orphaned virtual-record mappings
# ---------------------------------------------------------------------------


def _orphan_graph(mappings, records_by_vrid, trashed_by_vrid=None):
    """Graph stub paging virtualRecordToDocIdMapping.

    Signature spelled out for the same reason as _sweep_graph: AsyncMock would
    happily accept a misspelled pagination keyword.
    """
    graph = AsyncMock()
    state = {"rows": list(mappings)}

    async def _paged(
        collection,
        skip=0,
        limit=50,
        filters=None,
        sort_field=None,
        transaction=None,
        raise_on_error=False,
    ):
        return state["rows"][skip : skip + limit]

    graph.get_documents_paginated = AsyncMock(side_effect=_paged)
    async def _records(vrid, *_args, raise_on_error=False, visibility=RecordVisibility.LIVE, **_kwargs):
        # Asserted, not just accepted: this stub cannot fail, so without the
        # assertion every test here would still pass if the sweep went back to
        # a read that swallows -- the bug they exist to hold closed.
        assert raise_on_error is True
        # As the providers answer: LIVE and DELETED are separate sets.
        if visibility is RecordVisibility.DELETED:
            return list((trashed_by_vrid or {}).get(vrid, []))
        assert visibility is RecordVisibility.LIVE
        return list(records_by_vrid.get(vrid, []))

    graph.get_records_by_virtual_record_id = AsyncMock(side_effect=_records)
    return graph


class TestSweepOrphanedVirtualRecordMappings:
    @pytest.fixture(autouse=True)
    def _reset_cursor(self):
        """The scan cursor is module state that persists across ticks."""
        import app.indexing_main as m

        m._orphan_sweep_cursor = 0
        yield
        m._orphan_sweep_cursor = 0

    @pytest.mark.asyncio
    async def test_rows_left_by_an_incomplete_drop_are_reclaimed(self):
        """The backstop `purge_connector` relies on when its scan is bounded.

        A drop whose VRID scan hit the point cap forgets only the ids it read,
        leaving the rest of the mapping rows behind. Those are not stranded:
        this sweep enumerates the mapping collection itself, so it never needs
        the dropped collection to find them — which is why the drop proceeds
        rather than refusing and leaving the whole collection in place.
        """
        from app.indexing_main import _sweep_orphaned_virtual_record_mappings

        # The connector's records went with it, so nothing references these.
        unscanned = [{"_key": "vr-beyond-the-cap-1"}, {"_key": "vr-beyond-the-cap-2"}]
        graph = _orphan_graph(unscanned, records_by_vrid={})
        pipeline = AsyncMock()
        pipeline.rewrite_or_delete_vector_membership = AsyncMock(return_value="deleted")

        swept = await _sweep_orphaned_virtual_record_mappings(
            graph_provider=graph,
            pipeline=pipeline,
            logger=MagicMock(),
            page_size=100,
        )

        assert swept == 2
        assert [
            c.args[0] for c in pipeline.rewrite_or_delete_vector_membership.await_args_list
        ] == ["vr-beyond-the-cap-1", "vr-beyond-the-cap-2"]

    @pytest.mark.asyncio
    async def test_content_held_by_a_trashed_record_is_left_for_the_purge(self) -> None:
        """Its vectors went at soft delete; releasing the mapping row here would
        also remove the stored content of a record that can still be restored."""
        from app.indexing_main import _sweep_orphaned_virtual_record_mappings

        graph = _orphan_graph(
            [{"_key": "vr-trashed"}, {"_key": "vr-abandoned"}],
            records_by_vrid={},
            trashed_by_vrid={"vr-trashed": ["rec-in-trash"]},
        )
        pipeline = AsyncMock()
        pipeline.rewrite_or_delete_vector_membership = AsyncMock(return_value="deleted")

        swept = await _sweep_orphaned_virtual_record_mappings(
            graph_provider=graph, pipeline=pipeline, logger=MagicMock(), page_size=100,
        )

        assert swept == 1
        pipeline.rewrite_or_delete_vector_membership.assert_awaited_once_with("vr-abandoned")

    @pytest.mark.asyncio
    async def test_vrid_with_no_records_is_cleaned_up(self):
        """The abandoned side of an N:1 split.

        The record is repointed at the new VRID before the old one is cleaned
        up, so a failed cleanup leaves vectors no record can reach — and the
        membership backfill walks records, so it cannot reach them either.
        """
        from app.indexing_main import _sweep_orphaned_virtual_record_mappings

        graph = _orphan_graph([{"_key": "vr-abandoned"}], records_by_vrid={})
        pipeline = AsyncMock()
        pipeline.rewrite_or_delete_vector_membership = AsyncMock(return_value="deleted")

        swept = await _sweep_orphaned_virtual_record_mappings(
            graph_provider=graph,
            pipeline=pipeline,
            logger=MagicMock(),
            page_size=100,
        )

        assert swept == 1
        pipeline.rewrite_or_delete_vector_membership.assert_awaited_once_with(
            "vr-abandoned"
        )

    @pytest.mark.asyncio
    async def test_vrid_still_referenced_is_never_touched(self):
        """Deleting a live VRID's points would silently drop it from search."""
        from app.indexing_main import _sweep_orphaned_virtual_record_mappings

        graph = _orphan_graph(
            [{"_key": "vr-live"}], records_by_vrid={"vr-live": [{"_key": "r1"}]}
        )
        pipeline = AsyncMock()

        swept = await _sweep_orphaned_virtual_record_mappings(
            graph_provider=graph,
            pipeline=pipeline,
            logger=MagicMock(),
            page_size=100,
        )

        assert swept == 0
        pipeline.rewrite_or_delete_vector_membership.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_failed_cleanup_does_not_abort_the_rest(self):
        from app.indexing_main import _sweep_orphaned_virtual_record_mappings

        graph = _orphan_graph(
            [{"_key": "vr-bad"}, {"_key": "vr-good"}], records_by_vrid={}
        )
        pipeline = AsyncMock()

        async def _cleanup(vrid):
            if vrid == "vr-bad":
                raise RuntimeError("qdrant down")
            return "deleted"

        pipeline.rewrite_or_delete_vector_membership = AsyncMock(side_effect=_cleanup)

        swept = await _sweep_orphaned_virtual_record_mappings(
            graph_provider=graph,
            pipeline=pipeline,
            logger=MagicMock(),
            page_size=100,
        )

        assert swept == 1
        assert pipeline.rewrite_or_delete_vector_membership.await_count == 2

    @pytest.mark.asyncio
    async def test_scan_is_bounded_and_resumes_where_it_stopped(self):
        """A full scan per tick would be a standing N+1 for a rare payoff.

        Each tick walks a bounded slice; the next one picks up the cursor rather
        than re-walking the head of the collection for ever.
        """
        import app.indexing_main as m

        rows = [{"_key": f"vr{i}"} for i in range(40)]
        graph = _orphan_graph(rows, records_by_vrid={r["_key"]: [{}] for r in rows})
        pipeline = AsyncMock()

        await m._sweep_orphaned_virtual_record_mappings(
            graph_provider=graph, pipeline=pipeline, logger=MagicMock(), page_size=5
        )

        scanned = m.ORPHAN_SCAN_MAX_PAGES_PER_TICK * 5
        assert graph.get_records_by_virtual_record_id.await_count == scanned
        assert m._orphan_sweep_cursor == scanned

        graph.get_records_by_virtual_record_id.reset_mock()
        await m._sweep_orphaned_virtual_record_mappings(
            graph_provider=graph, pipeline=pipeline, logger=MagicMock(), page_size=5
        )

        resumed = [
            c.args[0] for c in graph.get_records_by_virtual_record_id.await_args_list
        ]
        assert resumed[0] == f"vr{scanned}"

    @pytest.mark.asyncio
    async def test_cursor_wraps_at_the_end_of_the_collection(self):
        """Without the wrap the sweep would stick past the tail and never
        revisit rows that became orphans in the meantime."""
        import app.indexing_main as m

        rows = [{"_key": "vr-live"}]
        graph = _orphan_graph(rows, records_by_vrid={"vr-live": [{}]})

        await m._sweep_orphaned_virtual_record_mappings(
            graph_provider=graph, pipeline=AsyncMock(), logger=MagicMock(), page_size=5
        )

        assert m._orphan_sweep_cursor == 0


# ===================================================================
# _republish_stranded_records
# ===================================================================


def _stranded_env(after_seconds=3600.0):
    """Set the sweep's threshold; 0 disables it entirely (default is 3600).

    Patches the environment rather than the property because messaging_env
    re-reads os.getenv on every access by design.
    """
    return patch.dict(
        os.environ,
        {"STRANDED_RECORD_REPUBLISH_AFTER_SECONDS": str(after_seconds)},
    )


async def _run_stranded(graph, producer=None, concurrency_manager=None, read_backlog=None, logger=None):
    from app.indexing_main import _republish_stranded_records

    async def run_coordination(coro):
        return await coro

    return await _republish_stranded_records(
        graph_provider=graph,
        logger=logger or MagicMock(),
        producer=producer or AsyncMock(),
        run_coordination=run_coordination,
        concurrency_manager=concurrency_manager,
        page_size=100,
        read_backlog=read_backlog,
    )


def _lanes(oldest_waiting_ms: dict[str, float], lanes_for_event=None) -> AsyncMock:
    """A broker that answers: per lane, when its oldest unfinished event was published."""
    from app.services.messaging.lanes.backlog import LaneBacklog

    return AsyncMock(return_value=LaneBacklog("record-events", oldest_waiting_ms, lanes_for_event))


def _sweep_summary(logger: MagicMock) -> dict[str, int]:
    """The counters from the sweep's one summary line."""
    lines = [c.args for c in logger.log.call_args_list if "Stranded-record sweep" in c.args[1]]
    assert len(lines) == 1, f"expected one summary line per pass, got {len(lines)}"
    _level, _message, considered, still_queued, republished, backed_off, _note = lines[0]
    return {
        "considered": considered,
        "still_queued": still_queued,
        "republished": republished,
        "backed_off": backed_off,
    }


class TestRepublishStrandedRecords:
    """The net for records whose event was lost.

    A row on a live connector is invisible to both other sweeps: the stale scan
    filters on IN_PROGRESS, and the connector sweep only touches connectors that
    are gone. That gap is how records sat in QUEUED for ever after their event
    was discarded.
    """

    @staticmethod
    def _old_record(**overrides):
        record = {
            "_key": "r1",
            "connectorId": "live",
            "origin": "CONNECTOR",
            "recordName": "PA-1 Something",
            "orgId": "org-1",
            "version": 0,
            "updatedAtTimestamp": 1,  # epoch ms — far older than any cutoff
        }
        record.update(overrides)
        return record

    @pytest.mark.asyncio
    async def test_disabled_by_default(self):
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._old_record()]}, active_ids={"live"}
        )
        producer = AsyncMock()

        with _stranded_env(0.0):
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()
        graph.get_documents_paginated.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_republishes_a_stranded_row_on_a_live_connector(self):
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._old_record()]}, active_ids={"live"}
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 1

        kwargs = producer.send_event.await_args.kwargs
        assert kwargs["payload"]["recordId"] == "r1"
        assert kwargs["event_type"] == EventTypes.NEW_RECORD.value
        assert kwargs["key"] == "r1"

    @pytest.mark.asyncio
    async def test_an_already_indexed_row_reindexes_instead(self):
        """A record with a version and a VRID has been indexed before."""
        graph = _sweep_graph(
            {
                ProgressStatus.QUEUED.value: [
                    self._old_record(version=2, virtualRecordId="vr-1")
                ]
            },
            active_ids={"live"},
        )
        producer = AsyncMock()

        with _stranded_env():
            await _run_stranded(graph, producer)

        assert (
            producer.send_event.await_args.kwargs["event_type"]
            == EventTypes.REINDEX_RECORD.value
        )

    @pytest.mark.asyncio
    async def test_a_recently_touched_row_is_left_alone(self):
        """Its event may legitimately still be queued behind a backlog."""
        graph = _sweep_graph(
            {
                ProgressStatus.QUEUED.value: [
                    self._old_record(updatedAtTimestamp=get_epoch_timestamp_in_ms())
                ]
            },
            active_ids={"live"},
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inactive_connector_rows_belong_to_the_other_sweep(self):
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._old_record()]}, active_ids=set()
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_dedup_parked_duplicate_is_left_to_its_twin(self):
        """A record parked behind an in-flight md5 twin is legitimately QUEUED.

        It is released when the twin completes, not by re-publishing it.
        """
        graph = _sweep_graph(
            {
                ProgressStatus.QUEUED.value: [
                    self._old_record(md5Checksum="abc", virtualRecordId="vr-1")
                ]
            },
            active_ids={"live"},
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uploads_are_not_swept(self):
        """Only connector-origin records are re-published from here."""
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._old_record(origin="UPLOAD")]},
            active_ids={"live"},
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()

    @staticmethod
    def _restored_upload(**overrides) -> dict:
        return TestRepublishStrandedRecords._old_record(**{
            "origin": "UPLOAD",
            "connectorName": "KB",
            "md5Checksum": "abc",
            "virtualRecordId": "vr-1",
            "restoredAtTimestamp": 1,
            **overrides,
        })

    @pytest.mark.asyncio
    async def test_a_restored_upload_whose_reindex_was_lost_is_reindexed(self) -> None:
        """Its restore committed, then the publish was lost; nothing else would queue it."""
        graph = _sweep_graph(
            {ProgressStatus.NOT_STARTED.value: [self._restored_upload()]}, active_ids={"live"}
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 1

        kwargs = producer.send_event.await_args.kwargs
        assert (kwargs["event_type"], kwargs["payload"]["recordId"]) == (EventTypes.REINDEX_RECORD.value, "r1")
        assert kwargs["payload"]["virtualRecordId"] == "vr-1"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status, overrides", [
        (ProgressStatus.NOT_STARTED.value, {"restoredAtTimestamp": None}),
        (ProgressStatus.NOT_STARTED.value, {"isDeleted": True}),
        (ProgressStatus.QUEUED.value, {}),
    ], ids=["never restored", "in the trash again", "parked behind a twin"])
    async def test_other_uploads_are_still_left_alone(self, status, overrides) -> None:
        graph = _sweep_graph({status: [self._restored_upload(**overrides)]}, active_ids={"live"})
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_republishing_marks_the_row(self):
        """Publishing changes nothing about the record on its own.

        Without a marker the row stays eligible and every sweep tick sends
        another copy of the same event — worst exactly when the consumer is
        backlogged, which is the case this sweep exists for.
        """
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._old_record()]}, active_ids={"live"}
        )
        producer = AsyncMock()

        with _stranded_env():
            await _run_stranded(graph, producer)

        key, collection, fields = graph.update_node.await_args.args
        assert key == "r1"
        assert collection == CollectionNames.RECORDS.value
        assert "lastRepublishedAt" in fields
        # updatedAtTimestamp means "when the record last changed" and belongs
        # to the connectors; a recovery sweep must not move it.
        assert "updatedAtTimestamp" not in fields

    @pytest.mark.asyncio
    async def test_a_fresh_queue_stamp_outweighs_a_source_system_updated_at(self) -> None:
        """A Jira issue last edited a year ago, synced a minute ago, is not stranded.

        Connectors may fill updatedAtTimestamp with source-system time, so aged
        on that alone every freshly synced row was sent a second event.
        """
        graph = _sweep_graph(
            {
                ProgressStatus.QUEUED.value: [
                    self._old_record(queuedAtTimestamp=get_epoch_timestamp_in_ms())
                ]
            },
            active_ids={"live"},
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_stale_queue_stamp_is_still_recovered(self) -> None:
        graph = _sweep_graph(
            {ProgressStatus.NOT_STARTED.value: [self._old_record(queuedAtTimestamp=1)]},
            active_ids={"live"},
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 1

    @pytest.mark.asyncio
    async def test_a_malformed_clock_does_not_hide_a_stale_one(self) -> None:
        graph = _sweep_graph(
            {
                ProgressStatus.QUEUED.value: [
                    self._old_record(updatedAtTimestamp="not-a-number", queuedAtTimestamp=1)
                ]
            },
            active_ids={"live"},
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 1

    @pytest.mark.asyncio
    async def test_a_recently_republished_row_is_skipped(self):
        """At most one republish per threshold window, per record."""
        graph = _sweep_graph(
            {
                ProgressStatus.QUEUED.value: [
                    self._old_record(lastRepublishedAt=get_epoch_timestamp_in_ms())
                ]
            },
            active_ids={"live"},
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_old_republish_does_not_block_a_retry(self):
        """A second lost event is still recoverable once the window passes."""
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._old_record(lastRepublishedAt=1)]},
            active_ids={"live"},
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 1

    @pytest.mark.asyncio
    async def test_a_contended_record_lease_skips_the_row(self):
        """Somebody is working on it after all."""
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._old_record()]}, active_ids={"live"}
        )
        producer = AsyncMock()
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(return_value=False)

        with _stranded_env():
            assert await _run_stranded(graph, producer, manager) == 0

        producer.send_event.assert_not_awaited()
        manager.release.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_record_lease_is_always_released(self):
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._old_record()]}, active_ids={"live"}
        )
        producer = AsyncMock()
        producer.send_event = AsyncMock(side_effect=Exception("broker down"))
        manager = AsyncMock()
        manager.try_acquire = AsyncMock(return_value=True)

        with _stranded_env():
            assert await _run_stranded(graph, producer, manager) == 0

        manager.release.assert_awaited_once_with("record:r1", ANY)


class TestRepublishClaimIsWrittenBeforeTheSend:
    """The republish marker is a durable claim taken *before* the send.

    Written after the send, a Neo4j failure following a successful Redis send
    left `lastRepublishedAt` unset, so the record stayed eligible and was
    re-sent every 60s tick until the consumer moved it out of
    QUEUED/NOT_STARTED -- and the 60s loop passes no concurrency_manager, so
    nothing else bounded it. Under a backlog that inflates the very backlog
    delaying the consumer. Claiming first means a record can never be sent
    without a persisted claim, which caps it at one send per interval.
    """

    @staticmethod
    def _row():
        return TestRepublishStrandedRecords._old_record()

    @pytest.mark.asyncio
    async def test_a_failed_claim_write_sends_nothing(self):
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._row()]}, active_ids={"live"}
        )
        graph.update_node = AsyncMock(return_value=False)
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_raising_claim_write_sends_nothing(self):
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._row()]}, active_ids={"live"}
        )
        graph.update_node = AsyncMock(side_effect=RuntimeError("neo4j down"))
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_claim_is_persisted_before_the_send(self):
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._row()]}, active_ids={"live"}
        )
        order: list[str] = []
        graph.update_node = AsyncMock(side_effect=lambda *a, **k: order.append("claim") or True)
        producer = AsyncMock()
        producer.send_event = AsyncMock(side_effect=lambda **k: order.append("send") or True)

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 1

        assert order == ["claim", "send"]
        claim_fields = graph.update_node.await_args_list[0].args[2]
        assert isinstance(claim_fields["lastRepublishedAt"], int)

    @pytest.mark.asyncio
    async def test_a_failed_send_clears_the_claim_so_the_next_tick_retries(self):
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._row()]}, active_ids={"live"}
        )
        graph.update_node = AsyncMock(return_value=True)
        producer = AsyncMock()
        producer.send_event = AsyncMock(side_effect=RuntimeError("redis down"))

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        writes = [c.args[2] for c in graph.update_node.await_args_list]
        assert len(writes) == 2
        assert isinstance(writes[0]["lastRepublishedAt"], int)   # the claim
        assert writes[0]["republishCount"] == 1
        # Put back as it was, so the send that never happened costs no back-off.
        assert writes[1] == {"lastRepublishedAt": None, "republishCount": 0}

    @pytest.mark.asyncio
    async def test_a_failed_send_keeps_the_back_off_already_earned(self) -> None:
        """Undoing the claim must not erase the re-sends that did go out."""
        two_hours_ago = get_epoch_timestamp_in_ms() - 2 * HOUR_MS - 1000
        row = TestRepublishStrandedRecords._old_record(
            lastRepublishedAt=two_hours_ago, republishCount=1
        )
        graph = _sweep_graph({ProgressStatus.QUEUED.value: [row]}, active_ids={"live"})
        graph.update_node = AsyncMock(return_value=True)
        producer = AsyncMock()
        producer.send_event = AsyncMock(side_effect=RuntimeError("redis down"))

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        writes = [c.args[2] for c in graph.update_node.await_args_list]
        assert writes[0]["republishCount"] == 2
        assert writes[1] == {"lastRepublishedAt": two_hours_ago, "republishCount": 1}


HOUR_MS = 3600 * 1000
ALLOWANCE_MS = 5 * 60 * 1000


class TestRepublishOnlyWhatTheQueueHasPassed:
    """A record waiting behind a backlog is as old as one whose event was lost.

    Aged alone, every healthy record still in line was sent another copy each
    hour once the consumer fell more than an hour behind, and each copy made
    the backlog longer: about 185,000 duplicate events for about 43,000 GitLab
    records in a day. The broker knows which is which, so the sweep asks it.
    """

    @staticmethod
    def _queued(hours_ago: float, **overrides) -> dict:
        return TestRepublishStrandedRecords._old_record(
            queuedAtTimestamp=get_epoch_timestamp_in_ms() - int(hours_ago * HOUR_MS),
            **overrides,
        )

    @pytest.mark.asyncio
    async def test_older_work_still_on_the_lane_means_the_event_has_not_been_reached(self) -> None:
        record = self._queued(3)
        graph = _sweep_graph({ProgressStatus.QUEUED.value: [record]}, active_ids={"live"})
        producer = AsyncMock()
        # The consumer is still working through events published five hours ago.
        backlog = _lanes({"record-events.3": get_epoch_timestamp_in_ms() - 5 * HOUR_MS})

        with _stranded_env():
            assert await _run_stranded(graph, producer, read_backlog=backlog) == 0

        producer.send_event.assert_not_awaited()
        graph.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_lane_that_has_moved_past_the_record_means_its_event_is_not_coming(self) -> None:
        record = self._queued(3)
        graph = _sweep_graph({ProgressStatus.QUEUED.value: [record]}, active_ids={"live"})
        producer = AsyncMock()
        # Everything published up to an hour ago has been dealt with.
        backlog = _lanes({"record-events.3": get_epoch_timestamp_in_ms() - HOUR_MS})

        with _stranded_env():
            assert await _run_stranded(graph, producer, read_backlog=backlog) == 1

        producer.send_event.assert_awaited_once()
        assert producer.send_event.await_args.kwargs["payload"]["recordId"] == "r1"

    @pytest.mark.asyncio
    async def test_an_empty_queue_means_its_event_is_not_coming(self) -> None:
        graph = _sweep_graph({ProgressStatus.QUEUED.value: [self._queued(3)]}, active_ids={"live"})
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer, read_backlog=_lanes({})) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("offset_ms, republished", [
        (-1000, 0),
        (ALLOWANCE_MS - 1000, 0),
        (ALLOWANCE_MS + 1000, 1),
    ], ids=["published just before", "within the clock allowance", "past the clock allowance"])
    async def test_the_two_clocks_are_allowed_to_disagree_a_little(self, offset_ms: int, republished: int) -> None:
        """The record is stamped by one host before its event is stamped by another.

        An event at the head of the lane that is a little *newer* than the
        record's queue time may well be the record's own.
        """
        record = self._queued(3)
        graph = _sweep_graph({ProgressStatus.QUEUED.value: [record]}, active_ids={"live"})
        backlog = _lanes({"record-events.3": record["queuedAtTimestamp"] + offset_ms})

        with _stranded_env():
            assert await _run_stranded(graph, AsyncMock(), read_backlog=backlog) == republished

    @pytest.mark.asyncio
    async def test_only_the_lanes_the_event_could_be_on_are_asked(self) -> None:
        """Another connector's backlog must not hold up recovering this record."""
        record = self._queued(3)
        graph = _sweep_graph({ProgressStatus.QUEUED.value: [record]}, active_ids={"live"})
        long_ago = get_epoch_timestamp_in_ms() - 50 * HOUR_MS
        seen: list[dict] = []

        def lanes_for_event(payload: dict) -> set[str]:
            seen.append(dict(payload))
            return {"record-events.3"}

        backlog = _lanes({"record-events.6": long_ago}, lanes_for_event)

        with _stranded_env():
            assert await _run_stranded(graph, AsyncMock(), read_backlog=backlog) == 1

        # Asked with the payload that will be published, so the lane is the one
        # the re-sent event would be routed to.
        assert seen[0]["connectorId"] == "live"
        assert seen[0]["recordId"] == "r1"

    @pytest.mark.asyncio
    async def test_the_broker_is_read_once_per_pass_however_many_records(self) -> None:
        records = [self._queued(3, _key=f"r{i}") for i in range(40)]
        graph = _sweep_graph({ProgressStatus.QUEUED.value: records}, active_ids={"live"})
        backlog = _lanes({"record-events.3": get_epoch_timestamp_in_ms() - 5 * HOUR_MS})

        with _stranded_env():
            assert await _run_stranded(graph, AsyncMock(), read_backlog=backlog) == 0

        backlog.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_broker_is_not_read_when_no_record_is_old_enough(self) -> None:
        graph = _sweep_graph({ProgressStatus.QUEUED.value: [self._queued(0.5)]}, active_ids={"live"})
        backlog = _lanes({})

        with _stranded_env():
            assert await _run_stranded(graph, AsyncMock(), read_backlog=backlog) == 0

        backlog.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_threshold_zero_still_disables_it_without_touching_the_broker(self) -> None:
        graph = _sweep_graph({ProgressStatus.QUEUED.value: [self._queued(3)]}, active_ids={"live"})
        producer = AsyncMock()
        backlog = _lanes({})

        with _stranded_env(0.0):
            assert await _run_stranded(graph, producer, read_backlog=backlog) == 0

        producer.send_event.assert_not_awaited()
        backlog.assert_not_awaited()
        graph.get_documents_paginated.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_republished_record_is_then_judged_from_its_republish_time(self) -> None:
        """Its new event is on the lane now, behind whatever was published meanwhile."""
        now = get_epoch_timestamp_in_ms()
        record = self._queued(9, lastRepublishedAt=now - 3 * HOUR_MS, republishCount=1)
        graph = _sweep_graph({ProgressStatus.QUEUED.value: [record]}, active_ids={"live"})
        producer = AsyncMock()
        # Past the original queue time, not yet past the re-sent event.
        backlog = _lanes({"record-events.3": now - 4 * HOUR_MS})

        with _stranded_env():
            assert await _run_stranded(graph, producer, read_backlog=backlog) == 0

        producer.send_event.assert_not_awaited()


class TestRepublishBackOff:
    """Each re-send of the same record doubles the wait before the next.

    This is the whole of the rule on a pass where the broker cannot be read,
    and a bound on everything else: no record is re-sent every period for ever.
    """

    @staticmethod
    def _resent(hours_ago: float, count: int | None, **overrides) -> dict:
        fields = {
            "queuedAtTimestamp": 1,
            "lastRepublishedAt": get_epoch_timestamp_in_ms() - int(hours_ago * HOUR_MS),
            **overrides,
        }
        if count is not None:
            fields["republishCount"] = count
        return TestRepublishStrandedRecords._old_record(**fields)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("count, hours_ago, republished", [
        (1, 1.5, 0),
        (1, 2.5, 1),
        (2, 3.5, 0),
        (2, 4.5, 1),
        (3, 7.5, 0),
        (3, 8.5, 1),
        (5, 23.5, 0),
        (5, 24.5, 1),
        (40, 24.5, 1),
    ], ids=[
        "second waits two hours", "second goes after two", "third waits four hours",
        "third goes after four", "fourth waits eight hours", "fourth goes after eight",
        "the wait stops growing at a day", "and goes after a day", "however many times",
    ])
    async def test_the_wait_doubles_with_each_resend_up_to_a_day(
        self, count: int, hours_ago: float, republished: int
    ) -> None:
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._resent(hours_ago, count)]}, active_ids={"live"}
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == republished

        assert producer.send_event.await_count == republished

    @pytest.mark.asyncio
    async def test_each_resend_is_counted_on_the_record(self) -> None:
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._resent(2.5, 1)]}, active_ids={"live"}
        )

        with _stranded_env():
            assert await _run_stranded(graph, AsyncMock()) == 1

        claim = graph.update_node.await_args_list[0].args[2]
        assert claim["republishCount"] == 2
        assert isinstance(claim["lastRepublishedAt"], int)

    @pytest.mark.asyncio
    async def test_an_unreadable_broker_falls_back_to_age_and_back_off(self) -> None:
        """First re-send at the minimum age, the second only after twice as long."""
        first = TestRepublishStrandedRecords._old_record(_key="first", queuedAtTimestamp=1)
        waiting = self._resent(1.5, 1, _key="resent-90-minutes-ago")
        due = self._resent(2.5, 1, _key="resent-150-minutes-ago")
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [first, waiting, due]}, active_ids={"live"}
        )
        producer = AsyncMock()
        logger = MagicMock()
        unreadable = AsyncMock(side_effect=ConnectionError("broker down"))

        with _stranded_env():
            assert await _run_stranded(graph, producer, read_backlog=unreadable, logger=logger) == 2

        sent = [c.kwargs["payload"]["recordId"] for c in producer.send_event.await_args_list]
        assert sent == ["first", "resent-150-minutes-ago"]
        # Asked once and reported once, not once per record.
        unreadable.assert_awaited_once()
        unreadable_warnings = [
            c.args for c in logger.warning.call_args_list if "backlog" in c.args[0]
        ]
        assert len(unreadable_warnings) == 1
        assert "broker down" in str(unreadable_warnings[0][-1])

    @pytest.mark.asyncio
    async def test_the_back_off_holds_even_when_the_queue_has_moved_on(self) -> None:
        """A re-sent event the consumer dropped again is not re-sent every period."""
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._resent(1.5, 1)]}, active_ids={"live"}
        )
        producer = AsyncMock()
        backlog = _lanes({})

        with _stranded_env():
            assert await _run_stranded(graph, producer, read_backlog=backlog) == 0

        producer.send_event.assert_not_awaited()
        backlog.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_being_queued_again_starts_the_count_over(self) -> None:
        """Re-sends from an earlier wait say nothing about this one."""
        now = get_epoch_timestamp_in_ms()
        record = TestRepublishStrandedRecords._old_record(
            lastRepublishedAt=now - 30 * HOUR_MS,
            republishCount=5,
            queuedAtTimestamp=now - int(1.5 * HOUR_MS),
        )
        graph = _sweep_graph({ProgressStatus.QUEUED.value: [record]}, active_ids={"live"})

        with _stranded_env():
            assert await _run_stranded(graph, AsyncMock()) == 1

        assert graph.update_node.await_args_list[0].args[2]["republishCount"] == 1

    @pytest.mark.asyncio
    async def test_a_row_resent_before_the_counter_existed_counts_as_resent_once(self) -> None:
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._resent(1.5, None)]}, active_ids={"live"}
        )
        producer = AsyncMock()

        with _stranded_env():
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_longer_minimum_age_is_never_shortened_by_the_cap(self) -> None:
        """An operator who set two days keeps two days between re-sends."""
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [self._resent(30, 3)]}, active_ids={"live"}
        )
        producer = AsyncMock()

        with _stranded_env(2 * 24 * 3600.0):
            assert await _run_stranded(graph, producer) == 0

        producer.send_event.assert_not_awaited()


class TestStrandedSweepSummary:
    @pytest.mark.asyncio
    async def test_one_line_per_pass_counts_what_happened_to_each_record(self) -> None:
        now = get_epoch_timestamp_in_ms()

        def row(key: str, connector: str, **fields) -> dict:
            return TestRepublishStrandedRecords._old_record(_key=key, connectorId=connector, **fields)

        records = [
            row("behind-1", "busy", queuedAtTimestamp=now - 3 * HOUR_MS),
            row("behind-2", "busy", queuedAtTimestamp=now - 3 * HOUR_MS),
            row("lost", "idle", queuedAtTimestamp=now - 3 * HOUR_MS),
            row("resent", "idle", queuedAtTimestamp=1, lastRepublishedAt=now - int(1.5 * HOUR_MS), republishCount=1),
            row("fresh", "idle", queuedAtTimestamp=now - 60_000),
            row("upload", "idle", origin="UPLOAD", queuedAtTimestamp=now - 3 * HOUR_MS),
        ]
        graph = _sweep_graph({ProgressStatus.QUEUED.value: records}, active_ids={"busy", "idle"})
        producer = AsyncMock()
        logger = MagicMock()
        backlog = _lanes(
            {"busy-lane": now - 5 * HOUR_MS},
            lambda payload: {"busy-lane"} if payload["connectorId"] == "busy" else {"idle-lane"},
        )

        with _stranded_env():
            assert await _run_stranded(graph, producer, read_backlog=backlog, logger=logger) == 1

        # Too young, and an upload: neither is the sweep's to consider.
        assert _sweep_summary(logger) == {
            "considered": 4, "still_queued": 2, "republished": 1, "backed_off": 1,
        }
        assert producer.send_event.await_args.kwargs["payload"]["recordId"] == "lost"

    @pytest.mark.asyncio
    async def test_an_unreadable_queue_is_said_on_the_summary_line(self) -> None:
        graph = _sweep_graph(
            {ProgressStatus.QUEUED.value: [TestRepublishStrandedRecords._old_record()]},
            active_ids={"live"},
        )
        logger = MagicMock()

        with _stranded_env():
            await _run_stranded(
                graph, read_backlog=AsyncMock(side_effect=TimeoutError("slow")), logger=logger
            )

        assert "queue not readable" in logger.log.call_args.args[-1]


class TestRecoveryAsksTheRecordConsumerForItsBacklog:
    @pytest.mark.asyncio
    async def test_the_sweep_reads_the_backlog_of_the_record_topic_from_the_consumer(self) -> None:
        from app.indexing_main import recover_in_progress_records
        from app.services.messaging.lanes.backlog import LaneBacklog

        now = get_epoch_timestamp_in_ms()
        container = _make_container()
        consumer = container.kafka_consumers[0][1]
        consumer.lane_backlog = AsyncMock(
            return_value=LaneBacklog("record-events", {"record-events.3": now - 5 * HOUR_MS})
        )
        behind = TestRepublishStrandedRecords._old_record(queuedAtTimestamp=now - 3 * HOUR_MS)

        async def _paged(
            collection, skip=0, limit=50, filters=None, sort_field=None, raise_on_error=False
        ) -> list[dict]:
            if collection != CollectionNames.RECORDS.value or skip:
                return []
            return [behind] if (filters or {}).get("indexingStatus") == ProgressStatus.QUEUED.value else []

        graph = AsyncMock()
        graph.get_documents_paginated = AsyncMock(side_effect=_paged)
        graph.get_document = AsyncMock(return_value={"isActive": True})

        with _stranded_env():
            await recover_in_progress_records(container, graph)

        consumer.lane_backlog.assert_awaited_once_with("record-events")
        container.kafka_consumers[0][2].send_event.assert_not_awaited()
