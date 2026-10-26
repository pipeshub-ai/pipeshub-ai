"""Access a personal S3 or MinIO connector grants.

Records and buckets carry no permission edges; they inherit from the app. A
personal connector gives the app to its creator alone, so when the creator
cannot be looked up the full sync fails rather than write content nobody owns.
"""

from unittest.mock import AsyncMock

import pytest
from object_store_behaviour_fakes import (
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
    idle_write,
    make_connector,
    run_full_sync_task,
    runner_graph,
)

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.constants import ConnectorStateKeys
from app.models.entities import User
from app.services.notification.types import NotificationType

CREATOR = User(id="u-1", user_id="creator-1", email="owner@example.com", org_id="org-1")
# A database error, or no creator with an email.
LOOKUP_FAILED = (RuntimeError, LookupError)


@pytest.fixture(params=["s3", "minio"])
def connector(
    request: pytest.FixtureRequest, store: FakeObjectStore, db: FakeRecordsDb,
    checkpoints: FakeCheckpointStore, config: FakeConfigService,
) -> BaseConnector:
    connector = make_connector(request.param, store, db, checkpoints, config, scope="personal")
    connector.notify = AsyncMock()
    return connector


def all_permissions(db: FakeRecordsDb) -> list:
    return [p for perms in [*db.record_permissions.values(), *db.group_permissions.values()] for p in perms]


def break_lookup(db: FakeRecordsDb, how: str) -> None:
    if how == "error":
        db.failing.add("get_user_by_user_id")
    elif how == "missing":
        db.users.clear()
    else:
        db.users["creator-1"] = User(email="", user_id="creator-1")


def sync_errors(connector: BaseConnector) -> list:
    return [c for c in connector.notify.await_args_list if c.kwargs["type"] == NotificationType.CONNECTOR_SYNC_ERROR]


class TestPersonalScopeAccess:
    @pytest.mark.asyncio
    async def test_only_the_creator_gets_the_app_and_nothing_gets_edges(self, connector, store, db) -> None:
        db.users["creator-1"] = CREATOR
        store.put("a/b.txt")
        store.put("c.txt")

        await connector.run_sync()

        assert {"bucket-1/a/b.txt", "bucket-1/c.txt"} <= db.paths()
        assert [u.email for u in db.app_users] == [CREATOR.email]
        assert all_permissions(db) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("how", ["error", "missing", "no_email"])
    async def test_a_failed_lookup_stops_the_sync_before_any_write(self, connector, store, db, how) -> None:
        db.users["creator-1"] = CREATOR
        break_lookup(db, how)
        store.put("a/b.txt")

        with pytest.raises(LOOKUP_FAILED):
            await connector.run_sync()

        assert db.records == {}
        assert db.record_groups == {}
        assert db.app_users == []
        assert len(sync_errors(connector)) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("how", ["error", "missing", "no_email"])
    async def test_a_failed_lookup_fails_the_run_so_a_full_sync_is_owed(self, connector, store, db, how) -> None:
        # Its prep already deleted the bookmarks; a run counted as done would leave an incremental to index all of it.
        db.users["creator-1"] = CREATOR
        break_lookup(db, how)
        store.put("a/b.txt")
        graph = runner_graph()

        with pytest.raises(LOOKUP_FAILED):
            await run_full_sync_task(connector, graph)

        assert idle_write(graph).get(ConnectorStateKeys.PENDING_FULL_SYNC) is True
        assert db.records == {}
        graph.unlink_nodes_missed_by_full_sync.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("next_sync", ["run_sync", "run_incremental_sync"])
    @pytest.mark.parametrize("how", ["error", "missing", "no_email"])
    async def test_a_failed_lookup_after_a_good_sync_never_grants_anyone(
        self, connector, store, db, how, next_sync,
    ) -> None:
        db.users["creator-1"] = CREATOR
        store.put("a/b.txt")
        await connector.run_sync()

        break_lookup(db, how)
        store.put("a/new.txt")
        store.put("a/b.txt", "changed")
        try:
            await getattr(connector, next_sync)()
        except LOOKUP_FAILED:
            assert next_sync == "run_sync"

        assert [u.email for u in db.app_users] == [CREATOR.email]
        assert all_permissions(db) == []


class TestCreatorLookupPerRun:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("run", "lookups"), [("run_sync", 1), ("run_incremental_sync", 0)])
    async def test_the_creator_is_looked_up_at_most_once_per_run(self, connector, store, db, run, lookups) -> None:
        db.users["creator-1"] = CREATOR
        lookup = db.get_user_by_user_id = AsyncMock(wraps=db.get_user_by_user_id)
        for name in ("one", "two", "three"):
            store.put(f"a/b/{name}.txt")

        await getattr(connector, run)()
        assert lookup.await_count == lookups
        await getattr(connector, run)()
        assert lookup.await_count == 2 * lookups

    @pytest.mark.asyncio
    async def test_a_failed_lookup_is_not_kept_for_the_next_run(self, connector, store, db) -> None:
        store.put("a/b.txt")
        with pytest.raises(LookupError):
            await connector.run_sync()
        assert db.records == {}

        db.users["creator-1"] = CREATOR
        await connector.run_sync()
        assert "bucket-1/a/b.txt" in db.paths()
