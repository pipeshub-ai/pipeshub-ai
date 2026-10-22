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
    Response,
    make_connector,
    run_full_sync_task,
    runner_graph,
    sync_with_failed_listing,
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

    @pytest.mark.asyncio
    async def test_the_surviving_copy_of_shared_content_keeps_a_record(
        self, connector: BaseConnector, store: FakeObjectStore, db: FakeRecordsDb,
    ) -> None:
        # Equal content at a second key is taken as a move of the first key's record,
        # so one record can stand for both; deleting the key it names must not lose it.
        store.put("a.txt", "same bytes")
        await connector.run_sync()
        store.put("b.txt", "same bytes")
        await connector.run_incremental_sync()
        owners = {p for p in db.paths() if p in {path("a.txt"), path("b.txt")}}
        assert owners
        owner = sorted(owners)[0]
        survivor = path("b.txt") if owner == path("a.txt") else path("a.txt")

        store.delete(owner[len(BUCKET) + 1:])
        await connector.run_incremental_sync()

        assert survivor in db.paths()
        assert owner not in db.paths()

    @pytest.mark.asyncio
    async def test_a_record_shared_by_two_live_copies_stays_put(
        self, connector: BaseConnector, store: FakeObjectStore, db: FakeRecordsDb,
    ) -> None:
        # The copy without a record of its own must not take the record while its holder is listed,
        # or the record moves back and forth between the two keys on every sync.
        store.put("a.txt", "same bytes")
        await connector.run_sync()
        store.put("b.txt", "same bytes")
        await connector.run_incremental_sync()
        before = {r.id: r.external_record_id for r in db.records.values()}
        db.written.clear()

        for _ in range(2):
            await connector.run_incremental_sync()
        # The copy without a record is left alone; before, it took the record on every sync.
        assert path("a.txt") not in db.written
        assert {r.id: r.external_record_id for r in db.records.values()} == before
        assert db.deleted == []


class TestRenameAcrossChosenFolders:
    @pytest.mark.asyncio
    async def test_a_rename_into_a_later_folder_keeps_its_record(
        self, connector: BaseConnector, store: FakeObjectStore, db: FakeRecordsDb, config: FakeConfigService,
    ) -> None:
        config.set_folders(["legal", "reports"])
        store.put("legal/a.txt", "contract")
        store.put("reports/x.txt", "numbers")
        await connector.run_sync()
        record_id = db.by_path()[path("legal/a.txt")].id

        store.rename("legal/a.txt", "reports/b.txt")
        await connector.run_incremental_sync()

        assert db.by_path()[path("reports/b.txt")].id == record_id
        assert record_id not in db.deleted
        assert path("legal/a.txt") not in db.paths()

    @pytest.mark.asyncio
    async def test_a_failed_later_folder_stops_removal_in_the_earlier_one(
        self, connector: BaseConnector, store: FakeObjectStore, db: FakeRecordsDb, config: FakeConfigService,
    ) -> None:
        config.set_folders(["legal", "reports"])
        store.put("legal/a.txt", "contract")
        store.put("reports/x.txt", "numbers")
        await connector.run_sync()

        store.rename("legal/a.txt", "reports/b.txt")
        store.fail_prefix = "reports/"
        await sync_with_failed_listing(connector)

        assert db.deleted == []
        assert path("legal/a.txt") in db.paths()


    @pytest.mark.asyncio
    async def test_a_chosen_folder_left_empty_goes_with_its_last_file(
        self, connector: BaseConnector, store: FakeObjectStore, db: FakeRecordsDb, config: FakeConfigService,
    ) -> None:
        config.set_folders(["legal", "reports"])
        store.put("legal/a.txt", "contract")
        store.put("reports/x.txt", "numbers")
        await connector.run_sync()
        assert path("reports") in db.paths()

        store.delete("reports/x.txt")
        await connector.run_incremental_sync()

        assert db.paths() == {path("legal"), path("legal/a.txt")}


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

        await sync_with_failed_listing(connector)

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
        await sync_with_failed_listing(connector)
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

        await sync_with_failed_listing(connector)

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


class TestPageWithoutContents:
    """S3 leaves ``Contents`` out of an empty page; a truncated one is not the end of the listing."""

    @staticmethod
    def _serve_one_empty_page(connector: BaseConnector, *, token: str | None) -> None:
        real = connector.data_source.list_objects_v2
        served: list[bool] = []

        async def list_objects_v2(**kwargs: object) -> Response:
            if kwargs.get("ContinuationToken") == "2" and not served:
                served.append(True)
                data = {"IsTruncated": True, "KeyCount": 0}
                if token is not None:
                    data["NextContinuationToken"] = token
                return Response(True, data)
            return await real(**kwargs)

        connector.data_source.list_objects_v2 = list_objects_v2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["s3", "minio"])
    async def test_an_empty_truncated_page_does_not_end_the_listing(self, connector, store, db) -> None:
        for key in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt"):
            store.put(key)
        await connector.run_sync()
        store.delete("a.txt")
        doomed = db.by_path()[path("a.txt")].id

        self._serve_one_empty_page(connector, token="2")
        await connector.run_incremental_sync()

        assert db.deleted == [doomed]
        assert {path(k) for k in ("b.txt", "c.txt", "d.txt", "e.txt")} <= db.paths()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["s3", "minio"])
    async def test_a_truncated_page_without_a_token_deletes_nothing(self, connector, store, db, checkpoints) -> None:
        for key in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt"):
            store.put(key)
        await connector.run_sync()
        store.delete("a.txt")
        saved = {k: dict(v) for k, v in checkpoints.sync_points.items()}

        self._serve_one_empty_page(connector, token=None)
        await connector.run_incremental_sync()

        assert db.deleted == []
        assert {path(k) for k in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt")} <= db.paths()
        assert {k: v.get("last_sync_time") for k, v in checkpoints.sync_points.items()} == {
            k: v.get("last_sync_time") for k, v in saved.items()
        }


class TestBucketDeletedAtTheSource:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["s3", "minio"])
    async def test_a_deleted_bucket_takes_its_records_and_group_with_it(self, connector, store, db) -> None:
        store.put("a.txt")
        store.put("docs/b.txt")
        await connector.run_sync()
        assert BUCKET in db.record_groups

        del connector.data_source.stores[BUCKET]
        await connector.run_sync()
        await connector.run_sync()

        assert db.paths() == set()
        assert BUCKET not in db.record_groups

        # Created again under the same name, with an object older than the last bookmark.
        connector.data_source.stores[BUCKET] = store
        store.objects = {"a.txt": store.objects["a.txt"]}
        await connector.run_incremental_sync()

        assert db.paths() == {path("a.txt")}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["s3", "minio"])
    @pytest.mark.parametrize("sync", ["run_sync", "run_incremental_sync"])
    async def test_a_deleted_bucket_leaves_the_index_with_no_bucket_filter(self, connector, store, db, sync) -> None:
        # Unfiltered, a deleted bucket drops out of ListBuckets; only its stored group still names it.
        connector.bucket_name = None
        store.put("a.txt")
        await connector.run_sync()
        assert BUCKET in db.record_groups

        del connector.data_source.stores[BUCKET]
        await getattr(connector, sync)()
        await getattr(connector, sync)()

        assert db.paths() == set()
        assert BUCKET not in db.record_groups

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["s3", "minio"])
    async def test_one_no_such_bucket_keeps_the_records(self, connector, store, db) -> None:
        # A MinIO restarted without its volume answers NoSuchBucket once, then has the bucket again.
        store.put("a.txt")
        await connector.run_sync()

        del connector.data_source.stores[BUCKET]
        await connector.run_sync()

        assert db.paths() == {path("a.txt")}
        assert db.deleted == []
        assert BUCKET in db.record_groups

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["s3", "minio"])
    async def test_a_listing_between_two_no_such_bucket_answers_keeps_the_records(self, connector, store, db) -> None:
        store.put("a.txt")
        await connector.run_sync()

        del connector.data_source.stores[BUCKET]
        await connector.run_sync()
        connector.data_source.stores[BUCKET] = store
        await connector.run_incremental_sync()
        del connector.data_source.stores[BUCKET]
        await connector.run_sync()

        assert db.paths() == {path("a.txt")}
        assert BUCKET in db.record_groups

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["s3", "minio"])
    async def test_a_bucket_that_cannot_be_listed_keeps_its_records(self, connector, store, db) -> None:
        store.put("a.txt")
        await connector.run_sync()

        store.fail_page = 0
        await connector.run_sync()

        assert db.paths() == {path("a.txt")}
        assert BUCKET in db.record_groups

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["s3", "minio"])
    @pytest.mark.parametrize("failure", ["no_such_bucket", "listing_error"])
    async def test_a_full_sync_past_a_failed_listing_unlinks_nothing(self, connector, store, db, failure) -> None:
        # The runner strips the edges of every record a full sync did not write, unless it reports a partial read.
        connector.bucket_name = None
        connector.data_source.add_bucket("bucket-2").put("b.txt")
        store.put("a.txt")
        await run_full_sync_task(connector, runner_graph())

        if failure == "no_such_bucket":
            del connector.data_source.stores[BUCKET]
        else:
            store.fail_page = 0
        graph = runner_graph()
        await run_full_sync_task(connector, graph)

        graph.unlink_nodes_missed_by_full_sync.assert_not_awaited()
        assert db.paths() == {path("a.txt"), "bucket-2/b.txt"}

        if failure == "no_such_bucket":
            connector.data_source.stores[BUCKET] = store
        else:
            store.fail_page = None
        graph = runner_graph()
        await run_full_sync_task(connector, graph)
        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()


class TestDateFilters:
    """PP-118: narrowing a date filter removes what it now leaves out, through the delete path."""

    @pytest.mark.asyncio
    async def test_a_modified_filter_removes_what_it_now_leaves_out(self, connector, store, db, config) -> None:
        store.put("old.txt")
        cutoff = store.tick()
        store.put("new.txt")
        await connector.run_sync()
        old = db.by_path()[path("old.txt")].id

        config.set_dates("modified", after=cutoff)
        await connector.run_incremental_sync()

        assert db.deleted == [old]
        assert db.paths() == {path("new.txt")}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["gcs", "azure_blob"])
    async def test_a_created_filter_judges_the_creation_time(self, connector, store, db, config) -> None:
        store.put("old.txt")
        cutoff = store.tick()
        store.put("new.txt")
        store.put("old.txt", "old, edited after the cutoff")
        await connector.run_sync()
        old = db.by_path()[path("old.txt")].id

        config.set_dates("created", after=cutoff)
        await connector.run_incremental_sync()

        assert db.deleted == [old]
        assert db.paths() == {path("new.txt")}
