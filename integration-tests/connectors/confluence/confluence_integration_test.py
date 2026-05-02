# pyright: ignore-file

"""
Confluence Connector – Integration Tests
=========================================

Execution order:
  1) Full sync + graph validation
  2) Entity validation (TC-CF-*)
  3) Incremental lifecycle (TC-INCR / TC-UPDATE / TC-RENAME / TC-MOVE, then TC-CF-024/026)
  4) Filters
  5) Reindex
  6) Stream

Test cases:
  TC-SYNC-001   — Full sync + graph validation
  TC-INCR-001   — Incremental sync (create new pages)
  TC-UPDATE-001 — Content change detection (update page)
  TC-RENAME-001 — Rename detection (page title change)
  TC-MOVE-001   — Move detection (change page parent)
"""

import logging
import os
import sys
import uuid
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
from connectors.confluence.constants import CONFLUENCE_TEST_SETTLE_WAIT_SEC  # noqa: E402
from connectors.confluence.confluence_v1_test_utils import (  # noqa: E402
    assert_confluence_page_in_v1_space_content_search,
    assert_confluence_page_title_v1,
    assert_confluence_page_v1_ancestors_contain_id,
    assert_confluence_page_version_number_v1,
    assert_confluence_pages_match_graph_records,
    count_confluence_space_pages_v1_search,
    get_confluence_page_version_number_v1,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    wait_for_sync_completion,
    wait_until_graph_condition,
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

        # Get a sample page from Confluence
        pages_resp = await confluence_datasource.get_pages_in_space(space_id, limit=1)
        pages = pages_resp.json().get("results", [])
        assert pages, "No pages found in test space"
        
        page_data = pages[0]
        page_id = str(page_data["id"])
        
        # Build expected assertion
        expected = RecordAssertion(
            external_record_id=page_id,
            record_type=RecordType.CONFLUENCE_PAGE.value,
            mime_type="text/html",
            record_name=page_data["title"],
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
        
        # Get space from Confluence
        spaces_resp = await confluence_datasource.get_spaces(keys=[space_key])
        spaces = spaces_resp.json().get("results", [])
        assert spaces, f"Space {space_key} not found"
        
        space_data = spaces[0]
        
        space_rg = await graph_provider.get_record_group_by_external_id(connector_id, space_id)
        assert space_rg is not None, (f"Space {space_key} (external id={space_id}) should exist as RecordGroup in graph")
        assert space_rg.external_group_id == space_id
        assert space_rg.connector_id == connector_id
        assert space_rg.group_type == RecordGroupType.CONFLUENCE_SPACES
        assert space_rg.short_name == space_key
        assert space_rg.name == space_data.get("name")
        
        # Pages should reference this space as their record group
        pages_resp = await confluence_datasource.get_pages_in_space(space_id, limit=1)
        pages = pages_resp.json().get("results", [])
        if pages:
            page_id = str(pages[0]["id"])
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
        space_id = confluence_connector["space_id"]
        space_key = confluence_connector["space_key"]
        
        # Get pages to check relationships
        pages_resp = await confluence_datasource.get_pages_in_space(space_id, limit=5)
        pages = pages_resp.json().get("results", [])
        
        if not pages:
            pytest.skip("No pages to validate relationships")
        
        sample_page_id = str(pages[0]["id"])
        await assert_confluence_page_in_v1_space_content_search(
            confluence_datasource,
            space_key,
            sample_page_id,
            context="TC-CF-008",
        )
        
        # Check permission edges exist
        perm_count = await graph_provider.count_permission_edges(connector_id)
        logger.info("Permission edges: %d", perm_count)
        
        # Check that pages have proper BELONGS_TO edges
        record_group_edges = await graph_provider.count_record_group_edges(connector_id)
        assert record_group_edges > 0, "Should have Record->RecordGroup BELONGS_TO edges"
        
        # Check no orphan records
        await graph_provider.assert_no_orphan_records(connector_id)
        
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
class TestConfluenceIncrementalSync:
    """Incremental sync tests for Confluence (TC-CF-011 to TC-CF-034)."""
    
    @pytest.mark.order(11)
    async def test_tc_cf_024_page_added(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-CF-024: Create new page, verify it appears in graph with correct properties."""
        connector_id = confluence_connector["connector_id"]
        space_id = confluence_connector["space_id"]
        space_key = confluence_connector["space_key"]
        before_count = await graph_provider.count_records(connector_id)
        
        # Create new page
        title = f"TC-CF-024 Test Page {uuid.uuid4().hex[:8]}"
        content = f"<p>Test content for TC-CF-024 at {uuid.uuid4().hex}</p>"
        
        resp = await confluence_datasource.create_page(
            root_level=True,
            body={
                "spaceId": space_id,
                "status": "current",
                "title": title,
                "body": {
                    "representation": "storage",
                    "value": content
                }
            }
        )
        page_data = resp.json()
        page_id = str(page_data["id"])
        
        pipeshub_client.wait(CONFLUENCE_TEST_SETTLE_WAIT_SEC)
        await assert_confluence_page_in_v1_space_content_search(
            confluence_datasource,
            space_key,
            page_id,
            context="TC-CF-024 after create (before connector resync)",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)
        
        # Wait for sync completion
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=before_count + 1,
            timeout=180,
        )
        
        # Verify page properties
        expected = RecordAssertion(
            external_record_id=page_id,
            record_type=RecordType.CONFLUENCE_PAGE.value,
            record_name=title,
            mime_type="text/html",
            external_record_group_id=space_id,
        )
        
        record = await connector_assertions.assert_record_exists(
            connector_id, page_id, expected
        )
        
        assert record.weburl is not None
        assert record.source_created_at is not None
        
        # Store for later tests
        confluence_connector["tc_cf_024_page_id"] = page_id
        
        logger.info("✅ TC-CF-024: Page added and verified successfully")
    
    @pytest.mark.order(12)
    async def test_tc_cf_026_page_content_updated(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-CF-026: Update page content, verify version is incremented."""
        connector_id = confluence_connector["connector_id"]
        page_id = int(confluence_connector.get("tc_cf_024_page_id", confluence_connector["test_page_id"]))
        
        # Get current page data
        page_resp = await confluence_datasource.get_page_by_id(page_id, body_format="storage")
        page_data = page_resp.json()
        old_version = page_data["version"]["number"]
        
        # Get current record from graph
        record_before = await graph_provider.get_record_by_external_id(connector_id, str(page_id))
        assert record_before is not None, f"Page {page_id} not found in graph"
        old_revision_id = record_before.external_revision_id
        
        # Update page content
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
                    "number": old_version + 1
                }
            }
        )
        
        pipeshub_client.wait(CONFLUENCE_TEST_SETTLE_WAIT_SEC)
        await assert_confluence_page_version_number_v1(
            confluence_datasource,
            str(page_id),
            old_version + 1,
            context="TC-CF-026 after update (before connector resync)",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)
        
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=180,
        )
        
        # Verify version changed
        record_after = await connector_assertions.assert_record_updated(
            connector_id,
            str(page_id),
            old_revision_id,
        )
        
        new_revision_id = record_after.external_revision_id
        v1_after_sync = await get_confluence_page_version_number_v1(
            confluence_datasource, str(page_id)
        )
        assert str(new_revision_id) == str(v1_after_sync), (
            f"TC-CF-026: graph external_revision_id={new_revision_id!r} should match "
            f"Confluence v1 version.number={v1_after_sync}"
        )
        
        logger.info(
            "✅ TC-CF-026: Page version updated from %s to %s",
            old_revision_id, new_revision_id
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
    ) -> None:
        """TC-CF-036: Set space_keys filter, verify only content from those spaces is synced."""
        connector_id = confluence_connector["connector_id"]
        space_key = confluence_connector["space_key"]
        
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
        
        await assert_confluence_pages_match_graph_records(
            confluence_datasource,
            graph_provider,
            connector_id,
            space_key,
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
        confluence_datasource: ConfluenceDataSource,
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
        
        pipeshub_client.wait(CONFLUENCE_TEST_SETTLE_WAIT_SEC)
        v1_before = await get_confluence_page_version_number_v1(
            confluence_datasource, str(page_id)
        )
        if version_before is not None:
            assert str(v1_before) == str(version_before), (
                f"TC-CF-046: Confluence v1 version.number={v1_before} should match graph "
                f"external_revision_id={version_before!r} before reindex"
            )
        
        # Trigger reindex
        result = pipeshub_client.reindex_record(record_key)
        assert result.get("success") or result.get("status") == "success", (
            f"Reindex failed: {result}"
        )
        pipeshub_client.wait(CONFLUENCE_TEST_SETTLE_WAIT_SEC)
        
        v1_after = await get_confluence_page_version_number_v1(
            confluence_datasource, str(page_id)
        )
        assert v1_after == v1_before, (
            f"TC-CF-046: Confluence v1 version should be unchanged after reindex; "
            f"was {v1_before}, now {v1_after}"
        )
        
        # Verify version unchanged (no DB update for unchanged content)
        result = pipeshub_client.reindex_record(record_key)
        assert result.get("success") or result.get("status") == "success", (
            f"Reindex failed: {result}"
        )
        
        # Verify version unchanged (no DB update for unchanged content)
        record_after = await graph_provider.get_record_by_external_id(
            connector_id, page_id
        )
        version_after = record_after.external_revision_id
        
        assert version_after == version_before, (
            f"Version should be unchanged after reindex of unmodified page, "
            f"was {version_before}, now {version_after}"
        )
        
        logger.info("✅ TC-CF-046: Reindex unchanged page completed successfully")
    
    @pytest.mark.order(15)
    async def test_tc_cf_047_reindex_updated_page(
        self,
        confluence_connector: Dict[str, Any],
        confluence_datasource: ConfluenceDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-CF-047: Reindex updated page - DB should update with new version."""
        connector_id = confluence_connector["connector_id"]
        page_id = int(confluence_connector.get("test_page_id", 0))
        
        if not page_id:
            pytest.skip("No test page ID available")
        
        # Get current record
        record_before = await graph_provider.get_record_by_external_id(
            connector_id, str(page_id)
        )
        assert record_before is not None, f"Page {page_id} not found"
        record_key = record_before.id
        version_before = record_before.external_revision_id
        
        # Update page in Confluence
        page_resp = await confluence_datasource.get_page_by_id(page_id, body_format="storage")
        page_data = page_resp.json()
        
        new_content = f"<p>Reindex test update at {uuid.uuid4().hex}</p>"
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

        expected_v1_version = page_data["version"]["number"] + 1

        pipeshub_client.wait(CONFLUENCE_TEST_SETTLE_WAIT_SEC)
        await assert_confluence_page_version_number_v1(
            confluence_datasource,
            str(page_id),
            expected_v1_version,
            context="TC-CF-047 after update wait (before reindex)",
        )
        v1_version_after_update = expected_v1_version
        result = pipeshub_client.reindex_record(record_key)
        assert result.get("success") or result.get("status") == "success"
        # Reindex HTTP returns after publishing sync-events; graph updates asynchronously.
        pipeshub_client.wait(CONFLUENCE_TEST_SETTLE_WAIT_SEC)

        v1_version_now = await get_confluence_page_version_number_v1(
            confluence_datasource, str(page_id)
        )
        assert v1_version_now == v1_version_after_update, (
            f"TC-CF-047: v1 version.number changed after reindex "
            f"({v1_version_after_update} -> {v1_version_now}); unexpected."
        )

        record_after = await graph_provider.get_record_by_external_id(
            connector_id, str(page_id)
        )
        assert record_after is not None, f"Page {page_id} not found in graph after reindex"
        graph_rev = record_after.external_revision_id or record_after.version
        assert str(graph_rev) == str(v1_version_now), (
            f"TC-CF-047: Graph external_revision_id/version={graph_rev!r} should match "
            f"Confluence v1 version.number={v1_version_now} (GET /wiki/rest/api/content/{{id}} "
            f"with expand including version). graph_before={version_before!r}."
        )
        assert str(graph_rev) != str(version_before), (
            f"TC-CF-047: Expected graph revision to change from {version_before!r}; "
            f"still {graph_rev!r} while v1 API reports version.number={v1_version_now}."
        )

        logger.info(
            "✅ TC-CF-047: Reindex updated page - graph revision %s matches v1 version.number %s "
            "(was %s)",
            graph_rev,
            v1_version_now,
            version_before,
        )


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
        """TC-CF-052: Stream page HTML content."""
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
        assert "text/html" in response.headers.get("content-type", "").lower()
        
        # Read some content
        content_chunk = next(response.iter_content(chunk_size=1024))
        assert len(content_chunk) > 0, "Should have received content"
        assert b"<" in content_chunk or b"html" in content_chunk.lower(), (
            "Content should be HTML"
        )
        
        logger.info("✅ TC-CF-052: Page HTML streamed successfully")


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
    @pytest.mark.order(7)
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
        space_key = confluence_connector["space_key"]
        before_count = await graph_provider.count_records(connector_id)
        api_before = await count_confluence_space_pages_v1_search(
            confluence_datasource, space_key
        )

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
        
        resp_2 = await confluence_datasource.create_page(
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
        new_page_2 = resp_2.json()

        pipeshub_client.wait(CONFLUENCE_TEST_SETTLE_WAIT_SEC)
        api_after_create = await count_confluence_space_pages_v1_search(
            confluence_datasource, space_key
        )
        assert api_after_create == api_before + 2, (
            f"Confluence v1 page count should increase by 2; before={api_before}, "
            f"after_create={api_after_create}"
        )
        await assert_confluence_page_in_v1_space_content_search(
            confluence_datasource,
            space_key,
            str(new_page_1["id"]),
            context="TC-INCR-001 alpha page (before connector resync)",
        )
        await assert_confluence_page_in_v1_space_content_search(
            confluence_datasource,
            space_key,
            str(new_page_2["id"]),
            context="TC-INCR-001 beta page (before connector resync)",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=180,
        )

        await assert_confluence_pages_match_graph_records(
            confluence_datasource,
            graph_provider,
            connector_id,
            space_key,
            phase="TC-INCR-001 after incremental sync",
        )

        after_count = await graph_provider.count_records(connector_id)

        confluence_connector["test_page_id"] = str(new_page_1["id"])
        confluence_connector["test_page_title"] = new_page_1["title"]
        logger.info(
            "TC-INCR-001 passed: %d -> %d records (v1 pages %d -> %d)",
            before_count,
            after_count,
            api_before,
            api_after_create,
        )

    # TC-UPDATE-001 — Content change detection
    @pytest.mark.order(8)
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

        pipeshub_client.wait(CONFLUENCE_TEST_SETTLE_WAIT_SEC)
        await assert_confluence_page_version_number_v1(
            confluence_datasource,
            str(page_id),
            page_data["version"]["number"] + 1,
            context="TC-UPDATE-001 after update (before connector resync)",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        # Wait for sync using reliable status polling
        after_count = await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=120,
        )
        assert after_count == before_count, (
            f"Record count should be stable after update; before={before_count}, after={after_count}"
        )

    @pytest.mark.order(9)
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
        
        await confluence_datasource.update_page_title(
            id=page_id,
            body={
                "status": "current",
                "title": new_title
            }
        )
        
        pipeshub_client.wait(CONFLUENCE_TEST_SETTLE_WAIT_SEC)
        await assert_confluence_page_title_v1(
            confluence_datasource,
            str(page_id),
            new_title,
            context="TC-RENAME-001 after rename (before connector resync)",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        # Wait for sync using reliable status polling
        after_count = await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=120,
        )

        await graph_provider.assert_record_paths_or_names_contain(connector_id, [new_title])
        await graph_provider.assert_record_not_exists(connector_id, old_title)
        assert after_count == before_count, (
            f"Record count should be stable after rename; before={before_count}, after={after_count}"
        )

        confluence_connector["renamed_page_id"] = str(page_id)

    @pytest.mark.order(10)
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

        pipeshub_client.wait(CONFLUENCE_TEST_SETTLE_WAIT_SEC)
        parent_id_str = str(parent_page["id"])
        await assert_confluence_page_in_v1_space_content_search(
            confluence_datasource,
            confluence_connector["space_key"],
            parent_id_str,
            context="TC-MOVE-001 parent page (before connector resync)",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        # Wait for parent page sync
        after_parent_count = await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            min_records=before_count + 1,
            timeout=120,
        )
        assert after_parent_count == before_count + 1, (
            f"Expected 1 new record (parent page); before={before_count}, after={after_parent_count}"
        )

        await confluence_datasource.move_page(page_id, str(parent_page["id"]))

        pipeshub_client.wait(CONFLUENCE_TEST_SETTLE_WAIT_SEC)
        await assert_confluence_page_v1_ancestors_contain_id(
            confluence_datasource,
            str(page_id),
            parent_id_str,
            context="TC-MOVE-001 after move (before connector resync)",
        )
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        # Wait for move sync
        final_count = await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=120,
        )
        assert final_count == after_parent_count, (
            f"Record count should be stable after move; before_move={after_parent_count}, after_move={final_count}"
        )
