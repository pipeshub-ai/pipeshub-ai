"""A permission change written through the processor drops the connector's cached
access lists once it has committed, so a user who lost access stops finding the
records in search without waiting for the sync to end or the entry to expire."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import AppRole, AppUserGroup, FileRecord, RecordType, User

ORG = "org-1"
CONNECTOR = "conn-1"
_NOTIFY = (
    "app.connectors.core.base.data_processor.data_source_entities_processor."
    "notify_connector_permissions_changed"
)


@pytest.fixture
def events() -> list:
    return []


@pytest.fixture
def notify(events) -> Iterator[AsyncMock]:
    async def record(connector_id, org_id=None) -> None:
        events.append(("drop", connector_id, org_id))

    with patch(_NOTIFY, new=AsyncMock(side_effect=record)) as mock:
        yield mock


def _processor(tx_store, events) -> DataSourceEntitiesProcessor:
    async def commit(*_exc_info) -> bool:
        events.append("commit")
        return False

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(side_effect=commit)
    provider = MagicMock()
    provider.transaction = MagicMock(return_value=ctx)
    proc = DataSourceEntitiesProcessor(MagicMock(), provider, AsyncMock())
    proc.org_id = ORG
    proc.messaging_producer = AsyncMock()
    return proc


def _record() -> FileRecord:
    return FileRecord(
        id="rec-1",
        org_id=ORG,
        external_record_id="ext-1",
        record_name="plan.txt",
        origin=OriginTypes.CONNECTOR.value,
        connector_name=Connectors.GOOGLE_DRIVE,
        connector_id=CONNECTOR,
        record_type=RecordType.FILE,
        version=1,
        mime_type="text/plain",
        is_file=True,
        extension="txt",
        size_in_bytes=1,
        weburl="https://example.com/plan.txt",
    )


def _user() -> User:
    return User(id="user-key", email="ana@example.com", org_id=ORG)


def _dropped_after_commit(events) -> None:
    assert events[-1] == ("drop", CONNECTOR, ORG)
    assert "commit" in events[: events.index(("drop", CONNECTOR, ORG))]


class TestRemovalsDropTheConnector:
    async def test_member_removed_from_group(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_user_by_email = AsyncMock(return_value=_user())
        tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(id="grp-key", name="Finance"))
        tx.batch_delete_edges = AsyncMock(return_value=1)
        proc = _processor(tx, events)

        assert await proc.on_user_group_member_removed("grp-ext", "ana@example.com", CONNECTOR) is True

        _dropped_after_commit(events)

    async def test_member_who_was_not_in_the_group_drops_nothing(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_user_by_email = AsyncMock(return_value=_user())
        tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(id="grp-key", name="Finance"))
        tx.batch_delete_edges = AsyncMock(return_value=0)
        proc = _processor(tx, events)

        assert await proc.on_user_group_member_removed("grp-ext", "ana@example.com", CONNECTOR) is False

        notify.assert_not_called()

    async def test_group_deleted(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(id="grp-key", name="Finance"))
        proc = _processor(tx, events)

        await proc.on_user_group_deleted("grp-ext", CONNECTOR)

        _dropped_after_commit(events)

    async def test_role_deleted(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_app_role_by_external_id = AsyncMock(return_value=MagicMock(id="role-key", name="Viewer"))
        proc = _processor(tx, events)

        await proc.on_app_role_deleted("role-ext", CONNECTOR)

        _dropped_after_commit(events)

    async def test_record_group_deleted(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_record_group_by_external_id = AsyncMock(return_value=MagicMock(id="rg-key", name="Shared"))
        proc = _processor(tx, events)

        with patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.is_soft_delete_enabled",
            new=AsyncMock(return_value=False),
        ):
            assert await proc.on_record_group_deleted("rg-ext", CONNECTOR) is True

        _dropped_after_commit(events)

    async def test_record_unshared_from_a_user(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_user_by_email = AsyncMock(return_value=_user())
        tx.batch_delete_edges = AsyncMock(return_value=1)
        tx.get_record_by_key = AsyncMock(return_value={"id": "rec-1", "connectorId": CONNECTOR})
        proc = _processor(tx, events)

        await proc.delete_permission_from_record("rec-1", "ana@example.com")

        _dropped_after_commit(events)

    async def test_user_access_removed_from_a_folder_item(self, notify, events) -> None:
        tx = AsyncMock()
        proc = _processor(tx, events)

        await proc.remove_user_access_to_record(CONNECTOR, "ext-1", "user-key")

        _dropped_after_commit(events)

    async def test_record_permissions_replaced(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_edges_from_node = AsyncMock(return_value=[{"to_id": "rg-key"}])
        tx.get_record_group_by_external_id = AsyncMock(return_value=None)
        proc = _processor(tx, events)
        record = _record()
        record.shared_with_me_record_group_ids = []

        await proc.on_updated_record_permissions(record, [])

        tx.replace_record_permissions.assert_awaited_once()
        _dropped_after_commit(events)

    async def test_existing_group_membership_rewritten(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(id="grp-key"))
        proc = _processor(tx, events)
        group = AppUserGroup(
            app_name=Connectors.GOOGLE_DRIVE, connector_id=CONNECTOR,
            source_user_group_id="grp-ext", name="Finance", org_id=ORG,
        )

        await proc.on_new_user_groups([(group, [])])

        tx.replace_edges_to.assert_awaited_once()
        _dropped_after_commit(events)

    async def test_new_group_drops_nothing(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        proc = _processor(tx, events)
        group = AppUserGroup(
            app_name=Connectors.GOOGLE_DRIVE, connector_id=CONNECTOR,
            source_user_group_id="grp-ext", name="Finance", org_id=ORG,
        )

        await proc.on_new_user_groups([(group, [])])

        notify.assert_not_called()

    async def test_existing_role_membership_rewritten(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_app_role_by_external_id = AsyncMock(return_value=MagicMock(id="role-key"))
        proc = _processor(tx, events)
        role = AppRole(
            app_name=Connectors.GOOGLE_DRIVE, connector_id=CONNECTOR,
            source_role_id="role-ext", name="Viewer", org_id=ORG,
        )

        await proc.on_new_app_roles([(role, [])])

        _dropped_after_commit(events)

    async def test_record_moved_to_another_group(self, notify, events) -> None:
        proc = _processor(AsyncMock(), events)
        proc.messaging_producer.send_messages = AsyncMock(return_value=[True])

        await proc._publish_membership_sync([("vrid-1", CONNECTOR), ("vrid-2", CONNECTOR)])

        notify.assert_awaited_once_with(CONNECTOR, ORG)

    async def test_failed_write_drops_nothing(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_user_by_email = AsyncMock(return_value=_user())
        tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(id="grp-key", name="Finance"))
        tx.batch_delete_edges = AsyncMock(side_effect=RuntimeError("graph down"))
        proc = _processor(tx, events)

        with pytest.raises(RuntimeError):
            await proc.on_user_group_member_removed("grp-ext", "ana@example.com", CONNECTOR)

        notify.assert_not_called()
