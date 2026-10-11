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

        tx.get_record_by_key.assert_awaited_once_with("rec-1", raise_on_error=True)
        _dropped_after_commit(events)

    async def test_unshare_with_the_callers_connector_reads_no_record(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_user_by_email = AsyncMock(return_value=_user())
        tx.batch_delete_edges = AsyncMock(return_value=1)
        proc = _processor(tx, events)

        await proc.delete_permission_from_record("rec-1", "ana@example.com", connector_id=CONNECTOR)

        tx.get_record_by_key.assert_not_called()
        _dropped_after_commit(events)

    async def test_unreadable_record_fails_before_the_delete(self, notify, events) -> None:
        """Read after the delete, a failed lookup lost the drop for good: the edge
        was gone, so the retry found nothing to delete and never dropped either."""
        tx = AsyncMock()
        tx.get_user_by_email = AsyncMock(return_value=_user())
        tx.get_record_by_key = AsyncMock(side_effect=RuntimeError("graph down"))
        tx.batch_delete_edges = AsyncMock(return_value=1)
        proc = _processor(tx, events)

        with pytest.raises(RuntimeError):
            await proc.delete_permission_from_record("rec-1", "ana@example.com")

        tx.batch_delete_edges.assert_not_called()
        notify.assert_not_called()

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


class TestTheAffectedOrgIsDropped:
    async def test_record_permissions_use_the_records_org(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_edges_from_node = AsyncMock(return_value=[{"to_id": "rg-key"}])
        tx.get_record_group_by_external_id = AsyncMock(return_value=None)
        proc = _processor(tx, events)
        record = _record()
        record.org_id = "org-9"
        record.shared_with_me_record_group_ids = []

        await proc.on_updated_record_permissions(record, [])

        notify.assert_awaited_once_with(CONNECTOR, "org-9")

    async def test_processor_org_is_the_fallback(self, notify, events) -> None:
        proc = _processor(AsyncMock(), events)

        await proc._access_changed([CONNECTOR])

        notify.assert_awaited_once_with(CONNECTOR, ORG)


def _existing(group_id: str = "rg-key") -> FileRecord:
    existing = _record()
    existing.record_group_id = group_id
    return existing


class TestInheritedAccessRemovedInPlace:
    """A record that stays in its group but stops inheriting from it loses that
    group's access without moving, so no membership refresh ever covered it."""

    async def test_inherit_edge_removed_marks_the_connector(self, events) -> None:
        tx = AsyncMock()
        tx.get_edge = AsyncMock(return_value={"_from": "records/rec-1"})
        proc = _processor(tx, events)
        record = _record()
        record.inherit_permissions = False
        narrowed: set[str] = set()

        moved = await proc._link_record_to_group(record, "rg-key", tx, _existing(), narrowed)

        assert moved is False
        assert narrowed == {CONNECTOR}
        tx.link_record_to_group.assert_awaited_once()

    async def test_no_inherit_edge_to_remove_marks_nothing(self, events) -> None:
        tx = AsyncMock()
        tx.get_edge = AsyncMock(return_value=None)
        proc = _processor(tx, events)
        record = _record()
        record.inherit_permissions = False
        narrowed: set[str] = set()

        await proc._link_record_to_group(record, "rg-key", tx, _existing(), narrowed)

        assert narrowed == set()

    async def test_still_inheriting_reads_nothing(self, events) -> None:
        tx = AsyncMock()
        proc = _processor(tx, events)
        record = _record()
        record.inherit_permissions = True
        narrowed: set[str] = set()

        await proc._link_record_to_group(record, "rg-key", tx, _existing(), narrowed)

        tx.get_edge.assert_not_called()
        assert narrowed == set()

    async def test_leaving_a_group_marks_the_connector(self, events) -> None:
        tx = AsyncMock()
        proc = _processor(tx, events)
        narrowed: set[str] = set()

        await proc._link_record_to_group(_record(), "rg-new", tx, _existing("rg-old"), narrowed)

        assert narrowed == {CONNECTOR}

    async def test_metadata_update_drops_after_commit(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=None)
        proc = _processor(tx, events)

        async def narrowing(record, permissions, tx_store, moved, **kwargs) -> tuple[None, list]:
            kwargs["narrowed_connectors"].add(CONNECTOR)
            return None, []

        with patch.object(proc, "_process_record", new=AsyncMock(side_effect=narrowing)):
            await proc.on_record_metadata_update(_record())

        _dropped_after_commit(events)

    async def test_content_update_drops_after_commit(self, notify, events) -> None:
        proc = _processor(AsyncMock(), events)

        async def narrowing(record, permissions, tx_store, moved, **kwargs) -> tuple[None, list]:
            kwargs["narrowed_connectors"].add(CONNECTOR)
            return None, []

        with patch.object(proc, "_process_record", new=AsyncMock(side_effect=narrowing)):
            await proc.on_record_content_update(_record())

        _dropped_after_commit(events)


class TestTrashingDropsTheConnector:
    async def test_group_kept_for_the_trash(self, notify, events) -> None:
        tx = AsyncMock()
        tx.get_record_group_by_external_id = AsyncMock(return_value=MagicMock(id="rg-key", name="Shared"))
        tx.get_records_by_status = AsyncMock(return_value=[MagicMock()])
        tx.batch_update_nodes = AsyncMock(return_value=True)
        proc = _processor(tx, events)

        with patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.is_soft_delete_enabled",
            new=AsyncMock(return_value=True),
        ), patch.object(proc, "_trash_live_records_of_group", new=AsyncMock()) as trash:
            assert await proc.on_record_group_deleted("rg-ext", CONNECTOR, trash_live_records=True) is True

        trash.assert_awaited_once()
        tx.delete_nodes_and_edges.assert_not_called()
        _dropped_after_commit(events)

    async def test_trashed_records(self, notify, events) -> None:
        tx = AsyncMock()
        tx.soft_delete_records = AsyncMock(return_value={
            "soft_deleted_records": [{"record_id": "rec-1", "virtual_record_id": "vrid-1"}],
            "virtual_record_ids": ["vrid-1"],
            "org_id": ORG,
        })
        proc = _processor(tx, events)

        with patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.notify_kb_records_changed",
            new=AsyncMock(),
        ), patch.object(proc, "_publish_soft_delete_events", new=AsyncMock(return_value=[])):
            await proc.on_records_soft_deleted(["rec-1"], CONNECTOR, delete_source="CONNECTOR")

        _dropped_after_commit(events)

    async def test_nothing_trashed_drops_nothing(self, notify, events) -> None:
        tx = AsyncMock()
        tx.soft_delete_records = AsyncMock(return_value={"soft_deleted_records": [], "virtual_record_ids": []})
        proc = _processor(tx, events)

        with patch.object(proc, "_publish_soft_delete_events", new=AsyncMock(return_value=[])):
            await proc.on_records_soft_deleted(["rec-1"], CONNECTOR, delete_source="CONNECTOR")

        notify.assert_not_called()
