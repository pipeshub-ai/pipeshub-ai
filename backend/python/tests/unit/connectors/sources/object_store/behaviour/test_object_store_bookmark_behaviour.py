"""The sync bookmark (``last_sync_time``) and resume token of the object-store connectors.

Keys are listed by name, not by time, so the bookmark must never skip a change
the listing has not yet passed over.
"""

from datetime import timedelta

import pytest
from object_store_behaviour_fakes import (
    BUCKET,
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
    make_connector,
    sync_with_failed_listing,
)

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.sync_point.sync_point import (
    LISTING_CLOCK_SKEW_MS,
    listing_checkpoint,
)

ALL = ["s3", "minio", "gcs", "azure_blob"]


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


CLOCKED_MODULES = (
    "app.connectors.sources.s3.base_connector",
    "app.connectors.sources.google_cloud_storage.connector",
    "app.connectors.sources.azure_blob.connector",
)


@pytest.fixture
def wall_clock(store: FakeObjectStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """The connectors' clock reads the store's, so a listing starts at the store's "now"."""
    for module in CLOCKED_MODULES:
        monkeypatch.setattr(f"{module}.get_epoch_timestamp_in_ms", store.now_ms)


def bookmarks(checkpoints: FakeCheckpointStore) -> dict[str, object]:
    return {k: v.get("last_sync_time") for k, v in checkpoints.sync_points.items()}


class TestFailedPage:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["error", "raise"])
    async def test_a_change_on_the_failed_page_is_indexed_by_the_next_sync(
        self, connector, store, db, checkpoints, mode,
    ) -> None:
        """PP-99: page one holds the newest time, so a bookmark saved from it would skip page two."""
        for key in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt"):
            store.put(key)
        await connector.run_sync()
        store.put("d.txt", "d, second revision")
        store.put("a.txt", "a, second revision")
        saved = bookmarks(checkpoints)

        store.fail_page, store.fail_mode = 1, mode
        await sync_with_failed_listing(connector)
        assert bookmarks(checkpoints) == saved

        store.fail_page = None
        db.written.clear()
        await connector.run_incremental_sync()

        assert path("d.txt") in db.written


@pytest.mark.usefixtures("wall_clock")
class TestChangesDuringTheListing:
    """B4: keys change while a listing runs, behind and ahead of where it has reached."""

    @staticmethod
    async def _synced(connector: BaseConnector, store: FakeObjectStore) -> None:
        for key in ("a.txt", "b.txt", "c.txt", "e.txt"):
            store.put(key)
        await connector.run_sync()
        store.tick()

    @pytest.mark.asyncio
    async def test_an_overwrite_behind_the_cursor_is_indexed_next_sync(self, connector, store, db) -> None:
        """PP-96: z.txt, seen later in the listing, is newer than the overwrite of b.txt the walk had passed."""
        await self._synced(connector, store)
        store.before_page[1] = lambda: (store.put("b.txt", "b, second revision"), store.put("z.txt"))
        await connector.run_incremental_sync()
        assert path("z.txt") in db.paths()

        db.written.clear()
        await connector.run_incremental_sync()

        assert path("b.txt") in db.written

    @pytest.mark.asyncio
    async def test_a_new_key_behind_the_cursor_is_indexed_next_sync(self, connector, store, db) -> None:
        await self._synced(connector, store)
        store.before_page[1] = lambda: (store.put("aa.txt"), store.put("z.txt"))
        await connector.run_incremental_sync()

        await connector.run_incremental_sync()

        assert {path("aa.txt"), path("z.txt")} <= db.paths()

    @pytest.mark.asyncio
    async def test_an_overwrite_dated_just_before_the_listing_is_indexed_next_sync(self, connector, store, db) -> None:
        """A multipart upload is dated by its start, which can fall before the listing began."""
        await self._synced(connector, store)
        started = store.clock
        store.before_page[1] = lambda: (
            store.put("b.txt", "b, second revision", modified=started - timedelta(minutes=5)),
            store.put("z.txt"),
        )
        await connector.run_incremental_sync()

        db.written.clear()
        await connector.run_incremental_sync()

        assert path("b.txt") in db.written

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["s3", "minio", "gcs"])
    async def test_an_overwrite_behind_a_resumed_listing_is_indexed_next_sync(self, connector, store, db) -> None:
        """The resumed run bookmarks by the start of the listing it resumes, not its own start."""
        await self._synced(connector, store)
        store.fail_page = 1
        await connector.run_incremental_sync()
        store.fail_page = None
        store.put("b.txt", "b, second revision")
        store.clock += timedelta(minutes=30)
        store.put("z.txt")
        await connector.run_incremental_sync()

        db.written.clear()
        await connector.run_incremental_sync()

        assert path("b.txt") in db.written


class _WorkerStopped(BaseException):
    """The worker stops midway, past every ``except Exception``."""


def fail_second_token_write(checkpoints: FakeCheckpointStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """The sync point write for the second page's token fails, as if the database went down."""
    tokens = 0
    update = checkpoints.update_sync_point

    async def update_sync_point(key: str, data: dict) -> None:
        nonlocal tokens
        if data.get("continuation_token"):
            tokens += 1
            if tokens == 2:
                raise RuntimeError("database unavailable (update_sync_point)")
        await update(key, data)

    monkeypatch.setattr(checkpoints, "update_sync_point", update_sync_point)


@pytest.mark.parametrize("kind", ["s3", "minio"])
class TestResumeToken:
    @pytest.mark.asyncio
    async def test_records_buffered_before_a_saved_token_are_written_by_the_next_sync(
        self, connector, store, db, checkpoints, monkeypatch,
    ) -> None:
        """A page's records sit in a part-full batch; the token after the page must not outlive them."""
        connector.batch_size = 5
        for key in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt"):
            store.put(key)
        with monkeypatch.context() as patch:
            fail_second_token_write(checkpoints, patch)
            with pytest.raises(RuntimeError):
                await connector.run_sync()

        await connector.run_sync()

        assert {path("a.txt"), path("b.txt")} <= db.paths()

    @pytest.mark.asyncio
    async def test_a_failed_object_is_retried_after_the_sync_is_stopped_midway(
        self, connector, store, db, monkeypatch,
    ) -> None:
        for key in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt"):
            store.put(key)
        process = connector._process_s3_object

        async def process_s3_object(obj: dict, bucket_name: str) -> tuple:
            if obj.get("Key") == "b.txt":
                raise RuntimeError("could not read b.txt")
            return await process(obj, bucket_name)

        def stop() -> None:
            raise _WorkerStopped

        store.before_page[2] = stop
        with monkeypatch.context() as patch:
            patch.setattr(connector, "_process_s3_object", process_s3_object)
            with pytest.raises(BaseExceptionGroup) as stopped:
                await connector.run_sync()
        assert stopped.value.subgroup(_WorkerStopped)

        await connector.run_sync()

        assert path("b.txt") in db.paths()


def test_a_resume_without_a_recorded_start_keeps_the_saved_bookmark() -> None:
    # A token saved before the start was recorded: the listing's start is unknown.
    assert listing_checkpoint(1_000_000_000_000, None) is None
    assert listing_checkpoint(1_000_000_000_000, 1_000_000_000_000) == 1_000_000_000_000 - LISTING_CLOCK_SKEW_MS
    assert listing_checkpoint(5, 1_000_000_000_000) == 5
