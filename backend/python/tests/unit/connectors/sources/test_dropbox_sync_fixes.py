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

from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.dropbox.connector import DropboxConnector, _reset_time
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
