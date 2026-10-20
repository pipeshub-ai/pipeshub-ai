"""The sync bookmark (``last_sync_time``) and resume token of the object-store connectors.

Keys are listed by name, not by time, so the bookmark must never skip a change
the listing has not yet passed over.
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
        await connector.run_incremental_sync()
        assert bookmarks(checkpoints) == saved

        store.fail_page = None
        db.written.clear()
        await connector.run_incremental_sync()

        assert path("d.txt") in db.written
