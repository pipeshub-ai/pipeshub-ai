"""PP-108: S3 and MinIO list 1,000 keys a page, use one client per run, and sync buckets side by side."""

import asyncio
import contextlib
from collections.abc import AsyncIterator

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

OTHER = "bucket-2"


@pytest.fixture(params=["s3", "minio"])
def kind(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def connector(
    kind: str, store: FakeObjectStore, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, config: FakeConfigService,
) -> BaseConnector:
    connector = make_connector(kind, store, db, checkpoints, config)
    del connector.list_page_size  # back to the connector class default from the fakes' small pages
    connector.batch_size = 100
    return connector


def _count_listings(connector: BaseConnector) -> list[dict]:
    calls: list[dict] = []
    listing = connector.data_source.list_objects_v2

    async def counted(**kwargs: object) -> object:
        calls.append(kwargs)
        return await listing(**kwargs)

    connector.data_source.list_objects_v2 = counted
    return calls


@pytest.mark.asyncio
async def test_lists_a_thousand_keys_a_page_and_saves_in_batches_of_a_hundred(connector, store, db) -> None:
    for i in range(2500):
        store.put(f"k{i:05d}.txt")
    calls = _count_listings(connector)
    batches: list[int] = []
    save = db.on_new_records

    async def counted_save(records: list) -> None:
        batches.append(len(records))
        await save(records)

    db.on_new_records = counted_save

    await connector.run_sync()

    assert [c["MaxKeys"] for c in calls] == [1000, 1000, 1000]
    assert max(batches) <= 100
    assert len(db.paths()) >= 2500


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", ["run_sync", "run_incremental_sync"])
async def test_one_client_per_run(connector, store, sync) -> None:
    store.put("a.txt")
    opened: list[int] = []

    @contextlib.asynccontextmanager
    async def shared_client() -> AsyncIterator[None]:
        opened.append(1)
        yield

    connector.data_source.shared_client = shared_client
    await getattr(connector, sync)()
    assert opened == [1]


@pytest.mark.asyncio
async def test_buckets_sync_side_by_side_without_sharing_moves(connector, store, db, config) -> None:
    """Bucket B starts while A waits on its second page, and B's start does not forget a move A recorded."""
    other = connector.data_source.add_bucket(OTHER)
    other.put("b.txt")
    connector.bucket_name = None
    config.set_selection("buckets", [BUCKET, OTHER])
    store.put("old.txt", "moved bytes")
    await connector.run_sync()
    old_id = db.by_path()[f"{BUCKET}/old.txt"].id

    # Two new copies of a deleted object: only one may take its record.
    store.rename("old.txt", "m1.txt")
    store.put("m2.txt", "moved bytes")
    other.put("b2.txt")
    connector.list_page_size = 1
    b_started = asyncio.Event()
    b_started_before_a_page_2: list[bool] = []
    listing = connector.data_source.list_objects_v2

    async def gated(**kwargs: object) -> object:
        if kwargs["Bucket"] == OTHER:
            b_started.set()
        elif kwargs.get("ContinuationToken"):
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(b_started.wait(), 2)
            b_started_before_a_page_2.append(b_started.is_set())
        return await listing(**kwargs)

    connector.data_source.list_objects_v2 = gated
    await connector.run_sync()

    assert b_started_before_a_page_2 and all(b_started_before_a_page_2)
    records = db.by_path()
    assert {f"{BUCKET}/m1.txt", f"{BUCKET}/m2.txt", f"{OTHER}/b2.txt"} <= set(records)
    assert records[f"{BUCKET}/m1.txt"].id != records[f"{BUCKET}/m2.txt"].id
    assert old_id in {records[f"{BUCKET}/m1.txt"].id, records[f"{BUCKET}/m2.txt"].id}
