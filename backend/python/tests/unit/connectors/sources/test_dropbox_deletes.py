"""Dropbox deletes reach the graph, without taking a moved or still-shared item with them.

Dropbox names a deleted entry by path only, reports a move as a delete at the
old path, and shows a member who left a shared folder a delete there while the
owner keeps it. A record goes, with its subtree, only once its id stops resolving.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dropbox.files import DeletedMetadata

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.dropbox.deletes import (
    apply_deleted_entry,
    item_still_exists,
)
from app.connectors.sources.dropbox_individual.connector import (
    DropboxIndividualConnector,
)
from app.sources.client.dropbox.dropbox_ import DropboxResponse

LOGGER = logging.getLogger("dropbox-deletes")
CONNECTOR_ID = "dbx-conn-1"
MEMBER = "dbmid:member"
OWNER = "dbmid:owner"
NOT_FOUND = DropboxResponse(success=False, error="ApiError('x', GetMetadataError('path', LookupError('not_found', None)))")
THROTTLED = DropboxResponse(success=False, error="RateLimitError(too_many_requests)")


def _found() -> DropboxResponse:
    return DropboxResponse(success=True, data=MagicMock(id="id:still-here"))


def _record(record_id: str = "rec-1", external_id: str = "id:a", group: str = MEMBER) -> MagicMock:
    return MagicMock(id=record_id, external_record_id=external_id, external_record_group_id=group)


def _processor(*records: MagicMock, cascade_ok: bool = True) -> MagicMock:
    processor = MagicMock()
    processor.get_records_by_file_path = AsyncMock(return_value=list(records))
    processor.on_records_deleted_cascade = AsyncMock(return_value={"success": cascade_ok})
    return processor


class TestItemStillExists:
    def test_found(self) -> None:
        assert item_still_exists(_found()) is True

    def test_not_found(self) -> None:
        assert item_still_exists(NOT_FOUND) is False

    def test_any_other_failure_is_unknown(self) -> None:
        assert item_still_exists(THROTTLED) is None


class TestApplyDeletedEntry:
    @pytest.mark.asyncio
    async def test_a_gone_item_is_removed_with_its_subtree(self) -> None:
        processor = _processor(_record())

        done = await apply_deleted_entry(processor, CONNECTOR_ID, "/a", AsyncMock(return_value=False), LOGGER)

        assert done is True
        processor.get_records_by_file_path.assert_awaited_once_with(CONNECTOR_ID, "/a")
        processor.on_records_deleted_cascade.assert_awaited_once_with(["rec-1"], CONNECTOR_ID, include_trashed_roots=True)

    @pytest.mark.asyncio
    async def test_a_moved_item_is_kept(self) -> None:
        processor = _processor(_record())

        assert await apply_deleted_entry(processor, CONNECTOR_ID, "/a", AsyncMock(return_value=True), LOGGER) is True
        processor.on_records_deleted_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unconfirmed_delete_is_kept_and_reported(self) -> None:
        processor = _processor(_record())

        assert await apply_deleted_entry(processor, CONNECTOR_ID, "/a", AsyncMock(return_value=None), LOGGER) is False
        processor.on_records_deleted_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_only_the_gone_records_at_a_shared_path_are_removed(self) -> None:
        processor = _processor(_record("rec-gone", "id:gone"), _record("rec-moved", "id:moved"))

        async def exists(record: MagicMock) -> bool:
            return record.external_record_id == "id:moved"

        await apply_deleted_entry(processor, CONNECTOR_ID, "/a", exists, LOGGER)

        processor.on_records_deleted_cascade.assert_awaited_once_with(["rec-gone"], CONNECTOR_ID, include_trashed_roots=True)

    @pytest.mark.asyncio
    async def test_a_failed_cascade_is_reported(self) -> None:
        processor = _processor(_record(), cascade_ok=False)

        assert await apply_deleted_entry(processor, CONNECTOR_ID, "/a", AsyncMock(return_value=False), LOGGER) is False

    @pytest.mark.asyncio
    async def test_a_path_nothing_was_synced_at_is_a_no_op(self) -> None:
        processor = _processor()

        assert await apply_deleted_entry(processor, CONNECTOR_ID, "/a", AsyncMock(return_value=False), LOGGER) is True
        processor.on_records_deleted_cascade.assert_not_awaited()


@pytest.fixture
def team_connector() -> DropboxConnector:
    with patch("app.connectors.sources.dropbox.connector.DropboxApp"):
        connector = DropboxConnector(
            logger=LOGGER,
            data_entities_processor=_processor(_record(group=OWNER)),
            data_store_provider=MagicMock(),
            config_service=AsyncMock(),
            connector_id=CONNECTOR_ID,
            scope="team",
            created_by="admin",
        )
    connector.sync_filters = FilterCollection()
    connector.indexing_filters = FilterCollection()
    connector.data_source = AsyncMock()
    processor = connector.data_entities_processor
    processor.get_users_with_permission_to_node = AsyncMock(return_value=[])
    processor.get_app_user_by_email = AsyncMock(return_value=None)
    processor.get_record_by_external_id = AsyncMock(return_value=None)
    return connector


class TestTeamConnector:
    @pytest.mark.asyncio
    async def test_a_delete_entry_is_applied_and_yields_no_record(self, team_connector) -> None:
        team_connector.data_source.files_get_metadata = AsyncMock(return_value=NOT_FOUND)
        entry = DeletedMetadata(name="a.txt", path_lower="/a.txt", path_display="/a.txt")

        assert await team_connector._process_dropbox_entry(entry, MEMBER, "m@x.com", MEMBER, True) is None

        team_connector.data_entities_processor.get_records_by_file_path.assert_awaited_once_with(CONNECTOR_ID, "/a.txt")
        team_connector.data_entities_processor.on_records_deleted_cascade.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_member_who_left_a_shared_folder_does_not_remove_the_owners_item(self, team_connector) -> None:
        async def lookup(path: str, team_member_id: str) -> DropboxResponse:
            return _found() if team_member_id == OWNER else NOT_FOUND

        team_connector.data_source.files_get_metadata = AsyncMock(side_effect=lookup)

        assert await team_connector._team_item_still_exists(_record(group=OWNER), MEMBER) is True

    @pytest.mark.asyncio
    async def test_an_owner_granted_on_a_folder_above_keeps_the_item(self, team_connector) -> None:
        """The record was last written from the leaving member's own view, so its writer is that member."""
        holder = "dbmid:folder-owner"
        processor = team_connector.data_entities_processor
        folder = MagicMock(id="rec-folder", parent_external_record_id=None)
        processor.get_record_by_external_id = AsyncMock(return_value=folder)
        processor.get_users_with_permission_to_node = AsyncMock(
            side_effect=lambda node_id, _c: [MagicMock(email="owner@x.com")] if node_id == "rec-folder" else []
        )
        processor.get_app_user_by_email = AsyncMock(return_value=MagicMock(source_user_id=holder))

        async def lookup(path: str, team_member_id: str) -> DropboxResponse:
            return _found() if team_member_id == holder else NOT_FOUND

        team_connector.data_source.files_get_metadata = AsyncMock(side_effect=lookup)
        record = MagicMock(id="rec-1", external_record_id="id:a", external_record_group_id=MEMBER,
                           parent_external_record_id="id:folder")

        assert await team_connector._team_item_still_exists(record, MEMBER) is True

    @pytest.mark.asyncio
    async def test_gone_for_both_the_member_and_the_owner(self, team_connector) -> None:
        team_connector.data_source.files_get_metadata = AsyncMock(return_value=NOT_FOUND)

        assert await team_connector._team_item_still_exists(_record(group=OWNER), MEMBER) is False

    @pytest.mark.asyncio
    async def test_an_unreadable_answer_keeps_the_record(self, team_connector) -> None:
        team_connector.data_source.files_get_metadata = AsyncMock(side_effect=[NOT_FOUND, THROTTLED])

        assert await team_connector._team_item_still_exists(_record(group=OWNER), MEMBER) is None


@pytest.fixture
def personal_connector() -> DropboxIndividualConnector:
    with patch("app.connectors.sources.dropbox_individual.connector.DropboxIndividualApp"):
        connector = DropboxIndividualConnector(
            logger=LOGGER,
            data_entities_processor=_processor(_record()),
            data_store_provider=MagicMock(),
            config_service=AsyncMock(),
            connector_id=CONNECTOR_ID,
            scope="personal",
            created_by="owner",
        )
    connector.sync_filters = FilterCollection()
    connector.indexing_filters = FilterCollection()
    connector.data_source = AsyncMock()
    return connector


class TestPersonalConnector:
    @pytest.mark.asyncio
    async def test_a_deleted_folder_goes_with_its_subtree(self, personal_connector) -> None:
        personal_connector.data_source.files_get_metadata = AsyncMock(return_value=NOT_FOUND)
        entry = DeletedMetadata(name="other", path_lower="/pr3880p/other", path_display="/PR3880p/other")

        assert await personal_connector._process_dropbox_entry(entry, "acct", "o@x.com", "acct") is None

        personal_connector.data_entities_processor.on_records_deleted_cascade.assert_awaited_once_with(
            ["rec-1"], CONNECTOR_ID, include_trashed_roots=True
        )

    @pytest.mark.asyncio
    async def test_a_move_keeps_the_record(self, personal_connector) -> None:
        personal_connector.data_source.files_get_metadata = AsyncMock(return_value=_found())
        entry = DeletedMetadata(name="b.txt", path_lower="/pr3880p/sub/b.txt", path_display="/PR3880p/sub/b.txt")

        assert await personal_connector._process_dropbox_entry(entry, "acct", "o@x.com", "acct") is None

        personal_connector.data_entities_processor.on_records_deleted_cascade.assert_not_awaited()
