"""End-to-end tests for the Local FS connector's event-batch ingest.

Unlike ``tests/unit/connectors/sources/local_fs/test_connector.py`` (which
mocks ``data_entities_processor`` entirely), these tests wire up the *real*
``LocalFsConnector`` and the *real* ``DataSourceEntitiesProcessor`` against a
single shared in-memory graph store. Only the network-facing boundary -- the
graph DB wire protocol and the Kafka/Redis broker client -- is faked; every
other line of production ingest logic actually runs, so a real file on a real
``tmp_path`` ends up as a real record with a real ``externalRevisionId``.

Records are seeded through ``_apply_file_event_batch``, the same engine
``run_sync`` drives once it has pulled a page of events from the desktop.
``TestRunSync`` below exercises the full pull loop against a fake transport,
so both the engine and the loop that feeds it are covered.

Real, docker-backed ArangoDB/Neo4j x Kafka/Redis coverage lives in the
top-level ``integration-tests/`` suite (see
``.github/workflows/integration-tests.yml``). This module instead
parametrizes the *shape* of the in-memory store over ``arango``/``neo4j`` --
reusing the two ``_InMemoryGraphStore`` subclasses
``test_connector_workflow_integration.py`` already established for other
connectors -- to prove the ingest path takes no provider-specific shortcuts.
"""

from __future__ import annotations

import copy
import hashlib
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

try:
    # redis is a pinned dependency (pyproject: redis==5.2.1) and provides every
    # name stubbed below. Stub only when it is genuinely absent: installing the
    # stub over a working install shadows it, and the stub then has to keep pace
    # with every redis symbol the app imports -- which it did not, so importing
    # this module first in a collection run failed outright.
    import redis.asyncio.cluster  # noqa: F401
    from redis.exceptions import NoScriptError  # noqa: F401
except ImportError:
    _redis_exc = types.ModuleType("redis.exceptions")
    _redis_exc.ConnectionError = type("RedisConnectionError", (Exception,), {})
    _redis_exc.TimeoutError = type("RedisTimeoutError", (Exception,), {})
    # app.services.messaging.distributed_concurrency imports this name at module
    # scope; without it the stub shadows a perfectly good installed redis and
    # the module fails to import whenever nothing else pulled redis in first.
    _redis_exc.NoScriptError = type("RedisNoScriptError", (Exception,), {})
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

from app.config.constants.arangodb import CollectionNames, MimeTypes
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    MultiselectOperator,
    SyncFilterKey,
)
from app.connectors.sources.local_fs.connector import (
    SYNC_ROOT_PATH_KEY,
    LocalFsConnector,
    LocalFsDesktopOfflineError,
    LocalFsDeviceMismatchError,
    LocalFsDeviceUnclaimedError,
    LocalFsRootUnavailableError,
    _client_path_for_display,
)
from app.connectors.sources.local_fs.models import LocalFsFileEvent, LocalFsPullBatch
from app.models.entities import AppMetadata, User
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.unit.connectors.sources.test_connector_workflow_integration import (
    MockArangoProvider,
    MockDataStoreProvider,
    MockNeo4jProvider,
    MockTransactionStore,
)

ORG_ID = "org-e2e-1"
CONNECTOR_ID = "local-fs-e2e-1"
DEVICE_ID = "device-e2e-1"
DEVICE_NAME = "owner-e2e-laptop"
OWNER = User(email="owner@example.com", id="owner-e2e-1", org_id=ORG_ID)
PRIOR_SYNC_TIME_MS = 1_700_000_000_000
RESUME_CURSOR = "cursor-from-the-previous-run"

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Store extensions: everything LocalFsConnector needs beyond the
# record/record-group/app-user CRUD MockTransactionStore and
# MockDataStoreProvider already provide for the other connectors' tests.
# ---------------------------------------------------------------------------


class _GraphProviderView:
    """Read/CAS-only view over the shared in-memory store, for the calls that
    run outside of any transaction."""

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
    """Adds the status-scan and external-id-delete surface Local FS needs,
    on top of the generic record/record-group/app-user CRUD every connector
    already exercises via ``MockTransactionStore``."""

    def __init__(self, store) -> None:
        super().__init__(store)
        self.txn = None
        self.graph_provider = _GraphProviderView(store)

    async def get_app_by_id(self, connector_id: str) -> AppMetadata | None:
        doc = self._s.get_node(CollectionNames.APPS.value, connector_id)
        return AppMetadata.from_db_document(doc) if doc is not None else None

    async def get_user_by_user_id(self, user_id: str) -> User | None:
        doc = self._s.get_node(CollectionNames.USERS.value, user_id)
        if doc is None:
            return None
        return User(
            id=doc["_key"],
            email=doc.get("email", ""),
            org_id=doc.get("orgId", ""),
            full_name=doc.get("fullName"),
            is_active=doc.get("isActive", True),
        )

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


class LocalFsDataStoreProvider(MockDataStoreProvider):
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[LocalFsTransactionStore]:
        yield LocalFsTransactionStore(self._store)


def _records_snapshot(graph_store) -> dict[str, dict[str, Any]]:
    return graph_store.collections.setdefault(CollectionNames.RECORDS.value, {})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _event(
    rel_path: str,
    *,
    event_type: str = "CREATED",
    old_path: str | None = None,
    sha256: str | None = None,
    mime_type: str | None = "text/plain",
) -> LocalFsFileEvent:
    """One desktop event.

    ``sha256`` defaults to a hash of the path, which makes every distinct path
    a distinct revision. Pass it explicitly to model the two cases where
    content and path move independently: a pure rename (content unchanged, so
    the *old* path's hash) and an edit in place (same path, new hash).
    """
    return LocalFsFileEvent(
        type=event_type,
        path=rel_path,
        oldPath=old_path,
        isDirectory=False,
        timestamp=get_epoch_timestamp_in_ms(),
        mimeType=mime_type,
        size=len(rel_path.encode("utf-8")),
        sha256=sha256 if sha256 is not None else _sha(rel_path),
    )


def _events_for(*rel_paths: str) -> list[LocalFsFileEvent]:
    return [_event(rel_path) for rel_path in rel_paths]


def _extensions_filter(extensions: list[str]) -> FilterCollection:
    """A sync filter limiting the connector to these file extensions."""
    return FilterCollection(
        filters=[
            Filter(
                key=SyncFilterKey.FILE_EXTENSIONS.value,
                type=FilterType.MULTISELECT,
                operator=MultiselectOperator.IN,
                value=extensions,
            )
        ]
    )


def _external_ids(graph_store) -> set[str]:
    return {
        doc.get("externalRecordId") for doc in _records_snapshot(graph_store).values()
    }


def _record_for(
    connector: LocalFsConnector, graph_store, rel_path: str
) -> dict[str, Any] | None:
    """The stored record document for a path, or None when it is not indexed."""
    ext_id = connector._external_record_id_for_rel_path(rel_path)
    for doc in _records_snapshot(graph_store).values():
        if doc.get("externalRecordId") == ext_id:
            return doc
    return None


async def _seed_files(connector: LocalFsConnector, *rel_paths: str) -> None:
    """Seed graph records through the real apply engine, the way run_sync does
    once a page has been pulled from the desktop."""
    await connector._reload_sync_settings()
    root = _client_path_for_display(connector.sync_root_path)
    owner, rg_external = await connector._ensure_owner_and_record_group(root)
    await connector._apply_file_event_batch(
        _events_for(*rel_paths),
        owner=owner,
        sync_filters=FilterCollection(filters=[]),
        indexing_filters=FilterCollection(filters=[]),
        external_record_group_id=rg_external,
        root_for_display=root,
        emitted_folder_paths=set(),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=["arango", "neo4j"])
def graph_store(request) -> MockArangoProvider | MockNeo4jProvider:
    store = MockArangoProvider() if request.param == "arango" else MockNeo4jProvider()
    store.upsert_node(
        CollectionNames.APPS.value,
        {
            "_key": CONNECTOR_ID,
            "isActive": True,
            "createdBy": OWNER.id,
            "ownerDeviceId": DEVICE_ID,
            "ownerDeviceName": DEVICE_NAME,
        },
    )
    store.upsert_node(
        CollectionNames.USERS.value,
        {
            "_key": OWNER.id,
            "email": OWNER.email,
            "orgId": ORG_ID,
            "isActive": True,
            "fullName": OWNER.email,
        },
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
    return conn


# ---------------------------------------------------------------------------
# run_sync drives the real apply engine from pulled pages
# ---------------------------------------------------------------------------


class TestRunSync:
    """run_sync against the fully production-wired connector, with only the
    desktop transport faked."""

    @staticmethod
    def _page(
        run_id: str,
        batch_index: int,
        events,
        *,
        has_more: bool,
        device_id: str = DEVICE_ID,
    ) -> LocalFsPullBatch:
        return LocalFsPullBatch(
            connectorId=CONNECTOR_ID,
            runId=run_id,
            batchIndex=batch_index,
            deviceId=device_id,
            cursor=f"c{batch_index}",
            hasMore=has_more,
            events=events,
        )

    async def _run(
        self,
        connector: LocalFsConnector,
        pages,
        *,
        sync_point: dict[str, Any] | None = None,
        device_id: str = DEVICE_ID,
        sync_filters: FilterCollection | None = None,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _pull(
            *, run_id, batch_index, cursor, mode, session, expected_device_id
        ) -> LocalFsPullBatch:
            captured.setdefault("mode", mode)
            captured.setdefault("cursor", cursor)
            captured["expected_device_id"] = expected_device_id
            page = pages[batch_index]
            answer = self._page(
                run_id, batch_index, page[0], has_more=page[1], device_id=device_id
            )
            # Mirror the guard _request_file_event_batch applies in production;
            # _pull_with_retry is stubbed out here, so it would otherwise be
            # invisible to these tests.
            if answer.deviceId != expected_device_id:
                raise LocalFsDeviceMismatchError(expected_device_id, answer.deviceId)
            return answer

        connector._pull_with_retry = AsyncMock(side_effect=_pull)
        connector.record_sync_point.read_sync_point = AsyncMock(
            return_value=dict(sync_point or {})
        )
        connector.record_sync_point.update_sync_point = AsyncMock()
        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(
                return_value=(
                    sync_filters or FilterCollection(filters=[]),
                    FilterCollection(filters=[]),
                )
            ),
        ):
            await connector.run_sync()
        connector._captured_mode = captured.get("mode")
        connector._captured_cursor = captured.get("cursor")
        connector._captured_expected_device_id = captured.get("expected_device_id")

    async def test_pulled_pages_become_records(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        await self._run(
            connector,
            [
                (_events_for("a.txt"), True),
                (_events_for("nested/b.txt"), False),
            ],
        )

        records = _records_snapshot(graph_store)
        seeded = {doc.get("externalRecordId") for doc in records.values()}
        for rel_path in ("a.txt", "nested/b.txt", "nested"):
            assert connector._external_record_id_for_rel_path(rel_path) in seeded
        assert connector._captured_mode == "FULL"
        # last_sync_time is only written once the desktop reported hasMore=false.
        final_write = connector.record_sync_point.update_sync_point.await_args_list[-1]
        assert "last_sync_time" in final_write.args[1]

    async def test_full_run_prunes_records_the_desktop_no_longer_reports(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        await _seed_files(connector, "stale.txt", "kept.txt")

        await self._run(connector, [(_events_for("kept.txt"), False)])

        records = _records_snapshot(graph_store)
        remaining = {doc.get("externalRecordId") for doc in records.values()}
        assert connector._external_record_id_for_rel_path("kept.txt") in remaining
        assert connector._external_record_id_for_rel_path("stale.txt") not in remaining

    @staticmethod
    def _resume_point() -> dict[str, Any]:
        """A sync point left by a previous run, which makes the next one INCREMENTAL."""
        return {
            "last_sync_time": PRIOR_SYNC_TIME_MS,
            "cursor": RESUME_CURSOR,
            "run_id": "run-previous",
            "last_batch_index": 3,
        }

    async def test_incremental_run_resumes_and_never_prunes(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        # The prune set is only built for a FULL run. If that guard were ever
        # dropped, every scheduled sync would delete each record the one page it
        # pulled did not happen to mention -- i.e. the whole index.
        await _seed_files(connector, "kept.txt", "untouched.txt")

        await self._run(
            connector,
            [(_events_for("added.txt"), False)],
            sync_point=self._resume_point(),
        )

        assert connector._captured_mode == "INCREMENTAL"
        assert connector._captured_cursor == RESUME_CURSOR
        remaining = _external_ids(graph_store)
        for rel_path in ("kept.txt", "untouched.txt", "added.txt"):
            assert connector._external_record_id_for_rel_path(rel_path) in remaining, (
                f"{rel_path} should survive an incremental run"
            )

    async def test_incremental_carries_last_sync_time_across_pages(
        self, connector: LocalFsConnector
    ) -> None:
        # update_sync_point rewrites the whole document, so a mid-run write that
        # omits last_sync_time demotes the next run to a destructive FULL.
        await self._run(
            connector,
            [
                (_events_for("one.txt"), True),
                (_events_for("two.txt"), False),
            ],
            sync_point=self._resume_point(),
        )

        writes = [
            call.args[1]
            for call in connector.record_sync_point.update_sync_point.await_args_list
        ]
        assert len(writes) >= 2
        assert all("last_sync_time" in payload for payload in writes)
        assert writes[0]["last_sync_time"] == PRIOR_SYNC_TIME_MS
        # Only the final write may advance the watermark.
        assert writes[-1]["last_sync_time"] > PRIOR_SYNC_TIME_MS

    async def test_delete_event_removes_the_record(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        await _seed_files(connector, "gone.txt", "kept.txt")

        # Incremental on purpose: on a FULL run the prune would remove the file
        # regardless, so the DELETE path itself would go unproven.
        await self._run(
            connector,
            [([_event("gone.txt", event_type="DELETED")], False)],
            sync_point=self._resume_point(),
        )

        assert _record_for(connector, graph_store, "gone.txt") is None
        assert _record_for(connector, graph_store, "kept.txt") is not None

    async def test_rename_reuses_the_existing_record_vertex(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        await _seed_files(connector, "before.txt")
        original = _record_for(connector, graph_store, "before.txt")
        assert original is not None

        await self._run(
            connector,
            [
                (
                    [
                        _event(
                            "after.txt",
                            event_type="RENAMED",
                            old_path="before.txt",
                            # A rename does not touch content, so the hash is
                            # still the one the old path was stored with.
                            sha256=_sha("before.txt"),
                        )
                    ],
                    False,
                )
            ],
            sync_point=self._resume_point(),
        )

        moved = _record_for(connector, graph_store, "after.txt")
        assert moved is not None, "the renamed file should still be indexed"
        # The vertex is reused rather than deleted and recreated: a new _key
        # would mean the file is re-indexed from scratch and loses its history.
        assert moved["_key"] == original["_key"]
        assert moved["externalRevisionId"] == original["externalRevisionId"]
        assert _record_for(connector, graph_store, "before.txt") is None

    async def test_modified_event_bumps_the_revision_in_place(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        await _seed_files(connector, "doc.txt")
        original = _record_for(connector, graph_store, "doc.txt")
        assert original is not None
        assert original["externalRevisionId"] == _sha("doc.txt")

        edited_sha = _sha("doc.txt after the edit")
        await self._run(
            connector,
            [([_event("doc.txt", event_type="MODIFIED", sha256=edited_sha)], False)],
            sync_point=self._resume_point(),
        )

        updated = _record_for(connector, graph_store, "doc.txt")
        assert updated is not None
        # externalRevisionId is what marks the record for re-indexing; if the
        # new hash did not land, the edited file keeps serving stale content.
        assert updated["externalRevisionId"] == edited_sha
        assert updated["_key"] == original["_key"]

    async def test_full_run_prunes_records_a_new_sync_filter_excludes(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        """Narrowing the sync filter drops the records it now excludes.

        Intended behaviour, and it falls out of ordering rather than an explicit
        delete: a filtered-out event is skipped before ``flush_upserts`` can add
        it to ``seen_external_ids``, so the full-run prune treats it as a file
        the desktop no longer reports.
        """
        await _seed_files(connector, "keep.txt", "drop.md")

        # The desktop still reports both files; only the filter has changed.
        await self._run(
            connector,
            [(_events_for("keep.txt", "drop.md"), False)],
            sync_filters=_extensions_filter(["txt"]),
        )

        assert _record_for(connector, graph_store, "keep.txt") is not None
        assert _record_for(connector, graph_store, "drop.md") is None

    async def test_event_mime_type_wins_over_the_servers_own_guess(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        # Precedence is `event.mimeType or guess_type(name) or UNKNOWN`. The
        # desktop stamps the type because the server's guess is platform- and
        # version-dependent (3.12 answers None for .webp), and a record stored
        # as application/unknown gets no parser at indexing time.
        await self._run(
            connector,
            [
                (
                    [
                        _event("stamped.txt", mime_type="application/x-pipeshub-test"),
                        _event("guessable.txt", mime_type=None),
                        _event("opaque.xyzzy", mime_type=None),
                    ],
                    False,
                )
            ],
        )

        stamped = _record_for(connector, graph_store, "stamped.txt")
        guessable = _record_for(connector, graph_store, "guessable.txt")
        opaque = _record_for(connector, graph_store, "opaque.xyzzy")
        assert stamped is not None and guessable is not None and opaque is not None
        assert stamped["mimeType"] == "application/x-pipeshub-test"
        assert guessable["mimeType"] == "text/plain"
        assert opaque["mimeType"] == MimeTypes.UNKNOWN.value

    async def test_records_carry_an_owner_permission_edge(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        # Without this edge the file is indexed but invisible to the only
        # person who can see it, which no record-count assertion would catch.
        await self._run(connector, [(_events_for("nested/a.txt"), False)])

        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        owner_ref = f"{CollectionNames.USERS.value}/{OWNER.id}"
        # The synthesized parent folder needs one too, or the tree it hangs in
        # is unreachable.
        for rel_path in ("nested/a.txt", "nested"):
            doc = _record_for(connector, graph_store, rel_path)
            assert doc is not None, f"{rel_path} was not indexed"
            record_ref = f"{CollectionNames.RECORDS.value}/{doc['_key']}"
            granted = [
                edge
                for edge in perm_edges
                if edge.get("_to") == record_ref and edge.get("_from") == owner_ref
            ]
            assert granted, f"{rel_path} has no owner permission edge"
            assert granted[0]["role"] == "OWNER"

    async def test_owner_device_comes_from_the_app_document(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        await self._run(connector, [(_events_for("a.txt"), False)])

        assert connector._captured_expected_device_id == DEVICE_ID
        for write in connector.record_sync_point.update_sync_point.await_args_list:
            assert "device_id" not in write.args[1]

    async def test_unclaimed_connector_pulls_nothing_and_keeps_records(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        await _seed_files(connector, "kept.txt")
        before = copy.deepcopy(_records_snapshot(graph_store))
        graph_store.get_node(CollectionNames.APPS.value, CONNECTOR_ID)["ownerDeviceId"] = None
        connector.notify = AsyncMock()

        with pytest.raises(LocalFsDeviceUnclaimedError):
            await self._run(connector, [(_events_for("intruder.txt"), False)])

        connector._pull_with_retry.assert_not_awaited()
        assert _records_snapshot(graph_store) == before
        assert connector.record_sync_point.update_sync_point.await_count == 0
        payload = connector.notify.await_args.kwargs["payload"]
        assert payload["error_code"] == "DESKTOP_UNCLAIMED"

    async def test_page_from_a_foreign_device_aborts_without_pruning(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        # A second machine claiming the socket must not be able to make a full
        # run prune everything the owning machine synced.
        await _seed_files(connector, "kept.txt")
        before = copy.deepcopy(_records_snapshot(graph_store))
        connector.notify = AsyncMock()

        with pytest.raises(LocalFsDeviceMismatchError):
            await self._run(
                connector,
                [(_events_for("intruder.txt"), False)],
                device_id="device-someone-else",
            )

        assert _records_snapshot(graph_store) == before
        assert connector.record_sync_point.update_sync_point.await_count == 0
        # Only the user can resolve this, so it must not fail silently.
        connector.notify.assert_awaited_once()

    async def test_missing_root_keeps_records_and_tells_the_user_to_update_the_path(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        # A moved, renamed, or deleted folder must not prune the index, and
        # the user has to be pointed at connector settings to retarget it.
        await _seed_files(connector, "kept.txt")
        before = copy.deepcopy(_records_snapshot(graph_store))
        connector.notify = AsyncMock()
        connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsRootUnavailableError(
                "ROOT_MISSING", "Local sync root folder does not exist"
            )
        )
        connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": PRIOR_SYNC_TIME_MS, "cursor": RESUME_CURSOR}
        )
        connector.record_sync_point.update_sync_point = AsyncMock()
        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(
                return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))
            ),
        ):
            with pytest.raises(LocalFsRootUnavailableError):
                await connector.run_sync()

        assert _records_snapshot(graph_store) == before
        connector.record_sync_point.update_sync_point.assert_not_awaited()
        kwargs = connector.notify.await_args.kwargs
        assert kwargs["payload"]["error_code"] == "ROOT_MISSING"
        assert "was moved, renamed or deleted" in kwargs["message"]
        assert "Indexed files are kept" in kwargs["message"]
        assert "update the folder path in connector" in kwargs["message"]

    async def test_offline_desktop_leaves_existing_records_untouched(
        self, connector: LocalFsConnector, graph_store
    ) -> None:
        # The prune must never run on a skipped sync, or one closed laptop
        # would empty the user's index.
        await _seed_files(connector, "kept.txt")
        before = copy.deepcopy(_records_snapshot(graph_store))

        connector._pull_with_retry = AsyncMock(
            side_effect=LocalFsDesktopOfflineError("asleep")
        )
        connector.record_sync_point.read_sync_point = AsyncMock(return_value={})
        connector.record_sync_point.update_sync_point = AsyncMock()
        with patch(
            "app.connectors.sources.local_fs.connector.load_connector_filters",
            new=AsyncMock(
                return_value=(FilterCollection(filters=[]), FilterCollection(filters=[]))
            ),
        ):
            with pytest.raises(LocalFsDesktopOfflineError):
                await connector.run_sync()

        assert _records_snapshot(graph_store) == before
        connector.record_sync_point.update_sync_point.assert_not_awaited()
