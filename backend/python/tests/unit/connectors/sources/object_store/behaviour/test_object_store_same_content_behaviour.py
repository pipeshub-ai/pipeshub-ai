"""Objects with equal content at different keys in the object-store connectors.

The connectors spot a move by finding a stored record with the new key's
content fingerprint (S3 ETag, GCS MD5, Azure Blob content MD5). Equal content
alone is not a move: a copy leaves the original in place, and each key needs
its own record.
"""

from collections.abc import Awaitable, Callable

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
SAME = "same bytes"


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


def copy(store: FakeObjectStore, key: str, new_key: str) -> None:
    store.put(new_key, store.objects[key].body.decode())


class TestSameContentAtTwoKeys:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("second_sync", ["run_incremental_sync", "run_sync"])
    async def test_a_copy_gets_its_own_record(self, connector, store, db, second_sync) -> None:
        store.put("a.txt", SAME)
        await connector.run_sync()
        original = db.by_path()[path("a.txt")].id

        copy(store, "a.txt", "b.txt")
        await getattr(connector, second_sync)()

        records = db.by_path()
        assert records[path("a.txt")].id == original
        assert records[path("b.txt")].id != original

    @pytest.mark.asyncio
    async def test_the_two_records_stay_put_over_later_syncs(self, connector, store, db) -> None:
        store.put("a.txt", SAME)
        await connector.run_sync()
        copy(store, "a.txt", "b.txt")
        await connector.run_incremental_sync()
        ids = {p: r.id for p, r in db.by_path().items()}
        assert ids[path("a.txt")] != ids[path("b.txt")]

        await connector.run_sync()
        await connector.run_sync()

        assert {p: r.id for p, r in db.by_path().items()} == ids
        assert db.deleted == []


class TestMove:
    @pytest.mark.asyncio
    async def test_a_move_keeps_the_record(self, connector, store, db) -> None:
        store.put("a.txt", SAME)
        store.put("other.txt")
        await connector.run_sync()
        original = db.by_path()[path("a.txt")].id

        store.rename("a.txt", "z.txt")
        await connector.run_incremental_sync()

        records = db.by_path()
        assert records[path("z.txt")].id == original
        assert path("a.txt") not in records

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["s3", "minio", "gcs"])
    async def test_a_move_is_saved_as_a_move(self, connector, store, db) -> None:
        """Saved as a new record at the new key, the stored content stays at the old path.
        Azure Blob still saves a move as a new record."""
        store.put("a.txt", SAME)
        await connector.run_sync()

        store.rename("a.txt", "z.txt")
        await connector.run_incremental_sync()

        assert db.moved == [(path("a.txt"), path("z.txt"))]


class TestCopyThenDeleteTheOriginal:
    @pytest.mark.asyncio
    async def test_between_two_syncs_it_is_a_move(self, connector, store, db) -> None:
        store.put("a.txt", SAME)
        await connector.run_sync()
        original = db.by_path()[path("a.txt")].id

        copy(store, "a.txt", "b.txt")
        store.delete("a.txt")
        await connector.run_incremental_sync()

        records = db.by_path()
        assert records[path("b.txt")].id == original
        assert path("a.txt") not in records

    @pytest.mark.asyncio
    async def test_after_the_copy_synced_the_copy_keeps_its_record(self, connector, store, db) -> None:
        store.put("a.txt", SAME)
        await connector.run_sync()
        original = db.by_path()[path("a.txt")].id
        copy(store, "a.txt", "b.txt")
        await connector.run_incremental_sync()
        the_copy = db.by_path()[path("b.txt")].id
        assert the_copy != original

        store.delete("a.txt")
        await connector.run_sync()

        assert db.by_path()[path("b.txt")].id == the_copy

    @pytest.mark.asyncio
    async def test_two_copies_do_not_share_the_original_record(self, connector, store, db) -> None:
        # Both copies land in one unsaved batch, where the stored record still names the deleted key.
        store.put("a.txt", SAME)
        await connector.run_sync()
        original = db.by_path()[path("a.txt")].id

        copy(store, "a.txt", "b.txt")
        copy(store, "a.txt", "c.txt")
        store.delete("a.txt")
        await connector.run_incremental_sync()

        records = db.by_path()
        assert path("a.txt") not in records
        ids = [records[path("b.txt")].id, records[path("c.txt")].id]
        assert len(set(ids)) == 2
        assert original in ids


class TestExistenceCheckOfTheOriginal:
    """S3 and MinIO ask the store whether the original key still exists, with ``head_object``."""

    @pytest.fixture(params=["s3", "minio"])
    def connector(self, request, store, db, checkpoints, config) -> BaseConnector:
        return make_connector(request.param, store, db, checkpoints, config)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["error", "raise"])
    async def test_a_failed_check_counts_as_still_there(self, connector, store, db, failure) -> None:
        store.put("a.txt", SAME)
        await connector.run_sync()
        original = db.by_path()[path("a.txt")].id

        copy(store, "a.txt", "b.txt")
        connector.data_source.head_fails = failure
        await connector.run_incremental_sync()

        records = db.by_path()
        assert records[path("a.txt")].id == original
        assert records[path("b.txt")].id != original

    @pytest.mark.asyncio
    async def test_the_check_reads_the_one_key(self, connector, store, db) -> None:
        # A prefix listing of "a.txt" is not ordered on directory buckets and also matches "a.txt.bak".
        store.put("a.txt", SAME)
        await connector.run_sync()
        original = db.by_path()[path("a.txt")].id
        connector.data_source.list_objects_v2 = _listing_without(connector.data_source, "a.txt")

        copy(store, "a.txt", "b.txt")
        await connector.run_incremental_sync()

        assert db.by_path()[path("b.txt")].id != original


def _listing_without(source, hidden_from_prefix_listing: str) -> Callable[..., Awaitable[object]]:
    """The sync listing as is; a prefix listing of exactly ``hidden_from_prefix_listing`` misses it."""
    real = source.list_objects_v2

    async def list_objects_v2(**kwargs) -> object:
        response = await real(**kwargs)
        if kwargs.get("Prefix") == hidden_from_prefix_listing and response.data:
            response.data["Contents"] = [o for o in response.data.get("Contents", []) if o["Key"] != hidden_from_prefix_listing]
        return response

    return list_objects_v2


class TestAMoveIsSavedInPlace:
    """S3 and MinIO hand a move to ``on_records_moved``, which keeps the indexed content; a new record is indexed again."""

    @pytest.fixture(params=["s3", "minio"])
    def connector(self, request, store, db, checkpoints, config) -> BaseConnector:
        return make_connector(request.param, store, db, checkpoints, config)

    @pytest.mark.asyncio
    async def test_a_renamed_folder_moves_each_object(self, connector, store, db) -> None:
        store.put("a/x.txt")
        store.put("a/y.txt")
        await connector.run_sync()
        ids = {k: db.by_path()[path(f"a/{k}")].id for k in ("x.txt", "y.txt")}
        db.written.clear()

        store.rename("a/x.txt", "b/x.txt")
        store.rename("a/y.txt", "b/y.txt")
        await connector.run_incremental_sync()

        assert sorted(db.moved) == [(path("a/x.txt"), path("b/x.txt")), (path("a/y.txt"), path("b/y.txt"))]
        assert path("b/x.txt") not in db.written and path("b/y.txt") not in db.written
        assert {k: db.by_path()[path(f"b/{k}")].id for k in ("x.txt", "y.txt")} == ids

    @pytest.mark.asyncio
    async def test_a_copy_that_takes_a_gone_original_is_a_move(self, connector, store, db) -> None:
        store.put("a.txt", SAME)
        store.put("b.txt", SAME)
        await connector.run_sync()
        original = db.by_path()[path("a.txt")].id
        # As an older sync left it: the copy without a record of its own.
        del db.records[db.by_path()[path("b.txt")].id]
        store.delete("a.txt")

        await connector.run_incremental_sync()

        assert db.moved == [(path("a.txt"), path("b.txt"))]
        assert db.by_path()[path("b.txt")].id == original
