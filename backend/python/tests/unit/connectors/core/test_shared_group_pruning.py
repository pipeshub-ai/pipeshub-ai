"""A record's other groups: pruned when the connector's list is complete, and
left alone when a group it is only shared into is deleted."""

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.arangodb import Connectors as ConnectorsEnum
from app.models.entities import FileRecord
from tests.unit.connectors.core.test_data_source_entities_processor import (
    _make_ctx,
    _make_processor,
    _make_record,
    _make_tx_store,
)

RECORDS = CollectionNames.RECORDS.value
GROUPS = CollectionNames.RECORD_GROUPS.value


def _belongs_to(*group_ids: str) -> list[dict]:
    return [{"_to": f"{GROUPS}/{group_id}"} for group_id in group_ids]


def _groups_by_external_id(mapping: dict[str, str]) -> AsyncMock:
    async def lookup(connector_id: str, external_id: str) -> MagicMock | None:
        if external_id not in mapping:
            return None
        group = MagicMock()
        group.id = mapping[external_id]
        return group

    return AsyncMock(side_effect=lookup)


def _dropped(tx_store: AsyncMock, record_id: str, group_id: str) -> list:
    return [
        call(record_id, RECORDS, group_id, GROUPS, CollectionNames.BELONGS_TO.value),
        call(record_id, RECORDS, group_id, GROUPS, CollectionNames.INHERIT_PERMISSIONS.value),
        call(group_id, GROUPS, record_id, RECORDS, CollectionNames.NODE_RELATIONS.value),
    ]


def _deleted_groups(tx_store: AsyncMock) -> set[str]:
    return {
        c.args[2] if c.args[1] == RECORDS else c.args[0]
        for c in tx_store.delete_edge.await_args_list
    }


def _shared_record(connector: ConnectorsEnum, shared: list[str]) -> FileRecord:
    record = _make_record(connector_name=connector, inherit_permissions=True)
    record.id = "rec-1"
    record.shared_with_me_record_group_ids = shared
    record.shared_groups_complete = connector in (ConnectorsEnum.SLACK_WORKSPACE, ConnectorsEnum.LINEAR)
    return record


class TestLinkRecordToGroupPrunesLeftGroups:
    @pytest.mark.asyncio
    async def test_a_slack_file_out_of_a_channel_loses_all_three_edges(self) -> None:
        proc, tx_store = _make_processor(), _make_tx_store()
        tx_store.get_edges_from_node.return_value = _belongs_to("own", "rg-a", "rg-b")
        tx_store.get_record_group_by_external_id = _groups_by_external_id({"chan-a": "rg-a"})
        narrowed: set[str] = set()

        moved = await proc._link_record_to_group(
            _shared_record(ConnectorsEnum.SLACK_WORKSPACE, ["chan-a"]), "own", tx_store,
            narrowed_connectors=narrowed,
        )

        assert moved is True
        assert tx_store.delete_edge.await_args_list == _dropped(tx_store, "rec-1", "rg-b")
        assert narrowed == {"conn-1"}

    @pytest.mark.asyncio
    async def test_a_linear_project_removed_from_its_last_other_team_is_pruned(self) -> None:
        """An empty list is a complete one too: every other team is gone."""
        proc, tx_store = _make_processor(), _make_tx_store()
        tx_store.get_edges_from_node.return_value = _belongs_to("own", "team-b")

        await proc._link_record_to_group(_shared_record(ConnectorsEnum.LINEAR, []), "own", tx_store)

        assert tx_store.delete_edge.await_args_list == _dropped(tx_store, "rec-1", "team-b")

    @pytest.mark.asyncio
    async def test_a_slack_file_without_shares_keeps_its_channels(self) -> None:
        """A file object without shares does not say which channels it left."""
        proc, tx_store = _make_processor(), _make_tx_store()
        tx_store.get_edges_from_node.return_value = _belongs_to("own", "rg-b")
        record = _shared_record(ConnectorsEnum.SLACK_WORKSPACE, [])
        record.shared_groups_complete = False

        await proc._link_record_to_group(record, "own", tx_store)

        tx_store.delete_edge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_own_group_is_never_pruned(self) -> None:
        proc, tx_store = _make_processor(), _make_tx_store()
        tx_store.get_edges_from_node.return_value = _belongs_to("own")

        moved = await proc._link_record_to_group(_shared_record(ConnectorsEnum.LINEAR, []), "own", tx_store)

        assert moved is False
        tx_store.delete_edge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_drive_record_with_a_shorter_list_keeps_its_groups(self) -> None:
        """Drive sends one user's share: a group missing from it is another user's."""
        proc, tx_store = _make_processor(), _make_tx_store()
        tx_store.get_edges_from_node.return_value = _belongs_to("own", "0S-alice", "0S-bob")
        tx_store.get_record_group_by_external_id = _groups_by_external_id({"0S:alice": "0S-alice"})

        await proc._link_record_to_group(
            _shared_record(ConnectorsEnum.GOOGLE_DRIVE_WORKSPACE, ["0S:alice"]), "own", tx_store
        )

        tx_store.delete_edge.assert_not_awaited()


class TestPermissionUpdatePrunesLeftGroups:
    @staticmethod
    def _setup(attached: list[str], groups: dict[str, str]) -> tuple:
        proc, tx_store = _make_processor(), _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        tx_store.get_edges_from_node.return_value = _belongs_to(*attached)
        tx_store.get_record_group_by_external_id = _groups_by_external_id(groups)
        tx_store.batch_update_nodes = AsyncMock()
        stored = MagicMock()
        stored.virtual_record_id = "vrid-1"
        tx_store.get_record_by_external_id.return_value = stored
        proc._publish_membership_sync = AsyncMock()
        return proc, tx_store

    @pytest.mark.asyncio
    async def test_a_channel_the_file_left_is_pruned_and_its_membership_republished(self) -> None:
        proc, tx_store = self._setup(["own", "rg-a", "rg-b"], {"chan-own": "own", "chan-a": "rg-a"})
        record = _shared_record(ConnectorsEnum.SLACK_WORKSPACE, ["chan-a"])
        record.external_record_group_id = "chan-own"

        await proc.on_updated_record_permissions(record, [])

        assert _deleted_groups(tx_store) == {"rg-b"}
        assert tx_store.delete_edge.await_args_list == _dropped(tx_store, "rec-1", "rg-b")
        proc._publish_membership_sync.assert_awaited_once_with([("vrid-1", "conn-1")])

    @pytest.mark.asyncio
    async def test_nothing_is_pruned_while_the_own_group_is_unknown(self) -> None:
        """Any attached group could be the record's own one."""
        proc, tx_store = self._setup(["own", "rg-b"], {})
        record = _shared_record(ConnectorsEnum.LINEAR, [])
        record.external_record_group_id = "team-own"

        await proc.on_updated_record_permissions(record, [])

        tx_store.delete_edge.assert_not_awaited()
        proc._publish_membership_sync.assert_awaited_once_with([])


class TestGroupDeleteKeepsRecordsSharedIntoIt:
    @pytest.mark.asyncio
    async def test_a_record_whose_own_group_is_elsewhere_only_leaves_the_group(self) -> None:
        proc, tx_store = _make_processor(), _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        group = MagicMock()
        group.id, group.name = "g1", "Team B"
        tx_store.get_record_group_by_external_id.return_value = group
        tx_store.get_edges_to_node = AsyncMock(return_value=[{"_from": "records/r1"}, {"_from": "records/r2"}])
        docs = {
            "r1": {"recordGroupId": "g1"},
            "r2": {"recordGroupId": "team-a", "virtualRecordId": "vrid-2"},
        }
        tx_store.get_record_by_key = AsyncMock(side_effect=lambda key, raise_on_error: docs[key])
        proc.on_records_deleted_cascade = AsyncMock(return_value={"success": True})
        proc._publish_membership_sync = AsyncMock()

        assert await proc.on_record_group_deleted("ext-grp-1", "conn-1") is True

        assert [c.args[0] for c in proc.on_records_deleted_cascade.await_args_list] == [["r1"]]
        assert tx_store.delete_edge.await_args_list == _dropped(tx_store, "r2", "g1")
        [refreshes] = proc._publish_membership_sync.await_args.args
        assert list(refreshes) == [("vrid-2", "conn-1")]
        # Read with raise_on_error: a failed read answered None would delete the record.
        assert all(c.kwargs == {"raise_on_error": True} for c in tx_store.get_record_by_key.await_args_list)
