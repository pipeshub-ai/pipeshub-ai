# pyright: ignore-file

"""
Local FS Connector – Integration Tests
======================================

Local FS reaches its files two ways, and each has a suite here.

A folder the backend can read (``local_fs_connector``): every sync walks the
folder, so these tests change files on disk and sync again.

  TC-LFS-SYNC-001   — Full sync: every seeded file and folder is a record
  TC-LFS-STREAM-001 — A file's record streams the file's content
  TC-LFS-UPD-001    — An edited file is re-indexed with its new content
  TC-LFS-INCR-001   — A new file in a new folder appears, under that folder
  TC-LFS-DEL-001    — A deleted file's record is removed
  TC-LFS-FILTER-001 — Excluding .txt drops .txt files and keeps the rest
  TC-LFS-PERM-001   — Another member of the org cannot find the files

The desktop app (``local_fs_desktop_connector``): the folder is on the user's
machine, so the app uploads each change with its content.

  TC-LFS-UP-001 — Uploaded files become records and stream their content
  TC-LFS-UP-002 — A modified file streams its new content, with no second record
  TC-LFS-UP-003 — A renamed file moves to its new path
  TC-LFS-UP-004 — A deleted file's record is removed

Not asserted, because of how the connector works today: every sync of a
readable folder deletes all of the connector's records and creates them again,
so record ids change on each sync and every file is indexed again.
"""

import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.local_fs.local_fs_seed import SEED_FILES  # type: ignore[import-not-found]
from connectors.local_fs.local_fs_source_helper import (  # type: ignore[import-not-found]
    LocalFsFolder,
    external_record_id,
    upload_file_events,
)
from helper.connector_visibility import (
    found,
    search_connector_as,
    search_connector_as_admin,
    wait_until_searchable,
)
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import (
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from helper.storage_incremental import (
    DEFAULT_SYNC_TIMEOUT_SEC,
    restart_sync,
    settle_record_baseline,
    sync_until_names_absent,
    sync_until_names_visible,
)
from helper.second_user import SecondUser, describe_search, has_no_access
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("local-fs-lifecycle-test")

LEAVE_POLICY = "Handbook/leave-policy.txt"


async def _record(graph_provider: GraphProviderProtocol, connector_id: str, rel_path: str):
    return await graph_provider.get_record_by_external_id(
        connector_id, external_record_id(connector_id, rel_path)
    )


def _streamed(pipeshub_client: PipeshubClient, record_id: str) -> str:
    response = pipeshub_client.stream_record(record_id)
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.skip(
    reason=(
        "Local FS now pulls events from the desktop; this suite still assumes "
        "the connector walks a compose-mounted folder"
    )
)
@pytest.mark.integration
@pytest.mark.local_fs
@pytest.mark.asyncio(loop_scope="session")
class TestLocalFsFolderSync:
    """A folder the connector reads directly."""

    @pytest.mark.order(1)
    async def test_tc_lfs_sync_001_full_sync(
        self,
        local_fs_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LFS-SYNC-001: Every seeded file and folder is a record."""
        connector_id = local_fs_connector["connector_id"]
        names = [*(p.rsplit("/", 1)[-1] for p in SEED_FILES), "Handbook"]

        await graph_provider.assert_record_names_contain(connector_id, names)
        assert await graph_provider.count_records(connector_id) == len(names)
        for rel_path in SEED_FILES:
            assert await _record(graph_provider, connector_id, rel_path) is not None, (
                f"TC-LFS-SYNC-001: {rel_path} is not in the graph"
            )
        policy = await _record(graph_provider, connector_id, LEAVE_POLICY)
        assert policy.parent_external_record_id == external_record_id(connector_id, "Handbook")
        logger.info("TC-LFS-SYNC-001 passed: %s", names)

    @pytest.mark.order(2)
    async def test_tc_lfs_stream_001_file_streams_its_content(
        self,
        local_fs_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LFS-STREAM-001: Streaming a file's record returns the file."""
        connector_id = local_fs_connector["connector_id"]
        record = await _record(graph_provider, connector_id, LEAVE_POLICY)
        assert record is not None, f"TC-LFS-STREAM-001: {LEAVE_POLICY} is not in the graph"
        assert _streamed(pipeshub_client, record.id) == SEED_FILES[LEAVE_POLICY]

    @pytest.mark.order(3)
    async def test_tc_lfs_upd_001_edited_file_is_reindexed(
        self,
        local_fs_connector: dict[str, Any],
        local_fs_folder: LocalFsFolder,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LFS-UPD-001: An edited file gets a new revision and streams its new content."""
        connector_id = local_fs_connector["connector_id"]
        before_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        before = await _record(graph_provider, connector_id, LEAVE_POLICY)
        assert before is not None

        updated = "Everyone gets 30 days of paid leave a year."
        local_fs_folder.write(local_fs_connector["folder"], LEAVE_POLICY, updated)
        restart_sync(pipeshub_client, connector_id)

        async def _new_revision() -> bool:
            record = await _record(graph_provider, connector_id, LEAVE_POLICY)
            return record is not None and record.external_revision_id != before.external_revision_id

        await wait_until_graph_condition(
            connector_id,
            check=_new_revision,
            timeout=DEFAULT_SYNC_TIMEOUT_SEC,
            poll_interval=5,
            description=f"re-index of {LEAVE_POLICY}",
        )
        after_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        assert after_count == before_count, (
            f"TC-LFS-UPD-001: record count moved from {before_count} to {after_count}"
        )
        after = await _record(graph_provider, connector_id, LEAVE_POLICY)
        assert _streamed(pipeshub_client, after.id) == updated

    @pytest.mark.order(4)
    async def test_tc_lfs_incr_001_new_file_appears_under_its_folder(
        self,
        local_fs_connector: dict[str, Any],
        local_fs_folder: LocalFsFolder,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LFS-INCR-001: A new file in a new folder appears, filed under that folder."""
        connector_id = local_fs_connector["connector_id"]
        before_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)

        local_fs_folder.write(
            local_fs_connector["folder"], "Engineering/runbook.md", "Drain traffic, then restart."
        )
        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, ["Engineering", "runbook.md"]
        )
        assert after_count == before_count + 2, (
            f"TC-LFS-INCR-001: expected the folder and the file as new records; "
            f"count went from {before_count} to {after_count}"
        )
        runbook = await _record(graph_provider, connector_id, "Engineering/runbook.md")
        assert runbook is not None
        assert runbook.parent_external_record_id == external_record_id(connector_id, "Engineering")

    @pytest.mark.order(5)
    async def test_tc_lfs_del_001_deleted_file_is_removed(
        self,
        local_fs_connector: dict[str, Any],
        local_fs_folder: LocalFsFolder,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LFS-DEL-001: Deleting a file removes its record, and only its record."""
        connector_id = local_fs_connector["connector_id"]
        before_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)

        local_fs_folder.delete(local_fs_connector["folder"], "notes.txt")
        await sync_until_names_absent(pipeshub_client, graph_provider, connector_id, ["notes.txt"])

        after_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        assert after_count == before_count - 1, (
            f"TC-LFS-DEL-001: expected one record fewer; count went from {before_count} to {after_count}"
        )

    @pytest.mark.order(6)
    async def test_tc_lfs_filter_001_excluded_extension_is_dropped(
        self,
        local_fs_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LFS-FILTER-001: Excluding .txt drops .txt files and keeps .md files.

        Before the fix alongside this test, the connector ignored the operator
        and treated "exclude .txt" as "only .txt".
        """
        connector_id = local_fs_connector["connector_id"]
        pipeshub_client.update_connector_filters_sync_safe(
            connector_id,
            filters={
                "sync": {
                    "values": {
                        "file_extensions": {
                            "operator": "not_in",
                            "value": ["txt"],
                            "type": "multiselect",
                        }
                    }
                }
            },
        )

        async def _txt_gone() -> bool:
            return await graph_provider.get_record_by_name(connector_id, "leave-policy.txt") is None

        await wait_until_graph_condition(
            connector_id,
            check=_txt_gone,
            timeout=DEFAULT_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description="sync after excluding .txt",
        )
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC
        )
        await graph_provider.assert_record_names_contain(
            connector_id, ["guide.md", "benefits.md", "runbook.md"]
        )
        await graph_provider.assert_record_not_exists(connector_id, "leave-policy.txt")


@pytest.mark.skip(
    reason=(
        "Local FS now pulls events from the desktop; this suite still POSTs "
        "the removed /file-events/upload route"
    )
)
@pytest.mark.integration
@pytest.mark.local_fs
@pytest.mark.permissions
@pytest.mark.asyncio(loop_scope="session")
class TestLocalFsVisibility:
    """Local FS is a personal connector: its files are its owner's alone."""

    @pytest.mark.order(2)
    async def test_tc_lfs_perm_001_another_member_cannot_find_the_files(
        self,
        local_fs_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        second_user: SecondUser,
    ) -> None:
        """TC-LFS-PERM-001: The owner finds a file; another member of the org does not.

        Runs before any test re-syncs, since every sync recreates the records.
        """
        connector_id = local_fs_connector["connector_id"]
        record = await wait_until_searchable(graph_provider, connector_id, "notes.txt")
        query = SEED_FILES["notes.txt"]

        owner = search_connector_as_admin(pipeshub_client, connector_id, query)
        assert found(owner, record["virtualRecordId"]), (
            "TC-LFS-PERM-001 precondition: the owner could not find their own file, "
            f"so the other member finding nothing proves nothing. {describe_search(owner)}"
        )

        other = search_connector_as(second_user, connector_id, query)
        assert has_no_access(other), (
            "TC-LFS-PERM-001: another member of the org found files from someone "
            f"else's personal connector. {describe_search(other)}"
        )


@pytest.mark.integration
@pytest.mark.local_fs
@pytest.mark.asyncio(loop_scope="session")
class TestLocalFsDesktopUploads:
    """Files the desktop app uploads, for a folder the backend cannot see."""

    @pytest.mark.order(7)
    async def test_tc_lfs_up_001_uploaded_files_become_records(
        self,
        local_fs_desktop_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LFS-UP-001: Uploaded files become records, under their folder, and stream back."""
        connector_id = local_fs_desktop_connector["connector_id"]
        files = {
            "Reports/q1.txt": "Revenue grew 12%.",
            "Reports/q2.txt": "Revenue grew 9%.",
        }
        upload_file_events(
            pipeshub_client,
            connector_id,
            [{"type": "CREATED", "path": p, "content": c} for p, c in files.items()],
        )

        for rel_path, content in files.items():
            record = await _record(graph_provider, connector_id, rel_path)
            assert record is not None, f"TC-LFS-UP-001: {rel_path} is not in the graph"
            assert record.parent_external_record_id == external_record_id(connector_id, "Reports")
            assert _streamed(pipeshub_client, record.id) == content
        assert await graph_provider.count_records(connector_id) == 3  # two files and Reports

    @pytest.mark.order(8)
    async def test_tc_lfs_up_002_modified_file_streams_new_content(
        self,
        local_fs_desktop_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LFS-UP-002: A modified file streams its new content, with no second record."""
        connector_id = local_fs_desktop_connector["connector_id"]
        before_count = await graph_provider.count_records(connector_id)
        updated = f"Revenue grew 14% (restated {uuid.uuid4().hex[:6]})."

        upload_file_events(
            pipeshub_client,
            connector_id,
            [{"type": "MODIFIED", "path": "Reports/q1.txt", "content": updated}],
        )

        record = await _record(graph_provider, connector_id, "Reports/q1.txt")
        assert record is not None
        assert _streamed(pipeshub_client, record.id) == updated
        assert await graph_provider.count_records(connector_id) == before_count

    @pytest.mark.order(9)
    async def test_tc_lfs_up_003_renamed_file_moves(
        self,
        local_fs_desktop_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LFS-UP-003: A renamed file is at its new path, and gone from its old one."""
        connector_id = local_fs_desktop_connector["connector_id"]
        before_count = await graph_provider.count_records(connector_id)

        upload_file_events(
            pipeshub_client,
            connector_id,
            [{
                "type": "RENAMED",
                "path": "Reports/q2-final.txt",
                "oldPath": "Reports/q2.txt",
                "content": "Revenue grew 9%.",
            }],
        )

        assert await _record(graph_provider, connector_id, "Reports/q2-final.txt") is not None
        assert await _record(graph_provider, connector_id, "Reports/q2.txt") is None
        assert await graph_provider.count_records(connector_id) == before_count

    @pytest.mark.order(10)
    async def test_tc_lfs_up_004_deleted_file_is_removed(
        self,
        local_fs_desktop_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LFS-UP-004: A deleted file's record is removed, and only its record."""
        connector_id = local_fs_desktop_connector["connector_id"]
        before_count = await graph_provider.count_records(connector_id)

        upload_file_events(
            pipeshub_client, connector_id, [{"type": "DELETED", "path": "Reports/q1.txt"}]
        )

        assert await _record(graph_provider, connector_id, "Reports/q1.txt") is None
        assert await graph_provider.count_records(connector_id) == before_count - 1
