"""GCS: a full sync that could not read part of the source unlinks nothing it did not write.

After a full sync the runner strips every edge of each node the sync did not write,
unless the connector reports the read incomplete. A record still in GCS that the
sync skipped after an error would lose its edges and become unreadable.
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
)

from app.connectors.core.base.connector.connector_service import BaseConnector

OTHER = "bucket-2"


@pytest.fixture
def connector(
    store: FakeObjectStore, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, config: FakeConfigService,
) -> BaseConnector:
    connector = make_connector("gcs", store, db, checkpoints, config)
    connector.bucket_name = None
    connector.data_source.add_bucket(OTHER)
    return connector


async def full_sync(connector: BaseConnector, db: FakeRecordsDb) -> set[str]:
    """Run a full sync through the runner; the ids of the stored nodes its unlink step strips."""
    # The event service deletes the connector's sync points before it starts a full sync.
    connector.data_store_provider.sync_points.clear()
    graph = runner_graph()
    await run_full_sync_task(connector, graph)
    if not graph.unlink_nodes_missed_by_full_sync.await_count:
        return set()
    seen = graph.unlink_nodes_missed_by_full_sync.await_args.args[1]
    stored = {("records", r.id) for r in db.records.values()}
    stored |= {("recordGroups", g.id) for g in db.record_groups.values()}
    return {node_id for collection, node_id in stored if node_id not in seen.get(collection, set())}


def ids(db: FakeRecordsDb, *paths: str) -> set[str]:
    by_path = db.by_path()
    return {by_path[p].id for p in paths}


class TestFullSyncCoverage:
    @pytest.mark.asyncio
    async def test_a_bucket_that_raises_keeps_its_records_linked(self, connector, store, db) -> None:
        store.put("a.txt")
        store.put("b.txt")
        connector.data_source.stores[OTHER].put("c.txt")
        await full_sync(connector, db)
        still_there = ids(db, f"{BUCKET}/a.txt", f"{BUCKET}/b.txt")
        read = db.get_records_in_record_group

        async def fail_for_bucket(connector_id: str, external_group_id: str, *args: object, **kwargs: object) -> list:
            if external_group_id == BUCKET:
                raise RuntimeError("database unavailable (get_records_in_record_group)")
            return await read(connector_id, external_group_id, *args, **kwargs)

        db.get_records_in_record_group = fail_for_bucket
        unlinked = await full_sync(connector, db)

        assert len(unlinked & still_there) == 0
        assert f"{OTHER}/c.txt" in db.paths()

    @pytest.mark.asyncio
    async def test_a_failed_listing_keeps_its_records_linked(self, connector, store, db) -> None:
        store.put("a.txt")
        store.put("b.txt")
        await full_sync(connector, db)
        still_there = ids(db, f"{BUCKET}/a.txt", f"{BUCKET}/b.txt")

        store.fail_page = 0
        unlinked = await full_sync(connector, db)

        assert len(unlinked & still_there) == 0

    @pytest.mark.asyncio
    async def test_a_listing_that_raises_part_way_keeps_the_unread_records_linked(self, connector, store, db) -> None:
        for key in ("a.txt", "b.txt", "c.txt", "d.txt"):
            store.put(key)
        await full_sync(connector, db)
        still_there = ids(db, *(f"{BUCKET}/{k}" for k in ("a.txt", "b.txt", "c.txt", "d.txt")))

        store.fail_page = 1
        store.fail_mode = "raise"
        unlinked = await full_sync(connector, db)

        assert len(unlinked & still_there) == 0

    @pytest.mark.asyncio
    async def test_an_object_that_fails_keeps_its_record_linked(self, connector, store, db) -> None:
        store.put("a.txt")
        store.put("b.txt")
        await full_sync(connector, db)
        still_there = ids(db, f"{BUCKET}/a.txt", f"{BUCKET}/b.txt")
        lookup = db.get_record_by_external_id

        async def fail_for_b(connector_id: str, external_record_id: str) -> object:
            if external_record_id == f"{BUCKET}/b.txt":
                raise RuntimeError("database unavailable (get_record_by_external_id)")
            return await lookup(connector_id, external_record_id)

        db.get_record_by_external_id = fail_for_b
        unlinked = await full_sync(connector, db)

        assert len(unlinked & still_there) == 0

    @pytest.mark.asyncio
    async def test_a_bucket_group_that_fails_to_save_stays_linked(self, connector, store, db) -> None:
        connector.data_source.stores[OTHER].put("c.txt")
        await full_sync(connector, db)
        still_there = {db.record_groups[BUCKET].id}
        save = db.on_new_record_groups

        async def fail_for_bucket(groups: list) -> None:
            if any(group.external_group_id == BUCKET for group, _ in groups):
                raise RuntimeError("database unavailable (on_new_record_groups)")
            await save(groups)

        db.on_new_record_groups = fail_for_bucket
        unlinked = await full_sync(connector, db)

        assert len(unlinked & still_there) == 0

    @pytest.mark.asyncio
    async def test_a_deleted_bucket_is_unlinked_past_a_new_object_that_fails(self, connector, store, db) -> None:
        store.put("a.txt")
        connector.data_source.stores[OTHER].put("c.txt")
        await full_sync(connector, db)
        gone = ids(db, f"{OTHER}/c.txt") | {db.record_groups[OTHER].id}
        lookup = db.get_record_by_external_id

        async def fail_for_new(connector_id: str, external_record_id: str) -> object:
            if external_record_id == f"{BUCKET}/new.txt":
                raise RuntimeError("database unavailable (get_record_by_external_id)")
            return await lookup(connector_id, external_record_id)

        db.get_record_by_external_id = fail_for_new
        store.put("new.txt")
        del connector.data_source.stores[OTHER]
        unlinked = await full_sync(connector, db)

        assert unlinked & gone == gone
        assert len(unlinked - gone) == 0

    @pytest.mark.asyncio
    async def test_a_clean_full_sync_after_a_failed_one_unlinks_what_is_gone(self, connector, store, db) -> None:
        store.put("a.txt")
        store.put("b.txt")
        await full_sync(connector, db)
        kept = ids(db, f"{BUCKET}/a.txt")

        store.fail_page = 0
        await full_sync(connector, db)
        assert connector.full_sync_coverage().complete is False

        store.fail_page = None
        store.delete("b.txt")
        connector.data_store_provider.sync_points.clear()
        graph = runner_graph()
        await run_full_sync_task(connector, graph)

        assert connector.full_sync_coverage().complete is True
        assert graph.unlink_nodes_missed_by_full_sync.await_count == 1
        seen = graph.unlink_nodes_missed_by_full_sync.await_args.args[1]
        assert kept <= seen.get("records", set())
        assert f"{BUCKET}/b.txt" not in db.paths()

    @pytest.mark.asyncio
    async def test_a_failed_bucket_list_unlinks_nothing(self, connector, store, db) -> None:
        store.put("a.txt")
        await full_sync(connector, db)

        async def fail_to_list() -> Response:
            return Response(False, error="GCS API error: 503 Service Unavailable")

        connector.data_source.list_buckets = fail_to_list
        unlinked = await full_sync(connector, db)

        assert unlinked == set()
        assert connector.full_sync_coverage().complete is False

    @pytest.mark.asyncio
    async def test_a_batch_that_fails_to_save_keeps_its_records_linked(self, connector, store, db) -> None:
        for key in ("a.txt", "b.txt", "c.txt"):
            store.put(key)
        await full_sync(connector, db)
        still_there = ids(db, *(f"{BUCKET}/{k}" for k in ("a.txt", "b.txt", "c.txt")))
        save = db.on_new_records

        async def fail_for_a(records: list) -> None:
            if any(record.external_record_id == f"{BUCKET}/a.txt" for record, _ in records):
                raise RuntimeError("database unavailable (on_new_records)")
            await save(records)

        db.on_new_records = fail_for_a
        unlinked = await full_sync(connector, db)

        assert len(unlinked & still_there) == 0

    @pytest.mark.asyncio
    async def test_a_new_object_whose_folder_fails_keeps_the_stored_folder_linked(self, connector, store, db) -> None:
        store.put("x/old.txt")
        await full_sync(connector, db)
        folder = ids(db, f"{BUCKET}/x")
        save = db.on_new_records

        async def fail_for_folder(records: list) -> None:
            if any(record.external_record_id == f"{BUCKET}/x" for record, _ in records):
                raise RuntimeError("database unavailable (on_new_records)")
            await save(records)

        store.delete("x/old.txt")
        store.put("x/new.txt")
        db.on_new_records = fail_for_folder
        unlinked = await full_sync(connector, db)

        assert len(unlinked & folder) == 0
        assert connector.full_sync_coverage().complete is False

    @pytest.mark.asyncio
    async def test_a_never_stored_object_that_fails_leaves_the_sync_complete(self, connector, store, db) -> None:
        store.put("a.txt")
        store.put("b.txt")
        await full_sync(connector, db)
        still_there = ids(db, f"{BUCKET}/b.txt")
        lookup = db.get_record_by_external_id

        async def fail_for_new(connector_id: str, external_record_id: str) -> object:
            if external_record_id == f"{BUCKET}/new.txt":
                raise RuntimeError("database unavailable (get_record_by_external_id)")
            return await lookup(connector_id, external_record_id)

        db.get_record_by_external_id = fail_for_new
        store.delete("a.txt")
        store.put("new.txt")
        connector.data_store_provider.sync_points.clear()
        graph = runner_graph()
        await run_full_sync_task(connector, graph)

        assert connector.full_sync_coverage().complete is True
        assert graph.unlink_nodes_missed_by_full_sync.await_count == 1
        assert f"{BUCKET}/a.txt" not in db.paths()
        assert still_there <= graph.unlink_nodes_missed_by_full_sync.await_args.args[1].get("records", set())
