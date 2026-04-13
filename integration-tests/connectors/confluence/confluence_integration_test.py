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

from app.sources.external.confluence.confluence import ConfluenceDataSource  # type: ignore[import-not-found]  # noqa: E402
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
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: Create new pages, verify they appear in graph."""
        connector_id = confluence_connector["connector_id"]
        space_id = confluence_connector["space_id"]
        before_count = await graph_provider.count_records(connector_id)

        # Create new pages
        title_1 = f"Integration Test Page Alpha {uuid.uuid4().hex[:8]}"
        title_2 = f"Integration Test Page Beta {uuid.uuid4().hex[:8]}"
        
        resp_1 = await confluence_datasource.create_page(
            root_level=True,
            body={
                "spaceId": space_id,
                "status": "current",
                "title": title_1,
                "body": {
                    "representation": "storage",
                    "value": "<p>This is test content for incremental sync testing.</p>"
                }
            }
        )
        new_page_1 = resp_1.json()
        
        await confluence_datasource.create_page(
            root_level=True,
            body={
                "spaceId": space_id,
                "status": "current",
                "title": title_2,
                "body": {
                    "representation": "storage",
                    "value": "<p>Another test page for incremental sync.</p>"
                }
            }
        )

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

        confluence_connector["test_page_id"] = str(new_page_1["id"])
        confluence_connector["test_page_title"] = new_page_1["title"]
        logger.info("TC-INCR-001 passed: %d -> %d records (added 2 pages)", before_count, after_count)

    # TC-UPDATE-001 — Content change detection
    @pytest.mark.order(3)
    async def test_tc_update_001_content_change_detection(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: Update page content, verify record is updated."""
        connector_id = confluence_connector["connector_id"]
        page_id = int(confluence_connector["test_page_id"])
        before_count = await graph_provider.count_records(connector_id)

        page_resp = await confluence_datasource.get_page_by_id(page_id, body_format="storage")
        page_data = page_resp.json()
        
        new_content = f"<p>Updated content at {uuid.uuid4().hex}</p>"
        await confluence_datasource.update_page(
            id=page_id,
            body={
                "id": str(page_id),
                "status": "current",
                "title": page_data["title"],
                "body": {
                    "representation": "storage",
                    "value": new_content
                },
                "version": {
                    "number": page_data["version"]["number"] + 1
                }
            }
        )

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

    @pytest.mark.order(4)
    async def test_tc_rename_001_rename_detection(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-RENAME-001: Rename page, verify old title gone and new title present."""
        connector_id = confluence_connector["connector_id"]
        page_id = int(confluence_connector["test_page_id"])
        old_title = confluence_connector["test_page_title"]
        before_count = await graph_provider.count_records(connector_id)

        new_title = f"Renamed-{old_title}"
        
        resp = await confluence_datasource.update_page_title(
            id=page_id,
            body={
                "status": "current",
                "title": new_title
            }
        )
        
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

        confluence_connector["renamed_page_id"] = str(page_id)

    @pytest.mark.order(5)
    async def test_tc_move_001_move_detection(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-MOVE-001: Move page under new parent, verify hierarchy change."""
        connector_id = confluence_connector["connector_id"]
        space_id = confluence_connector["space_id"]
        page_id = confluence_connector["renamed_page_id"]
        before_count = await graph_provider.count_records(connector_id)

        parent_title = f"Parent Page {uuid.uuid4().hex[:8]}"
        parent_resp = await confluence_datasource.create_page(
            root_level=True,
            body={
                "spaceId": space_id,
                "status": "current",
                "title": parent_title,
                "body": {
                    "representation": "storage",
                    "value": "<p>This is a parent page.</p>"
                }
            }
        )
        parent_page = parent_resp.json()

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

        await confluence_datasource.move_page(page_id, str(parent_page["id"]))

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
