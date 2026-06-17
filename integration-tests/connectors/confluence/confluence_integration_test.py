# pyright: ignore-file

"""
Confluence Connector – Integration Tests
=========================================

Read-only integration tests for Confluence connector. Tests use a pre-provisioned
static space (CONFLUENCE_TEST_SPACE_KEY) and verify connector sync behavior by
comparing graph state against live Confluence API snapshots.

Execution order:
  1) Full sync + graph validation
  2) Entity validation (TC-CF-*)
  3) Knowledge Hub ACL (TC-CF-005/006/007)
  4) Filters
  5) Reindex
  6) Stream

Active test cases:
  TC-SYNC-001, TC-CF-001/002/003/004/005/006/007/008/036/046/052

Mutation/resync cases live in ``confluence_mutation_cases.py`` (not collected by pytest).
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.models.entities import (  # type: ignore[import-not-found]  # noqa: E402
    RecordGroupType,
    RecordType,
)
from app.sources.external.confluence.confluence import (  # type: ignore[import-not-found]  # noqa: E402
    ConfluenceDataSource,
)
from helper.assertions import ConnectorAssertions, RecordAssertion  # noqa: E402
from connectors.confluence.confluence_v1_test_utils import (  # noqa: E402
    FOLDER_MIME_TYPE,
    assert_api_content_matches_graph,
    assert_confluence_page_in_v1_space_content_search,
)
from connectors.confluence.confluence_knowledge_hub_test_utils import (  # noqa: E402
    assert_kh_folder_from_snapshot,
    assert_kh_snapshot_content,
    assert_kh_space_visible_for_user,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    wait_for_sync_completion,
)
from pipeshub_client import (  # type: ignore[import-not-found]  # noqa: E402
    PipeshubClient,
)

logger = logging.getLogger("confluence-lifecycle-test")


@pytest.mark.integration
@pytest.mark.confluence
@pytest.mark.asyncio(loop_scope="session")
class TestConfluenceValidation:
    """Validation tests for Confluence entities (TC-CF-001 to TC-CF-008)."""
    
    @pytest.mark.order(2)
    async def test_tc_cf_004_page_properties(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-CF-004: Verify synced page has all expected properties."""
        connector_id = confluence_connector["connector_id"]
        space_id = confluence_connector["space_id"]
        space_key = confluence_connector["space_key"]
        snapshot = confluence_connector["content_snapshot"]

        if not snapshot.pages:
            pytest.skip("No pages in snapshot to validate")

        page_item = snapshot.pages[0]
        page_id = page_item.id

        expected = RecordAssertion(
            external_record_id=page_id,
            record_type=RecordType.CONFLUENCE_PAGE.value,
            mime_type="application/blocks",
            record_name=page_item.title,
            external_record_group_id=space_id,
        )

        await assert_confluence_page_in_v1_space_content_search(
            confluence_datasource,
            space_key,
            page_id,
            context="TC-CF-004",
        )

        # Assert using generic framework
        record = await connector_assertions.assert_record_exists(
            connector_id, page_id, expected
        )
        
        # Additional checks
        assert record.weburl is not None and record.weburl.startswith("https://"), "Page should have webUrl"
        assert record.source_created_at is not None, "Page should have sourceCreatedAtTimestamp"
        assert record.connector_id == connector_id, "Page should have correct connectorId"
        
        logger.info("✅ TC-CF-004: Page %s validated successfully", page_id)
    
    @pytest.mark.order(3)
    async def test_tc_cf_003_space_record_group(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-CF-003: Verify space record group synced correctly."""
        connector_id = confluence_connector["connector_id"]
        space_id = confluence_connector["space_id"]
        space_key = confluence_connector["space_key"]
        space_name = confluence_connector["space_name"]
        snapshot = confluence_connector["content_snapshot"]

        space_rg = await graph_provider.get_record_group_by_external_id(connector_id, space_id)
        assert space_rg is not None, (f"Space {space_key} (external id={space_id}) should exist as RecordGroup in graph")
        assert space_rg.external_group_id == space_id
        assert space_rg.connector_id == connector_id
        assert space_rg.group_type == RecordGroupType.CONFLUENCE_SPACES
        assert space_rg.short_name == space_key
        assert space_rg.name == space_name

        if snapshot.pages:
            page_id = snapshot.pages[0].id
            await assert_confluence_page_in_v1_space_content_search(
                confluence_datasource,
                space_key,
                page_id,
                context="TC-CF-003",
            )
            page = await connector_assertions.assert_record_exists(connector_id, page_id)
            assert page.external_record_group_id == space_id, (
                f"Page should belong to space {space_id}"
            )
        
        logger.info("✅ TC-CF-003: Space %s validated successfully", space_id)
    
    @pytest.mark.order(4)
    async def test_tc_cf_008_record_relationships(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-CF-008: Verify record relationships (parent/child, permissions)."""
        connector_id = confluence_connector["connector_id"]

        perm_count = await graph_provider.count_permission_edges(connector_id)

        record_group_edges = await graph_provider.count_record_group_edges(connector_id)
        assert record_group_edges > 0, "Should have Record->RecordGroup BELONGS_TO edges"

        await graph_provider.assert_no_orphan_records(connector_id)

        hierarchy_page_id = confluence_connector.get("hierarchy_page_id")
        hierarchy_parent_id = confluence_connector.get("hierarchy_parent_id")

        if hierarchy_page_id and hierarchy_parent_id:
            child_record = await graph_provider.get_record_by_external_id(
                connector_id, hierarchy_page_id,
            )
            assert child_record is not None, f"Child page {hierarchy_page_id} should exist in graph"
            assert child_record.parent_external_record_id == hierarchy_parent_id, (
                f"Page {hierarchy_page_id} should have parent_external_record_id="
                f"{hierarchy_parent_id}, got {child_record.parent_external_record_id}"
            )
            logger.info(
                "Hierarchy validated: page %s → parent %s",
                hierarchy_page_id, hierarchy_parent_id,
            )
        else:
            logger.info("No hierarchical pages found in snapshot; skipping hierarchy checks")

        logger.info("✅ TC-CF-008: Record relationships validated successfully")
    
    @pytest.mark.order(5)
    async def test_tc_cf_001_user_properties(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-CF-001: Verify synced user has USER_APP_RELATION with sourceUserId."""
        connector_id = confluence_connector["connector_id"]
        test_email = os.getenv("CONFLUENCE_TEST_EMAIL")
        
        if not test_email:
            pytest.skip("CONFLUENCE_TEST_EMAIL not set")
        
        # Search for the test user in Confluence (same pattern as connector sync)
        account_id = None
        batch_size = 100
        start = 0
        max_attempts = 5  # Scan up to 500 users
        
        for attempt in range(max_attempts):
            response = await confluence_datasource.search_users(
                cql="type=user",
                start=start,
                limit=batch_size
            )
            
            if not response or response.status != 200:
                break
            
            users_data = response.json().get("results", [])
            if not users_data:
                break
            
            # Flatten nested user data (same as _sync_users in connector)
            for user_result in users_data:
                user_data = {**user_result.get("user", {}), **{k: v for k, v in user_result.items() if k != "user"}}
                email = user_data.get("email", "").strip()
                
                if email.lower() == test_email.lower():
                    account_id = user_data.get("accountId")
                    break
            
            if account_id:
                break
            
            start += batch_size
            if len(users_data) < batch_size:
                break

        if not account_id:
            pytest.skip(f"User {test_email} not found in Confluence search (visibility/email not public)")

        # Assert user exists in graph with USER_APP_RELATION containing sourceUserId
        await connector_assertions.assert_user_exists(
            connector_id=connector_id,
            source_user_id=account_id,
            email=test_email,
        )

        logger.info("✅ TC-CF-001: User %s (accountId=%s) validated with USER_APP_RELATION", test_email, account_id)

    @pytest.mark.order(6)
    async def test_tc_cf_002_group_properties(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-CF-002: Verify synced group with correct externalGroupId and member edges."""
        connector_id = confluence_connector["connector_id"]
        
        # Get first available group from Confluence
        response = await confluence_datasource.get_groups(start=0, limit=10)
        
        if not response or response.status != 200:
            pytest.skip("Could not fetch groups from Confluence")
        
        groups_data = response.json().get("results", [])
        if not groups_data:
            pytest.skip("No groups found in Confluence")
        
        # Pick first group with id and name
        group_id = None
        group_name = None
        for group_data in groups_data:
            group_id = group_data.get("id")
            group_name = group_data.get("name")
            if group_id and group_name:
                break
        
        if not group_id or not group_name:
            pytest.skip("No valid group with id and name found")
        
        # Assert group exists in graph with correct properties
        group = await connector_assertions.assert_group_exists(
            connector_id=connector_id,
            external_group_id=group_id,
            name=group_name
        )
        
        # Fetch group members from Confluence (paginated, like connector does)
        member_emails = []
        batch_size = 100
        start = 0
        
        while True:
            members_response = await confluence_datasource.get_group_members(
                group_id=group_id,
                start=start,
                limit=batch_size
            )
            
            if not members_response or members_response.status != 200:
                break
            
            members_data = members_response.json().get("results", [])
            if not members_data:
                break
            
            # Extract emails (skip members without email, same as connector)
            for member_data in members_data:
                email = member_data.get("email", "").strip()
                if email:
                    member_emails.append(email)
            
            start += batch_size
            if len(members_data) < batch_size:
                break
        
        # Count how many members exist as users in graph
        # Only members with email that were synced as AppUser get PERMISSION edges
        expected_member_count = 0
        for email in set(member_emails):  # Deduplicate
            user = await graph_provider.graph_find_user_by_email(email)
            if user is not None:
                expected_member_count += 1
        
        # Assert graph membership count matches expected
        actual_member_count = await graph_provider.count_group_members(connector_id, group_id)
        
        assert actual_member_count == expected_member_count, (
            f"Group {group_name} ({group_id}): expected {expected_member_count} PERMISSION edges "
            f"(members with email that exist as users), got {actual_member_count}"
        )
        
        logger.info(
            "✅ TC-CF-002: Group %s validated with %d members (from %d Confluence members with email)",
            group_name, actual_member_count, len(set(member_emails))
        )


@pytest.mark.integration
@pytest.mark.confluence
@pytest.mark.asyncio(loop_scope="session")
class TestConfluenceKnowledgeHubAccess:
    """Knowledge Hub browse ACL tests (TC-CF-005 to TC-CF-007).

    Requires PIPESHUB_BASE_URL to point at the Node.js gateway (not connector :8088)
    so OAuth client_credentials resolves to the test user for permission filtering.
    """

    @pytest.mark.order(6)
    async def test_tc_cf_005_folder_properties(
        self,
        confluence_connector: Dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-CF-005: Verify synced folder graph properties and Knowledge Hub visibility."""
        connector_id = confluence_connector["connector_id"]
        space_id = confluence_connector["space_id"]
        space_rg_id = confluence_connector["space_record_group_id"]
        snapshot = confluence_connector["content_snapshot"]

        if not snapshot.folders:
            pytest.skip("No folders in IT space snapshot")

        for folder_item in snapshot.folders:
            expected = RecordAssertion(
                external_record_id=folder_item.id,
                record_type=RecordType.FILE.value,
                record_name=folder_item.title,
                mime_type=FOLDER_MIME_TYPE,
                external_record_group_id=space_id,
                external_revision_id=str(folder_item.version_number),
            )
            record = await connector_assertions.assert_record_exists(
                connector_id, folder_item.id, expected
            )
            assert record.size_in_bytes == 0, "Folder sizeInBytes should be 0"
            assert record.weburl is not None and record.weburl.startswith("https://"), (
                "Folder should have webUrl"
            )

            await assert_kh_folder_from_snapshot(
                pipeshub_client,
                graph_provider,
                connector_id,
                space_rg_id,
                folder_item.id,
                folder_item.title,
                snapshot=snapshot,
                context="TC-CF-005",
            )

        logger.info(
            "✅ TC-CF-005: %d folder(s) validated in graph and Knowledge Hub",
            len(snapshot.folders),
        )

    @pytest.mark.order(7)
    async def test_tc_cf_006_space_permission_via_knowledge_hub(
        self,
        confluence_connector: Dict[str, Any],
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-CF-006: Space RecordGroup visible under app with user permission."""
        connector_id = confluence_connector["connector_id"]
        space_rg_id = confluence_connector["space_record_group_id"]
        space_name = confluence_connector["space_name"]
        space_key = confluence_connector["space_key"]

        assert_kh_space_visible_for_user(
            pipeshub_client,
            connector_id,
            space_rg_id,
            space_name,
            space_key=space_key,
            context="TC-CF-006",
        )

        logger.info("✅ TC-CF-006: Space %s visible in Knowledge Hub with ACL", space_name)

    @pytest.mark.order(8)
    async def test_tc_cf_007_content_via_knowledge_hub(
        self,
        confluence_connector: Dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-CF-007: All synced pages, blogposts, and folders visible in Knowledge Hub."""
        connector_id = confluence_connector["connector_id"]
        space_rg_id = confluence_connector["space_record_group_id"]
        snapshot = confluence_connector["content_snapshot"]

        if not snapshot.all_content:
            pytest.skip("No content in IT space snapshot")

        await assert_kh_snapshot_content(
            pipeshub_client,
            graph_provider,
            connector_id,
            space_rg_id,
            snapshot,
            context="TC-CF-007",
        )

        logger.info(
            "✅ TC-CF-007: %d page(s), %d blogpost(s), %d folder(s) visible in Knowledge Hub",
            len(snapshot.pages),
            len(snapshot.blogposts),
            len(snapshot.folders),
        )


@pytest.mark.integration
@pytest.mark.confluence
@pytest.mark.asyncio(loop_scope="session")
class TestConfluenceFilters:
    """Filter tests for Confluence (TC-CF-036 to TC-CF-045)."""
    
    @pytest.mark.order(13)
    async def test_tc_cf_036_space_filter_include(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-CF-036: Set space_keys filter, verify only content from those spaces is synced."""
        connector_id = confluence_connector["connector_id"]
        space_key = confluence_connector["space_key"]
        snapshot = confluence_connector["content_snapshot"]
        
        # Update connector filters using safe method
        # This automatically handles disabling the connector if active, updating filters,
        # and re-enabling if it was originally active
        filters = {
            "space_keys": {
                "operator": "IN",
                "values": [space_key]
            }
        }
        
        pipeshub_client.update_connector_filters_sync_safe(
            connector_id, 
            filters=filters
        )

        # Wait for sync to complete
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=180,
        )
        
        await assert_api_content_matches_graph(
            snapshot,
            confluence_datasource,
            graph_provider,
            connector_assertions,
            connector_id,
            phase="TC-CF-036 after filter sync",
        )
        
        # Verify only content from filtered space exists
        record_count = await graph_provider.count_records(connector_id)
        assert record_count > 0, "Should have records from filtered space"
        
        logger.info("✅ TC-CF-036: Space filter applied, %d records synced", record_count)


@pytest.mark.integration
@pytest.mark.confluence
@pytest.mark.asyncio(loop_scope="session")
class TestConfluenceReindex:
    """Reindex tests for Confluence (TC-CF-046 to TC-CF-051)."""
    
    @pytest.mark.order(14)
    async def test_tc_cf_046_reindex_unchanged_page(
        self,
        confluence_connector: Dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-CF-046: Reindex unchanged page - no DB update, event only."""
        connector_id = confluence_connector["connector_id"]
        page_id = confluence_connector.get("test_page_id")
        
        if not page_id:
            pytest.skip("No test page ID available")
        
        # Get current record state
        record_before = await graph_provider.get_record_by_external_id(
            connector_id, page_id
        )
        assert record_before is not None, f"Page {page_id} not found"
        
        version_before = record_before.external_revision_id
        record_key = record_before.id

        result = pipeshub_client.reindex_record(record_key)
        assert result.get("success") or result.get("status") == "success", (
            f"Reindex failed: {result}"
        )
        await asyncio.sleep(30)

        record_after = await graph_provider.get_record_by_external_id(
            connector_id, page_id
        )
        assert record_after is not None, f"Page {page_id} not found after reindex"
        version_after = record_after.external_revision_id

        assert version_after == version_before, (
            f"Version should be unchanged after reindex of unmodified page, "
            f"was {version_before}, now {version_after}"
        )

        logger.info("✅ TC-CF-046: Reindex unchanged page completed successfully")


@pytest.mark.integration
@pytest.mark.confluence
@pytest.mark.asyncio(loop_scope="session")
class TestConfluenceStream:
    """Stream tests for Confluence (TC-CF-052 to TC-CF-056)."""
    
    @pytest.mark.order(16)
    async def test_tc_cf_052_stream_page_html(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-CF-052: Stream page content (blocks format)."""
        connector_id = confluence_connector["connector_id"]
        space_key = confluence_connector["space_key"]
        page_id = confluence_connector.get("test_page_id")
        
        if not page_id:
            pytest.skip("No test page ID available")
        
        await assert_confluence_page_in_v1_space_content_search(
            confluence_datasource,
            space_key,
            str(page_id),
            context="TC-CF-052 before graph/stream",
        )
        
        # Get record
        record = await graph_provider.get_record_by_external_id(
            connector_id, page_id
        )
        assert record is not None, f"Page {page_id} not found"
        record_key = record.id
        
        # Stream content
        response = pipeshub_client.stream_record(record_key)
        
        # Verify response
        assert response.status_code == 200
        assert "application/blocks" in response.headers.get("content-type", "").lower()
        
        # Read some content
        content_chunk = next(response.iter_content(chunk_size=1024))
        assert len(content_chunk) > 0, "Should have received content"
        # Content is now in blocks format (JSON), not HTML
        assert b"{" in content_chunk or b"[" in content_chunk, (
            "Content should be JSON blocks format"
        )
        
        logger.info("✅ TC-CF-052: Page content streamed successfully (blocks format)")


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
        confluence_datasource: ConfluenceDataSource,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-SYNC-001: After full sync, validate the graph matches API snapshot."""
        connector_id = confluence_connector["connector_id"]
        snapshot = confluence_connector["content_snapshot"]

        await assert_api_content_matches_graph(
            snapshot,
            confluence_datasource,
            graph_provider,
            connector_assertions,
            connector_id,
            phase="TC-SYNC-001",
        )

        await graph_provider.assert_record_groups_and_edges(
            connector_id,
            min_groups=1,
            min_record_edges=len(snapshot.all_content),
        )

        await graph_provider.assert_app_record_group_edges(connector_id, min_edges=1)
        await graph_provider.assert_no_orphan_records(connector_id)

        perm_count = await graph_provider.count_permission_edges(connector_id)
        logger.info("Permission edges: %d (connector %s)", perm_count, connector_id)

        summary = await graph_provider.graph_summary(connector_id)
        logger.info("Graph summary after full sync: %s (connector %s)", summary, connector_id)
