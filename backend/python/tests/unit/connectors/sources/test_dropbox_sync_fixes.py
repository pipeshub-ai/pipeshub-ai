"""The Dropbox connectors' failure handling, cursor resets, writes, membership and identity (audit of 2026-10-26).

Each test names the behaviour it guards; the audit report (dbx-audit/REPORT.md) names the finding.
"""

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata
from dropbox.team_log import EventCategory

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.core.sync import sync_runner
from app.connectors.core.sync.full_sync_coverage import FullSyncSeen
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.dropbox.errors import (
    is_auth_error,
    is_cursor_reset,
    missing_scope,
)
from app.connectors.sources.dropbox.owed import OwedDeletes
from app.connectors.sources.dropbox.parents import ParentResolver, parent_path
from app.connectors.sources.dropbox.paths import repath_descendants
from app.connectors.sources.dropbox.reconcile import remove_unlisted
from app.connectors.sources.dropbox.sync_health import (
    DropboxAuthFailed,
    DropboxSyncIncomplete,
    SyncHealth,
)
from app.connectors.sources.dropbox.sync_notices import SyncNotices
from app.connectors.sources.dropbox.team_directory import read_team_members, withdrawn
from app.connectors.sources.dropbox.weblinks import HOME_URL, WebLinks
from app.connectors.sources.dropbox_individual.connector import (
    DropboxIndividualConnector,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.ee.permission_sync.dropbox_team import _reset_time
from app.models.entities import AppUser
from app.sources.client.dropbox.dropbox_ import DropboxResponse

LOGGER = logging.getLogger("dropbox-sync-fixes")
RESET = "ApiError('r', ListFolderContinueError('reset', None))"
AUTH = "AuthError('r', AuthError('invalid_access_token', None))"


def ok(data: object) -> DropboxResponse:
    return DropboxResponse(success=True, data=data)


def fail(error: str) -> DropboxResponse:
    return DropboxResponse(success=False, error=error)


def page(entries: list[Any], cursor: str = "c", has_more: bool = False) -> DropboxResponse:
    return ok(SimpleNamespace(entries=entries, cursor=cursor, has_more=has_more))


def file_entry(name: str = "a.txt", item_id: str = "id:a", path: str = "/a.txt", explicit: bool = False) -> FileMetadata:
    entry = FileMetadata(name=name, id=item_id, path_lower=path, path_display=path, rev="0123456789",
                         client_modified=datetime(2026, 1, 1, tzinfo=timezone.utc), server_modified=datetime(2026, 1, 1, tzinfo=timezone.utc), size=3)
    entry.has_explicit_shared_members = explicit
    return entry


@pytest.fixture
def team() -> DropboxConnector:
    with patch("app.connectors.sources.dropbox.connector.DropboxApp"):
        conn = DropboxConnector(LOGGER, MagicMock(), MagicMock(), AsyncMock(), "dbx-1", "team", "admin")
    conn.sync_filters = FilterCollection()
    conn.indexing_filters = FilterCollection()
    conn.data_source = AsyncMock()
    conn.data_source.sharing_create_shared_link_with_settings = AsyncMock(
        return_value=ok(SimpleNamespace(url="https://www.dropbox.com/scl/fi/x")))
    conn.dropbox_cursor_sync_point = AsyncMock()
    conn.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
    p = conn.data_entities_processor
    p.org_id = "org-1"
    p.skips_permission_writes = AsyncMock(return_value=False)
    for name in ("on_new_records", "on_record_metadata_update", "on_record_content_update", "on_new_app_users",
                 "withdraw_app_user_access", "on_record_group_deleted", "batch_update_nodes"):
        setattr(p, name, AsyncMock())
    p.get_record_by_external_id = AsyncMock(return_value=None)
    p.get_records_by_file_path = AsyncMock(return_value=[])
    p.get_file_record_by_id = AsyncMock(return_value=None)
    p.get_records_in_record_group = AsyncMock(return_value=[])
    p.on_records_deleted_cascade = AsyncMock(return_value={"success": True})
    return conn


# ---- small modules ------------------------------------------------------------------------------------

class TestErrors:
    def test_kinds(self) -> None:
        assert is_cursor_reset(RESET) and not is_cursor_reset("ApiError('r', LookupError('not_found'))")
        assert is_cursor_reset("GetTeamEventsContinueError('bad_cursor', None)")
        assert is_auth_error(AUTH) and not is_auth_error("InternalServerError(None, 503, 'x')")
        assert missing_scope("AuthError('missing_scope', TokenScopeError(required_scope='events.read'))") == "events.read"

    def test_reset_time_is_read_from_the_sdk_error(self) -> None:
        when = _reset_time("ApiError(None, GetTeamEventsContinueError('reset', datetime.datetime(2026, 10, 26, 8, 3, 2)))")
        assert (when.year, when.hour, when.minute, when.second) == (2026, 8, 3, 2)
        assert _reset_time("GetTeamEventsContinueError('bad_cursor', None)") is None


class TestSyncHealth:
    def test_complete_until_something_is_skipped(self) -> None:
        health = SyncHealth()
        assert health.complete
        health.relisted.append("x")
        assert health.complete, "a relisted folder was read in full"
        health.skipped("listing of x", "503")
        with pytest.raises(DropboxSyncIncomplete):
            health.raise_if_incomplete()

    def test_an_auth_failure_is_its_own_kind(self) -> None:
        health = SyncHealth()
        health.auth_failed(AUTH)
        with pytest.raises(DropboxAuthFailed):
            health.raise_if_incomplete()


class TestParentResolver:
    def test_parent_path(self) -> None:
        assert parent_path("/a.txt") is None
        assert parent_path("/d/e/f.txt") == "/d/e"

    @pytest.mark.asyncio
    async def test_listed_then_stored_then_dropbox(self) -> None:
        stored = AsyncMock(side_effect=lambda path: "id:stored" if path == "/s" else None)
        ask = AsyncMock(return_value="id:asked")
        resolver = ParentResolver(stored, ask)
        resolver.note_folder("/l", "id:listed")
        assert await resolver.parent_id("/l/x") == "id:listed"
        assert await resolver.parent_id("/s/x") == "id:stored"
        assert await resolver.parent_id("/q/x") == "id:asked"
        assert await resolver.parent_id("/q/y") == "id:asked"
        assert ask.await_count == 1, "a parent asked for once is remembered"


class TestHandleRecordUpdates:
    """A deleted update deletes the stored record by its key; nothing is written for it."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["team", "personal"])
    async def test_a_deleted_update_deletes_the_record(self, which: str, request: pytest.FixtureRequest) -> None:
        conn = request.getfixturevalue(which)
        p = conn.data_entities_processor
        p.get_record_by_external_id = AsyncMock(return_value=SimpleNamespace(id="rec-1"))
        p.on_record_deleted = AsyncMock()
        p.on_new_records = AsyncMock()
        await conn._handle_record_updates(SimpleNamespace(is_deleted=True, external_record_id="id:a", record=None))
        p.get_record_by_external_id.assert_awaited_once_with(conn.connector_id, "id:a")
        p.on_record_deleted.assert_awaited_once_with(record_id="rec-1")
        p.on_new_records.assert_not_awaited()


class TestOwedGroupRemovals:
    """A group cursor that resets keeps the removals still owed: Dropbox does not list their events again."""

    GROUP_EVENTS = "user_group_events"

    @pytest.mark.asyncio
    async def test_a_reset_keeps_them_with_the_new_cursor(self, team: DropboxConnector) -> None:
        stored = {"cursor": "old", "pendingGroupDeletes": ["g:gone"], "pendingGroupMemberReads": []}
        team.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value=stored)
        team.data_entities_processor.on_user_group_deleted = AsyncMock(side_effect=RuntimeError("db down"))
        team.data_source.team_log_get_events_continue = AsyncMock(return_value=fail(RESET))
        team.data_source.team_log_get_events = AsyncMock(return_value=ok(SimpleNamespace(cursor="new")))
        team._sync_user_groups = AsyncMock()

        await team._sync_group_changes_with_cursor()

        team.dropbox_cursor_sync_point.update_sync_point.assert_awaited_once()
        _, written = team.dropbox_cursor_sync_point.update_sync_point.await_args.args
        assert written == {"cursor": "new", "pendingGroupMemberReads": [], "pendingGroupDeletes": ["g:gone"]}
        team._sync_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_new_cursor_keeps_them_with_the_old_one(self, team: DropboxConnector) -> None:
        stored = {"cursor": "old", "pendingGroupDeletes": ["g:gone"]}
        team.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value=stored)
        team.data_entities_processor.on_user_group_deleted = AsyncMock(side_effect=RuntimeError("db down"))
        team.data_source.team_log_get_events_continue = AsyncMock(return_value=fail(RESET))
        team.data_source.team_log_get_events = AsyncMock(return_value=fail("InternalServerError 503"))
        team._sync_user_groups = AsyncMock()

        await team._sync_group_changes_with_cursor()

        written = team.dropbox_cursor_sync_point.update_sync_point.await_args.kwargs["sync_point_data"]
        assert written == {"cursor": "old", "pendingGroupMemberReads": [], "pendingGroupDeletes": ["g:gone"]}

    @pytest.mark.asyncio
    async def test_a_new_cursor_keeps_what_is_stored(self, team: DropboxConnector) -> None:
        team.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "old", "pendingGroupDeletes": ["g:gone"], "other": 1})
        team.data_source.team_log_get_events = AsyncMock(return_value=ok(SimpleNamespace(cursor="new")))

        assert await team._initialize_event_cursor("k", EventCategory.groups) is None

        team.dropbox_cursor_sync_point.update_sync_point.assert_awaited_once_with(
            "k", {"cursor": "new", "pendingGroupDeletes": ["g:gone"]})


class TestRunState:
    @pytest.mark.asyncio
    async def test_a_run_starts_with_no_fresh_or_moved_folders(self, team: DropboxConnector) -> None:
        """Left over, a fresh folder would be listed from the start on every later run of the same connector."""
        team._fresh_folders = {"ns:old"}
        team._moved_folders = [("id:f", "/a", "/b")]
        seen: dict[str, object] = {}

        async def steps() -> None:
            seen["fresh"], seen["moved"] = set(team._fresh_folders), list(team._moved_folders)

        team._sync_steps = steps
        await team.run_sync()
        assert seen == {"fresh": set(), "moved": []}


class TestAskParent:
    """Only Dropbox's not_found means "no parent": a failed lookup raises, so the entry is held, not put at the root."""

    @pytest.mark.asyncio
    async def test_team(self, team: DropboxConnector) -> None:
        team.data_source.files_get_metadata = AsyncMock(return_value=ok(SimpleNamespace(id="id:p")))
        assert await team._ask_parent_id("/p", "m1", None) == "id:p"
        team.data_source.files_get_metadata = AsyncMock(return_value=fail("ApiError: path/not_found/"))
        assert await team._ask_parent_id("/p", "m1", None) is None
        team.data_source.files_get_metadata = AsyncMock(return_value=fail("InternalServerError 503"))
        with pytest.raises(RuntimeError, match="could not read the parent folder /p"):
            await team._ask_parent_id("/p", "m1", None)

    @pytest.mark.asyncio
    async def test_personal(self, personal: DropboxIndividualConnector) -> None:
        personal.data_source.files_get_metadata = AsyncMock(return_value=ok(SimpleNamespace(id="id:p")))
        assert await personal._ask_parent_id("/p") == "id:p"
        personal.data_source.files_get_metadata = AsyncMock(return_value=fail("ApiError: path/not_found/"))
        assert await personal._ask_parent_id("/p") is None
        personal.data_source.files_get_metadata = AsyncMock(return_value=fail("RateLimitError"))
        with pytest.raises(RuntimeError, match="could not read the parent folder /p"):
            await personal._ask_parent_id("/p")


class TestWebLinks:
    @pytest.mark.asyncio
    async def test_a_stored_link_is_kept_without_a_call(self) -> None:
        source = AsyncMock()
        links = WebLinks(source, LOGGER)
        assert await links.link_for(file_entry(), stored="https://www.dropbox.com/scl/fi/old") == \
            "https://www.dropbox.com/scl/fi/old"
        source.sharing_create_shared_link_with_settings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_existing_link_is_read_when_dropbox_says_one_exists(self) -> None:
        source = AsyncMock()
        source.sharing_create_shared_link_with_settings = AsyncMock(return_value=fail(
            "ApiError(CreateSharedLinkWithSettingsError('shared_link_already_exists', None))"))
        source.sharing_list_shared_links = AsyncMock(return_value=ok(SimpleNamespace(
            links=[SimpleNamespace(url="https://www.dropbox.com/scl/fi/existing")])))
        assert await WebLinks(source, LOGGER).link_for(file_entry(), stored=None) == \
            "https://www.dropbox.com/scl/fi/existing"

    @pytest.mark.asyncio
    async def test_a_refused_link_cites_the_web_app(self) -> None:
        source = AsyncMock()
        source.sharing_create_shared_link_with_settings = AsyncMock(return_value=fail("settings_error"))
        assert (await WebLinks(source, LOGGER).link_for(file_entry(), stored=None)).startswith(HOME_URL)


class TestOwedDeletes:
    @pytest.mark.asyncio
    async def test_kept_with_the_sync_points(self) -> None:
        point = AsyncMock()
        point.read_sync_point = AsyncMock(return_value={"externalIds": ["id:1"]})
        owed = OwedDeletes(point)
        assert await owed.load() == ["id:1"]
        owed.settle([])
        owed.add(["id:2", "id:2"])
        await owed.save()
        assert point.update_sync_point.await_args.args[1] == {"externalIds": ["id:2"]}


class TestTeamDirectory:
    @staticmethod
    def member(member_id: str, status: str = "active") -> SimpleNamespace:
        return SimpleNamespace(profile=SimpleNamespace(team_member_id=member_id,
                                                       status=SimpleNamespace(is_active=lambda: status == "active")))

    @pytest.mark.asyncio
    async def test_every_page_is_read(self) -> None:
        source = AsyncMock()
        source.team_members_list = AsyncMock(return_value=ok(SimpleNamespace(
            members=[self.member("dbmid:a")], has_more=True, cursor="c1")))
        source.team_members_list_continue = AsyncMock(return_value=ok(SimpleNamespace(
            members=[self.member("dbmid:b", "suspended")], has_more=False, cursor="c2")))
        members = await read_team_members(source)
        assert members.complete and members.active_ids() == {"dbmid:a"}

    @pytest.mark.asyncio
    async def test_a_failed_page_leaves_the_list_incomplete_and_withdraws_nobody(self) -> None:
        source = AsyncMock()
        source.team_members_list = AsyncMock(return_value=ok(SimpleNamespace(
            members=[self.member("dbmid:a")], has_more=True, cursor="c1")))
        source.team_members_list_continue = AsyncMock(return_value=fail("503"))
        members = await read_team_members(source)
        stored = [AppUser(app_name=Connectors.DROPBOX, connector_id="c", source_user_id="dbmid:z", email="z@x.com",
                          full_name="Z")]
        assert not members.complete and withdrawn(stored, members) == []


class TestRepathAndReconcile:
    @pytest.mark.asyncio
    async def test_descendants_follow_a_moved_folder(self) -> None:
        processor = MagicMock()
        child = MagicMock(id="r-child", external_record_id="id:child")
        processor.get_records_by_parent = AsyncMock(side_effect=lambda c, parent: [child] if parent == "id:dir" else [])
        processor.get_file_record_by_id = AsyncMock(return_value=MagicMock(path="/docs/b.md"))
        processor.batch_update_nodes = AsyncMock()

        assert await repath_descendants(processor, "c", "id:dir", "/docs", "/documents", LOGGER) == (1, True)
        assert processor.batch_update_nodes.await_args.args[0] == [{"id": "r-child", "path": "/documents/b.md"}]

    @pytest.mark.asyncio
    async def test_a_failed_path_write_is_incomplete(self) -> None:
        processor = MagicMock()
        child = MagicMock(id="r-child", external_record_id="id:child")
        processor.get_records_by_parent = AsyncMock(side_effect=lambda c, parent: [child] if parent == "id:dir" else [])
        processor.get_file_record_by_id = AsyncMock(return_value=MagicMock(path="/docs/b.md"))
        processor.batch_update_nodes = AsyncMock(return_value=False)

        assert await repath_descendants(processor, "c", "id:dir", "/docs", "/documents", LOGGER) == (0, False)

    @pytest.mark.asyncio
    async def test_a_walk_that_raises_is_incomplete(self) -> None:
        """The moved folder is already stored under its new path: raising would leave the cursor before a move
        whose replay no longer shows it."""
        processor = MagicMock()
        processor.get_records_by_parent = AsyncMock(side_effect=RuntimeError("graph down"))

        assert await repath_descendants(processor, "c", "id:dir", "/docs", "/documents", LOGGER) == (0, False)

    @pytest.mark.asyncio
    async def test_a_walk_stopped_at_its_cap_says_so(self) -> None:
        processor = MagicMock()
        children = iter(range(10))
        processor.get_records_by_parent = AsyncMock(side_effect=lambda c, parent: [
            MagicMock(id=f"r{n}", external_record_id=f"id:{n}") for n in [next(children)]])
        processor.get_file_record_by_id = AsyncMock(return_value=MagicMock(path="/docs/x"))
        processor.batch_update_nodes = AsyncMock()

        with patch("app.connectors.sources.dropbox.paths._MAX_NODES", 3):
            moved, complete = await repath_descendants(processor, "c", "id:dir", "/docs", "/documents", LOGGER)

        assert (moved, complete) == (2, False)

    @pytest.mark.asyncio
    async def test_an_unlisted_record_goes_only_once_dropbox_says_it_is_gone(self) -> None:
        processor = MagicMock()
        processor.get_records_in_record_group = AsyncMock(return_value=[
            MagicMock(id="r1", external_record_id="id:listed"), MagicMock(id="r2", external_record_id="id:gone"),
            MagicMock(id="r3", external_record_id="id:moved")])
        processor.on_records_deleted_cascade = AsyncMock(return_value={"success": True})
        exists = AsyncMock(side_effect=lambda record: record.external_record_id == "id:moved")

        outcome = await remove_unlisted(processor, "c", "g", {"id:listed"}, exists, LOGGER)

        assert outcome.removed == ["r2"]


# ---- the team connector ---------------------------------------------------------------------------------

class TestTeamListing:
    @pytest.mark.asyncio
    async def test_a_reset_cursor_lists_again_and_removes_what_the_new_listing_lacks(self, team) -> None:
        team.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={"cursor": "old"})
        team.data_source.files_list_folder_continue = AsyncMock(return_value=fail(RESET))
        team.data_source.files_list_folder = AsyncMock(return_value=page([file_entry()]))
        team.data_entities_processor.get_records_in_record_group = AsyncMock(return_value=[
            MagicMock(id="r-old", external_record_id="id:old")])
        team._team_item_still_exists = AsyncMock(return_value=False)

        await team._sync_namespace("dbmid:a", "a@x.com", None, frozenset())

        assert team._health.relisted and team._health.complete
        team.data_entities_processor.on_records_deleted_cascade.assert_awaited_once_with(
            ["r-old"], "dbx-1", include_trashed_roots=True)

    @pytest.mark.asyncio
    async def test_a_full_sync_removes_what_was_deleted_before_it(self, team) -> None:
        """Live, 2026-10-29: 'Down under' was deleted, then a full sync listed the team folder from the start; the
        delete was never listed again, so its records stayed in All Records through every later sync."""
        team.data_source.files_list_folder = AsyncMock(return_value=page([file_entry()]))
        team.data_entities_processor.get_records_in_record_group = AsyncMock(return_value=[
            MagicMock(id="r-down", external_record_id="id:down"), MagicMock(id="r-a", external_record_id="id:a")])
        team._team_item_still_exists = AsyncMock(return_value=False)

        await team._sync_namespace("dbmid:a", "a@x.com", "tf-1", frozenset())

        team._team_item_still_exists.assert_awaited_once()
        team.data_entities_processor.on_records_deleted_cascade.assert_awaited_once_with(
            ["r-down"], "dbx-1", include_trashed_roots=True)

    @pytest.mark.asyncio
    async def test_the_saved_cursor_stays_before_a_page_with_a_failed_entry(self, team) -> None:
        """Dropbox lists a change once: a cursor saved past a failed entry would lose it. The pages after it are
        still written, and the next run reads again from the last page that went through whole."""
        good, bad, later = file_entry("a.txt", "id:a", "/a.txt"), file_entry("b.txt", "id:b", "/b.txt"), \
            file_entry("c.txt", "id:c", "/c.txt")
        team.data_source.files_list_folder = AsyncMock(return_value=page([good], cursor="c1", has_more=True))
        team.data_source.files_list_folder_continue = AsyncMock(side_effect=[
            page([bad], cursor="c2", has_more=True), page([later], cursor="c3")])
        real = team._process_dropbox_entry

        async def process(entry: FileMetadata, *args: object, **kwargs: object) -> RecordUpdate | None:
            if entry.id == "id:b":
                team._health.skipped("entry /b.txt", "boom")
                return None
            return await real(entry, *args, **kwargs)
        team._process_dropbox_entry = process

        await team._sync_namespace("dbmid:a", "a@x.com", None, frozenset())

        saved = [call.kwargs["sync_point_data"]["cursor"]
                 for call in team.dropbox_cursor_sync_point.update_sync_point.await_args_list]
        assert saved == ["c1"]
        written = [r.external_record_id for call in team.data_entities_processor.on_new_records.await_args_list
                   for r, _ in call.args[0]]
        assert written == ["id:a", "id:c"]

    @pytest.mark.asyncio
    async def test_a_move_too_big_to_walk_lists_the_folder_again_from_the_start(self, team) -> None:
        """Saved at once: a later page that fails must not leave the cursor before the move, whose replay would
        no longer show it (the folder's stored path is already the new one)."""
        team.data_source.files_list_folder = AsyncMock(return_value=page([file_entry()], cursor="c1", has_more=True))
        team.data_source.files_list_folder_continue = AsyncMock(return_value=fail("InternalServerError(None, 503, 'x')"))
        team._repath_moved_folders = AsyncMock(return_value=False)

        await team._sync_namespace("dbmid:a", "a@x.com", None, frozenset())

        saved = [call.kwargs["sync_point_data"]["cursor"]
                 for call in team.dropbox_cursor_sync_point.update_sync_point.await_args_list]
        assert saved == [None]

    @pytest.mark.asyncio
    async def test_paths_not_all_rewritten_leave_the_run_incomplete(self, team) -> None:
        team._moved_folders = [("id:dir", "/docs", "/documents")]
        with patch("app.connectors.sources.dropbox.connector.repath_descendants", AsyncMock(return_value=(0, False))):
            assert await team._repath_moved_folders() is False
        assert not team._health.complete

    @pytest.mark.asyncio
    async def test_owed_deletes_are_saved_when_the_run_stops_part_way(self, team) -> None:
        team._owed.load = AsyncMock(return_value=[])
        team._owed.save = AsyncMock()
        team._process_users_in_batches = AsyncMock(side_effect=RuntimeError("graph down"))
        steps = ("_register_authenticated_identity", "_sync_members", "_initialize_event_cursor",
                 "_sync_user_groups", "sync_personal_record_groups")
        for name in steps:
            setattr(team, name, AsyncMock(return_value=[]))

        with patch("app.connectors.sources.dropbox.connector.load_connector_filters",
                   AsyncMock(return_value=(FilterCollection(), FilterCollection()))), pytest.raises(RuntimeError):
            await team._sync_steps()

        team._owed.save.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unlicensed", [True, False])
    async def test_a_first_group_sync_takes_no_cursor_while_unlicensed(self, team, unlicensed: bool) -> None:
        """Unlicensed, no group is read; a cursor taken anyway would make the first licensed run read only the changes
        after it, so the groups that already exist would never be stored."""
        team.data_entities_processor.skips_permission_writes = AsyncMock(return_value=unlicensed)
        team._owed.load = AsyncMock(return_value=[])
        team._owed.save = AsyncMock()
        team._process_users_in_batches = AsyncMock(side_effect=RuntimeError("stop after the group step"))
        for name in ("_register_authenticated_identity", "_sync_members", "_initialize_event_cursor",
                     "_sync_user_groups", "sync_personal_record_groups"):
            setattr(team, name, AsyncMock(return_value=[]))

        with patch("app.connectors.sources.dropbox.connector.load_connector_filters",
                   AsyncMock(return_value=(FilterCollection(), FilterCollection()))), pytest.raises(RuntimeError):
            await team._sync_steps()

        group_cursor = [c for c in team._initialize_event_cursor.await_args_list if c.args[1] == EventCategory.groups]
        assert len(group_cursor) == (0 if unlicensed else 1)
        assert team._sync_user_groups.await_count == (0 if unlicensed else 1)

    @pytest.mark.asyncio
    async def test_a_failed_page_is_recorded_and_nothing_is_reconciled(self, team) -> None:
        team.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={"cursor": "old"})
        team.data_source.files_list_folder_continue = AsyncMock(side_effect=[fail(RESET)])
        team.data_source.files_list_folder = AsyncMock(return_value=fail("InternalServerError(None, 503, 'x')"))

        await team._sync_namespace("dbmid:a", "a@x.com", None, frozenset())

        assert not team._health.complete
        team.data_entities_processor.get_records_in_record_group.assert_not_awaited()
        team.dropbox_cursor_sync_point.update_sync_point.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_auth_refusal_ends_the_run(self, team) -> None:
        team.data_source.files_list_folder = AsyncMock(return_value=fail(AUTH))
        await team._sync_namespace("dbmid:a", "a@x.com", None, frozenset())
        assert team._health.auth_error

    @pytest.mark.asyncio
    async def test_a_page_is_written_before_its_cursor_is_saved(self, team) -> None:
        order = []
        team.data_entities_processor.on_new_records = AsyncMock(side_effect=lambda *_: order.append("write"))
        team.dropbox_cursor_sync_point.update_sync_point = AsyncMock(side_effect=lambda *_a, **_k: order.append("cursor"))
        team.data_source.files_list_folder = AsyncMock(return_value=page([file_entry()]))

        await team._sync_namespace("dbmid:a", "a@x.com", None, frozenset())

        assert order == ["write", "cursor"]

    @pytest.mark.asyncio
    async def test_an_older_versions_cursor_lists_again_once(self, team) -> None:
        async def read(key: str) -> dict[str, str]:
            return {"cursor": "v2"} if "users_v2" in key else {}
        team.dropbox_cursor_sync_point.read_sync_point = AsyncMock(side_effect=read)
        team.data_source.files_list_folder = AsyncMock(return_value=page([file_entry()]))

        await team._sync_namespace("dbmid:a", "a@x.com", None, frozenset())

        team.data_source.files_list_folder.assert_awaited_once()
        team.data_entities_processor.get_records_in_record_group.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_file_without_explicit_members_costs_no_member_call(self, team) -> None:
        team.data_source.files_list_folder = AsyncMock(return_value=page([file_entry(explicit=False)]))
        await team._sync_namespace("dbmid:a", "a@x.com", None, frozenset())
        team.data_source.sharing_list_file_members.assert_not_awaited()
        team.data_source.files_get_temporary_link.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_child_listed_before_its_folder_gets_the_folders_id_without_a_call(self, team) -> None:
        folder = FolderMetadata(name="d", id="id:d", path_lower="/d", path_display="/d")
        team.data_source.files_list_folder = AsyncMock(return_value=page([file_entry(path="/d/a.txt"), folder]))

        await team._sync_namespace("dbmid:a", "a@x.com", None, frozenset())

        written = team.data_entities_processor.on_new_records.await_args.args[0]
        assert written[0][0].parent_external_record_id == "id:d"
        team.data_source.files_get_metadata.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_delete_dropbox_cannot_confirm_is_owed(self, team) -> None:
        team.data_entities_processor.get_records_by_file_path = AsyncMock(return_value=[
            MagicMock(id="r1", external_record_id="id:a", external_record_group_id="dbmid:a")])
        team._team_item_still_exists = AsyncMock(return_value=None)
        team.data_source.files_list_folder = AsyncMock(return_value=page([
            DeletedMetadata(name="a.txt", path_lower="/a.txt", path_display="/a.txt")]))

        await team._sync_namespace("dbmid:a", "a@x.com", None, frozenset())

        assert team._owed.ids == ["id:a"]
        team.data_entities_processor.on_records_deleted_cascade.assert_not_awaited()


class TestTeamMembers:
    @pytest.mark.asyncio
    async def test_suspended_withdrawn_and_left_member_loses_their_home(self, team) -> None:
        def member(member_id: str, email: str, status: str) -> SimpleNamespace:
            return SimpleNamespace(profile=SimpleNamespace(
                team_member_id=member_id, email=email, name=SimpleNamespace(display_name=email),
                status=SimpleNamespace(_tag=status, is_active=lambda: status == "active")),
                role=SimpleNamespace(_tag="member_only"))
        team.data_source.team_members_list = AsyncMock(return_value=ok(SimpleNamespace(members=[
            member("dbmid:a", "a@x.com", "active"), member("dbmid:s", "s@x.com", "suspended")], has_more=False,
            cursor="c")))
        stored = [AppUser(app_name=Connectors.DROPBOX, connector_id="dbx-1", source_user_id=mid, email=f"{k}@x.com",
                          full_name=k) for mid, k in (("dbmid:a", "a"), ("dbmid:s", "s"), ("dbmid:gone", "g"))]
        team.data_entities_processor.get_all_app_users = AsyncMock(return_value=stored)

        await team._sync_members()

        withdrawn_ids = [u.source_user_id for u in team.data_entities_processor.withdraw_app_user_access.await_args.args[1]]
        assert withdrawn_ids == ["dbmid:s", "dbmid:gone"]
        team.data_entities_processor.on_record_group_deleted.assert_awaited_once_with(
            external_group_id="dbmid:gone", connector_id="dbx-1", trash_live_records=True)


class TestTeamRun:
    @pytest.mark.asyncio
    async def test_a_run_that_skipped_part_of_dropbox_fails_notifies_and_removes_nothing(self, team) -> None:
        async def steps() -> None:
            team._health.skipped("listing of x", "503")
        team._sync_steps = steps
        team.notify = AsyncMock()

        with pytest.raises(DropboxSyncIncomplete):
            await team.run_sync()

        assert team.full_sync_coverage().complete is False
        team.notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_refused_token_fails_the_run_with_an_auth_notice(self, team) -> None:
        async def steps() -> None:
            team._health.auth_failed(AUTH)
        team._sync_steps = steps
        team.notify = AsyncMock()

        with pytest.raises(DropboxAuthFailed):
            await team.run_sync()

        assert "AUTH" in str(team.notify.await_args.kwargs["type"])

    @pytest.mark.asyncio
    async def test_a_clean_run_says_nothing(self, team) -> None:
        team._sync_steps = AsyncMock()
        team.notify = AsyncMock()
        await team.run_sync()
        team.notify.assert_not_awaited()
        assert team.full_sync_coverage().complete

    @pytest.mark.asyncio
    async def test_folders_gone_for_every_member_are_deleted_only_on_a_complete_answer(self, team) -> None:
        team.data_entities_processor.get_nodes_by_filters = AsyncMock(return_value=[
            {"externalGroupId": "sf-kept", "description": "Shared Folder"},
            {"externalGroupId": "sf-gone", "description": "Team Folder"},
            {"externalGroupId": "dbmid:a", "description": "Personal Folder"}])
        team._plan_complete = False
        await team._drop_vanished_shared_folders({"sf-kept": MagicMock()})
        team.data_entities_processor.on_record_group_deleted.assert_not_awaited()

        team._plan_complete = True
        await team._drop_vanished_shared_folders({"sf-kept": MagicMock()})
        team.data_entities_processor.on_record_group_deleted.assert_awaited_once_with(
            external_group_id="sf-gone", connector_id="dbx-1", trash_live_records=True)


class TestNotices:
    @pytest.mark.asyncio
    async def test_a_missing_scope_names_it(self) -> None:
        connector = MagicMock()
        connector.notify = AsyncMock()
        connector._notification_title = lambda event: f"Dropbox (x): {event}"
        health = SyncHealth()
        health.auth_failed("AuthError('missing_scope', TokenScopeError(required_scope='team_data.member'))")

        await SyncNotices(connector).report(health)

        assert "team_data.member" in connector.notify.await_args.kwargs["message"]
        assert connector.notify.await_args.kwargs["title"].startswith("Dropbox (x)")


# ---- what a full sync of the team unlinks when it ends ----------------------------------------------------

def _team_member(member_id: str, email: str, status: str) -> SimpleNamespace:
    return SimpleNamespace(profile=SimpleNamespace(
        team_member_id=member_id, email=email, name=SimpleNamespace(display_name=email),
        status=SimpleNamespace(_tag=status, is_active=lambda: status == "active")),
        role=SimpleNamespace(_tag="member_only"))


def _shared_folder_entry(folder_id: str, name: str, path: str | None, access: str = "owner") -> SimpleNamespace:
    return SimpleNamespace(shared_folder_id=folder_id, name=name, parent_shared_folder_id=None,
                           access_inheritance=SimpleNamespace(_tag="inherit"), is_team_folder=False,
                           is_inside_team_folder=False, access_type=SimpleNamespace(_tag=access), path_lower=path)


def _deleted_groups(world: "_DropboxTeam") -> list[str]:
    return sorted(c.kwargs["external_group_id"]
                  for c in world.conn.data_entities_processor.on_record_group_deleted.await_args_list)


def _suspended_holds_plans(world: "_DropboxTeam") -> "_DropboxTeam":
    """Shared folder sf:plans, mounted and listed only by suspended member s, who owns it."""
    world.store_group("sf:plans", "Shared Folder")
    world.store_record("id:p1", "p1.txt", "sf:plans")
    world.at_source |= {"id:p1"}
    world.shared_lists["dbmid:s"] = [_shared_folder_entry("sf:plans", "plans", "/plans")]
    world.listings["sf:plans"] = [file_entry("p1.txt", "id:p1", "/plans/p1.txt")]
    world.grants["sf:plans"] = ["s@x.com", "ext@y.com"]
    world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
    return world


class _DropboxTeam:
    """A Dropbox team at the source, the connector's nodes as an earlier sync stored them, and one full sync.

    ``full_sync`` runs ``run_sync`` as a full sync does (no sync points) and then the runner's own end-of-sync
    step, ``_unlink_what_full_sync_missed``, against a graph that unlinks every stored node the run did not note
    as seen. A node the connector deleted itself is counted as removed too. Nodes are named ``record:<id>`` and
    ``recordGroup:<id>``.
    """

    def __init__(self, conn: DropboxConnector) -> None:
        self.conn = conn
        self.members: list[SimpleNamespace] = []
        # member id -> the shared folders sharing/list_folders gives them
        self.shared_lists: dict[str, list[SimpleNamespace]] = {}
        # namespace (member id for a home, shared folder id) -> entries listed in it
        self.listings: dict[str, list[FileMetadata]] = {}
        self.at_source: set[str] = set()
        self.records: dict[str, SimpleNamespace] = {}
        self.record_groups: dict[str, str] = {}
        # record group external id -> the external id of the stored group it sits in
        self.group_parents: dict[str, str] = {}
        # record group external id -> emails of the users the stored graph grants it to
        self.grants: dict[str, list[str]] = {}
        # record group external id -> ids of the stored user groups the graph grants it to (members in ``grants``)
        self.group_grants: dict[str, list[str]] = {}
        # stored groups with a parent that do not inherit from it
        self.no_inherit: set[str] = set()
        self.seen = FullSyncSeen()
        self.deleted: set[str] = set()
        self._wire()

    def store_record(self, item_id: str, name: str, group: str) -> None:
        self.records[item_id] = SimpleNamespace(
            id=f"r-{item_id}", external_record_id=item_id, external_record_group_id=group, record_name=name,
            parent_external_record_id=None, path=f"/{name}", external_revision_id="0123456789",
            weburl="https://www.dropbox.com/home", source_updated_at=1, updated_at=1, source_created_at=1,
            created_at=1)

    def store_group(self, external_id: str, description: str) -> None:
        self.record_groups[external_id] = description

    def _wire(self) -> None:
        conn, p, ds = self.conn, self.conn.data_entities_processor, self.conn.data_source
        records = CollectionNames.RECORDS.value
        groups = CollectionNames.RECORD_GROUPS.value

        def note(collection: str, node_id: str) -> None:
            self.seen.add(collection, node_id)

        async def on_new_records(batch) -> None:
            for record, _ in batch:
                self.records[record.external_record_id] = record
                note(records, record.id)
                note(groups, record.external_record_group_id)

        async def on_update(record) -> None:
            note(records, record.id)
            note(groups, record.external_record_group_id)

        async def on_new_record_groups(batch) -> None:
            for group, _ in batch:
                self.record_groups.setdefault(group.external_group_id, group.description)
                note(groups, group.external_group_id)

        async def on_record_group_deleted(external_group_id, connector_id, **_) -> bool:
            self.record_groups.pop(external_group_id, None)
            self.deleted.add(f"recordGroup:{external_group_id}")
            for item_id, record in list(self.records.items()):
                if record.external_record_group_id == external_group_id:
                    del self.records[item_id]
                    self.deleted.add(f"record:{item_id}")
            return True

        async def on_records_deleted_cascade(record_ids, _connector_id, **_) -> dict:
            for item_id, record in list(self.records.items()):
                if record.id in record_ids:
                    del self.records[item_id]
                    self.deleted.add(f"record:{item_id}")
            return {"success": True}

        async def get_record_by_external_id(_connector_id, external_id) -> SimpleNamespace | None:
            return self.records.get(external_id)

        async def get_records_in_record_group(_connector_id, group_id, limit, after=None) -> list:
            page = sorted((r for r in self.records.values() if r.external_record_group_id == group_id),
                          key=lambda r: r.id)
            if after is not None:
                page = [r for r in page if r.id > after]
            return page[:limit]

        async def get_record_group_by_external_id(_connector_id, external_id) -> SimpleNamespace | None:
            if external_id not in self.record_groups:
                return None
            return SimpleNamespace(id=external_id, parent_external_group_id=self.group_parents.get(external_id))

        async def get_nodes_by_filters(_collection, _filters, *_args, **_kwargs) -> list[dict]:
            return [{"externalGroupId": gid, "description": desc, "groupName": gid}
                    for gid, desc in self.record_groups.items()]

        async def get_users_with_permission_to_node(node_id, _collection, **_) -> list[SimpleNamespace]:
            return [SimpleNamespace(email=email) for email in self.grants.get(node_id, [])]

        async def get_groups_with_permission_to_node(node_id, _collection, **_) -> list[SimpleNamespace]:
            return [SimpleNamespace(id=group_id) for group_id in self.group_grants.get(node_id, [])]

        async def inheritance_when_unreadable(_collection, stored_id) -> bool:
            return stored_id in self.group_parents and stored_id not in self.no_inherit

        def note_kept_records(_connector_id, record_ids) -> None:
            for record_id in record_ids:
                note(records, record_id)

        async def note_kept_record_group(_connector_id, external_id) -> None:
            note(groups, external_id)

        p.on_new_records = AsyncMock(side_effect=on_new_records)
        p.on_record_metadata_update = AsyncMock(side_effect=on_update)
        p.on_record_content_update = AsyncMock(side_effect=on_update)
        p.on_new_record_groups = AsyncMock(side_effect=on_new_record_groups)
        p.on_record_group_deleted = AsyncMock(side_effect=on_record_group_deleted)
        p.on_records_deleted_cascade = AsyncMock(side_effect=on_records_deleted_cascade)
        p.get_record_by_external_id = AsyncMock(side_effect=get_record_by_external_id)
        p.get_records_in_record_group = AsyncMock(side_effect=get_records_in_record_group)
        p.get_record_group_by_external_id = AsyncMock(side_effect=get_record_group_by_external_id)
        p.get_nodes_by_filters = AsyncMock(side_effect=get_nodes_by_filters)
        p.note_kept_records = MagicMock(side_effect=note_kept_records)
        p.note_kept_record_group = AsyncMock(side_effect=note_kept_record_group)
        p.get_all_active_users = AsyncMock(side_effect=lambda: [
            SimpleNamespace(email=m.profile.email) for m in self.members])
        p.get_all_app_users = AsyncMock(side_effect=lambda *_a, **_k: [
            AppUser(app_name=Connectors.DROPBOX, connector_id="dbx-1", source_user_id=m.profile.team_member_id,
                    email=m.profile.email, full_name=m.profile.email) for m in self.members])
        p.get_users_with_permission_to_node = AsyncMock(side_effect=get_users_with_permission_to_node)
        p.get_groups_with_permission_to_node = AsyncMock(side_effect=get_groups_with_permission_to_node)
        p.inheritance_when_unreadable = AsyncMock(side_effect=inheritance_when_unreadable)
        p.get_groups_with_access_to_record = AsyncMock(return_value=[])
        p.on_external_app_users = AsyncMock()
        p.reap_external_app_users = AsyncMock()
        p.finish_full_sync_memberships = AsyncMock()
        p.full_sync_running = MagicMock(return_value=False)

        def active(member_id: str) -> bool:
            return any(m.profile.team_member_id == member_id and m.profile.status._tag == "active"
                       for m in self.members)

        async def list_folder(path="", team_member_id=None, team_folder_id=None, **_) -> DropboxResponse:
            if not active(team_member_id):
                return fail("AuthError('r', AuthError('user_suspended', None))")
            return page(list(self.listings.get(team_folder_id or team_member_id, [])), cursor="end")

        async def get_metadata(path, team_member_id=None, team_folder_id=None, **_) -> DropboxResponse:
            if path in self.at_source:
                return ok(SimpleNamespace(id=path))
            return fail("ApiError('r', GetMetadataError('path', LookupError('not_found', None)))")

        async def list_shared(team_member_id=None, **_) -> DropboxResponse:
            return ok(SimpleNamespace(entries=list(self.shared_lists.get(team_member_id, [])), cursor=None))

        ds.team_token_get_authenticated_admin = AsyncMock(return_value=fail("not an admin token"))
        ds.team_members_list = AsyncMock(side_effect=lambda *a, **k: ok(SimpleNamespace(
            members=list(self.members), has_more=False, cursor="c")))
        ds.team_groups_list = AsyncMock(return_value=ok(SimpleNamespace(groups=[], has_more=False, cursor=None)))
        ds.team_log_get_events = AsyncMock(return_value=ok(SimpleNamespace(cursor="ev", events=[], has_more=False)))
        ds.sharing_list_folders = AsyncMock(side_effect=list_shared)
        ds.sharing_list_folder_members = AsyncMock(return_value=ok(SimpleNamespace(
            users=[], groups=[], invitees=[], cursor=None)))
        ds.files_list_folder = AsyncMock(side_effect=list_folder)
        ds.files_get_metadata = AsyncMock(side_effect=get_metadata)
        conn.register_authenticated_source_user = AsyncMock()
        conn.notify = AsyncMock()
        conn._owed = OwedDeletes(conn.dropbox_cursor_sync_point)

    def _stored(self) -> dict[str, dict[str, str]]:
        return {
            CollectionNames.RECORDS.value: {r.id: f"record:{item_id}" for item_id, r in self.records.items()},
            CollectionNames.RECORD_GROUPS.value: {gid: f"recordGroup:{gid}" for gid in self.record_groups},
        }

    async def sync(self, *, full: bool) -> None:
        """One ``run_sync``, run as part of a full sync or not."""
        self.seen = FullSyncSeen()
        self.deleted = set()
        filters = (FilterCollection(), FilterCollection())
        running = self.conn.data_entities_processor.full_sync_running
        running.return_value = full
        try:
            with patch("app.connectors.sources.dropbox.connector.load_connector_filters",
                       AsyncMock(return_value=filters)):
                await self.conn.run_sync()
        finally:
            running.return_value = False

    async def full_sync(self) -> tuple[bool, set[str]]:
        """Returns (whether no further full sync is owed, the nodes the sync and the unlink after it removed)."""
        await self.sync(full=True)
        return await self.unlink()

    async def unlink(self) -> tuple[bool, set[str]]:
        """The runner's end-of-sync step after a full sync: (no further full sync owed, nodes removed)."""
        stored = self._stored()
        unlinked: set[str] = set()

        async def unlink(_connector_id, seen_by_collection, **_) -> tuple[int, bool]:
            for collection, names in stored.items():
                seen_ids = seen_by_collection.get(collection) or set()
                unlinked.update(name for node_id, name in names.items() if node_id not in seen_ids)
            return len(unlinked), True

        graph_provider = AsyncMock()
        graph_provider.unlink_nodes_missed_by_full_sync = AsyncMock(side_effect=unlink)
        done = await sync_runner._unlink_what_full_sync_missed(
            self.conn, "dbx-1", graph_provider, LOGGER, self.seen
        )
        return done, unlinked | self.deleted


@pytest.fixture
def dropbox_team(team) -> _DropboxTeam:
    world = _DropboxTeam(team)
    world.members = [_team_member("dbmid:a", "a@x.com", "active"), _team_member("dbmid:s", "s@x.com", "active")]
    world.store_group("dbmid:a", "Personal Folder")
    world.store_group("dbmid:s", "Personal Folder")
    world.store_record("id:a1", "a1.txt", "dbmid:a")
    world.store_record("id:gone", "gone.txt", "dbmid:a")
    world.listings["dbmid:a"] = [file_entry("a1.txt", "id:a1", "/a1.txt")]
    world.at_source = {"id:a1"}
    return world


class TestTeamFullSyncUnlink:
    """A full sync unlinks what it did not see: only what is gone from Dropbox, never what is still there.

    ``removed - GONE`` is what was wrongly removed (still in Dropbox); ``removed & GONE`` what was rightly removed.
    """

    GONE = {"record:id:gone"}

    @pytest.mark.asyncio
    async def test_a_suspended_members_home_keeps_its_links(self, dropbox_team) -> None:
        """A suspended member's files stay in Dropbox, and what they shared stays shared (Dropbox Help:
        "sharing relationships remain intact"), but Dropbox lists nothing as them."""
        world = dropbox_team
        world.store_record("id:s1", "s1.txt", "dbmid:s")
        world.store_record("id:s2", "shared-with-a.txt", "dbmid:s")
        world.at_source |= {"id:s1", "id:s2"}
        world.listings["dbmid:s"] = [file_entry("s1.txt", "id:s1", "/s1.txt"),
                                     file_entry("shared-with-a.txt", "id:s2", "/shared-with-a.txt", explicit=True)]
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")

        done, removed = await world.full_sync()

        assert (sorted(removed - self.GONE), sorted(removed & self.GONE)) == ([], ["record:id:gone"])
        assert done is True

    @pytest.mark.asyncio
    async def test_a_suspended_members_home_is_kept_in_full_across_pages(self, dropbox_team) -> None:
        world = dropbox_team
        for n in (1, 2, 3):
            world.store_record(f"id:s{n}", f"s{n}.txt", "dbmid:s")
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
        kept = world.conn.data_entities_processor.note_kept_records

        with patch("app.connectors.sources.dropbox.connector._KEPT_RECORDS_PAGE", 2):
            done, removed = await world.full_sync()

        assert (sorted(removed - self.GONE), sorted(removed & self.GONE)) == ([], ["record:id:gone"])
        assert done is True
        assert [c.args[1] for c in kept.call_args_list] == [["r-id:s1", "r-id:s2"], ["r-id:s3"]]

    @pytest.mark.asyncio
    async def test_a_suspended_member_with_no_display_name_keeps_their_home(self, dropbox_team) -> None:
        """Their personal record group is not written again, so only keeping it saves its records' links."""
        world = dropbox_team
        world.store_record("id:s1", "s1.txt", "dbmid:s")
        member = _team_member("dbmid:s", "s@x.com", "suspended")
        member.profile.name.display_name = ""
        world.members[1] = member

        done, removed = await world.full_sync()

        assert (sorted(removed - self.GONE), sorted(removed & self.GONE)) == ([], ["record:id:gone"])
        assert done is True

    @pytest.mark.asyncio
    async def test_a_suspended_home_that_cannot_be_read_back_removes_nothing(self, dropbox_team, monkeypatch) -> None:
        monkeypatch.setattr(sync_runner, "_INCOMPLETE_FULL_SYNC_RETRYING", set())
        world = dropbox_team
        world.store_record("id:s1", "s1.txt", "dbmid:s")
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
        p = world.conn.data_entities_processor
        read = p.get_records_in_record_group.side_effect

        async def failing(connector_id, group_id, *args, **kwargs) -> list:
            if group_id == "dbmid:s":
                raise RuntimeError("graph unavailable")
            return await read(connector_id, group_id, *args, **kwargs)

        p.get_records_in_record_group = AsyncMock(side_effect=failing)

        with pytest.raises(DropboxSyncIncomplete):
            await world.sync(full=True)
        done, removed = await world.unlink()

        # gone.txt is deleted by the run itself, which Dropbox told it is not_found; nothing else is removed.
        assert (done, sorted(removed)) == (False, ["record:id:gone"])
        assert world.deleted == {"record:id:gone"}

    @pytest.mark.asyncio
    async def test_a_run_outside_a_full_sync_does_not_read_a_suspended_home(self, dropbox_team) -> None:
        world = dropbox_team
        world.store_record("id:s1", "s1.txt", "dbmid:s")
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
        p = world.conn.data_entities_processor

        await world.sync(full=False)

        assert all(c.args[1] != "dbmid:s" for c in p.get_records_in_record_group.await_args_list)
        p.note_kept_records.assert_not_called()
        p.note_kept_record_group.assert_not_awaited()
        assert "record:id:s1" not in world.deleted

    @pytest.mark.asyncio
    async def test_a_suspended_member_gains_no_access(self, dropbox_team) -> None:
        world = dropbox_team
        world.store_record("id:s1", "s1.txt", "dbmid:s")
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
        p, ds = world.conn.data_entities_processor, world.conn.data_source

        await world.full_sync()

        withdrawn_ids = [u.source_user_id for c in p.withdraw_app_user_access.await_args_list for u in c.args[1]]
        assert withdrawn_ids == ["dbmid:s"]
        for call in (*ds.files_list_folder.await_args_list, *ds.sharing_list_folders.await_args_list):
            assert call.kwargs.get("team_member_id") != "dbmid:s"

    @pytest.mark.asyncio
    async def test_a_member_active_again_is_listed_not_kept(self, dropbox_team) -> None:
        """An active member's home is listed, so what is no longer in it goes."""
        world = dropbox_team
        world.store_record("id:s9", "s9.txt", "dbmid:s")
        world.conn._keep_stored_group = AsyncMock(wraps=world.conn._keep_stored_group)
        gone = self.GONE | {"record:id:s9"}

        done, removed = await world.full_sync()

        assert (sorted(removed - gone), sorted(removed & gone)) == ([], ["record:id:gone", "record:id:s9"])
        assert done is True
        world.conn._keep_stored_group.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_folder_a_suspended_member_shared_outside_the_team_is_kept(self, dropbox_team) -> None:
        """Only the suspended owner lists the folder; the people outside the team it is shared with still open it."""
        world = dropbox_team
        world.store_group("sf:plans", "Shared Folder")
        world.store_record("id:p1", "p1.txt", "sf:plans")
        world.at_source |= {"id:p1"}
        world.shared_lists["dbmid:s"] = [_shared_folder_entry("sf:plans", "plans", "/plans")]
        world.listings["sf:plans"] = [file_entry("p1.txt", "id:p1", "/plans/p1.txt")]
        world.grants["sf:plans"] = ["s@x.com", "ext@y.com"]
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")

        done, removed = await world.full_sync()

        assert (sorted(removed - self.GONE), sorted(removed & self.GONE)) == ([], ["record:id:gone"])
        assert done is True
        assert _deleted_groups(world) == []

    @pytest.mark.asyncio
    async def test_a_folder_only_a_suspended_member_has_in_their_dropbox_is_kept(self, dropbox_team) -> None:
        """An active member can open the folder but never added it to their Dropbox; the suspended owner has it."""
        world = dropbox_team
        world.store_group("sf:plans", "Shared Folder")
        world.store_record("id:p1", "p1.txt", "sf:plans")
        world.at_source |= {"id:p1"}
        world.shared_lists["dbmid:s"] = [_shared_folder_entry("sf:plans", "plans", "/plans")]
        world.shared_lists["dbmid:a"] = [_shared_folder_entry("sf:plans", "plans", None, "editor")]
        world.listings["sf:plans"] = [file_entry("p1.txt", "id:p1", "/plans/p1.txt")]
        world.grants["sf:plans"] = ["s@x.com", "a@x.com"]
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")

        done, removed = await world.full_sync()

        assert (sorted(removed - self.GONE), sorted(removed & self.GONE)) == ([], ["record:id:gone"])
        assert done is True
        assert _deleted_groups(world) == []

    @pytest.mark.asyncio
    async def test_a_kept_folder_is_not_listed_as_anyone(self, dropbox_team) -> None:
        world = _suspended_holds_plans(dropbox_team)
        world.shared_lists["dbmid:a"] = [_shared_folder_entry("sf:plans", "plans", None, "editor")]
        ds = world.conn.data_source

        await world.full_sync()

        assert all(c.kwargs.get("team_folder_id") != "sf:plans" for c in ds.files_list_folder.await_args_list)
        assert all(c.kwargs.get("shared_folder_id") != "sf:plans" and "sf:plans" not in c.args
                   for c in ds.sharing_list_folder_members.await_args_list)
        assert "sf:plans" in world.conn._kept_folders

    @pytest.mark.asyncio
    async def test_a_folder_held_by_a_suspended_member_survives_a_run_outside_a_full_sync(self, dropbox_team) -> None:
        world = _suspended_holds_plans(dropbox_team)
        p = world.conn.data_entities_processor

        await world.sync(full=False)

        assert _deleted_groups(world) == []
        p.note_kept_records.assert_not_called()
        p.note_kept_record_group.assert_not_awaited()
        assert world.conn.full_sync_coverage().complete

    @pytest.mark.asyncio
    async def test_a_deleted_folder_no_suspended_member_is_in_still_goes(self, dropbox_team) -> None:
        world = dropbox_team
        world.store_group("sf:old", "Shared Folder")
        world.store_record("id:o1", "o1.txt", "sf:old")
        world.grants["sf:old"] = ["a@x.com"]
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
        gone = self.GONE | {"recordGroup:sf:old", "record:id:o1"}

        done, removed = await world.full_sync()

        assert (sorted(removed - gone), sorted(removed & gone)) == (
            [], ["record:id:gone", "record:id:o1", "recordGroup:sf:old"])
        assert done is True

    @pytest.mark.asyncio
    async def test_an_unmounted_folder_no_suspended_member_is_in_still_goes(self, dropbox_team) -> None:
        world = dropbox_team
        world.store_group("sf:del", "Shared Folder")
        world.store_record("id:d1", "d1.txt", "sf:del")
        world.shared_lists["dbmid:a"] = [_shared_folder_entry("sf:del", "del", None, "editor")]
        world.grants["sf:del"] = ["a@x.com"]
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
        world.conn._drop_vanished_shared_folders = AsyncMock()

        await world.full_sync()

        world.conn.data_entities_processor.on_record_group_deleted.assert_awaited_once_with(
            external_group_id="sf:del", connector_id="dbx-1", trash_live_records=True)

    @pytest.mark.asyncio
    async def test_a_deleted_folder_an_invited_member_is_granted_still_goes(self, dropbox_team) -> None:
        """An invited member has joined nothing, so a grant naming them does not make a deleted folder held."""
        world = dropbox_team
        world.store_group("sf:del", "Shared Folder")
        world.store_record("id:d1", "d1.txt", "sf:del")
        world.shared_lists["dbmid:a"] = [_shared_folder_entry("sf:del", "del", None)]
        world.grants["sf:del"] = ["a@x.com", "i@x.com"]
        world.members[1] = _team_member("dbmid:i", "i@x.com", "invited")
        world.conn._drop_vanished_shared_folders = AsyncMock()

        await world.full_sync()

        world.conn.data_entities_processor.on_record_group_deleted.assert_awaited_once_with(
            external_group_id="sf:del", connector_id="dbx-1", trash_live_records=True)

    @pytest.mark.asyncio
    async def test_a_folder_whose_suspended_holder_left_the_team_goes(self, dropbox_team) -> None:
        world = dropbox_team
        world.store_group("sf:plans", "Shared Folder")
        world.store_record("id:p1", "p1.txt", "sf:plans")
        world.grants["sf:plans"] = ["r@x.com"]
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
        gone = self.GONE | {"recordGroup:sf:plans", "record:id:p1"}

        done, removed = await world.full_sync()

        assert (sorted(removed - gone), sorted(removed & gone)) == (
            [], ["record:id:gone", "record:id:p1", "recordGroup:sf:plans"])
        assert done is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("listed_by_a", ["sub", "sub and plans", ""],
                             ids=["unmounted", "unmounted under unmounted", "vanished"])
    async def test_a_folder_inside_a_kept_folder_is_kept(self, dropbox_team, listed_by_a) -> None:
        """The nested folder's own grants leave out the members it inherits from the folder it sits in."""
        world = _suspended_holds_plans(dropbox_team)
        world.store_group("sf:sub", "Shared Folder")
        world.group_parents["sf:sub"] = "sf:plans"
        world.store_record("id:u1", "u1.txt", "sf:sub")
        world.at_source |= {"id:u1"}
        if listed_by_a:
            nested = _shared_folder_entry("sf:sub", "sub", None, "editor")
            nested.parent_shared_folder_id = "sf:plans"
            world.shared_lists["dbmid:a"] = [nested]
        if listed_by_a == "sub and plans":
            world.shared_lists["dbmid:a"].append(_shared_folder_entry("sf:plans", "plans", None, "editor"))

        done, removed = await world.full_sync()

        assert (sorted(removed - self.GONE), sorted(removed & self.GONE)) == ([], ["record:id:gone"])
        assert done is True
        assert _deleted_groups(world) == []
        assert {"sf:plans", "sf:sub"} <= world.conn._kept_folders

    @pytest.mark.asyncio
    @pytest.mark.parametrize("listed_by_a", [True, False], ids=["unmounted", "vanished"])
    async def test_a_folder_gone_from_inside_a_live_folder_goes(self, dropbox_team, listed_by_a) -> None:
        """a has sf:proj in their Dropbox, so they would list a sub folder that inherits from it, too."""
        world = dropbox_team
        world.store_group("sf:proj", "Shared Folder")
        world.grants["sf:proj"] = ["a@x.com", "s@x.com"]
        world.shared_lists["dbmid:a"] = [_shared_folder_entry("sf:proj", "proj", "/proj")]
        world.store_group("sf:sub", "Shared Folder")
        world.group_parents["sf:sub"] = "sf:proj"
        world.grants["sf:sub"] = ["a@x.com"]
        world.store_record("id:u1", "u1.txt", "sf:sub")
        if listed_by_a:
            nested = _shared_folder_entry("sf:sub", "sub", None, "editor")
            nested.parent_shared_folder_id = "sf:proj"
            world.shared_lists["dbmid:a"].append(nested)
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")

        await world.full_sync()

        assert _deleted_groups(world) == ["sf:sub"]
        assert "sf:sub" not in world.conn._kept_folders

    @pytest.mark.asyncio
    async def test_a_folder_that_does_not_inherit_from_a_kept_folder_goes(self, dropbox_team) -> None:
        world = _suspended_holds_plans(dropbox_team)
        world.store_group("sf:sub", "Shared Folder")
        world.group_parents["sf:sub"] = "sf:plans"
        world.no_inherit.add("sf:sub")
        world.grants["sf:sub"] = ["a@x.com"]

        await world.full_sync()

        assert _deleted_groups(world) == ["sf:sub"]
        assert world.conn._kept_folders == {"sf:plans"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("listed_by_a", [True, False], ids=["unmounted", "vanished"])
    async def test_a_kept_folders_grants_are_read_once_a_run(self, dropbox_team, listed_by_a) -> None:
        world = _suspended_holds_plans(dropbox_team)
        world.store_group("sf:sub", "Shared Folder")
        world.group_parents["sf:sub"] = "sf:plans"
        if listed_by_a:
            nested = _shared_folder_entry("sf:sub", "sub", None, "editor")
            nested.parent_shared_folder_id = "sf:plans"
            world.shared_lists["dbmid:a"] = [nested]
        p = world.conn.data_entities_processor

        await world.full_sync()

        read = [c.args[0] for c in p.get_users_with_permission_to_node.await_args_list
                if c.args[1] == CollectionNames.RECORD_GROUPS.value]
        # Unmounted, sf:sub is decided before sf:plans, so its walk reads sf:plans before sf:plans is kept.
        assert (read.count("sf:sub"), read.count("sf:plans")) == (1, 2 if listed_by_a else 1)
        kept = [c.args[1] for c in p.note_kept_record_group.await_args_list]
        assert (kept.count("sf:sub"), kept.count("sf:plans")) == (1, 1)
        assert world.conn._kept_folders == {"sf:plans", "sf:sub"}

    @pytest.mark.asyncio
    async def test_a_grant_to_a_suspended_member_matches_in_any_case(self, dropbox_team) -> None:
        world = _suspended_holds_plans(dropbox_team)
        world.grants["sf:plans"] = ["S@X.COM"]

        await world.full_sync()

        assert _deleted_groups(world) == []

    @pytest.mark.asyncio
    async def test_a_folder_dropbox_deleted_inside_a_team_folder_goes(self, dropbox_team) -> None:
        """Dropbox answers not_found for the folder's own id: it is gone, whoever holds it."""
        world = dropbox_team
        world.store_group("sf:tsub", "Shared Folder")
        world.store_record("id:t1", "t1.txt", "sf:tsub")
        world.grants["sf:tsub"] = ["s@x.com"]
        inside = _shared_folder_entry("sf:tsub", "tsub", None, "editor")
        inside.is_inside_team_folder = True
        world.shared_lists["dbmid:a"] = [inside]
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
        world.conn.data_source.sharing_get_folder_metadata = AsyncMock(
            return_value=ok(SimpleNamespace(folder_id="id:tsub")))
        world.conn._drop_vanished_shared_folders = AsyncMock()

        await world.full_sync()

        assert _deleted_groups(world) == ["sf:tsub"]

    @pytest.mark.asyncio
    async def test_a_folder_kept_on_one_run_goes_once_its_holder_left_the_team(self, dropbox_team) -> None:
        world = _suspended_holds_plans(dropbox_team)
        await world.full_sync()
        assert _deleted_groups(world) == []

        world.members.pop()
        world.shared_lists.pop("dbmid:s")
        done, removed = await world.full_sync()

        assert _deleted_groups(world) == ["sf:plans"]
        assert {"recordGroup:sf:plans", "record:id:p1"} <= removed

    @pytest.mark.asyncio
    async def test_when_a_folders_holders_cannot_be_read_nothing_is_removed(self, dropbox_team, monkeypatch) -> None:
        monkeypatch.setattr(sync_runner, "_INCOMPLETE_FULL_SYNC_RETRYING", set())
        world = _suspended_holds_plans(dropbox_team)
        p = world.conn.data_entities_processor
        read = p.get_users_with_permission_to_node.side_effect

        async def failing(node_id, collection, **kwargs) -> list:
            if collection == CollectionNames.RECORD_GROUPS.value:
                raise RuntimeError("graph unavailable")
            return await read(node_id, collection, **kwargs)

        p.get_users_with_permission_to_node = AsyncMock(side_effect=failing)

        with pytest.raises(DropboxSyncIncomplete):
            await world.sync(full=True)
        done, removed = await world.unlink()

        # gone.txt is deleted by the run itself, which Dropbox told it is not_found; nothing else is removed.
        assert (done, sorted(removed)) == (False, ["record:id:gone"])
        assert _deleted_groups(world) == []

    @pytest.mark.asyncio
    async def test_a_folder_a_suspended_member_holds_only_through_a_group_is_kept(self, dropbox_team) -> None:
        """The suspended member has the only mount, and the folder is shared with a Dropbox group they are in, not
        with them by name."""
        world = _suspended_holds_plans(dropbox_team)
        world.grants["sf:plans"] = ["ext@y.com"]
        world.group_grants["sf:plans"] = ["ug-fin"]
        world.grants["ug-fin"] = ["S@x.com"]

        done, removed = await world.full_sync()

        assert (sorted(removed - self.GONE), sorted(removed & self.GONE)) == ([], ["record:id:gone"])
        assert done is True
        assert _deleted_groups(world) == []

    @pytest.mark.asyncio
    async def test_a_deleted_folder_granted_to_a_group_of_active_members_still_goes(self, dropbox_team) -> None:
        world = dropbox_team
        world.store_group("sf:old", "Shared Folder")
        world.store_record("id:o1", "o1.txt", "sf:old")
        world.group_grants["sf:old"] = ["ug-fin"]
        world.grants["ug-fin"] = ["a@x.com"]
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
        gone = self.GONE | {"recordGroup:sf:old", "record:id:o1"}

        done, removed = await world.full_sync()

        assert (sorted(removed - gone), sorted(removed & gone)) == (
            [], ["record:id:gone", "record:id:o1", "recordGroup:sf:old"])
        assert done is True
        world.conn.data_entities_processor.get_users_with_permission_to_node.assert_any_await(
            "ug-fin", CollectionNames.GROUPS.value, raise_on_error=True)

    @pytest.mark.asyncio
    async def test_when_a_folders_group_members_cannot_be_read_nothing_is_removed(
        self, dropbox_team, monkeypatch
    ) -> None:
        monkeypatch.setattr(sync_runner, "_INCOMPLETE_FULL_SYNC_RETRYING", set())
        world = dropbox_team
        world.store_group("sf:old", "Shared Folder")
        world.store_record("id:o1", "o1.txt", "sf:old")
        world.group_grants["sf:old"] = ["ug-fin"]
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
        p = world.conn.data_entities_processor
        read = p.get_users_with_permission_to_node.side_effect

        async def failing(node_id, collection, **kwargs) -> list:
            if collection == CollectionNames.GROUPS.value:
                raise RuntimeError("graph unavailable")
            return await read(node_id, collection, **kwargs)

        p.get_users_with_permission_to_node = AsyncMock(side_effect=failing)

        with pytest.raises(DropboxSyncIncomplete):
            await world.sync(full=True)
        done, removed = await world.unlink()

        assert (done, sorted(removed)) == (False, ["record:id:gone"])
        assert _deleted_groups(world) == []

    @pytest.mark.asyncio
    async def test_no_suspended_member_reads_no_stored_grants(self, dropbox_team) -> None:
        world = dropbox_team
        world.store_group("sf:old", "Shared Folder")
        world.store_record("id:o1", "o1.txt", "sf:old")
        p = world.conn.data_entities_processor

        await world.full_sync()

        assert all(c.args[1] != CollectionNames.RECORD_GROUPS.value
                   for c in p.get_users_with_permission_to_node.await_args_list)
        assert _deleted_groups(world) == ["sf:old"]

    @pytest.mark.asyncio
    async def test_the_home_of_a_member_who_left_the_team_goes(self, dropbox_team) -> None:
        """A member no longer on the team list has no Dropbox home to read: their home goes, suspended or not."""
        world = dropbox_team
        world.store_group("dbmid:r", "Personal Folder")
        world.store_record("id:r1", "r1.txt", "dbmid:r")
        world.members.append(_team_member("dbmid:r", "r@x.com", "active"))
        world.conn.data_entities_processor.get_all_app_users = AsyncMock(return_value=[
            AppUser(app_name=Connectors.DROPBOX, connector_id="dbx-1", source_user_id=m.profile.team_member_id,
                    email=m.profile.email, full_name=m.profile.email) for m in world.members])
        world.members.pop()
        world.members[1] = _team_member("dbmid:s", "s@x.com", "suspended")
        gone = self.GONE | {"recordGroup:dbmid:r", "record:id:r1"}

        done, removed = await world.full_sync()

        assert (sorted(removed - gone), sorted(removed & gone)) == (
            [], ["record:id:gone", "record:id:r1", "recordGroup:dbmid:r"])
        assert done is True

    @pytest.mark.asyncio
    async def test_with_every_member_active_only_what_is_gone_goes(self, dropbox_team) -> None:
        world = dropbox_team
        world.store_record("id:s1", "s1.txt", "dbmid:s")
        world.at_source |= {"id:s1"}
        world.listings["dbmid:s"] = [file_entry("s1.txt", "id:s1", "/s1.txt")]

        done, removed = await world.full_sync()

        assert (sorted(removed - self.GONE), sorted(removed & self.GONE)) == ([], ["record:id:gone"])
        assert done is True

    @pytest.mark.asyncio
    async def test_a_partial_team_list_deletes_no_unmounted_folder(self, dropbox_team) -> None:
        """Member b, on the team list page that could not be read, is the one who has sf:x in their Dropbox."""
        world = dropbox_team
        world.store_group("sf:x", "Shared Folder")
        world.store_record("id:x1", "x1.txt", "sf:x")
        world.shared_lists["dbmid:a"] = [_shared_folder_entry("sf:x", "x", None, "editor")]
        world.shared_lists["dbmid:b"] = [_shared_folder_entry("sf:x", "x", "/x")]
        world.members = world.members[:1]
        ds = world.conn.data_source
        ds.team_members_list = AsyncMock(return_value=ok(SimpleNamespace(
            members=list(world.members), has_more=True, cursor="c1")))
        ds.team_members_list_continue = AsyncMock(return_value=fail("503"))

        with pytest.raises(DropboxSyncIncomplete):
            await world.sync(full=True)

        assert _deleted_groups(world) == []

    @pytest.mark.asyncio
    async def test_a_complete_team_list_after_a_partial_one_deletes_an_unmounted_folder(self, dropbox_team) -> None:
        world = dropbox_team
        world.store_group("sf:x", "Shared Folder")
        world.store_record("id:x1", "x1.txt", "sf:x")
        world.shared_lists["dbmid:a"] = [_shared_folder_entry("sf:x", "x", None, "editor")]
        world.members = world.members[:1]
        ds = world.conn.data_source
        whole = ds.team_members_list
        ds.team_members_list = AsyncMock(return_value=ok(SimpleNamespace(
            members=list(world.members), has_more=True, cursor="c1")))
        ds.team_members_list_continue = AsyncMock(return_value=fail("503"))
        with pytest.raises(DropboxSyncIncomplete):
            await world.sync(full=True)
        assert _deleted_groups(world) == []

        ds.team_members_list = whole
        await world.full_sync()

        assert _deleted_groups(world) == ["sf:x"]

    @pytest.mark.asyncio
    async def test_a_partial_team_list_after_a_complete_one_deletes_no_unmounted_folder(self, dropbox_team) -> None:
        """Member b has sf:x in their Dropbox; on the second run b is on the page that could not be read."""
        world = dropbox_team
        world.store_group("sf:x", "Shared Folder")
        world.store_record("id:x1", "x1.txt", "sf:x")
        world.shared_lists["dbmid:a"] = [_shared_folder_entry("sf:x", "x", None, "editor")]
        world.shared_lists["dbmid:b"] = [_shared_folder_entry("sf:x", "x", "/x")]
        world.members[1] = _team_member("dbmid:b", "b@x.com", "active")
        await world.full_sync()
        assert _deleted_groups(world) == []

        ds = world.conn.data_source
        ds.team_members_list = AsyncMock(return_value=ok(SimpleNamespace(
            members=world.members[:1], has_more=True, cursor="c1")))
        ds.team_members_list_continue = AsyncMock(return_value=fail("503"))
        with pytest.raises(DropboxSyncIncomplete):
            await world.sync(full=True)

        assert _deleted_groups(world) == []

    @pytest.mark.asyncio
    async def test_a_complete_team_list_still_deletes_an_unmounted_folder(self, dropbox_team) -> None:
        world = dropbox_team
        world.store_group("sf:x", "Shared Folder")
        world.store_record("id:x1", "x1.txt", "sf:x")
        world.shared_lists["dbmid:a"] = [_shared_folder_entry("sf:x", "x", None, "editor")]

        await world.full_sync()

        assert _deleted_groups(world) == ["sf:x"]


# ---- the personal connector -------------------------------------------------------------------------------

@pytest.fixture
def personal() -> DropboxIndividualConnector:
    with patch("app.connectors.sources.dropbox_individual.connector.DropboxIndividualApp"):
        conn = DropboxIndividualConnector(LOGGER, MagicMock(), MagicMock(), AsyncMock(), "dbx-p", "personal", "creator")
    conn.data_source = AsyncMock()
    p = conn.data_entities_processor
    p.on_new_app_users = AsyncMock()
    p.withdraw_app_user_access = AsyncMock()
    conn.ensure_creator_user_app_relation = AsyncMock()
    conn.register_authenticated_source_user = AsyncMock()
    return conn


class TestPersonalIdentity:
    @pytest.mark.asyncio
    async def test_an_account_under_a_colleagues_email_reaches_the_creator_only(self, personal) -> None:
        personal._load_creator_email = AsyncMock()
        personal.creator_email = "creator@x.com"
        stored = [AppUser(app_name=Connectors.DROPBOX_PERSONAL, connector_id="dbx-p", source_user_id="dbid:1",
                          email="colleague@x.com", full_name="C")]
        personal.data_entities_processor.get_all_app_users = AsyncMock(return_value=stored)
        account = SimpleNamespace(email="colleague@x.com", account_id="dbid:1", name=SimpleNamespace(display_name="C"))

        await personal._grant_creator(account)

        personal.data_entities_processor.on_new_app_users.assert_not_awaited()
        personal.ensure_creator_user_app_relation.assert_awaited_once()
        personal.data_entities_processor.withdraw_app_user_access.assert_awaited_once_with("dbx-p", stored)

    @pytest.mark.asyncio
    async def test_the_creators_own_account_is_saved(self, personal) -> None:
        personal._load_creator_email = AsyncMock()
        personal.creator_email = "me@x.com"
        personal.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        account = SimpleNamespace(email="Me@x.com", account_id="dbid:1", name=SimpleNamespace(display_name="Me"))

        await personal._grant_creator(account)

        personal.data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_connection_test_reads_the_answer(self, personal) -> None:
        personal.data_source.users_get_current_account = AsyncMock(return_value=fail(AUTH))
        assert await personal.test_connection_and_access() is False
        personal.data_source.users_get_current_account = AsyncMock(return_value=ok(MagicMock()))
        assert await personal.test_connection_and_access() is True
