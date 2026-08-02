"""End-to-end tests for the Local FS connector's incremental sync and
orphan-recovery sweep.

Unlike ``tests/unit/connectors/sources/local_fs/test_connector.py`` (which
mocks ``data_entities_processor`` entirely), these tests wire up the *real*
``LocalFsConnector`` and the *real* ``DataSourceEntitiesProcessor`` against a
single shared in-memory graph store, and drive the *real*
``app.indexing_main.recover_in_progress_records`` sweep against that same
store afterwards. Only the network-facing boundary -- the graph DB wire
protocol and the Kafka/Redis broker client -- is faked; every other line of
production sync/recovery logic actually runs, so a real file on a real
``tmp_path`` ends up as a real record with a real ``externalRevisionId``,
diffed and re-queued by the real code from the incremental-sync design.

Real, docker-backed ArangoDB/Neo4j x Kafka/Redis coverage lives in the
top-level ``integration-tests/`` suite (see
``.github/workflows/integration-tests.yml``). This module instead
parametrizes the *shape* of the in-memory store over ``arango``/``neo4j`` --
reusing the two ``_InMemoryGraphStore`` subclasses
``test_connector_workflow_integration.py`` already established for other
connectors -- to prove the sync/recovery logic takes no provider-specific
shortcuts.
"""

from __future__ import annotations

import sys
import types
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# --- Import shims (must run before importing local_fs.connector). Same
# pattern as tests/unit/connectors/sources/local_fs/test_connector.py. ---
if "app.containers.connector" not in sys.modules:
    _stub_container = types.ModuleType("app.containers.connector")

    class _ContainerMeta(type):
        def __getattr__(cls, name) -> None:
            return None

    class _ConnectorAppContainer(metaclass=_ContainerMeta):
        pass

    _stub_container.ConnectorAppContainer = _ConnectorAppContainer
    sys.modules["app.containers.connector"] = _stub_container

if "redis" not in sys.modules:
    _redis_exc = types.ModuleType("redis.exceptions")
    _redis_exc.ConnectionError = type("RedisConnectionError", (Exception,), {})
    _redis_exc.TimeoutError = type("RedisTimeoutError", (Exception,), {})
    sys.modules["redis.exceptions"] = _redis_exc

    _redis_backoff = types.ModuleType("redis.backoff")

    class _ExponentialBackoff:
        pass

    _redis_backoff.ExponentialBackoff = _ExponentialBackoff
    sys.modules["redis.backoff"] = _redis_backoff

    _redis_retry = types.ModuleType("redis.asyncio.retry")

    class _Retry:
        pass

    _redis_retry.Retry = _Retry
    sys.modules["redis.asyncio.retry"] = _redis_retry

    _redis_asyncio = types.ModuleType("redis.asyncio")
    _redis_asyncio.Redis = type("Redis", (), {})
    sys.modules["redis.asyncio"] = _redis_asyncio

    _redis = types.ModuleType("redis")
    _redis.asyncio = _redis_asyncio
    sys.modules["redis"] = _redis

if "etcd3" not in sys.modules:
    _etcd3 = types.ModuleType("etcd3")
    _etcd3.client = type("client", (), {})
    sys.modules["etcd3"] = _etcd3
# --- end shims ---

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.local_fs.connector import (
    SYNC_ROOT_PATH_KEY,
    LocalFsConnector,
)
from app.indexing_main import recover_in_progress_records
from app.models.entities import User
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.unit.connectors.sources.test_connector_workflow_integration import (
    MockArangoProvider,
    MockDataStoreProvider,
    MockNeo4jProvider,
    MockTransactionStore,
)

ORG_ID = "org-e2e-1"
CONNECTOR_ID = "local-fs-e2e-1"
OWNER = User(email="owner@example.com", id="owner-e2e-1", org_id=ORG_ID)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Store extensions: everything LocalFsConnector and recover_in_progress_records
# need beyond the record/record-group/app-user CRUD MockTransactionStore and
# MockDataStoreProvider already provide for the other connectors' tests.
# ---------------------------------------------------------------------------


class _GraphProviderView:
    """Read/CAS-only view over the shared in-memory store.

    Serves two callers that talk to a graph provider directly rather than
    through a transaction: ``tx_store.graph_provider`` (the incremental diff
    scan in ``LocalFsConnector._scan_existing_records``) and the bare
    ``graph_provider`` argument to ``recover_in_progress_records``.
    """

    def __init__(self, store) -> None:
        self._s = store

    async def get_document(self, document_key, collection, transaction=None) -> dict | None:
        return self._s.get_node(collection, document_key)

    async def get_documents_paginated(
        self,
        collection,
        skip=0,
        limit=50,
        filters=None,
        sort_field=None,
        transaction=None,
        raise_on_error=False,
    ) -> list[dict]:
        filters = filters or {}
        docs = [
            d
            for d in self._s.collections.get(collection, {}).values()
            if all(d.get(k) == v for k, v in filters.items())
        ]
        docs.sort(key=lambda d: str(d.get(sort_field or "_key", "")))
        return docs[skip : skip + limit]

    async def compare_and_set_indexing_status(self, record_ids, expected, new_status) -> list[str]:
        swapped = []
        for rid in record_ids:
            doc = self._s.get_node(CollectionNames.RECORDS.value, rid)
            if doc is not None and doc.get("indexingStatus") == expected:
                doc["indexingStatus"] = new_status
                swapped.append(rid)
        return swapped

    async def update_node(self, key, collection, updates) -> bool:
        doc = self._s.get_node(collection, key)
        if doc is None:
            return False
        doc.update(updates)
        return True


class LocalFsTransactionStore(MockTransactionStore):
    """Adds the sync-point, status-scan, and external-id-delete surface
    Local FS needs, on top of the generic record/record-group/app-user CRUD
    every connector already exercises via ``MockTransactionStore``."""

    def __init__(self, store) -> None:
        super().__init__(store)
        self.txn = None
        self.graph_provider = _GraphProviderView(store)

    async def get_records_by_status(
        self,
        org_id,
        connector_id,
        status_filters,
        limit=None,
        offset=0,
        record_group_id=None,
        is_placeholder=None,
        after_key=None,
        exclude_statuses=None,
    ) -> list:
        docs = [
            d
            for d in self._s.collections.get(CollectionNames.RECORDS.value, {}).values()
            if d.get("orgId") == org_id
            and d.get("connectorId") == connector_id
            and d.get("indexingStatus") in status_filters
        ]
        if exclude_statuses:
            docs = [d for d in docs if d.get("indexingStatus") not in exclude_statuses]
        if is_placeholder is not None:
            docs = [d for d in docs if bool(d.get("isPlaceholder", False)) == is_placeholder]
        docs.sort(key=lambda d: d["_key"])
        if after_key is not None:
            docs = [d for d in docs if d["_key"] > after_key]
        else:
            docs = docs[offset:]
        if limit is not None:
            docs = docs[:limit]
        return [self._doc_to_record(d) for d in docs]

    async def delete_record_by_external_id(self, connector_id, external_id, user_id=None) -> None:
        records = self._s.collections.get(CollectionNames.RECORDS.value, {})
        for key, doc in list(records.items()):
            if doc.get("connectorId") == connector_id and doc.get("externalRecordId") == external_id:
                del records[key]

    async def get_sync_point(self, sync_point_key) -> dict | None:
        return self._s.collections.get(CollectionNames.SYNC_POINTS.value, {}).get(sync_point_key)

    async def update_sync_point(self, sync_point_key, sync_point_data) -> None:
        self._s.collections.setdefault(CollectionNames.SYNC_POINTS.value, {})[
            sync_point_key
        ] = dict(sync_point_data)

    async def delete_sync_point(self, sync_point_key) -> None:
        self._s.collections.get(CollectionNames.SYNC_POINTS.value, {}).pop(sync_point_key, None)


class LocalFsDataStoreProvider(MockDataStoreProvider):
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[LocalFsTransactionStore]:
        yield LocalFsTransactionStore(self._store)


def _make_recovery_container() -> tuple[MagicMock, AsyncMock]:
    """Mirrors test_indexing_main.py's _make_container(): a mock
    IndexingAppContainer with a single non-distributed record consumer/retry
    producer, which is all recover_in_progress_records needs to run its
    non-distributed code path."""
    container = MagicMock()
    container.logger.return_value = MagicMock()
    mock_producer = AsyncMock()
    mock_producer.send_event = AsyncMock(return_value=True)
    mock_consumer = MagicMock()
    mock_consumer.concurrency_manager = None
    mock_consumer._run_on_main_loop = None
    container.kafka_consumers = [("record", mock_consumer, mock_producer)]
    return container, mock_producer


def _records_snapshot(graph_store) -> dict[str, dict[str, Any]]:
    return graph_store.collections.setdefault(CollectionNames.RECORDS.value, {})


async def _run_sync(connector: LocalFsConnector) -> None:
    with patch(
        "app.connectors.sources.local_fs.connector.load_connector_filters",
        new=AsyncMock(return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))),
    ):
        await connector.run_sync()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=["arango", "neo4j"])
def graph_store(request) -> MockArangoProvider | MockNeo4jProvider:
    store = MockArangoProvider() if request.param == "arango" else MockNeo4jProvider()
    store.upsert_node(
        CollectionNames.APPS.value,
        {"_key": CONNECTOR_ID, "isActive": True, "createdBy": OWNER.id},
    )
    return store


@pytest.fixture
def data_store_provider(graph_store) -> LocalFsDataStoreProvider:
    return LocalFsDataStoreProvider(graph_store)


@pytest.fixture
def processor(data_store_provider) -> DataSourceEntitiesProcessor:
    logger = MagicMock()
    config_service = AsyncMock()
    proc = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
    proc.org_id = ORG_ID
    proc.messaging_producer = AsyncMock()
    # A real broker acks per-message; the fake always succeeds so tests can
    # focus on what gets published, not on retry/backoff (covered elsewhere).
    proc.messaging_producer.send_messages = AsyncMock(
        side_effect=lambda _topic, items: [True] * len(items)
    )
    proc.messaging_producer.send_message = AsyncMock(return_value=True)
    return proc


@pytest.fixture
def connector(processor, data_store_provider, tmp_path) -> LocalFsConnector:
    logger = MagicMock()
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(
        return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path), "batchSize": "50"}}
    )
    conn = LocalFsConnector(
        logger,
        processor,
        data_store_provider,
        config_service,
        CONNECTOR_ID,
        "personal",
        OWNER.id,
    )
    # _resolve_owner_user's own APPS/users lookups are exercised by the unit
    # suite; short-circuiting it here keeps these tests focused on sync +
    # recovery.
    conn._resolve_owner_user = AsyncMock(return_value=OWNER)
    return conn


def _key_for_rel_path(connector: LocalFsConnector, records: dict[str, dict[str, Any]], rel_path: str) -> str:
    ext_id = connector._external_record_id_for_rel_path(rel_path)
    return next(key for key, doc in records.items() if doc.get("externalRecordId") == ext_id)


# ---------------------------------------------------------------------------
# Scenario 1: index N files, add one, resync -> only the new file is embedded
# ---------------------------------------------------------------------------


class TestFullSyncThenIncrementalAddOneFile:
    async def test_incremental_resync_embeds_only_the_new_file(
        self, connector: LocalFsConnector, graph_store, tmp_path
    ) -> None:
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")

        await _run_sync(connector)

        records = _records_snapshot(graph_store)
        assert len(records) == 2
        a_key = _key_for_rel_path(connector, records, "a.txt")
        b_key = _key_for_rel_path(connector, records, "b.txt")
        # Simulate the indexing service having finished embedding both.
        records[a_key]["indexingStatus"] = ProgressStatus.COMPLETED.value
        records[b_key]["indexingStatus"] = ProgressStatus.COMPLETED.value

        sync_point_before = await connector._record_sync_point.read_sync_point(
            connector._sync_point_key
        )
        assert sync_point_before["recordCount"] == 2

        connector.data_entities_processor.messaging_producer.send_messages.reset_mock()
        (tmp_path / "c.txt").write_text("c", encoding="utf-8")

        await _run_sync(connector)

        # Exactly one publish call, for exactly one record: the new file.
        connector.data_entities_processor.messaging_producer.send_messages.assert_awaited_once()
        _topic, published = (
            connector.data_entities_processor.messaging_producer.send_messages.await_args.args
        )
        assert len(published) == 1
        published_record_id, _event = published[0]
        c_doc = graph_store.get_node(CollectionNames.RECORDS.value, published_record_id)
        assert c_doc["externalRecordId"] == connector._external_record_id_for_rel_path("c.txt")
        assert c_doc["indexingStatus"] == ProgressStatus.QUEUED.value

        # The two pre-existing, already-COMPLETED records were never touched
        # -- this is the regression Bug A's destructive run_sync() caused.
        assert records[a_key]["indexingStatus"] == ProgressStatus.COMPLETED.value
        assert records[b_key]["indexingStatus"] == ProgressStatus.COMPLETED.value
        assert len(records) == 3

        sync_point_after = await connector._record_sync_point.read_sync_point(
            connector._sync_point_key
        )
        assert sync_point_after["recordCount"] == 3

    async def test_second_incremental_resync_with_no_changes_publishes_nothing(
        self, connector: LocalFsConnector, graph_store, tmp_path
    ) -> None:
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        await _run_sync(connector)

        connector.data_entities_processor.messaging_producer.send_messages.reset_mock()
        connector.data_entities_processor.messaging_producer.send_message.reset_mock()

        await _run_sync(connector)

        connector.data_entities_processor.messaging_producer.send_messages.assert_not_awaited()
        connector.data_entities_processor.messaging_producer.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Scenario 2: restart mid-backlog -> redelivery-in-flight records are left
# alone, genuinely orphaned ones are recovered exactly once
# ---------------------------------------------------------------------------


class TestRestartMidBacklogRecovery:
    async def test_sweep_skips_fresh_queued_and_recovers_stale_queued_once(
        self, connector: LocalFsConnector, graph_store, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv("STALE_QUEUED_RECOVERY_AFTER_SECONDS", "60")
        (tmp_path / "fresh.txt").write_text("f", encoding="utf-8")
        (tmp_path / "stale.txt").write_text("s", encoding="utf-8")
        await _run_sync(connector)

        records = _records_snapshot(graph_store)
        fresh_key = _key_for_rel_path(connector, records, "fresh.txt")
        stale_key = _key_for_rel_path(connector, records, "stale.txt")
        assert records[fresh_key]["indexingStatus"] == ProgressStatus.QUEUED.value
        assert records[stale_key]["indexingStatus"] == ProgressStatus.QUEUED.value

        now_ms = get_epoch_timestamp_in_ms()
        # fresh.txt: a live consumer may be mid-flight on the still-inbound
        # message -- must not be touched (no double-processing).
        records[fresh_key]["queuedAt"] = now_ms
        # stale.txt: queuedAt is older than the threshold -- its message was
        # lost (broker retention, or the publish failed silently upstream).
        records[stale_key]["queuedAt"] = now_ms - 120_000

        container, retry_producer = _make_recovery_container()
        await recover_in_progress_records(container, _GraphProviderView(graph_store))

        retry_producer.send_event.assert_awaited_once()
        assert retry_producer.send_event.await_args.kwargs["key"] == str(stale_key)
        assert records[stale_key]["indexingStatus"] == ProgressStatus.QUEUED.value
        assert records[stale_key]["queuedAt"] >= now_ms
        assert records[fresh_key]["indexingStatus"] == ProgressStatus.QUEUED.value
        assert records[fresh_key]["queuedAt"] == now_ms

        # Idempotency: the record recovery just re-queued now has a fresh
        # queuedAt, so an immediate second sweep tick (e.g. the next
        # stale_recovery_interval_seconds) must not touch it again -- no
        # duplicate embedding.
        retry_producer.send_event.reset_mock()
        await recover_in_progress_records(container, _GraphProviderView(graph_store))
        retry_producer.send_event.assert_not_awaited()


# ---------------------------------------------------------------------------
# Scenario 3: retention loss while multiple records are QUEUED -> the sweep
# recovers exactly the orphans, nothing else
# ---------------------------------------------------------------------------


class TestRetentionLossRecoversExactlyOrphans:
    async def test_sweep_recovers_only_the_aged_orphan(
        self, connector: LocalFsConnector, graph_store, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv("STALE_QUEUED_RECOVERY_AFTER_SECONDS", "60")
        for name in ("orphan.txt", "missing_ts.txt", "not_started.txt"):
            (tmp_path / name).write_text(name, encoding="utf-8")
        await _run_sync(connector)

        records = _records_snapshot(graph_store)
        now_ms = get_epoch_timestamp_in_ms()

        orphan_key = _key_for_rel_path(connector, records, "orphan.txt")
        missing_ts_key = _key_for_rel_path(connector, records, "missing_ts.txt")
        not_started_key = _key_for_rel_path(connector, records, "not_started.txt")

        # orphan.txt: the message that would have consumed it fell out of
        # the broker's retention window (Kafka's 24h log retention, or a
        # Redis restart without persistence) while still QUEUED.
        records[orphan_key]["queuedAt"] = now_ms - 3_600_000

        # missing_ts.txt: queuedAt was never stamped (pre-rollout row, or
        # the best-effort stamp lost the race against a fast consumer) --
        # must NOT be treated as stale, unlike the IN_PROGRESS convention.
        records[missing_ts_key]["queuedAt"] = None

        # not_started.txt: stuck outside both QUEUED and IN_PROGRESS with an
        # old on-disk mtime. Local FS's createdAtTimestamp is the file's
        # mtime, not a server clock, so the NOT_STARTED pass must never
        # sweep a Local FS record on that basis.
        records[not_started_key]["indexingStatus"] = ProgressStatus.NOT_STARTED.value
        records[not_started_key]["createdAtTimestamp"] = now_ms - 10_000_000

        container, retry_producer = _make_recovery_container()
        await recover_in_progress_records(container, _GraphProviderView(graph_store))

        recovered_keys = {
            call.kwargs["key"] for call in retry_producer.send_event.await_args_list
        }
        assert recovered_keys == {str(orphan_key)}
        assert records[orphan_key]["indexingStatus"] == ProgressStatus.QUEUED.value
        assert records[orphan_key]["queuedAt"] >= now_ms
        assert records[missing_ts_key]["indexingStatus"] == ProgressStatus.QUEUED.value
        assert records[missing_ts_key]["queuedAt"] is None
        assert records[not_started_key]["indexingStatus"] == ProgressStatus.NOT_STARTED.value
