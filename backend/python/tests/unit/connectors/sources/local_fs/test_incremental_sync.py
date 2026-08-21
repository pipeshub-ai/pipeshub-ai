"""Regression tests for issue #2842: Local FS sync was fully destructive.

``run_sync`` used to call ``_reset_existing_records(delete_storage_documents=True)``
before walking the tree, deleting every stored record (including COMPLETED ones)
regardless of whether the underlying file had changed. Every sync therefore
re-uploaded and re-queued the entire corpus for (re-)embedding, and
``run_incremental_sync`` was a literal alias for that destructive ``run_sync``.

These tests exercise ``run_sync``/``run_incremental_sync`` end-to-end against a real
temp directory, with a lightweight fake data store that mimics the one contract that
matters here: ``DataSourceEntitiesProcessor._process_record``'s revision diff (an
unchanged ``external_revision_id`` keeps a record's id and indexing status; a changed
one requeues it). See
``app/connectors/core/base/data_processor/data_source_entities_processor.py:953-1035``
for the real logic this fake stands in for.
"""

from __future__ import annotations

import sys
import types
from typing import Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

# --- Import shims (must run before the connector import; same pattern as
# test_connector.py in this directory) ---
if "app.containers.connector" not in sys.modules:
    _stub_container = types.ModuleType("app.containers.connector")

    class _ContainerMeta(type):
        def __getattr__(cls, name):
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

from app.connectors.core.registry.filters import FilterCollection  # noqa: E402
from app.connectors.sources.local_fs.connector import (  # noqa: E402
    LOCAL_FS_PRUNE_VALVE_MAX_FRACTION,
    LOCAL_FS_PRUNE_VALVE_MIN_ABSOLUTE,
    SYNC_ROOT_PATH_KEY,
    LocalFsConnector,
)
from app.models.entities import User  # noqa: E402

pytestmark = pytest.mark.asyncio


class _FakeRecordGroup:
    def __init__(self, group_id: str) -> None:
        self.id = group_id


class _FakeTxStore:
    """Backs both the pruning path and the upsert path with the same dict."""

    def __init__(self, store: Dict[str, object]) -> None:
        self._store = store

    async def get_record_group_by_external_id(self, connector_id, external_id):
        return _FakeRecordGroup("rg-1")

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
    ):
        items = list(self._store.values())
        end = offset + limit if limit else len(items)
        return items[offset:end]

    async def delete_record_by_external_id(self, connector_id, external_id, owner_user_id):
        self._store.pop(external_id, None)


class _FakeTxContext:
    def __init__(self, store: Dict[str, object]) -> None:
        self._store = store

    async def __aenter__(self):
        return _FakeTxStore(self._store)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeDataStoreProvider:
    def __init__(self, store: Dict[str, object]) -> None:
        self._store = store

    def transaction(self):
        return _FakeTxContext(self._store)


class _FakeEntitiesProcessor:
    """Stands in for DataSourceEntitiesProcessor's on_new_records/_process_record.

    Mimics only the revision-diff contract this fix depends on: an existing
    record with the same external_revision_id keeps its id and indexing_status;
    a changed one is requeued (NOT_STARTED). Real logic:
    data_source_entities_processor.py:953-1035, 1112-1189.
    """

    def __init__(self, store: Dict[str, object]) -> None:
        self._store = store
        self.org_id = "org-1"

    async def on_new_app_users(self, users):
        return None

    async def on_new_record_groups(self, groups):
        return None

    async def on_new_records(self, records_with_permissions):
        for record, _perms in records_with_permissions:
            existing = self._store.get(record.external_record_id)
            if existing is None:
                record.indexing_status = "NOT_STARTED"
            else:
                record.id = existing.id
                if record.external_revision_id == existing.external_revision_id:
                    record.indexing_status = existing.indexing_status
                else:
                    record.indexing_status = "NOT_STARTED"
            self._store[record.external_record_id] = record


@pytest.fixture
def synced_folder(tmp_path):
    """A LocalFsConnector wired to a real temp directory and a fake in-memory store."""
    store: Dict[str, object] = {}
    logger = MagicMock()
    processor = _FakeEntitiesProcessor(store)
    provider = _FakeDataStoreProvider(store)
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(
        return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path)}}
    )

    connector = LocalFsConnector(
        logger, processor, provider, config_service, "conn-2842", "personal", "user-1",
    )
    owner = User(email="o@x.com", id="owner-1", org_id="org-1")
    connector._resolve_owner_user = AsyncMock(return_value=owner)

    async def _no_filters(*_args, **_kwargs):
        return FilterCollection(filters=[]), FilterCollection(filters=[])

    import app.connectors.sources.local_fs.connector as connector_module

    connector_module.load_connector_filters = _no_filters  # type: ignore[assignment]

    return connector, store, tmp_path


@pytest.mark.asyncio
class TestIncrementalSyncDoesNotWipeUnchangedRecords:
    async def test_unchanged_kept_changed_requeued_deleted_pruned_new_added(
        self, synced_folder
    ):
        connector, store, root = synced_folder

        # keep.txt lives in a subfolder so the folder record itself must also
        # survive the prune diff (regression coverage for the folder-loop half
        # of walked_external_ids, not just the file-loop half).
        (root / "docs").mkdir()
        (root / "docs" / "keep.txt").write_text("same", encoding="utf-8")
        (root / "modify.txt").write_text("v1", encoding="utf-8")
        (root / "remove.txt").write_text("gone-soon", encoding="utf-8")

        await connector.run_sync()

        docs_ext = connector._external_record_id_for_rel_path("docs")
        keep_ext = connector._external_record_id_for_rel_path("docs/keep.txt")
        modify_ext = connector._external_record_id_for_rel_path("modify.txt")
        remove_ext = connector._external_record_id_for_rel_path("remove.txt")
        assert {docs_ext, keep_ext, modify_ext, remove_ext} <= set(store.keys())

        # Simulate the indexing service having finished embedding all three,
        # same as real life between two syncs.
        for ext_id in (docs_ext, keep_ext, modify_ext, remove_ext):
            store[ext_id].indexing_status = "COMPLETED"
        docs_id_before = store[docs_ext].id
        keep_id_before = store[keep_ext].id
        modify_id_before = store[modify_ext].id

        # Mutate the tree: docs/keep.txt untouched, modify.txt changes content
        # (mtime+size revision changes), remove.txt deleted, new.txt added.
        (root / "modify.txt").write_text("v2-different-length", encoding="utf-8")
        (root / "remove.txt").unlink()
        (root / "new.txt").write_text("brand new", encoding="utf-8")

        await connector.run_incremental_sync()

        # Untouched folder + file: same record ids, still COMPLETED — NOT
        # deleted and recreated, NOT re-queued for embedding. This is the
        # assertion that fails on the pre-fix code (every file got a fresh
        # uuid every sync).
        assert docs_ext in store
        assert store[docs_ext].id == docs_id_before
        assert store[docs_ext].indexing_status == "COMPLETED"
        assert keep_ext in store
        assert store[keep_ext].id == keep_id_before
        assert store[keep_ext].indexing_status == "COMPLETED"

        # Modified file: same record id (in-place update via _process_record's
        # contract), but re-queued because its revision changed.
        assert modify_ext in store
        assert store[modify_ext].id == modify_id_before
        assert store[modify_ext].indexing_status == "NOT_STARTED"

        # Deleted file: pruned exactly once, not left behind.
        assert remove_ext not in store

        # New file: created.
        new_ext = connector._external_record_id_for_rel_path("new.txt")
        assert new_ext in store
        assert store[new_ext].indexing_status == "NOT_STARTED"

    async def test_run_sync_no_longer_calls_reset_existing_records(self, synced_folder):
        """Guards against regressing back to the pre-walk destructive wipe."""
        connector, _store, root = synced_folder
        (root / "a.txt").write_text("x", encoding="utf-8")
        connector._reset_existing_records = AsyncMock(
            side_effect=AssertionError(
                "run_sync must not call the wholesale reset any more"
            )
        )

        await connector.run_sync()

        connector._reset_existing_records.assert_not_called()


@pytest.mark.asyncio
class TestPruneStaleRecordsValve:
    async def test_refuses_to_prune_when_stale_fraction_is_implausibly_high(self):
        """A failed/partial walk must not be treated as mass deletion."""
        store: Dict[str, object] = {}
        provider = _FakeDataStoreProvider(store)
        logger = MagicMock()
        connector = LocalFsConnector(
            logger, AsyncMock(org_id="org-1"), provider, AsyncMock(),
            "conn-valve", "personal", "user-1",
        )

        class _Rec:
            def __init__(self, external_record_id, path=None):
                self.external_record_id = external_record_id
                self.path = path

        n_records = LOCAL_FS_PRUNE_VALVE_MIN_ABSOLUTE + 10
        for i in range(n_records):
            ext_id = f"ext-{i}"
            store[ext_id] = _Rec(ext_id)

        # Walk "saw" almost nothing — well over the stale-fraction valve.
        walked = {f"ext-{i}" for i in range(2)}
        assert (n_records - len(walked)) / n_records >= LOCAL_FS_PRUNE_VALVE_MAX_FRACTION

        deleted = await connector._prune_stale_records("owner-1", "rg-ext", walked)

        assert deleted == 0
        assert len(store) == n_records  # nothing was pruned
        connector.logger.error.assert_called()

    async def test_prunes_normally_below_the_valve(self):
        store: Dict[str, object] = {}
        provider = _FakeDataStoreProvider(store)
        logger = MagicMock()
        connector = LocalFsConnector(
            logger, AsyncMock(org_id="org-1"), provider, AsyncMock(),
            "conn-valve2", "personal", "user-1",
        )

        class _Rec:
            def __init__(self, external_record_id, path=None):
                self.external_record_id = external_record_id
                self.path = path

        store["keep"] = _Rec("keep")
        store["gone"] = _Rec("gone")

        deleted = await connector._prune_stale_records("owner-1", "rg-ext", {"keep"})

        assert deleted == 1
        assert "keep" in store
        assert "gone" not in store
