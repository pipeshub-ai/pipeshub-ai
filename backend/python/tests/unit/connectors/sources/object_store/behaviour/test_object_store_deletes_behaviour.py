"""Deletes at the source, failed listings and the file-extensions filter in the object-store connectors.

S3 and MinIO share ``S3CompatibleBaseConnector``; GCS and Azure Blob have their
own copies of the same listing loop, so every scenario runs against all four.
"""

import pytest
from object_store_behaviour_fakes import (
    BUCKET,
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
    make_connector,
)

from app.connectors.core.base.connector.connector_service import BaseConnector

ALL = ["s3", "minio", "gcs", "azure_blob"]
PAGED = ["s3", "minio", "gcs"]


def path(key: str) -> str:
    return f"{BUCKET}/{key}"


@pytest.fixture(params=ALL)
def kind(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def connector(
    kind: str, store: FakeObjectStore, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, config: FakeConfigService,
) -> BaseConnector:
    return make_connector(kind, store, db, checkpoints, config)


class TestDeleteAtTheSource:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("second_sync", ["run_incremental_sync", "run_sync"])
    async def test_a_deleted_object_leaves_the_index(self, connector, store, db, second_sync) -> None:
        for key in ("a.txt", "b.txt", "docs/c.txt"):
            store.put(key)
        await connector.run_sync()
        assert {path("a.txt"), path("b.txt"), path("docs/c.txt"), path("docs")} <= db.paths()
        doomed = db.by_path()[path("b.txt")].id

        store.delete("b.txt")
        await getattr(connector, second_sync)()

        assert path("b.txt") not in db.paths()
        assert db.deleted == [doomed]
        assert {path("a.txt"), path("docs/c.txt"), path("docs")} <= db.paths()

    @pytest.mark.asyncio
    async def test_a_folder_left_empty_goes_with_its_last_file(self, connector, store, db) -> None:
        store.put("a.txt")
        store.put("docs/c.txt")
        await connector.run_sync()

        store.delete("docs/c.txt")
        await connector.run_incremental_sync()

        assert db.paths() == {path("a.txt")}

    @pytest.mark.asyncio
    async def test_a_renamed_object_keeps_its_record(self, connector, store, db) -> None:
        store.put("a.txt", "first")
        store.put("b.txt", "second")
        await connector.run_sync()
        record_id = db.by_path()[path("b.txt")].id

        store.rename("b.txt", "z.txt")
        await connector.run_incremental_sync()

        assert db.by_path()[path("z.txt")].id == record_id
        assert db.deleted == []


class TestFailedListing:
    @staticmethod
    async def _synced_then_first_key_deleted(connector: BaseConnector, store: FakeObjectStore) -> None:
        for key in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt"):
            store.put(key, key)
        await connector.run_sync()
        store.delete("a.txt")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["error", "raise"])
    async def test_a_listing_that_fails_partway_deletes_nothing(self, connector, store, db, mode) -> None:
        await self._synced_then_first_key_deleted(connector, store)
        store.fail_page, store.fail_mode = 1, mode

        await connector.run_incremental_sync()

        assert db.deleted == []
        assert path("a.txt") in db.paths()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", PAGED)
    async def test_a_listing_resumed_from_a_saved_token_deletes_nothing(self, connector, store, db) -> None:
        # The failed run saved a token past the page holding the deleted key, so
        # the next run lists only what follows it; only the full listing after that may delete.
        await self._synced_then_first_key_deleted(connector, store)
        store.fail_page = 1
        await connector.run_incremental_sync()
        store.fail_page = None

        await connector.run_incremental_sync()
        assert db.deleted == []

        await connector.run_incremental_sync()
        assert path("a.txt") not in db.paths()
        assert len(db.deleted) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["azure_blob"])
    async def test_the_next_complete_listing_deletes(self, connector, store, db) -> None:
        await self._synced_then_first_key_deleted(connector, store)
        store.fail_page = 1
        await connector.run_incremental_sync()
        store.fail_page = None

        await connector.run_incremental_sync()

        assert path("a.txt") not in db.paths()

    @pytest.mark.asyncio
    async def test_an_unreadable_record_list_deletes_nothing(self, connector, store, db) -> None:
        store.put("a.txt")
        store.put("b.txt")
        await connector.run_sync()
        store.delete("a.txt")
        db.failing.add("get_records_in_record_group")

        await connector.run_incremental_sync()

        assert db.deleted == []


class TestFileExtensionsFilter:
    @staticmethod
    def _seed(store: FakeObjectStore) -> None:
        for key in ("report.pdf", "debug.log", "Makefile", "notes/todo.LOG"):
            store.put(key)

    @pytest.mark.asyncio
    async def test_not_in_excludes_the_listed_extensions(self, connector, store, db, config) -> None:
        self._seed(store)
        config.set_extensions("not_in", ["log"])

        await connector.run_sync()

        files = {p for p, r in db.by_path().items() if r.is_file}
        assert files == {path("report.pdf"), path("Makefile")}

    @pytest.mark.asyncio
    async def test_in_includes_only_the_listed_extensions(self, connector, store, db, config) -> None:
        self._seed(store)
        config.set_extensions("in", ["log"])

        await connector.run_sync()

        files = {p for p, r in db.by_path().items() if r.is_file}
        assert files == {path("debug.log"), path("notes/todo.LOG")}

    @pytest.mark.asyncio
    async def test_a_newly_excluded_file_is_removed_through_the_delete_path(self, connector, store, db, config) -> None:
        self._seed(store)
        await connector.run_sync()
        excluded = {db.by_path()[path(k)].id for k in ("debug.log", "notes/todo.LOG")}
        notes_folder = db.by_path()[path("notes")].id

        config.set_extensions("not_in", ["log"])
        await connector.run_incremental_sync()

        # on_record_deleted is the processor's one delete path (graph, vectors, blob and Mongo).
        assert set(db.deleted) == excluded | {notes_folder}
        assert {p for p, r in db.by_path().items() if r.is_file} == {path("report.pdf"), path("Makefile")}
