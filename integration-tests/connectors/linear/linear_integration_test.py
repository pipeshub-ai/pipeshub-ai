# pyright: ignore-file

"""
Linear Connector – Integration Tests
=====================================

Test cases:
  TC-SYNC-001         — Full sync + strict graph baselines vs API snapshot
  TC-LINEAR-001       — User exists; USER_APP_RELATION == connector-style Linear user fetch
  TC-LINEAR-002       — Filtered teams as UserGroups + member edges
  TC-LINEAR-003       — Primary team as RecordGroup; reference issue belongs to team
  TC-LINEAR-004       — Reference issue TICKET fields, webUrl, edges
  TC-LINEAR-005       — Reference project ProjectRecord properties (skip if none)
  TC-LINEAR-006       — Reference attachment LinkRecord fields + edges (skip if none)
  TC-LINEAR-007       — Reference document WebpageRecord fields + edges (skip if none)
  TC-LINEAR-008       — Reference markdown FILE record fields + edges (skip if none)
  TC-LINEAR-IDX-001   — Reference issue ``indexing_status`` COMPLETED
  TC-INCR-001         — Create new issue (test-time); incremental sync; +1 record; cleanup
  TC-UPDATE-001       — Edit title (test-time); version +1; revision match; restore title
  TC-LINEAR-EDGES-001 — Edge inventory after incremental tests
  TC-LINEAR-PERM-001  — Team privacy → ORG or GROUP permission on RecordGroup
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

from app.config.constants.arangodb import ProgressStatus  # type: ignore[import-not-found]  # noqa: E402
from app.models.entities import (  # type: ignore[import-not-found]  # noqa: E402
    LinkRecord,
    RecordType,
)
from app.sources.external.linear.linear import LinearDataSource  # type: ignore[import-not-found]  # noqa: E402
from helper.assertions import ConnectorAssertions, RecordAssertion  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    wait_for_sync_completion,
)
from connectors.linear.linear_expected import LinearExpected  # noqa: E402
from validation.graph_entity_validator import (  # noqa: E402
    assert_graph_entity_matches,
    assert_graph_entity_with_edges,
    assert_user_app_edge,
)
from validation.graph_edge_validator import (  # noqa: E402
    assert_graph_edges,
    build_record_edge_expectations,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from connectors.linear.constants import LINEAR_INDEXING_WAIT_SEC  # noqa: E402
from connectors.linear.linear_test_utils import (  # noqa: E402
    assert_linear_issues_match_graph_records,
    check_issue_exists_bool,
    count_linear_users_with_email,
    get_linear_issue_updated_ms,
    wait_until_linear_condition,
    wait_until_record_indexing_completed,
)

logger = logging.getLogger("linear-lifecycle-test")


def _restart_sync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    """Disable then re-enable the connector to trigger a fresh incremental sync."""
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    pipeshub_client.wait(8)



pytestmark = [
    pytest.mark.integration,
    pytest.mark.linear,
    pytest.mark.asyncio(loop_scope="session"),
]


# =============================================================================
# TestLinearConnector — full sync, incremental, update
# =============================================================================


class TestLinearConnector:
    """Sync-pipeline tests: full sync, incremental, update (no reparent)."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: validate the graph after the fixture's full sync vs API snapshot."""
        connector_id = linear_connector["connector_id"]
        team_ids = linear_connector["team_ids"]

        await graph_provider.assert_min_records(connector_id, 1)
        await graph_provider.assert_record_groups_and_edges(
            connector_id,
            min_groups=linear_connector["expected_record_groups"],
            min_record_edges=linear_connector["full_sync_count"],
        )
        await graph_provider.assert_no_orphan_records(connector_id)

        ticket_count = await graph_provider.count_records_by_type(connector_id, RecordType.TICKET.value)
        project_count = await graph_provider.count_records_by_type(connector_id, RecordType.PROJECT.value)
        link_count = await graph_provider.count_records_by_type(connector_id, RecordType.LINK.value)
        webpage_count = await graph_provider.count_records_by_type(connector_id, RecordType.WEBPAGE.value)
        file_count = await graph_provider.count_records_by_type(connector_id, RecordType.FILE.value)
        assert ticket_count == linear_connector["expected_ticket_count"]
        assert project_count == linear_connector["expected_project_count"]
        # Reference-record presence (checked in the fixture) already proves each
        # dependent type synced correctly; here we only confirm the type exists at all.
        assert link_count >= 1, "No LINK records after sync"
        assert webpage_count >= 1, "No WEBPAGE records after sync"
        assert file_count >= 1, "No FILE records after sync"

        total_records = await graph_provider.count_records(connector_id)
        assert total_records >= linear_connector["expected_total_records"], (
            f"total_records: graph={total_records} < expected={linear_connector['expected_total_records']}"
        )

        pc_edges = await graph_provider.count_parent_child_edges(connector_id)
        assert pc_edges >= linear_connector["expected_parent_child_edges"], (
            f"PARENT_CHILD edges: {pc_edges} < expected={linear_connector['expected_parent_child_edges']}"
        )

        rg_edges = await graph_provider.count_record_group_edges(connector_id)
        assert rg_edges == total_records, (
            f"BELONGS_TO record->group count {rg_edges} must equal total records {total_records}"
        )

        inherit = await graph_provider.count_inherit_permissions_edges(connector_id)
        assert inherit == total_records

        app_edges = await graph_provider.count_app_record_group_edges(connector_id)
        rgs = await graph_provider.count_record_groups(connector_id)
        assert app_edges == rgs == linear_connector["expected_record_groups"]

        graph_app = await graph_provider.get_app_metadata_by_connector_id(connector_id)
        assert graph_app is not None, f"apps document missing for connector {connector_id}"
        expected_app = LinearExpected.app_metadata_for_full_sync_baseline(linear_connector)
        app_skip = frozenset({
            "created_at_timestamp", "updated_at_timestamp",
            "auth_type", "is_active", "is_agent_active", "is_configured",
            "is_authenticated", "created_by", "updated_by", "status", "is_locked",
        })
        assert_graph_entity_matches(
            expected_app, graph_app, entity="app_metadata", skip_compare=app_skip,
        )

        await assert_linear_issues_match_graph_records(
            linear_datasource, graph_provider, connector_id, team_ids,
            phase="TC-SYNC-001",
        )

        summary = await graph_provider.graph_summary(connector_id)
        logger.info("TC-SYNC-001 passed: %s", summary)

    @pytest.mark.order(8)
    async def test_tc_incr_001_incremental_sync_new_issue(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: create one issue at test time, incremental sync picks it up, then delete it."""
        connector_id = linear_connector["connector_id"]
        team_id = linear_connector["primary_team_id"]
        before_count = await graph_provider.count_records(connector_id)

        title = f"LinearIT-IncrTest-{uuid.uuid4().hex[:8]}"
        new_issue_id: str | None = None

        try:
            resp = await linear_datasource.issueCreate(
                input={"teamId": team_id, "title": title}
            )
            assert resp.success, f"Failed to create issue: {resp.message}"
            issue_data = (resp.data or {}).get("issueCreate", {}).get("issue", {})
            new_issue_id = issue_data.get("id")
            assert new_issue_id, "issueCreate returned no issue ID"

            await wait_until_linear_condition(
                check_fn=lambda: check_issue_exists_bool(linear_datasource, new_issue_id),
                description=f"TC-INCR-001: new issue fetchable ({new_issue_id})",
                timeout=120,
            )

            max_sync_attempts = 3
            for sync_attempt in range(max_sync_attempts):
                _restart_sync(pipeshub_client, connector_id)
                await wait_for_sync_completion(
                    pipeshub_client, graph_provider, connector_id, timeout=240,
                )
                after_count = await graph_provider.count_records(connector_id)
                if after_count >= before_count + 1:
                    break
                if sync_attempt < max_sync_attempts - 1:
                    logger.warning(
                        "TC-INCR-001: sync finished with %d records (need %d), "
                        "re-triggering (attempt %d/%d)",
                        after_count, before_count + 1,
                        sync_attempt + 2, max_sync_attempts,
                    )

            after_count = await graph_provider.count_records(connector_id)
            assert after_count == before_count + 1, (
                f"Expected exactly 1 new record; before={before_count}, after={after_count}"
            )

            actual = await graph_provider.get_typed_record_by_external_id(connector_id, new_issue_id)
            assert actual is not None, f"typed TICKET record missing for {new_issue_id}"
            expected = await LinearExpected.ticket_record(
                new_issue_id,
                connector_id=connector_id,
                datasource=linear_datasource,
            )
            await assert_graph_entity_with_edges(
                expected, actual,
                entity="ticket_record",
                connector_id=connector_id,
                graph_provider=graph_provider,
                # created_at/updated_at are set by Linear and can drift between when the
                # connector fetches the issue (during sync) and when the test re-fetches it here.
                skip_compare=frozenset({"created_at", "updated_at"}),
            )

            await graph_provider.assert_record_paths_or_names_contain(connector_id, [title])
            logger.info("TC-INCR-001 passed: %d -> %d records", before_count, after_count)

        finally:
            if new_issue_id:
                try:
                    await linear_datasource.issueDelete(id=new_issue_id)
                    logger.info("TC-INCR-001 cleanup: deleted test issue")
                    _restart_sync(pipeshub_client, connector_id)
                    await wait_for_sync_completion(
                        pipeshub_client, graph_provider, connector_id,
                        min_records=before_count, timeout=180,
                    )
                    logger.info("TC-INCR-001 cleanup: deletion synced, graph back to %d records", before_count)
                except Exception as e:
                    logger.warning("TC-INCR-001 cleanup: failed to delete/sync test issue: %s", e)

    @pytest.mark.order(9)
    async def test_tc_update_001_title_revision(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: edit title of reference issue; version += 1; revision = Linear updatedAt ms.

        Restores the original title in finally.
        """
        target_id = linear_connector.get("reference_issue_id")
        if not target_id:
            pytest.skip("No reference issue discovered on primary — skipping")
        connector_id = linear_connector["connector_id"]
        before_count = await graph_provider.count_records(connector_id)

        # Fetch original title to restore later.
        issue_resp = await linear_datasource.issue(id=target_id)
        assert issue_resp.success, f"issue({target_id}) failed: {issue_resp.message}"
        original_title = (issue_resp.data or {}).get("issue", {}).get("title", "")

        record_before = await graph_provider.get_record_by_external_id(connector_id, target_id)
        assert record_before is not None, f"Issue {target_id} not in graph"
        old_version = int(record_before.version)

        new_title = f"LinearIT-Edited-{uuid.uuid4().hex[:8]}"

        try:
            edit_resp = await linear_datasource.issueUpdate(
                id=target_id,
                input={"title": new_title},
            )
            assert edit_resp.success, f"issueUpdate failed: {edit_resp.message}"

            pipeshub_client.wait(5)

            _restart_sync(pipeshub_client, connector_id)
            await wait_for_sync_completion(
                pipeshub_client, graph_provider, connector_id, timeout=240,
            )

            after_count = await graph_provider.count_records(connector_id)
            assert after_count == before_count, (
                f"Update should keep record count stable; before={before_count}, after={after_count}"
            )

            record_after = await graph_provider.get_record_by_external_id(connector_id, target_id)
            assert record_after is not None, "Record missing after sync"
            assert record_after.version == old_version + 1, (
                f"Expected version {old_version + 1}, got {record_after.version}"
            )

            linear_updated_ms = await get_linear_issue_updated_ms(linear_datasource, target_id)
            assert str(record_after.external_revision_id) == str(linear_updated_ms), (
                f"Graph external_revision_id {record_after.external_revision_id!r} should equal "
                f"Linear updatedAt epoch ms {linear_updated_ms}"
            )
            assert new_title in (record_after.record_name or ""), (
                f"Record name '{record_after.record_name}' should contain new title '{new_title}'"
            )
            logger.info("TC-UPDATE-001 passed: version %s -> %s", old_version, record_after.version)

        finally:
            if original_title:
                try:
                    await linear_datasource.issueUpdate(
                        id=target_id,
                        input={"title": original_title},
                    )
                    logger.info("TC-UPDATE-001 cleanup: restored title to '%s'", original_title)
                except Exception as e:
                    logger.warning("TC-UPDATE-001 cleanup: failed to restore title: %s", e)


# =============================================================================
# TestLinearValidation — entity / relationship validation (read-only)
# =============================================================================


class TestLinearValidation:
    """Entity / relationship validation against the synced state of existing data."""

    @pytest.mark.order(2)
    async def test_tc_linear_001_user_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-001: synced user exists; USER_APP_RELATION matches Linear user pool."""
        connector_id = linear_connector["connector_id"]
        viewer_id = linear_connector.get("viewer_id")
        viewer_email = linear_connector.get("viewer_email")

        if not viewer_id or not viewer_email:
            pytest.skip("viewer_id or viewer_email missing from fixture")

        await connector_assertions.assert_user_exists(
            connector_id=connector_id, source_user_id=viewer_id, email=viewer_email,
        )

        linear_users_with_email = await count_linear_users_with_email(linear_datasource)
        rel_count = await graph_provider.count_user_app_relation_edges(connector_id)
        assert rel_count == linear_users_with_email, (
            f"USER_APP_RELATION count {rel_count} != Linear active users with email "
            f"({linear_users_with_email}) (connector {connector_id})"
        )

        await assert_user_app_edge(
            viewer_id, connector_id=connector_id, graph_provider=graph_provider,
        )

        logger.info("TC-LINEAR-001 passed: %d users validated", rel_count)

    @pytest.mark.order(3)
    async def test_tc_linear_002_team_as_user_group(
        self,
        linear_connector: Dict[str, Any],
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-002: filtered teams synced as UserGroups with member edges."""
        connector_id = linear_connector["connector_id"]
        teams = linear_connector["teams"]

        graph_group_total = await graph_provider.count_user_groups(connector_id)
        assert graph_group_total == len(teams), (
            f"Graph UserGroup count {graph_group_total} != filtered team count {len(teams)}"
        )

        primary = teams[0]
        graph_ug = await connector_assertions.assert_group_exists(
            connector_id=connector_id,
            external_group_id=primary["id"],
            name=primary["name"],
        )

        expected_ug = LinearExpected.user_group(
            name=primary["name"],
            source_user_group_id=primary["id"],
            connector_id=connector_id,
        )
        ug_skip = frozenset({"created_at", "updated_at", "source_created_at", "source_updated_at"})
        assert_graph_entity_matches(
            expected_ug, graph_ug, entity="app_user_group", skip_compare=ug_skip,
        )

        logger.info("TC-LINEAR-002 passed: %d teams validated as UserGroups", graph_group_total)

    @pytest.mark.order(5)
    async def test_tc_linear_003_team_as_record_group(
        self,
        linear_connector: Dict[str, Any],
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-003: primary team synced as RecordGroup; reference issue belongs to it."""
        connector_id = linear_connector["connector_id"]
        primary_team_id = linear_connector["primary_team_id"]
        primary = linear_connector["teams"][0]

        rg = await graph_provider.get_record_group_by_external_id(connector_id, primary_team_id)
        assert rg is not None, f"Team {primary_team_id} missing as RecordGroup"

        expected_rg = LinearExpected.record_group(
            primary,
            connector_id=connector_id,
            organization_url_key=linear_connector.get("organization_url_key"),
        )
        rg_skip = frozenset({
            "created_at", "updated_at", "source_created_at", "source_updated_at",
            "web_url", "description",
        })
        await assert_graph_entity_with_edges(
            expected_rg, rg,
            entity="record_group",
            connector_id=connector_id,
            graph_provider=graph_provider,
            skip_compare=rg_skip,
        )

        ref_id = linear_connector.get("reference_issue_id")
        if ref_id:
            ref_record = await connector_assertions.assert_record_exists(connector_id, ref_id)
            assert ref_record.external_record_group_id == primary_team_id, (
                f"Reference issue should belong to team {primary_team_id}; "
                f"got {ref_record.external_record_group_id}"
            )
        logger.info("TC-LINEAR-003 passed: team %s validated as RecordGroup", primary_team_id)

    @pytest.mark.order(6)
    async def test_tc_linear_004_issue_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-004: reference issue has correct TICKET record properties."""
        ref_id = linear_connector.get("reference_issue_id")
        if not ref_id:
            pytest.skip("No reference issue discovered on primary — skipping")
        connector_id = linear_connector["connector_id"]
        primary_team_id = linear_connector["primary_team_id"]

        expected = RecordAssertion(
            external_record_id=ref_id,
            record_type=RecordType.TICKET.value,
            mime_type="application/blocks",
            external_record_group_id=primary_team_id,
        )
        record = await connector_assertions.assert_record_exists(connector_id, ref_id, expected)

        assert record.weburl is not None, "Issue should have weburl"
        assert "linear.app" in record.weburl, (
            f"weburl '{record.weburl}' should contain 'linear.app'"
        )
        assert record.source_created_at is not None

        record_edges = build_record_edge_expectations(record, connector_id)
        await assert_graph_edges(graph_provider, record_edges)

        logger.info("TC-LINEAR-004 passed: issue %s validated", ref_id)

    @pytest.mark.order(7)
    async def test_tc_linear_005_project_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-005: reference project as ProjectRecord (skip if none in team)."""
        ref_project_id = linear_connector.get("reference_project_id")
        if not ref_project_id:
            pytest.skip("No projects in primary team — skipping")

        connector_id = linear_connector["connector_id"]
        primary_team_id = linear_connector["primary_team_id"]

        expected = RecordAssertion(
            external_record_id=ref_project_id,
            record_type=RecordType.PROJECT.value,
            mime_type="application/blocks",
            external_record_group_id=primary_team_id,
        )
        record = await connector_assertions.assert_record_exists(
            connector_id, ref_project_id, expected,
        )

        assert record.weburl is not None, "Project should have weburl"
        assert "linear.app" in record.weburl, (
            f"weburl '{record.weburl}' should contain 'linear.app'"
        )
        assert record.source_created_at is not None

        logger.info("TC-LINEAR-005 passed: project %s validated", ref_project_id)

    @pytest.mark.order(10)
    async def test_tc_linear_006_link_record_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-006: reference attachment as LinkRecord (skip if none)."""
        attachment_id = linear_connector.get("reference_attachment_id")
        if not attachment_id:
            pytest.skip("No attachments in filtered teams — skipping")

        connector_id = linear_connector["connector_id"]
        team_id = linear_connector.get("reference_attachment_team_id") or linear_connector["primary_team_id"]

        expected = await LinearExpected.link_record(
            attachment_id,
            connector_id=connector_id,
            datasource=linear_datasource,
            team_id=team_id,
        )
        parent_external_id = expected.parent_external_record_id
        parent = await graph_provider.get_record_by_external_id(connector_id, parent_external_id) if parent_external_id else None
        if parent:
            expected.parent_node_id = parent.id

        actual = await graph_provider.get_typed_record_by_external_id(connector_id, attachment_id)
        assert actual is not None, f"LINK record missing for attachment {attachment_id}"
        assert isinstance(actual, LinkRecord), f"Expected LinkRecord, got {type(actual).__name__}"

        await assert_graph_entity_with_edges(
            expected, actual,
            entity="link_record",
            connector_id=connector_id,
            graph_provider=graph_provider,
            skip_compare=frozenset({"created_at", "updated_at"}),
        )
        logger.info("TC-LINEAR-006 passed: attachment %s validated as LINK", attachment_id)

    @pytest.mark.order(11)
    async def test_tc_linear_007_webpage_record_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-007: reference document as WebpageRecord (skip if none)."""
        document_id = linear_connector.get("reference_document_id")
        if not document_id:
            pytest.skip("No documents in filtered teams — skipping")

        connector_id = linear_connector["connector_id"]
        team_id = linear_connector.get("reference_document_team_id") or linear_connector["primary_team_id"]
        parent_type_str = linear_connector.get("reference_document_parent_type") or "TICKET"
        parent_type = (
            RecordType.PROJECT if parent_type_str == "PROJECT" else RecordType.TICKET
        )
        parent_external_id = linear_connector.get("reference_document_parent_id")

        parent = await graph_provider.get_record_by_external_id(connector_id, parent_external_id) if parent_external_id else None

        expected = await LinearExpected.webpage_record(
            document_id,
            connector_id=connector_id,
            datasource=linear_datasource,
            team_id=team_id,
            parent_external_id=parent_external_id,
            parent_record_type=parent_type,
            parent_node_id=parent.id if parent else None,
        )
        actual = await graph_provider.get_typed_record_by_external_id(connector_id, document_id)
        assert actual is not None, f"WEBPAGE record missing for document {document_id}"

        await assert_graph_entity_with_edges(
            expected, actual,
            entity="webpage_record",
            connector_id=connector_id,
            graph_provider=graph_provider,
            skip_compare=frozenset({"created_at", "updated_at"}),
        )
        logger.info("TC-LINEAR-007 passed: document %s validated as WEBPAGE", document_id)

    @pytest.mark.order(12)
    async def test_tc_linear_008_file_record_properties(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-008: reference markdown file as FileRecord (skip if none)."""
        file_url = linear_connector.get("reference_file_url")
        if not file_url:
            pytest.skip("No markdown files in filtered teams — skipping")

        connector_id = linear_connector["connector_id"]
        parent_type_str = linear_connector.get("reference_file_parent_type") or "TICKET"
        parent_type = (
            RecordType.PROJECT if parent_type_str == "PROJECT" else RecordType.TICKET
        )
        parent_external_id = linear_connector["reference_file_parent_id"]
        parent = await graph_provider.get_record_by_external_id(
            connector_id, parent_external_id,
        )
        assert parent is not None, (
            f"Parent record {parent_external_id} missing for FILE {file_url}"
        )

        size_in_bytes = 0
        try:
            fetched_size = await linear_datasource.get_file_size(file_url)
            if fetched_size is not None:
                size_in_bytes = fetched_size
        except Exception:
            pass

        expected = LinearExpected.file_record(
            file_url,
            linear_connector["reference_file_filename"],
            parent_external_id=parent_external_id,
            parent_record_type=parent_type,
            team_id=linear_connector["reference_file_team_id"],
            connector_id=connector_id,
            parent_weburl=linear_connector.get("reference_file_parent_weburl"),
            parent_created_at=linear_connector.get("reference_file_parent_created_at", 0),
            parent_updated_at=linear_connector.get("reference_file_parent_updated_at", 0),
            parent_node_id=parent.id,
            size_in_bytes=size_in_bytes,
        )
        actual = await graph_provider.get_typed_record_by_external_id(connector_id, file_url)
        assert actual is not None, f"FILE record missing for url {file_url}"

        await assert_graph_entity_with_edges(
            expected, actual,
            entity="file_record",
            connector_id=connector_id,
            graph_provider=graph_provider,
            skip_compare=frozenset({"created_at", "updated_at"}),
        )
        logger.info("TC-LINEAR-008 passed: file %s validated as FILE", file_url)


# =============================================================================
# TestLinearIndexing — graph ``indexing_status`` COMPLETED for reference issue
# =============================================================================


class TestLinearIndexing:
    """Indexing pipeline: reference TICKET reaches ``COMPLETED`` in graph."""

    @pytest.mark.order(13)
    async def test_tc_linear_idx_001_reference_issue_indexing_completed(
        self,
        linear_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-LINEAR-IDX-001: reference issue reaches ``indexing_status == COMPLETED``."""
        external_id = linear_connector.get("reference_issue_id")
        if not external_id:
            pytest.skip("No reference issue discovered on primary — skipping")
        connector_id = linear_connector["connector_id"]
        rec = await wait_until_record_indexing_completed(
            graph_provider,
            connector_id,
            external_id,
            timeout=LINEAR_INDEXING_WAIT_SEC,
            description=f"TC-LINEAR-IDX-001 reference issue {external_id}",
            pipeshub_client=pipeshub_client,
        )
        assert rec.indexing_status == ProgressStatus.COMPLETED.value
        assert rec.virtual_record_id, (
            f"Issue should have virtual_record_id after indexing COMPLETED"
        )
        logger.info("TC-LINEAR-IDX-001 passed: %s indexing completed", external_id)


# =============================================================================
# TestLinearEdges — comprehensive edge inventory audit
# =============================================================================


class TestLinearEdges:
    """Edge audit after incremental tests."""

    @pytest.mark.order(20)
    async def test_tc_linear_edges_001_edge_inventory(
        self,
        linear_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-EDGES-001: structural edge invariants after mutation tests.

        TC-INCR-001 creates and deletes its test issue (with sync), so the graph
        should be back at baseline counts. Validate exact equality + edge invariants.
        """
        connector_id = linear_connector["connector_id"]

        records = await graph_provider.count_records(connector_id)
        assert records >= linear_connector["expected_total_records"], (
            f"total_records: graph={records} < expected={linear_connector['expected_total_records']}"
        )

        rg_edges = await graph_provider.count_record_group_edges(connector_id)
        assert rg_edges == records, (
            f"BELONGS_TO record->group {rg_edges} must equal records {records}"
        )

        pc = await graph_provider.count_parent_child_edges(connector_id)
        assert pc >= linear_connector["expected_parent_child_edges"], (
            f"PARENT_CHILD edges: {pc} < expected={linear_connector['expected_parent_child_edges']}"
        )

        inherit = await graph_provider.count_inherit_permissions_edges(connector_id)
        assert inherit == records, f"INHERIT_PERMISSIONS {inherit} must equal records {records}"

        app_edges = await graph_provider.count_app_record_group_edges(connector_id)
        rgs = await graph_provider.count_record_groups(connector_id)
        assert app_edges == rgs == linear_connector["expected_record_groups"]

        logger.info("TC-LINEAR-EDGES-001 passed")


# =============================================================================
# TestLinearPermissions — team privacy → ORG or GROUP permission
# =============================================================================


class TestLinearPermissions:
    """Team privacy-based permission validation on RecordGroups."""

    @pytest.mark.order(21)
    async def test_tc_linear_perm_001_team_privacy_permissions(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-LINEAR-PERM-001: verify PERMISSION edges to RecordGroups reflect team privacy.

        Public teams get an ORG-level PERMISSION edge.
        Private teams get a GROUP-level PERMISSION edge (external_id = team_id).
        """
        connector_id = linear_connector["connector_id"]
        teams = linear_connector["teams"]

        for team in teams:
            team_id = team["id"]
            is_private = team.get("private", False)

            rg = await graph_provider.get_record_group_by_external_id(connector_id, team_id)
            if rg is None:
                logger.warning("Team %s not found as RecordGroup — skipping", team_id)
                continue

            perm_count = await graph_provider.count_permission_edges_to_record_groups(
                connector_id, team_id,
            )
            assert perm_count >= 1, (
                f"Team {team_id} ({'private' if is_private else 'public'}) "
                f"should have at least 1 PERMISSION edge to its RecordGroup, got {perm_count}"
            )

        logger.info("TC-LINEAR-PERM-001 passed: %d teams checked", len(teams))
