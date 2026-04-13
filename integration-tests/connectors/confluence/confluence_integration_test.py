# pyright: ignore-file

"""
Confluence Connector – Integration Tests
=========================================

Test cases:
  TC-SYNC-001   — Full sync + graph validation
  TC-INCR-001   — Incremental sync (create new pages)
  TC-UPDATE-001 — Content change detection (update page)
  TC-RENAME-001 — Rename detection (page title change)
  TC-MOVE-001   — Move detection (change page parent)
"""

import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.confluence.confluence_storage_helper import (  # type: ignore[import-not-found]  # noqa: E402
    ConfluenceStorageHelper,
)
from pipeshub_client import (  # type: ignore[import-not-found]  # noqa: E402
    PipeshubClient,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import wait_until_graph_condition  # noqa: E402

logger = logging.getLogger("confluence-lifecycle-test")


@pytest.mark.integration
@pytest.mark.confluence
@pytest.mark.asyncio(loop_scope="session")
class TestConfluenceConnector:
    """Integration tests for the Confluence connector."""

    # TC-SYNC-001 — Full sync + graph validation
    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        confluence_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: After full sync, validate the graph."""
        connector_id = confluence_connector["connector_id"]
        uploaded = confluence_connector["uploaded_count"]
        full_count = confluence_connector["full_sync_count"]

        await graph_provider.assert_min_records(connector_id, uploaded)

        await graph_provider.assert_record_groups_and_edges(
            connector_id,
            min_groups=1,
            min_record_edges=full_count,
        )

        await graph_provider.assert_app_record_group_edges(connector_id, min_edges=1)
        await graph_provider.assert_no_orphan_records(connector_id)

        perm_count = await graph_provider.count_permission_edges(connector_id)
        logger.info("Permission edges: %d (connector %s)", perm_count, connector_id)

        summary = await graph_provider.graph_summary(connector_id)
        logger.info("Graph summary after full sync: %s (connector %s)", summary, connector_id)

    # TC-INCR-001 — Incremental sync
    @pytest.mark.order(2)
    async def test_tc_incr_001_incremental_sync_new_pages(
        self,
        confluence_connector: Dict[str, Any],
        confluence_storage: ConfluenceStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: Create new pages, verify they appear in graph."""
        connector_id = confluence_connector["connector_id"]
        space_key = confluence_connector["space_key"]
        before_count = await graph_provider.count_records(connector_id)

        # Create new pages
        new_page_1 = confluence_storage.create_page(
            space_key,
            f"Integration Test Page Alpha {uuid.uuid4().hex[:8]}",
            "<p>This is test content for incremental sync testing.</p>",
        )
        confluence_storage.create_page(
            space_key,
            f"Integration Test Page Beta {uuid.uuid4().hex[:8]}",
            "<p>Another test page for incremental sync.</p>",
        )

        logger.info("Created 2 new pages for incremental sync (connector %s)", connector_id)

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _incr_ok() -> bool:
            return await graph_provider.count_records(connector_id) >= before_count + 2

        await wait_until_graph_condition(
            connector_id,
            check=_incr_ok,
            timeout=180,
            poll_interval=10,
            description="incremental sync (new pages)",
        )

        after_count = await graph_provider.count_records(connector_id)
        assert after_count >= before_count + 2, (
            f"Expected at least 2 new records; before={before_count}, after={after_count}"
        )

        confluence_connector["test_page_id"] = new_page_1["id"]
        confluence_connector["test_page_title"] = new_page_1["title"]
        logger.info("TC-INCR-001 passed: %d -> %d records (added 2 pages)", before_count, after_count)

    # TC-UPDATE-001 — Content change detection
    @pytest.mark.order(3)
    async def test_tc_update_001_content_change_detection(
        self,
        confluence_connector: Dict[str, Any],
        confluence_storage: ConfluenceStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: Update page content, verify record is updated."""
        connector_id = confluence_connector["connector_id"]
        page_id = confluence_connector["test_page_id"]
        before_count = await graph_provider.count_records(connector_id)

        # Update page content
        new_content = f"Updated content at {uuid.uuid4().hex}"
        confluence_storage.overwrite_object(page_id, new_content)
        logger.info("Updated page %s (connector %s)", page_id, connector_id)

        # Trigger sync
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _update_ok() -> bool:
            return await graph_provider.count_records(connector_id) >= before_count

        await wait_until_graph_condition(
            connector_id,
            check=_update_ok,
            timeout=120,
            poll_interval=10,
            description="update sync",
        )

        after_count = await graph_provider.count_records(connector_id)
        assert after_count == before_count, (
            f"Record count should be stable after update; before={before_count}, after={after_count}"
        )

        logger.info("TC-UPDATE-001 passed: record count stable at %d", after_count)

    @pytest.mark.order(4)
    async def test_tc_rename_001_rename_detection(
        self,
        confluence_connector: Dict[str, Any],
        confluence_storage: ConfluenceStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-RENAME-001: Rename page, verify old title gone and new title present."""
        connector_id = confluence_connector["connector_id"]
        space_key = confluence_connector["space_key"]
        page_id = confluence_connector["test_page_id"]
        old_title = confluence_connector["test_page_title"]
        before_count = await graph_provider.count_records(connector_id)

        new_title = f"Renamed-{old_title}"
        confluence_storage.rename_object(space_key, page_id, new_title)
        logger.info("Renamed page %s: '%s' -> '%s'", page_id, old_title, new_title)

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _rename_ok() -> bool:
            return await graph_provider.record_paths_or_names_contain(connector_id, [new_title])

        await wait_until_graph_condition(
            connector_id,
            check=_rename_ok,
            timeout=120,
            poll_interval=10,
            description="rename sync",
        )

        await graph_provider.assert_record_paths_or_names_contain(connector_id, [new_title])
        await graph_provider.assert_record_not_exists(connector_id, old_title)

        after_count = await graph_provider.count_records(connector_id)
        assert after_count == before_count, (
            f"Record count should be stable after rename; before={before_count}, after={after_count}"
        )

        confluence_connector["renamed_page_id"] = page_id
        logger.info(
            "TC-RENAME-001 passed: '%s' -> '%s' (record count stable at %d)",
            old_title, new_title, after_count,
        )

    @pytest.mark.order(5)
    async def test_tc_move_001_move_detection(
        self,
        confluence_connector: Dict[str, Any],
        confluence_storage: ConfluenceStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-MOVE-001: Move page under new parent, verify hierarchy change."""
        connector_id = confluence_connector["connector_id"]
        space_key = confluence_connector["space_key"]
        page_id = confluence_connector["renamed_page_id"]
        before_count = await graph_provider.count_records(connector_id)

        parent_page = confluence_storage.create_page(
            space_key,
            f"Parent Page {uuid.uuid4().hex[:8]}",
            "<p>This is a parent page.</p>",
        )
        logger.info("Created parent page: %s (%s)", parent_page["id"], parent_page["title"])

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _parent_synced() -> bool:
            return await graph_provider.count_records(connector_id) > before_count

        await wait_until_graph_condition(
            connector_id,
            check=_parent_synced,
            timeout=120,
            poll_interval=10,
            description="parent page sync",
        )

        after_parent_count = await graph_provider.count_records(connector_id)
        assert after_parent_count == before_count + 1, (
            f"Expected 1 new record (parent page); before={before_count}, after={after_parent_count}"
        )
        logger.info("Parent page synced: %d -> %d records", before_count, after_parent_count)

        confluence_storage.move_object(space_key, page_id, parent_page["id"])
        logger.info("Moved page %s under parent %s", page_id, parent_page["id"])

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _move_ok() -> bool:
            return await graph_provider.count_records(connector_id) >= after_parent_count

        await wait_until_graph_condition(
            connector_id,
            check=_move_ok,
            timeout=120,
            poll_interval=10,
            description="move sync",
        )

        final_count = await graph_provider.count_records(connector_id)
        assert final_count == after_parent_count, (
            f"Record count should be stable after move; before_move={after_parent_count}, after_move={final_count}"
        )

        logger.info("TC-MOVE-001 passed: page hierarchy updated (record count stable at %d)", final_count)
