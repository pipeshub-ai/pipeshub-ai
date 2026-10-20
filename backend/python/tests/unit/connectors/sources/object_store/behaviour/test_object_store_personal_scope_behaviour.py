"""Permissions a personal S3 or MinIO connector writes.

A personal connector's content belongs to its creator. When the creator cannot
be looked up, the sync must stop rather than grant the whole org, because
record permission edges are only ever added and nothing takes them back.
"""

from unittest.mock import AsyncMock

import pytest
from object_store_behaviour_fakes import (
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
    make_connector,
)

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.models.entities import User
from app.models.permission import EntityType, PermissionType
from app.services.notification.types import NotificationType

CREATOR = User(id="u-1", user_id="creator-1", email="owner@example.com", org_id="org-1")


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


class TestPersonalScopePermissions:
    @pytest.mark.asyncio
    async def test_only_the_creator_is_granted(self, connector, store, db) -> None:
        db.users["creator-1"] = CREATOR
        store.put("a/b.txt")
        store.put("c.txt")

        await connector.run_sync()

        assert set(db.record_permissions) == {"bucket-1/a", "bucket-1/a/b.txt", "bucket-1/c.txt"}
        assert db.group_permissions
        for permission in all_permissions(db):
            assert (permission.entity_type, permission.type, permission.email) == (
                EntityType.USER, PermissionType.OWNER, CREATOR.email,
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("how", ["error", "missing", "no_email"])
    async def test_a_failed_lookup_stops_the_sync_before_any_write(self, connector, store, db, how) -> None:
        db.users["creator-1"] = CREATOR
        break_lookup(db, how)
        store.put("a/b.txt")

        await connector.run_sync()

        assert db.records == {}
        assert db.record_groups == {}
        assert all_permissions(db) == []
        assert len(sync_errors(connector)) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("next_sync", ["run_sync", "run_incremental_sync"])
    @pytest.mark.parametrize("how", ["error", "missing", "no_email"])
    async def test_a_failed_lookup_after_a_good_sync_never_widens_access(
        self, connector, store, db, how, next_sync,
    ) -> None:
        db.users["creator-1"] = CREATOR
        store.put("a/b.txt")
        await connector.run_sync()
        granted = {k: list(v) for k, v in db.record_permissions.items()}

        break_lookup(db, how)
        store.put("a/new.txt")
        store.put("a/b.txt", "changed")
        await getattr(connector, next_sync)()

        assert db.record_permissions == granted
        assert all(p.entity_type != EntityType.ORG for p in all_permissions(db))
