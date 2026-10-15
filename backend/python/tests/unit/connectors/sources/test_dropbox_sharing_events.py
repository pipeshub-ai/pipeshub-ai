"""Dropbox Team sharing events re-read the changed item, whoever made the change.

Found in a live run of PR #3880: every sharing event made through an app has an
app actor, and reading ``actor.get_user()`` raised, so the event was dropped and
a removed member kept access until a full sync (DB-02, DB-10). The item is now
re-read as the member whose content it is, and a folder's stored subtree with it.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dropbox.files import FileMetadata, FolderMetadata

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.dropbox.connector import (
    SHARING_CHANGE_EVENTS,
    DropboxConnector,
)
from app.sources.client.dropbox.dropbox_ import DropboxResponse

OWNER = ("dbmid:owner", "owner@acme.com")


def _member_info(member: tuple[str, str]) -> MagicMock:
    return MagicMock(team_member_id=member[0], email=member[1])


def _event(*, context: tuple[str, str] | None, actor: str = "app", actor_member: tuple[str, str] | None = None,
           event_type: str = "shared_content_remove_member", asset: MagicMock | None = None) -> MagicMock:
    event = MagicMock()
    event.event_type._tag = event_type
    event.context = MagicMock()
    event.context.is_team_member.return_value = context is not None
    event.context.get_team_member.return_value = _member_info(context) if context else None
    event.actor.is_user.return_value = actor == "user"
    event.actor.is_admin.return_value = actor == "admin"
    event.actor.get_user.side_effect = (lambda: _member_info(actor_member)) if actor == "user" else AttributeError("tag 'user' not set")
    event.actor.get_admin.side_effect = (lambda: _member_info(actor_member)) if actor == "admin" else AttributeError("tag 'admin' not set")
    event.assets = [asset] if asset else []
    return event


def _file_asset(file_id: str = "id:single") -> MagicMock:
    asset = MagicMock()
    asset.is_file.return_value = True
    asset.is_folder.return_value = False
    asset.get_file.return_value = MagicMock(file_id=file_id, path=MagicMock(namespace_relative=MagicMock(ns_id="ns-home")))
    return asset


@pytest.fixture
def connector() -> DropboxConnector:
    with patch("app.connectors.sources.dropbox.connector.DropboxApp"):
        conn = DropboxConnector(
            logger=logging.getLogger("dropbox-sharing-events"),
            data_entities_processor=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=AsyncMock(),
            connector_id="dbx-conn-1",
            scope="team",
            created_by="admin",
        )
    conn.sync_filters = FilterCollection()
    conn.indexing_filters = FilterCollection()
    conn.data_source = AsyncMock()
    conn.dropbox_cursor_sync_point = MagicMock()
    conn.dropbox_cursor_sync_point.update_sync_point = AsyncMock()
    return conn


class TestSharingEventMember:
    def test_the_context_member_is_used_even_when_an_app_made_the_change(self) -> None:
        assert DropboxConnector._sharing_event_member(_event(context=OWNER, actor="app")) == OWNER

    def test_a_user_actor_is_the_fallback(self) -> None:
        actor = ("dbmid:actor", "actor@acme.com")
        assert DropboxConnector._sharing_event_member(_event(context=None, actor="user", actor_member=actor)) == actor

    def test_an_app_change_with_no_member_context_names_nobody(self) -> None:
        assert DropboxConnector._sharing_event_member(_event(context=None, actor="app")) is None


class TestSharingEventsCursor:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("event_type", sorted(SHARING_CHANGE_EVENTS))
    async def test_an_app_made_change_re_reads_the_item_as_its_owner(self, connector, event_type) -> None:
        event = _event(context=OWNER, actor="app", event_type=event_type, asset=_file_asset())
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=DropboxResponse(
            success=True, data=MagicMock(events=[event], cursor="c2", has_more=False),
        ))
        connector._resync_record_by_external_id = AsyncMock()

        await connector._sync_sharing_changes_with_cursor("key", "c1")

        connector._resync_record_by_external_id.assert_awaited_once_with(
            external_id="id:single", team_member_id=OWNER[0], user_email=OWNER[1], is_folder=False, ns_id="ns-home",
        )
        connector.dropbox_cursor_sync_point.update_sync_point.assert_awaited_once_with(
            "key", sync_point_data={"cursor": "c2"}
        )


class TestResyncDescendants:
    @pytest.mark.asyncio
    async def test_every_stored_item_below_the_folder_is_re_read(self, connector) -> None:
        children = {
            "id:folder": [MagicMock(external_record_id="id:sub"), MagicMock(external_record_id="id:a")],
            "id:sub": [MagicMock(external_record_id="id:b")],
        }
        connector.data_entities_processor.get_records_by_parent = AsyncMock(
            side_effect=lambda _cid, parent: children.get(parent, [])
        )
        metadata = {
            "id:sub": FolderMetadata(name="sub", id="id:sub", path_lower="/f/sub"),
            "id:a": FileMetadata(name="a.txt", id="id:a", path_lower="/f/a.txt"),
            "id:b": FileMetadata(name="b.txt", id="id:b", path_lower="/f/sub/b.txt"),
        }
        connector.data_source.files_get_metadata = AsyncMock(
            side_effect=lambda path, team_member_id: DropboxResponse(success=True, data=metadata[path])
        )
        connector._process_dropbox_entry = AsyncMock(return_value=MagicMock(is_updated=True))
        connector._handle_record_updates = AsyncMock()

        await connector._resync_descendants("id:folder", *OWNER, "dbmid:owner", is_person_folder=True)

        re_read = [call.args[0].id for call in connector._process_dropbox_entry.await_args_list]
        assert sorted(re_read) == ["id:a", "id:b", "id:sub"]
        assert connector._handle_record_updates.await_count == 3


class TestFolderEvent:
    @pytest.mark.asyncio
    async def test_a_shared_folder_is_found_from_the_members_own_root_and_its_subtree_re_read(self, connector) -> None:
        """Rooted at the folder's own namespace, "ns:<id>" answers not_found, which dropped every folder event."""
        folder = FolderMetadata(name="v2-open", id="id:open", path_lower="/v2/v2-open")
        lookups: list[dict] = []

        async def get_metadata(**kwargs) -> DropboxResponse:
            lookups.append(kwargs)
            return DropboxResponse(success=True, data=folder)

        connector.data_source.files_get_metadata = AsyncMock(side_effect=get_metadata)
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=MagicMock(external_record_group_id=OWNER[0])
        )
        connector._process_dropbox_entry = AsyncMock(return_value=MagicMock(is_updated=True))
        connector._handle_record_updates = AsyncMock()
        connector._resync_descendants = AsyncMock()

        await connector._resync_record_by_external_id("13667846609", OWNER[0], OWNER[1], is_folder=True)

        assert lookups[0] == {"path": "ns:13667846609", "team_member_id": OWNER[0]}
        assert all("team_folder_id" not in kwargs for kwargs in lookups)
        connector._handle_record_updates.assert_awaited_once()
        connector._resync_descendants.assert_awaited_once_with(
            "id:open", OWNER[0], OWNER[1], OWNER[0], is_person_folder=True,
        )
