"""Objects with equal content at different keys in the object-store connectors.

The connectors spot a move by finding a stored record with the new key's
content fingerprint (S3 ETag, GCS MD5, Azure Blob content MD5). Equal content
alone is not a move: a copy leaves the original in place, and each key needs
its own record.
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
