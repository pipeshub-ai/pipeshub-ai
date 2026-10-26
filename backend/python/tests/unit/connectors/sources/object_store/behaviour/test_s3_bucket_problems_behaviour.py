"""S3 and MinIO: buckets the key cannot read, buckets that fail, versions, reindex of a gone object, console links.

A key that can list buckets it may not read is common on AWS (one IAM user, many
buckets). Such a bucket is skipped with one notification for the run, gets no
empty group in the browse tree, and does not stop the full sync's cleanup.
"""

from unittest.mock import AsyncMock

import pytest
from object_store_behaviour_fakes import (
    BUCKET,
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
    Response,
    make_connector,
)

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.services.notification.types import NotificationSeverity

DENIED = ["logs-1", "logs-2"]


@pytest.fixture(params=["s3", "minio"])
def kind(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def connector(
    kind: str, store: FakeObjectStore, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, config: FakeConfigService,
) -> BaseConnector:
    """No bucket chosen: the sync takes every bucket the key can list; two of them it may not read."""
    connector = make_connector(kind, store, db, checkpoints, config)
    connector.bucket_name = None
    for name in DENIED:
        connector.data_source.add_bucket(name)
        connector.data_source.denied.add(name)
    connector.notify = AsyncMock()
    return connector


def notices(connector: BaseConnector) -> list[dict]:
    return [c.kwargs for c in connector.notify.await_args_list]


class TestUnreadableBuckets:
    @pytest.mark.asyncio
    async def test_one_warning_names_every_skipped_bucket(self, connector, store, db) -> None:
        store.put("a.txt")

        await connector.run_sync()

        sent = notices(connector)
        assert len(sent) == 1, sent
        assert sent[0]["severity"] == NotificationSeverity.WARNING
        assert all(name in sent[0]["message"] for name in DENIED)
        assert "Bucket Names filter" in sent[0]["message"]
        assert f"{BUCKET}/a.txt" in db.paths()

    @pytest.mark.asyncio
    async def test_a_bucket_it_cannot_read_gets_no_group(self, connector, store, db) -> None:
        store.put("a.txt")

        await connector.run_sync()

        assert set(db.record_groups) == {BUCKET}

    @pytest.mark.asyncio
    async def test_an_empty_readable_bucket_still_gets_its_group(self, connector, db) -> None:
        await connector.run_sync()

        assert set(db.record_groups) == {BUCKET}

    @pytest.mark.asyncio
    async def test_buckets_never_read_do_not_stop_the_full_sync_cleanup(self, connector, store) -> None:
        store.put("a.txt")

        await connector.run_sync()

        assert connector.full_sync_coverage().complete

    @pytest.mark.asyncio
    async def test_a_bucket_that_loses_access_keeps_its_records_and_the_cleanup_waits(self, connector, store, db) -> None:
        store.put("a.txt")
        await connector.run_sync()
        connector.data_source.denied.add(BUCKET)

        await connector.run_sync()

        assert f"{BUCKET}/a.txt" in db.paths()
        assert db.deleted == []
        assert not connector.full_sync_coverage().complete

    @pytest.mark.asyncio
    async def test_a_chosen_bucket_it_cannot_read_is_an_error(self, connector, config) -> None:
        config.set_selection("buckets", [DENIED[0]])

        await connector.run_sync()

        sent = notices(connector)
        assert len(sent) == 1, sent
        assert sent[0]["severity"] == NotificationSeverity.ERROR
        assert "Bucket Names filter" not in sent[0]["message"]

    @pytest.mark.asyncio
    async def test_an_incremental_run_also_sends_one_notice_and_writes_no_group(self, connector, store, db) -> None:
        store.put("a.txt")

        await connector.run_incremental_sync()

        assert len(notices(connector)) == 1
        assert db.record_groups == {}


class TestFailedBuckets:
    @pytest.mark.asyncio
    async def test_a_source_listing_failure_is_reported_once_and_the_run_goes_on(self, connector, store, db) -> None:
        connector.data_source.denied.clear()
        store.put("a.txt")
        broken = connector.data_source.stores[DENIED[0]]
        broken.fail_page = 0

        await connector.run_sync()

        sent = notices(connector)
        assert [n["title"].split(": ", 1)[1] for n in sent] == ["Some buckets could not be listed"]
        assert DENIED[0] in sent[0]["message"] and "service unavailable" in sent[0]["message"]
        assert f"{BUCKET}/a.txt" in db.paths()

    @pytest.mark.asyncio
    async def test_a_bucket_that_fails_here_is_reported_and_fails_the_run_after_the_others(
        self, connector, store, db,
    ) -> None:
        connector.data_source.denied.clear()
        connector.data_source.stores[DENIED[0]].put("b.txt")
        store.put("a.txt")
        sync_bucket = connector._sync_bucket

        async def save_fails_in_one_bucket(name: str) -> None:
            if name == DENIED[0]:
                raise RuntimeError("graph unavailable")
            await sync_bucket(name)

        connector._sync_bucket = save_fails_in_one_bucket

        with pytest.raises(RuntimeError, match="sync failed"):
            await connector.run_sync()

        sent = notices(connector)
        assert [n["title"].split(": ", 1)[1] for n in sent] == ["Some buckets failed to sync"]
        assert f"{BUCKET}/a.txt" in db.paths()
        assert not connector.full_sync_coverage().complete

    @pytest.mark.asyncio
    async def test_an_empty_bucket_whose_listing_fails_keeps_the_cleanup_waiting(self, connector, db) -> None:
        """Its stored group is not written this run, so a complete run would unlink the group's edges."""
        connector.data_source.denied.clear()
        await connector.run_sync()
        assert DENIED[0] in db.record_groups
        connector.data_source.stores[DENIED[0]].fail_page = 0

        await connector.run_sync()

        assert not connector.full_sync_coverage().complete
        assert DENIED[0] in db.record_groups

    @pytest.mark.asyncio
    async def test_an_unsure_group_check_keeps_the_cleanup_waiting(self, connector, db) -> None:
        connector.data_source.denied.clear()
        await connector.run_sync()
        connector.data_source.stores[DENIED[0]].fail_page = 0
        db.failing.add("get_nodes_by_filters")

        await connector.run_sync()

        assert not connector.full_sync_coverage().complete

    @pytest.mark.asyncio
    async def test_a_failed_group_write_fails_the_run_and_is_not_called_a_listing_failure(
        self, connector, db,
    ) -> None:
        """Our own write failing while a bucket is listed: the bucket fails, not "could not be listed"."""
        connector.data_source.denied.clear()
        await connector.run_sync()
        assert BUCKET in db.record_groups
        db.failing.add("on_new_record_groups")
        connector.notify.reset_mock()

        with pytest.raises(RuntimeError, match="sync failed"):
            await connector.run_sync()

        assert [n["title"].split(": ", 1)[1] for n in notices(connector)] == ["Some buckets failed to sync"]
        assert not connector.full_sync_coverage().complete
        assert BUCKET in db.record_groups


class TestVersion:
    @pytest.mark.asyncio
    async def test_records_are_sent_at_version_zero_for_the_processor_to_number(
        self, connector, store, db, checkpoints,
    ) -> None:
        """The processor bumps a stored version only when the revision changed; S3 used to add one on every resend."""
        connector.data_source.denied.clear()
        store.put("a.txt")
        await connector.run_sync()
        # Without its checkpoint the next sync resends every unchanged object.
        checkpoints.sync_points.clear()
        db.written.clear()

        await connector.run_sync()
        store.put("a.txt", "new content")
        await connector.run_sync()

        assert db.written
        assert {r.version for r in db.records.values()} == {0}


class TestReindex:
    @pytest.fixture
    def stored(self, connector, store, db) -> BaseConnector:
        connector.data_source.denied.clear()
        store.put("gone.txt")
        store.put("kept.txt")
        db.reindex_existing_records = AsyncMock()
        return connector

    @pytest.mark.asyncio
    async def test_an_object_gone_from_the_bucket_is_not_reindexed(self, stored, store, db) -> None:
        await stored.run_sync()
        records = list(db.records.values())
        store.delete("gone.txt")
        written_before = list(db.written)

        await stored.reindex_records(records)

        queued = db.reindex_existing_records.await_args.args[0]
        assert {r.external_record_id for r in queued} == {f"{BUCKET}/kept.txt"}
        assert db.written == written_before

    @pytest.mark.asyncio
    async def test_a_failed_check_still_reindexes_and_logs_the_reason(self, stored, db, caplog) -> None:
        await stored.run_sync()
        records = list(db.records.values())
        stored.data_source.head_fails = "error"

        await stored.reindex_records(records)

        assert len(db.reindex_existing_records.await_args.args[0]) == len(records)
        assert "503" in caplog.text and "Service Unavailable" in caplog.text


@pytest.mark.asyncio
async def test_s3_console_links_encode_the_key() -> None:
    connector = make_connector("s3", FakeObjectStore(), FakeRecordsDb(), FakeCheckpointStore(), FakeConfigService())
    key = "Q1 reports/R&D #2+final.pdf"

    url = connector._generate_web_url(BUCKET, key)
    parent = connector._generate_parent_web_url(f"{BUCKET}/Q1 reports")

    assert url.endswith("?prefix=Q1%20reports/R%26D%20%232%2Bfinal.pdf")
    assert parent.endswith("?prefix=Q1%20reports/")


@pytest.mark.asyncio
async def test_a_failed_access_check_raises_with_the_reason() -> None:
    from app.connectors.core.base.connector.connector_service import ConnectorInitError

    connector = make_connector("s3", FakeObjectStore(), FakeRecordsDb(), FakeCheckpointStore(), FakeConfigService())
    connector.notify = AsyncMock()
    connector.data_source.list_objects_v2 = AsyncMock(side_effect=ConnectionError("connection reset"))

    with pytest.raises(ConnectorInitError, match="connection reset"):
        await connector.test_connection_and_access()


@pytest.mark.asyncio
async def test_listing_logs_carry_the_status_and_the_vendor_reason(caplog) -> None:
    store = FakeObjectStore()
    connector = make_connector("s3", store, FakeRecordsDb(), FakeCheckpointStore(), FakeConfigService())
    connector.notify = AsyncMock()
    connector.data_source.list_objects_v2 = AsyncMock(
        return_value=Response(False, error="SlowDown: Please reduce your request rate.", status_code=503)
    )

    await connector.run_sync()

    assert "status 503" in caplog.text and "Please reduce your request rate" in caplog.text


class TestStaleListingPosition:
    @pytest.mark.asyncio
    async def test_a_refused_saved_token_relists_the_bucket_and_says_so(self, connector, store, db, checkpoints) -> None:
        """AWS answers 400 InvalidArgument for a continuation token it no longer takes; that must not fail every run."""
        connector.data_source.denied.clear()
        for key in ("a.txt", "b.txt", "c.txt"):
            store.put(key)
        await connector.run_sync()
        store.delete("a.txt")
        for key, point in checkpoints.sync_points.items():
            if key.endswith(f"/{BUCKET}"):
                point["continuation_token"] = "stale"
        listing = connector.data_source.list_objects_v2

        async def refuses_stale(**kwargs: object) -> Response:
            if kwargs.get("ContinuationToken") == "stale":
                return Response(False, error="InvalidArgument: The continuation token provided is incorrect", status_code=400)
            return await listing(**kwargs)

        connector.data_source.list_objects_v2 = refuses_stale
        connector.notify.reset_mock()

        await connector.run_sync()

        assert db.paths() >= {f"{BUCKET}/b.txt", f"{BUCKET}/c.txt"}
        # Listed in full again, so the removal pass ran.
        assert f"{BUCKET}/a.txt" not in db.paths()
        assert all(p.get("continuation_token") is None for p in checkpoints.sync_points.values())
        assert [n["title"].split(": ", 1)[1] for n in notices(connector)] == ["Bucket listed again from the start"]


    @pytest.mark.asyncio
    async def test_a_position_refused_again_after_the_restart_is_a_listing_failure(
        self, connector, store, db, checkpoints,
    ) -> None:
        """One restart per listing: a store that refuses every token must not be re-listed for ever."""
        connector.data_source.denied.clear()
        for key in ("a.txt", "b.txt", "c.txt"):
            store.put(key)
        await connector.run_sync()
        for key, point in checkpoints.sync_points.items():
            if key.endswith(f"/{BUCKET}"):
                point["continuation_token"] = "stale"
        listing = connector.data_source.list_objects_v2
        calls = []

        async def refuses_every_token(**kwargs: object) -> Response:
            calls.append(kwargs.get("ContinuationToken"))
            if kwargs.get("Bucket") == BUCKET and kwargs.get("ContinuationToken"):
                return Response(False, error="InvalidArgument: The continuation token provided is incorrect", status_code=400)
            return await listing(**kwargs)

        connector.data_source.list_objects_v2 = refuses_every_token
        connector.notify.reset_mock()

        await connector.run_sync()

        assert [n["title"].split(": ", 1)[1] for n in notices(connector)] == [
            "Bucket listed again from the start", "Some buckets could not be listed",
        ]
        assert len(calls) < 10
        # Not listed to the end, so nothing is removed and the cleanup waits.
        assert db.deleted == [] and not connector.full_sync_coverage().complete
