"""Buckets and containers taken out of the Bucket Names / Container Names filter in the object-store connectors.

Their records are removed at the start of the next sync. The decision comes from
the saved filter alone, so a failed read of it, or a bucket list that happens to
leave a bucket out, removes nothing.
"""

import logging

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
OTHER = "bucket-2"


def filter_name(kind: str) -> str:
    return "containers" if kind == "azure_blob" else "buckets"


def in_bucket(db: FakeRecordsDb, bucket: str) -> set[str]:
    return {p for p in db.paths() if p.startswith(f"{bucket}/")}


@pytest.fixture(params=ALL)
def kind(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def other(store: FakeObjectStore) -> FakeObjectStore:
    return FakeObjectStore()


@pytest.fixture
def connector(
    kind: str, store: FakeObjectStore, other: FakeObjectStore, db: FakeRecordsDb,
    checkpoints: FakeCheckpointStore, config: FakeConfigService,
) -> BaseConnector:
    """Two buckets, chosen through the filter rather than a bucket fixed in the connector's settings."""
    connector = make_connector(kind, store, db, checkpoints, config)
    if kind == "azure_blob":
        connector.container_name = None
    else:
        connector.bucket_name = None
    connector.data_source.stores[OTHER] = other
    return connector


@pytest.fixture
async def both_synced(
    kind: str, connector: BaseConnector, store: FakeObjectStore, other: FakeObjectStore,
    db: FakeRecordsDb, config: FakeConfigService,
) -> None:
    for key in ("a.txt", "docs/b.txt"):
        store.put(key)
        other.put(key, f"other {key}")
    config.set_selection(filter_name(kind), [BUCKET, OTHER])
    await connector.run_sync()
    assert in_bucket(db, BUCKET) == {f"{BUCKET}/a.txt", f"{BUCKET}/docs/b.txt", f"{BUCKET}/docs"}
    assert in_bucket(db, OTHER) == {f"{OTHER}/a.txt", f"{OTHER}/docs/b.txt", f"{OTHER}/docs"}


@pytest.mark.usefixtures("both_synced")
class TestDeselectedBucket:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("next_sync", ["run_sync", "run_incremental_sync"])
    async def test_its_records_leave_and_the_others_stay(self, kind, connector, db, config, next_sync) -> None:
        kept = in_bucket(db, BUCKET)
        doomed = {r.id for r in db.records.values() if r.external_record_id.startswith(f"{OTHER}/")}

        config.set_selection(filter_name(kind), [BUCKET])
        await getattr(connector, next_sync)()

        assert in_bucket(db, OTHER) == set()
        assert set(db.deleted) == doomed
        assert OTHER not in db.record_groups
        assert in_bucket(db, BUCKET) == kept
        assert BUCKET in db.record_groups

    @pytest.mark.asyncio
    async def test_a_trashed_record_in_it_is_removed_too(self, kind, connector, db, config) -> None:
        trashed = next(r for r in db.records.values() if r.external_record_id == f"{OTHER}/a.txt")
        trashed.is_deleted = True

        config.set_selection(filter_name(kind), [BUCKET])
        await connector.run_sync()

        assert trashed.id in db.deleted
        assert in_bucket(db, OTHER) == set()
        assert OTHER not in db.record_groups

    @pytest.mark.asyncio
    async def test_a_group_that_cannot_be_removed_is_reported_and_retried(
        self, kind, connector, db, config, caplog
    ) -> None:
        db.refused_group_deletes.add(OTHER)
        config.set_selection(filter_name(kind), [BUCKET])

        with caplog.at_level(logging.WARNING):
            await connector.run_sync()

        assert OTHER in db.record_groups
        assert any(f"record group of de-selected {OTHER}" in r.getMessage() for r in caplog.records)

        db.refused_group_deletes.clear()
        await connector.run_sync()
        assert OTHER not in db.record_groups

    @pytest.mark.asyncio
    async def test_selecting_it_again_syncs_it_again(self, kind, connector, db, config) -> None:
        config.set_selection(filter_name(kind), [BUCKET])
        await connector.run_sync()
        assert in_bucket(db, OTHER) == set()

        # The sync points of the earlier syncs are kept: nothing in the bucket changed since.
        config.set_selection(filter_name(kind), [BUCKET, OTHER])
        await connector.run_sync()

        assert in_bucket(db, OTHER) == {f"{OTHER}/a.txt", f"{OTHER}/docs/b.txt", f"{OTHER}/docs"}
        assert OTHER in db.record_groups

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("fail_mode", "first_failing_read"),
        [("raise", 0), ("empty", 0), ("raise", 1), ("empty", 1)],
        ids=["every-read-raises", "every-read-empty", "second-read-raises", "second-read-empty"],
    )
    async def test_a_failed_read_of_the_filter_removes_nothing(
        self, kind, connector, db, config, fail_mode, first_failing_read,
    ) -> None:
        # The sync reads the config once for its filters, then again before removing anything.
        config.set_selection(filter_name(kind), [BUCKET])
        config.fail_mode, config.fail_from = fail_mode, config.reads + first_failing_read

        await connector.run_sync()

        assert db.deleted == []
        assert in_bucket(db, OTHER) == {f"{OTHER}/a.txt", f"{OTHER}/docs/b.txt", f"{OTHER}/docs"}
        assert OTHER in db.record_groups

    @pytest.mark.asyncio
    async def test_no_filter_means_every_bucket_even_one_the_bucket_list_leaves_out(
        self, kind, connector, db, config,
    ) -> None:
        del config.sync_filters[filter_name(kind)]
        connector.data_source.hidden.add(OTHER)

        await connector.run_sync()

        assert db.deleted == []
        assert in_bucket(db, OTHER) == {f"{OTHER}/a.txt", f"{OTHER}/docs/b.txt", f"{OTHER}/docs"}

    @pytest.mark.asyncio
    async def test_a_not_in_filter_removes_nothing(self, kind, connector, db, config) -> None:
        # The sync reads the list as the buckets to sync whatever the operator, so Not in is not trusted to remove.
        config.set_selection(filter_name(kind), [OTHER], operator="not_in")

        await connector.run_sync()

        assert db.deleted == []
        assert OTHER in db.record_groups

    @pytest.mark.asyncio
    async def test_a_bucket_fixed_in_the_settings_ignores_the_filter(self, kind, connector, db, config) -> None:
        if kind == "azure_blob":
            connector.container_name = BUCKET
        else:
            connector.bucket_name = BUCKET
        config.set_selection(filter_name(kind), [OTHER])

        await connector.run_sync()

        assert db.deleted == []
        assert in_bucket(db, BUCKET) and in_bucket(db, OTHER)
