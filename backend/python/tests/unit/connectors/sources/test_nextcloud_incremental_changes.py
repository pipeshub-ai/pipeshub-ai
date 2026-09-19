"""Nextcloud incremental sync: merged activities, new folders, and unchanged records.

Each case was found by syncing a real Nextcloud server; see the Nextcloud
integration tests for the end-to-end version.
"""

import json
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.nextcloud.connector import NextcloudConnector
from app.models.entities import FileRecord, RecordGroupType, RecordType

ROOT = "/remote.php/dav/files/alice"


@pytest.fixture
def connector():
    @asynccontextmanager
    async def _transaction():
        tx = MagicMock()
        tx.get_record_by_path = AsyncMock(return_value=None)
        yield tx

    provider = MagicMock()
    provider.transaction = _transaction
    dep = MagicMock()
    dep.org_id = "org-nc"
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_record_group_by_external_id = AsyncMock(return_value=MagicMock(external_group_id="alice"))
    dep.on_new_records = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    with patch("app.connectors.sources.nextcloud.connector.NextcloudApp"), \
         patch("app.connectors.sources.nextcloud.connector.SyncPoint"):
        conn = NextcloudConnector(
            logger=logging.getLogger("test.nextcloud.incremental"),
            data_entities_processor=dep,
            data_store_provider=provider,
            config_service=MagicMock(),
            connector_id="nc-1",
            scope="personal",
            created_by="u1",
        )
    conn.data_source = MagicMock()
    conn.current_user_id = "alice"
    conn.current_user_email = "alice@example.com"
    conn.activity_sync_point = MagicMock()
    conn.activity_sync_point.read_sync_point = AsyncMock(return_value={"cursor": "100"})
    conn.activity_sync_point.update_sync_point = AsyncMock()
    return conn


def _response(payload) -> MagicMock:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    response = MagicMock(status=200, success=True)
    response.bytes = MagicMock(return_value=body)
    return response


def _activity(activity_id, activity_type, objects):
    first_id, first_name = next(iter(objects.items()))
    return {
        "activity_id": activity_id,
        "type": activity_type,
        "object_type": "files",
        "object_id": int(first_id),
        "object_name": first_name,
        "objects": objects,
    }


def _propfind(path: str, file_id: str, folder: bool = True) -> bytes:
    resource = "<d:resourcetype><d:collection/></d:resourcetype>" if folder else "<d:resourcetype/>"
    href = f"{ROOT}{path}/" if folder else f"{ROOT}{path}"
    return f"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:response>
    <d:href>{href}</d:href>
    <d:propstat><d:prop>
      {resource}
      <d:displayname>{path.rsplit("/", 1)[-1]}</d:displayname>
      <d:getetag>"etag-{file_id}"</d:getetag>
      <d:getlastmodified>Fri, 18 Sep 2026 10:00:00 GMT</d:getlastmodified>
      <oc:fileid>{file_id}</oc:fileid>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>""".encode()


class TestMergedActivities:
    """Nextcloud merges events that happen together into one activity.

    ``object_id`` and ``object_name`` then name only the first file; every file
    the activity covers is in ``objects``. Reading only the first skipped the
    rest of an upload.
    """

    @pytest.mark.asyncio
    async def test_every_file_in_a_merged_upload_is_processed(self, connector):
        connector.data_source.get_activities = AsyncMock(return_value=_response({"ocs": {"data": [
            _activity(110, "file_created", {"59": "/Handbook", "60": "/Handbook/leave.txt"}),
        ]}}))
        connector._process_modified_files = AsyncMock()

        await connector._run_incremental_sync_internal()

        paths = connector._process_modified_files.await_args.args[0]
        assert sorted(paths) == ["/Handbook", "/Handbook/leave.txt"]
        connector.activity_sync_point.update_sync_point.assert_awaited_once_with(
            "activity_cursor", {"cursor": "110"}
        )

    @pytest.mark.asyncio
    async def test_every_file_in_a_merged_deletion_is_deleted(self, connector):
        connector.data_source.get_activities = AsyncMock(return_value=_response({"ocs": {"data": [
            _activity(113, "file_deleted", {"61": "/a.txt", "62": "/b.txt"}),
        ]}}))
        connector._process_deletions = AsyncMock()

        await connector._run_incremental_sync_internal()

        assert connector._process_deletions.await_args.args[0] == {"61", "62"}

    @pytest.mark.asyncio
    async def test_an_activity_without_objects_still_uses_its_one_file(self, connector):
        activity = _activity(111, "file_changed", {"70": "/notes.txt"})
        del activity["objects"]
        connector.data_source.get_activities = AsyncMock(
            return_value=_response({"ocs": {"data": [activity]}})
        )
        connector._process_modified_files = AsyncMock()

        await connector._run_incremental_sync_internal()

        assert connector._process_modified_files.await_args.args[0] == ["/notes.txt"]


class TestNewFolders:
    """A file can arrive in folders the index has never seen."""

    @pytest.mark.asyncio
    async def test_missing_folders_are_created_under_their_real_parents(self, connector):
        folders = {"/Projects": "80", "/Projects/Apollo": "81"}
        connector.data_source.list_directory = AsyncMock(
            side_effect=lambda user_id, path, depth: _response(
                _propfind(path, folders[path]) if path in folders
                else _propfind(path, "82", folder=False)
            )
        )

        await connector._process_modified_files(
            ["/Projects/Apollo/plan.md"], "alice", "alice@example.com", "alice"
        )

        created = [
            call.args[0][0][0] for call in connector.data_entities_processor.on_new_records.await_args_list
        ]
        parents = {r.record_name: r.parent_external_record_id for r in created}
        # Before, Apollo was created at the root: its own parent was looked up
        # by a stored path, and records store none.
        assert parents == {"Projects": None, "Apollo": "80", "plan.md": "81"}

    @pytest.mark.asyncio
    async def test_a_folder_already_indexed_is_not_created_again(self, connector):
        connector.data_source.list_directory = AsyncMock(
            side_effect=lambda user_id, path, depth: _response(
                _propfind(path, "80") if path == "/Projects" else _propfind(path, "82", folder=False)
            )
        )
        existing = MagicMock(id="rec-80", external_record_id="80")
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, ext: existing if ext == "80" else None
        )

        await connector._process_modified_files(
            ["/Projects/plan.md"], "alice", "alice@example.com", "alice"
        )

        created = [
            call.args[0][0][0] for call in connector.data_entities_processor.on_new_records.await_args_list
        ]
        assert [r.record_name for r in created] == ["plan.md"]
        assert created[0].parent_external_record_id == "80"


class TestUnchangedRecords:
    @pytest.mark.asyncio
    async def test_an_unchanged_record_is_not_reported_as_moved(self, connector):
        # Nextcloud records are stored with path=None; that used to count as a
        # move, which re-indexed every file on every full sync.
        stored = FileRecord(
            record_name="plan.md",
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.DRIVE,
            external_record_group_id="alice",
            external_record_id="82",
            external_revision_id="etag-82",
            version=3,
            connector_name="NEXTCLOUD",
            connector_id="nc-1",
            origin="CONNECTOR",
            is_file=True,
            path=None,
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=stored)
        entry = {
            "file_id": "82",
            "path": f"{ROOT}/plan.md",
            "display_name": "plan.md",
            "is_collection": False,
            "etag": '"etag-82"',
        }
        connector.sync_filters = MagicMock(get=MagicMock(return_value=None))
        connector._cached_date_filters = (None, None, None, None)

        update = await connector._process_nextcloud_entry(
            entry, "alice", "alice@example.com", "alice", ROOT, {}
        )

        assert update is not None
        assert not update.is_updated
        assert update.record.external_revision_id == "etag-82"
