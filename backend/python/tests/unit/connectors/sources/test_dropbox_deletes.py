"""Dropbox deletes reach the graph, without taking a moved or still-shared item with them.

Dropbox names a deleted entry by path only, reports a move as a delete at the
old path, and shows a member who left a shared folder a delete there while the
owner keeps it. A record goes, with its subtree, only once its id stops resolving.
"""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dropbox.files import DeletedMetadata, FileMetadata

from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterOperator,
    FilterType,
)
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.dropbox.deletes import (
    apply_deleted_entry,
    apply_out_of_scope_entry,
    apply_replaced_entry,
    item_still_exists,
    recheck_owed_deletes,
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

        outcome = await apply_deleted_entry(processor, CONNECTOR_ID, "/a", AsyncMock(return_value=False), LOGGER)

        assert outcome.done and outcome.removed == ["rec-1"]
        processor.get_records_by_file_path.assert_awaited_once_with(CONNECTOR_ID, "/a")
        processor.on_records_deleted_cascade.assert_awaited_once_with(["rec-1"], CONNECTOR_ID, include_trashed_roots=True)

    @pytest.mark.asyncio
    async def test_a_moved_item_is_kept(self) -> None:
        processor = _processor(_record())

        assert (await apply_deleted_entry(processor, CONNECTOR_ID, "/a", AsyncMock(return_value=True), LOGGER)).done
        processor.on_records_deleted_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unconfirmed_delete_is_kept_and_reported(self) -> None:
        processor = _processor(_record())

        outcome = await apply_deleted_entry(processor, CONNECTOR_ID, "/a", AsyncMock(return_value=None), LOGGER)
        assert not outcome.done and outcome.unsure_external_ids == ["id:a"], "kept, and owed to the next run"
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

        outcome = await apply_deleted_entry(processor, CONNECTOR_ID, "/a", AsyncMock(return_value=False), LOGGER)
        assert outcome.failed and not outcome.done
        # Dropbox lists the delete once: the record is owed, and written on the next run.
        assert outcome.unsure_external_ids == ["id:a"]

    @pytest.mark.asyncio
    async def test_a_path_nothing_was_synced_at_is_a_no_op(self) -> None:
        processor = _processor()

        assert (await apply_deleted_entry(processor, CONNECTOR_ID, "/a", AsyncMock(return_value=False), LOGGER)).done
        processor.on_records_deleted_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_only_the_listings_own_record_group_is_considered(self) -> None:
        """Paths are relative to a namespace: the same path in another member's home is another item."""
        processor = _processor(_record("rec-mine", "id:mine", group=MEMBER), _record("rec-other", "id:other", group=OWNER))
        exists = AsyncMock(return_value=False)

        await apply_deleted_entry(processor, CONNECTOR_ID, "/a", exists, LOGGER, record_group_id=MEMBER)

        processor.on_records_deleted_cascade.assert_awaited_once_with(["rec-mine"], CONNECTOR_ID, include_trashed_roots=True)
        assert exists.await_count == 1


class TestReplacedEntry:
    @pytest.mark.asyncio
    async def test_a_new_item_at_a_stored_path_removes_the_old_one_once_it_is_gone(self) -> None:
        processor = _processor(_record("rec-old", "id:old"))

        outcome = await apply_replaced_entry(processor, CONNECTOR_ID, "/a", "id:new", MEMBER,
                                             AsyncMock(return_value=False), LOGGER)

        assert outcome.removed == ["rec-old"]

    @pytest.mark.asyncio
    async def test_the_listed_item_itself_and_live_items_are_kept(self) -> None:
        processor = _processor(_record("rec-same", "id:new"), _record("rec-live", "id:live"))

        outcome = await apply_replaced_entry(processor, CONNECTOR_ID, "/a", "id:new", MEMBER,
                                             AsyncMock(return_value=True), LOGGER)

        assert outcome.removed == [] and outcome.done
        processor.on_records_deleted_cascade.assert_not_awaited()


class TestOutOfScopeEntry:
    """Dropbox lists a file the sync filters now leave out: its stored record goes on that sync."""

    @pytest.mark.asyncio
    async def test_the_stored_record_is_removed_without_asking_dropbox(self) -> None:
        processor = _processor()
        processor.get_record_by_external_id = AsyncMock(return_value=_record())

        outcome = await apply_out_of_scope_entry(processor, CONNECTOR_ID, "id:a", LOGGER)

        assert outcome.done and outcome.removed == ["rec-1"]
        processor.get_record_by_external_id.assert_awaited_once_with(CONNECTOR_ID, "id:a")
        processor.on_records_deleted_cascade.assert_awaited_once_with(["rec-1"], CONNECTOR_ID, include_trashed_roots=True)

    @pytest.mark.asyncio
    async def test_a_file_never_stored_removes_nothing(self) -> None:
        processor = _processor()
        processor.get_record_by_external_id = AsyncMock(return_value=None)

        outcome = await apply_out_of_scope_entry(processor, CONNECTOR_ID, "id:a", LOGGER)

        assert outcome.done and not outcome.removed
        processor.on_records_deleted_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_write_is_reported(self) -> None:
        processor = _processor(cascade_ok=False)
        processor.get_record_by_external_id = AsyncMock(return_value=_record())

        outcome = await apply_out_of_scope_entry(processor, CONNECTOR_ID, "id:a", LOGGER)

        assert outcome.failed and not outcome.done


def _listed_file(name: str = "a.txt") -> FileMetadata:
    modified = datetime(2026, 10, 1, tzinfo=timezone.utc)
    entry = FileMetadata(name=name, id="id:a", client_modified=modified, server_modified=modified,
                         rev="0123456789abcdef", size=10)
    entry.path_lower = entry.path_display = f"/{name}"
    return entry


def _only_md() -> FilterCollection:
    return FilterCollection(filters=[Filter(key="file_extensions", value=["md"], type=FilterType.MULTISELECT,
                                            operator=FilterOperator.IN)])


class TestOwedDeletes:
    @pytest.mark.asyncio
    async def test_an_owed_delete_is_applied_once_dropbox_answers(self) -> None:
        processor = _processor()
        processor.get_record_by_external_id = AsyncMock(return_value=_record("rec-1", "id:a"))

        still = await recheck_owed_deletes(processor, CONNECTOR_ID, ["id:a"], AsyncMock(return_value=False), LOGGER)

        assert still == []
        processor.on_records_deleted_cascade.assert_awaited_once_with(["rec-1"], CONNECTOR_ID, include_trashed_roots=True)

    @pytest.mark.asyncio
    async def test_a_still_unanswered_delete_stays_owed(self) -> None:
        processor = _processor()
        processor.get_record_by_external_id = AsyncMock(return_value=_record("rec-1", "id:a"))

        still = await recheck_owed_deletes(processor, CONNECTOR_ID, ["id:a"], AsyncMock(return_value=None), LOGGER)

        assert still == ["id:a"]
        processor.on_records_deleted_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_owed_record_already_gone_from_the_graph_is_dropped(self) -> None:
        processor = _processor()
        processor.get_record_by_external_id = AsyncMock(return_value=None)

        assert await recheck_owed_deletes(processor, CONNECTOR_ID, ["id:a"], AsyncMock(), LOGGER) == []


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
    processor.get_groups_with_access_to_record = AsyncMock(return_value=[])
    processor.get_record_by_external_id = AsyncMock(return_value=None)
    return connector


class TestTeamConnector:
    @pytest.mark.asyncio
    async def test_a_delete_entry_is_applied_and_yields_no_record(self, team_connector) -> None:
        team_connector.data_source.files_get_metadata = AsyncMock(return_value=NOT_FOUND)
        team_connector.data_entities_processor.get_records_by_file_path = AsyncMock(return_value=[_record(group=MEMBER)])
        entry = DeletedMetadata(name="a.txt", path_lower="/a.txt", path_display="/a.txt")

        assert await team_connector._process_dropbox_entry(entry, MEMBER, "m@x.com", MEMBER, True) is None

        team_connector.data_entities_processor.get_records_by_file_path.assert_awaited_once_with(CONNECTOR_ID, "/a.txt")
        team_connector.data_entities_processor.on_records_deleted_cascade.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_member_who_left_a_shared_folder_does_not_remove_the_owners_item(self, team_connector) -> None:
        async def lookup(path: str, team_member_id: str, team_folder_id: str | None = None) -> DropboxResponse:
            return _found() if team_member_id == OWNER else NOT_FOUND

        team_connector.data_source.files_get_metadata = AsyncMock(side_effect=lookup)

        assert await team_connector._team_item_still_exists(_record(group=OWNER), MEMBER) is True

    @pytest.mark.asyncio
    async def test_an_owner_granted_on_a_folder_above_keeps_the_item(self, team_connector) -> None:
        """The record was last written from the leaving member's own view, so its writer is that member."""
        holder = "dbmid:folder-owner"
        processor = team_connector.data_entities_processor
        processor.get_users_with_permission_to_node = AsyncMock(return_value=[MagicMock(email="owner@x.com")])
        processor.get_app_user_by_email = AsyncMock(return_value=MagicMock(source_user_id=holder, is_active=True))

        async def lookup(path: str, team_member_id: str, team_folder_id: str | None = None) -> DropboxResponse:
            return _found() if team_member_id == holder else NOT_FOUND

        team_connector.data_source.files_get_metadata = AsyncMock(side_effect=lookup)
        record = MagicMock(id="rec-1", external_record_id="id:a", external_record_group_id=MEMBER,
                           parent_external_record_id="id:folder")

        assert await team_connector._team_item_still_exists(record, MEMBER) is True
        processor.get_users_with_permission_to_node.assert_awaited_once_with("rec-1", "records")
        processor.get_app_user_by_email.assert_awaited_once_with("owner@x.com", CONNECTOR_ID)

    @pytest.mark.asyncio
    async def test_a_shared_folder_item_is_asked_under_its_namespace(self, team_connector) -> None:
        """On the team member root layout an id outside the member's home is not_found from the default root."""
        team_connector.data_source.files_get_metadata = AsyncMock(return_value=_found())

        assert await team_connector._team_item_still_exists(_record(group="14978535235"), MEMBER) is True
        team_connector.data_source.files_get_metadata.assert_awaited_once_with(
            path="id:a", team_member_id=MEMBER, team_folder_id="14978535235",
        )

    @pytest.mark.asyncio
    async def test_a_home_item_is_asked_from_the_members_own_root(self, team_connector) -> None:
        team_connector.data_source.files_get_metadata = AsyncMock(return_value=_found())

        assert await team_connector._team_item_still_exists(_record(group=MEMBER), MEMBER) is True
        team_connector.data_source.files_get_metadata.assert_awaited_once_with(
            path="id:a", team_member_id=MEMBER, team_folder_id=None,
        )

    @pytest.mark.asyncio
    async def test_gone_for_both_the_member_and_the_owner(self, team_connector) -> None:
        team_connector.data_source.files_get_metadata = AsyncMock(return_value=NOT_FOUND)

        assert await team_connector._team_item_still_exists(_record(group=OWNER), MEMBER) is False

    @pytest.mark.asyncio
    async def test_nobody_to_ask_is_no_answer(self, team_connector) -> None:
        """A record with no member to ask (not a home record, no grants) is not judged gone."""
        team_connector.data_source.files_get_metadata = AsyncMock(return_value=NOT_FOUND)

        assert await team_connector._team_item_still_exists(_record(group="sf-1"), None) is None
        team_connector.data_source.files_get_metadata.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unreadable_answer_keeps_the_record(self, team_connector) -> None:
        team_connector.data_source.files_get_metadata = AsyncMock(side_effect=[NOT_FOUND, THROTTLED])

        assert await team_connector._team_item_still_exists(_record(group=OWNER), MEMBER) is None


class TestTeamConnectorFilters:
    @pytest.mark.asyncio
    async def test_a_file_renamed_out_of_the_filter_loses_its_record(self, team_connector) -> None:
        team_connector.sync_filters = _only_md()
        processor = team_connector.data_entities_processor
        processor.get_record_by_external_id = AsyncMock(return_value=_record())

        assert await team_connector._process_dropbox_entry(_listed_file("a.txt"), MEMBER, "m@x.com", MEMBER, True) is None

        processor.on_records_deleted_cascade.assert_awaited_once_with(["rec-1"], CONNECTOR_ID, include_trashed_roots=True)
        team_connector.data_source.files_get_metadata.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_file_edited_out_of_the_date_window_loses_its_record(self, team_connector) -> None:
        processor = team_connector.data_entities_processor
        processor.get_record_by_external_id = AsyncMock(return_value=_record())
        before = datetime(2026, 9, 1, tzinfo=timezone.utc)

        assert await team_connector._process_dropbox_entry(_listed_file(), MEMBER, "m@x.com", MEMBER, True,
                                                           modified_before=before) is None

        processor.on_records_deleted_cascade.assert_awaited_once()


@pytest.fixture
def personal_connector() -> DropboxIndividualConnector:
    with patch("app.connectors.sources.dropbox_individual.connector.DropboxIndividualApp"):
        connector = DropboxIndividualConnector(
            logger=LOGGER,
            data_entities_processor=_processor(_record(group="acct")),
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

    @pytest.mark.asyncio
    async def test_a_file_renamed_out_of_the_filter_loses_its_record(self, personal_connector) -> None:
        personal_connector.sync_filters = _only_md()
        processor = personal_connector.data_entities_processor
        processor.get_record_by_external_id = AsyncMock(return_value=_record(group="acct"))

        assert await personal_connector._process_dropbox_entry(_listed_file("a.txt"), "acct", "o@x.com", "acct") is None

        processor.on_records_deleted_cascade.assert_awaited_once_with(["rec-1"], CONNECTOR_ID, include_trashed_roots=True)
