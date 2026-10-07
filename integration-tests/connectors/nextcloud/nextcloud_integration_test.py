# pyright: ignore-file

"""
Nextcloud Connector – Integration Tests
=======================================

Tests receive a fully set-up connector via the ``nextcloud_connector`` fixture
(defined in conftest.py), which uploads the run's files, creates the connector
and waits for a full sync, then tears both down.

Nextcloud here runs in the integration stack, so this connector has live
coverage without an external account. After the first full sync the connector
syncs from Nextcloud's Activity feed, so every test after TC-SYNC-001 exercises
incremental sync.

Test cases:
  TC-SYNC-001   — Full sync: every seeded file and folder is a record in the user's drive
  TC-STREAM-001 — A file's record streams the file's content
  TC-INCR-001   — Files uploaded together into a new folder all appear, under that folder
  TC-UPD-001    — Editing a file re-indexes its record in place
  TC-DEL-001    — Deleting a file removes its record
  TC-FILTER-001 — A file type the sync filter excludes is not indexed

Not covered, because the connector does not do it: narrowing the sync filter
leaves files indexed earlier that the filter now excludes.
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

from connectors.nextcloud.nextcloud_seed import SEED_FILES, SYNC_USER  # type: ignore[import-not-found]
from connectors.nextcloud.nextcloud_source_helper import (  # type: ignore[import-not-found]
    NextcloudSourceHelper,
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
    wait_for_record_reindex,
)
from pipeshub_client import (
    PipeshubClient,  # type: ignore[import-not-found]
)

logger = logging.getLogger("nextcloud-lifecycle-test")

LEAVE_POLICY = "Handbook/leave-policy.txt"


@pytest.mark.integration
@pytest.mark.nextcloud
@pytest.mark.asyncio(loop_scope="session")
class TestNextcloudConnector:
    """Lifecycle coverage for the Nextcloud connector."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        nextcloud_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: Every seeded file and folder is in the graph, in the user's drive."""
        connector_id = nextcloud_connector["connector_id"]
        seeded = nextcloud_connector["seeded_names"]

        await graph_provider.assert_min_records(connector_id, len(seeded))
        await graph_provider.assert_record_names_contain(connector_id, seeded)
        await graph_provider.assert_no_orphan_records(connector_id)

        groups = await graph_provider.fetch_record_group_names(connector_id)
        assert f"{SYNC_USER}'s Files" in groups, (
            f"TC-SYNC-001: expected the drive group for {SYNC_USER}; found {groups}"
        )
        logger.info("TC-SYNC-001 passed: %s (connector %s)", seeded, connector_id)

    @pytest.mark.order(2)
    async def test_tc_stream_001_file_record_streams_its_content(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-001: Streaming a file's record returns the file, as indexing reads it."""
        connector_id = nextcloud_connector["connector_id"]
        path = f"{nextcloud_connector['folder']}/{LEAVE_POLICY}"
        record = await graph_provider.get_record_by_external_id(
            connector_id, nextcloud_source.file_id(path)
        )
        assert record is not None, f"TC-STREAM-001: {path} is not in the graph"

        response = pipeshub_client.stream_record(record.id)
        assert response.status_code == 200
        assert response.content.decode() == SEED_FILES[LEAVE_POLICY]
        logger.info("TC-STREAM-001 passed: %s streamed", path)

    @pytest.mark.order(3)
    async def test_tc_incr_001_files_uploaded_together_all_appear(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: Every file of an upload into a new folder appears, under that folder.

        Nextcloud merges events that happen together into one activity, which
        names only its first file. The folder must also be filed under its
        real parent, not at the top of the drive.
        """
        connector_id = nextcloud_connector["connector_id"]
        folder = nextcloud_connector["folder"]
        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        nextcloud_source.put(f"{folder}/Engineering/runbook.txt", "Drain traffic, then restart.")
        nextcloud_source.put(f"{folder}/Engineering/oncall.md", "# On-call\n\nWeekly rotation.")
        after_count = await sync_until_names_visible(
            pipeshub_client,
            graph_provider,
            connector_id,
            ["Engineering", "runbook.txt", "oncall.md"],
        )
        assert after_count == before_count + 3, (
            f"TC-INCR-001: expected the folder and both files as new records; "
            f"count went from {before_count} to {after_count}"
        )

        expected_parents = {
            f"{folder}/Engineering": folder,
            f"{folder}/Engineering/runbook.txt": f"{folder}/Engineering",
            f"{folder}/Engineering/oncall.md": f"{folder}/Engineering",
        }
        for path, parent in expected_parents.items():
            record = await graph_provider.get_record_by_external_id(
                connector_id, nextcloud_source.file_id(path)
            )
            assert record is not None, f"TC-INCR-001: {path} is not in the graph"
            assert record.parent_external_record_id == nextcloud_source.file_id(parent), (
                f"TC-INCR-001: {path} should be filed under {parent}"
            )
        logger.info("TC-INCR-001 passed: before=%d, after=%d", before_count, after_count)

    @pytest.mark.order(4)
    async def test_tc_upd_001_editing_a_file_reindexes_it(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPD-001: An edited file is re-indexed in place: a higher version, no second record."""
        connector_id = nextcloud_connector["connector_id"]
        path = f"{nextcloud_connector['folder']}/{LEAVE_POLICY}"
        name = LEAVE_POLICY.rsplit("/", 1)[-1]
        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )
        before_record = await graph_provider.get_record_by_name(connector_id, name)
        assert before_record is not None, f"TC-UPD-001: {name} is not in the graph"
        before_version = before_record.get("version")

        updated = "Everyone gets 30 days of paid leave a year."
        nextcloud_source.put(path, updated)
        restart_sync(pipeshub_client, connector_id)
        after_record = await wait_for_record_reindex(
            graph_provider, connector_id, name, before_version
        )
        assert after_record.get("version") > before_version

        after_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )
        assert after_count == before_count, (
            f"TC-UPD-001: record count moved from {before_count} to {after_count} "
            f"after editing {name}; it must update the record, not add one"
        )
        record = await graph_provider.get_record_by_external_id(
            connector_id, nextcloud_source.file_id(path)
        )
        assert pipeshub_client.stream_record(record.id).content.decode() == updated
        logger.info("TC-UPD-001 passed: %s re-indexed, count stable at %d", name, after_count)

    @pytest.mark.order(5)
    async def test_tc_del_001_deleted_file_is_removed(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DEL-001: Deleting a file removes its record, and only its record."""
        connector_id = nextcloud_connector["connector_id"]
        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        nextcloud_source.delete(f"{nextcloud_connector['folder']}/notes.txt")
        await sync_until_names_absent(pipeshub_client, graph_provider, connector_id, ["notes.txt"])

        after_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )
        assert after_count == before_count - 1, (
            f"TC-DEL-001: expected one record fewer after deleting notes.txt; "
            f"count went from {before_count} to {after_count}"
        )
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info("TC-DEL-001 passed: notes.txt removed, count %d", after_count)

    @pytest.mark.order(6)
    async def test_tc_filter_001_excluded_file_type_is_not_indexed(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-001: With .txt excluded, a new .txt file is skipped and a new .md is not."""
        connector_id = nextcloud_connector["connector_id"]
        folder = nextcloud_connector["folder"]
        suffix = uuid.uuid4().hex[:6]
        excluded, included = f"excluded-{suffix}.txt", f"included-{suffix}.md"
        nextcloud_source.put(f"{folder}/Handbook/{excluded}", "Must not be indexed.")
        nextcloud_source.put(f"{folder}/Handbook/{included}", "Must be indexed.")

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

        async def _included_visible() -> bool:
            return await graph_provider.get_record_by_name(connector_id, included) is not None

        await wait_until_graph_condition(
            connector_id,
            check=_included_visible,
            timeout=DEFAULT_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description=f"sync of {included} after the filter change",
        )
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC
        )
        await graph_provider.assert_record_not_exists(connector_id, excluded)
        logger.info("TC-FILTER-001 passed: %s indexed, %s skipped", included, excluded)
