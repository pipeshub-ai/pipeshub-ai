# pyright: ignore-file

"""
Jira Connector – Integration Tests
==================================

Test cases:
  TC-SYNC-001         — Full sync + strict graph baselines, Jira vs graph, JQL vs TICKET count + YAML ``app_metadata`` for connector app doc
  TC-JIRA-001         — User exists; USER_APP_RELATION == connector-style Jira user fetch (email + active)
  TC-JIRA-002         — Graph ``user_groups`` count == Jira bulk; fixture group + member edge + YAML ``app_user_group``
  TC-JIRA-003         — Project as RecordGroup (YAML ``record_group`` match); first seed issue belongs to project
  TC-JIRA-004         — First seed issue TICKET fields, webUrl, inherits permissions
  TC-JIRA-IDX-001     — Seed issue ``indexing_status`` COMPLETED; one reindex if graph shows AUTO_INDEX_OFF
  TC-INCR-001         — One new issue; incremental sync; YAML graph record match + path/name
  TC-UPDATE-001       — Edit summary + description; version +1; revision = Jira updated ms
  TC-MOVE-001         — Sub-task reparent: stable record count; parent field + PARENT_CHILD edges
  TC-MOVE-002         — Story under epic reparent: same (Epic Link fallback unchanged)
  TC-JIRA-ATTACH-001  — FILE record (YAML ``file_record`` match); ATTACHMENT or PARENT_CHILD inbound
  TC-JIRA-EDGES-001   — Edge inventory after INCR (+1); ACL counts unchanged; app↔RG = 1
  TC-BROWSE-001       — Default scheme: Jira ``BROWSE_PROJECTS`` preview == PERMISSION→RecordGroup (project)
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

from app.config.constants.arangodb import ProgressStatus  # type: ignore[import-not-found]  # noqa: E402
from app.models.entities import (  # type: ignore[import-not-found]  # noqa: E402
    FileRecord,
    RecordType,
)
from app.sources.external.jira.jira import JiraDataSource  # type: ignore[import-not-found]  # noqa: E402
from helper.assertions import ConnectorAssertions, RecordAssertion  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    wait_for_sync_completion,
)
from connectors.jira.jira_expected import JiraExpected  # noqa: E402
from validation.graph_entity_validator import (  # noqa: E402
    assert_graph_entity_matches,
    assert_graph_entity_with_edges,
    assert_user_app_edge,
)
from validation.graph_edge_validator import (  # noqa: E402
    PermissionExpectation,
    assert_graph_edges,
    build_permission_expectations,
    build_record_edge_expectations,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from connectors.jira.constants import JIRA_INDEXING_WAIT_SEC  # noqa: E402
from connectors.jira.jira_test_utils import (  # noqa: E402
    assert_jira_issues_match_graph_records,
    count_jira_site_groups_bulk,
    count_jira_users_with_visible_email,
    count_jira_project_issues_via_jql,
    check_issue_exists_bool,
    check_issue_parent_bool,
    get_jira_issue_updated_ms,
    preview_jira_browse_projects_permission_edges_to_record_group,
    wait_until_jira_condition,
    wait_until_record_indexing_completed,
)

logger = logging.getLogger("jira-lifecycle-test")


async def _assert_jql_ticket_sum_matches_graph(
    jira_datasource: JiraDataSource,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    project_keys: tuple[str, ...],
    *,
    phase: str,
) -> None:
    """Sum JQL issue counts across projects; must equal graph TICKET count."""
    total = 0
    for pk in project_keys:
        total += await count_jira_project_issues_via_jql(jira_datasource, pk)
    graph_n = await graph_provider.count_records_by_type(connector_id, RecordType.TICKET.value)
    assert total == graph_n, (
        f"{phase}: sum of Jira issues across {project_keys!r} = {total} != graph TICKET {graph_n}"
    )


def _adf(text: str) -> Dict[str, Any]:
    """Build a minimal Atlassian Document Format paragraph block."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _restart_sync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    """Disable then re-enable the connector to trigger a fresh incremental sync.

    The trailing wait is critical: after ``enable=True`` the connector takes a
    moment to transition from IDLE → SYNCING. Without this wait, a subsequent
    ``wait_for_sync_completion`` call can poll status while it's still IDLE
    (pre-transition) and return immediately — the sync never actually ran, so
    any source-side change made by the test won't be reflected in the graph.
    Symptom: ``wait_for_sync_completion`` finishes in under 1 second instead
    of ~25-30s.
    """
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    pipeshub_client.wait(8)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.jira,
    pytest.mark.asyncio(loop_scope="session"),
]


# =============================================================================
# TestJiraConnector — full sync, incremental, update, move
# =============================================================================


class TestJiraConnector:
    """Sync-pipeline tests: full sync, incremental, update, parent-change moves (with hierarchy checks)."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: validate the graph after the fixture's full sync (strict counts + apps doc YAML)."""
        connector_id = jira_connector["connector_id"]
        pk = jira_connector["project_key"]

        await graph_provider.assert_min_records(connector_id, jira_connector["uploaded_count"])
        await graph_provider.assert_record_groups_and_edges(
            connector_id,
            min_groups=jira_connector["expected_record_groups"],
            min_record_edges=jira_connector["full_sync_count"],
        )
        await graph_provider.assert_app_record_group_edges(
            connector_id, min_edges=jira_connector["expected_app_record_group_edges"],
        )
        await graph_provider.assert_no_orphan_records(connector_id)

        ticket_count = await graph_provider.count_records_by_type(connector_id, RecordType.TICKET.value)
        file_count = await graph_provider.count_records_by_type(connector_id, RecordType.FILE.value)
        assert ticket_count == jira_connector["expected_ticket_count"]
        assert file_count == jira_connector["expected_file_count"]

        pc_edges = await graph_provider.count_parent_child_edges(connector_id)
        assert pc_edges == jira_connector["expected_parent_child_edges"]

        attachment_edges = await graph_provider.count_record_relation_edges(
            connector_id, "ATTACHMENT",
        )
        assert attachment_edges == jira_connector["expected_attachment_edges"]

        total_records = await graph_provider.count_records(connector_id)
        assert total_records == jira_connector["expected_total_records"]

        rg_edges = await graph_provider.count_record_group_edges(connector_id)
        assert rg_edges == total_records, (
            f"BELONGS_TO record→group count {rg_edges} must equal total records {total_records}"
        )

        inherit = await graph_provider.count_inherit_permissions_edges(connector_id)
        assert inherit == total_records
        assert inherit == jira_connector["expected_inherit_edges"]

        perms = await graph_provider.count_permission_edges(connector_id)
        assert perms == jira_connector["expected_permission_aggregate"]

        ug_perm = await graph_provider.count_user_to_group_permission_edges(connector_id)
        ur_perm = await graph_provider.count_user_to_role_permission_edges(connector_id)
        rg_perm = await graph_provider.count_permission_edges_to_record_groups(connector_id)
        assert ug_perm == jira_connector["expected_permission_user_group_edges"], (
            f"User→Group PERMISSION {ug_perm} != baseline {jira_connector['expected_permission_user_group_edges']}"
        )
        assert ur_perm == jira_connector["expected_permission_user_role_edges"], (
            f"User→Role PERMISSION {ur_perm} != baseline {jira_connector['expected_permission_user_role_edges']}"
        )
        assert rg_perm == jira_connector["expected_permission_to_record_group_edges"], (
            f"→RecordGroup PERMISSION {rg_perm} != baseline {jira_connector['expected_permission_to_record_group_edges']}"
        )

        app_edges = await graph_provider.count_app_record_group_edges(connector_id)
        assert app_edges == jira_connector["expected_app_record_group_edges"]

        rgs = await graph_provider.count_record_groups(connector_id)
        assert rgs == jira_connector["expected_record_groups"]

        graph_app = await graph_provider.get_app_metadata_by_connector_id(connector_id)
        assert graph_app is not None, f"apps document missing for connector {connector_id}"
        expected_app = JiraExpected.app_metadata_for_full_sync_baseline(jira_connector)
        app_skip = frozenset({
            "created_at_timestamp", "updated_at_timestamp",
            "auth_type", "is_active", "is_agent_active", "is_configured",
            "is_authenticated", "created_by", "updated_by", "status", "is_locked",
        })
        assert_graph_entity_matches(
            expected_app, graph_app, entity="app_metadata", skip_compare=app_skip,
        )

        await assert_jira_issues_match_graph_records(
            jira_datasource, graph_provider, connector_id, pk, phase="TC-SYNC-001 INTTEST",
        )
        await _assert_jql_ticket_sum_matches_graph(
            jira_datasource, graph_provider, connector_id, (pk,), phase="TC-SYNC-001 JQL",
        )

        summary = await graph_provider.graph_summary(connector_id)
        logger.info("TC-SYNC-001 passed: %s", summary)

    @pytest.mark.order(8)
    async def test_tc_incr_001_incremental_sync_new_issue(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: incremental sync picks up one new issue; YAML + field match vs Jira-derived expected."""
        connector_id = jira_connector["connector_id"]
        project_key = jira_connector["project_key"]
        base_url = (os.getenv("JIRA_TEST_BASE_URL") or "").rstrip("/")
        issue_type = jira_connector.get("default_issue_type") or "Task"
        before_count = await graph_provider.count_records(connector_id)

        title_a = f"IncrTestAlpha-{uuid.uuid4().hex[:8]}"
        resp = await jira_datasource.create_issue(
            fields={
                "project": {"key": project_key},
                "summary": title_a,
                "issuetype": {"name": issue_type},
                "description": _adf("Incremental sync test issue."),
            }
        )
        assert resp.status in (200, 201), f"Failed to create '{title_a}': HTTP {resp.status}"
        data = resp.json()
        new_key = data["key"]
        new_id = str(data["id"])

        await wait_until_jira_condition(
            check_fn=lambda: check_issue_exists_bool(jira_datasource, new_key),
            description=f"TC-INCR-001: new issue fetchable ({new_key})",
            timeout=120,
        )

        _restart_sync(pipeshub_client, connector_id)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id,
            min_records=before_count + 1, timeout=240,
        )

        after_count = await graph_provider.count_records(connector_id)
        assert after_count == before_count + 1, (
            f"Expected exactly 1 new record; before={before_count}, after={after_count}"
        )

        actual = await graph_provider.get_typed_record_by_external_id(connector_id, new_id)
        assert actual is not None, f"typed TICKET record missing for external id {new_id}"
        expected = await JiraExpected.ticket_record(
            new_key,
            connector_id=connector_id,
            datasource=jira_datasource,
            site_base_url=base_url or None,
        )
        await assert_graph_entity_with_edges(
            expected, actual,
            entity="ticket_record",
            connector_id=connector_id,
            graph_provider=graph_provider,
        )

        await graph_provider.assert_record_paths_or_names_contain(connector_id, [title_a])
        logger.info("TC-INCR-001 passed: %d -> %d records", before_count, after_count)

    @pytest.mark.order(9)
    async def test_tc_update_001_content_and_summary_revision(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: one edit (summary + description); graph version += 1; revision = Jira fields.updated ms."""
        connector_id = jira_connector["connector_id"]
        target_key = jira_connector["seed_issue_keys"][0]
        before_count = await graph_provider.count_records(connector_id)

        issue_resp = await jira_datasource.get_issue(issueIdOrKey=target_key, fields="summary")
        assert issue_resp.status == 200, f"get_issue failed: HTTP {issue_resp.status}"
        target_id = str(issue_resp.json()["id"])

        record_before = await graph_provider.get_record_by_external_id(connector_id, target_id)
        assert record_before is not None, f"Issue {target_key} (id={target_id}) not in graph"
        old_version = int(record_before.version)

        new_summary = f"Edited-{uuid.uuid4().hex[:8]}"
        edit_resp = await jira_datasource.edit_issue(
            issueIdOrKey=target_key,
            fields={
                "summary": new_summary,
                "description": _adf("Edited via TC-UPDATE-001."),
            },
        )
        assert edit_resp.status in (200, 204), f"edit_issue failed: HTTP {edit_resp.status}"

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

        jira_updated_ms = await get_jira_issue_updated_ms(jira_datasource, target_key)
        assert str(record_after.external_revision_id) == str(jira_updated_ms), (
            f"Graph external_revision_id {record_after.external_revision_id!r} should equal "
            f"Jira fields.updated epoch ms {jira_updated_ms}"
        )
        assert new_summary in (record_after.record_name or ""), (
            f"Record name '{record_after.record_name}' should contain new summary '{new_summary}'"
        )
        logger.info("TC-UPDATE-001 passed: version %s -> %s", old_version, record_after.version)

    @pytest.mark.order(10)
    async def test_tc_move_001_subtask_reparent(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-MOVE-001: reparent sub-task in Jira; after sync, graph parent + PARENT_CHILD match Jira."""
        connector_id = jira_connector["connector_id"]
        subtask_key = jira_connector.get("seed_subtask_key")
        old_parent_key = jira_connector.get("seed_subtask_parent_key")
        new_parent_key = jira_connector.get("move_target_parent_key")
        if not (subtask_key and old_parent_key and new_parent_key):
            pytest.skip("Sub-task or move-target parent not seeded — skipping reparent test")

        st_resp = await jira_datasource.get_issue(issueIdOrKey=subtask_key, fields="parent")
        assert st_resp.status == 200
        subtask_id = str(st_resp.json()["id"])
        old_parent_resp = await jira_datasource.get_issue(issueIdOrKey=old_parent_key, fields="summary")
        new_parent_resp = await jira_datasource.get_issue(issueIdOrKey=new_parent_key, fields="summary")
        assert old_parent_resp.status == 200 and new_parent_resp.status == 200
        old_parent_id = str(old_parent_resp.json()["id"])
        new_parent_id = str(new_parent_resp.json()["id"])

        before_count = await graph_provider.count_records(connector_id)
        record_before = await graph_provider.get_record_by_external_id(connector_id, subtask_id)
        assert record_before is not None
        assert str(record_before.parent_external_record_id) == old_parent_id, (
            f"Sub-task {subtask_key} should start under {old_parent_key} (id={old_parent_id}); "
            f"got parent_external_record_id={record_before.parent_external_record_id!r}"
        )

        edit_resp = await jira_datasource.edit_issue(
            issueIdOrKey=subtask_key, fields={"parent": {"key": new_parent_key}},
        )
        assert edit_resp.status in (200, 204), f"edit_issue (reparent) failed: HTTP {edit_resp.status}"

        await wait_until_jira_condition(
            check_fn=lambda: check_issue_parent_bool(jira_datasource, subtask_key, new_parent_key),
            description=f"TC-MOVE-001: {subtask_key} parent == {new_parent_key}",
        )

        _restart_sync(pipeshub_client, connector_id)
        after_count = await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, timeout=240,
        )
        assert after_count == before_count, "Record count should be stable after sub-task reparent"

        record = await graph_provider.get_record_by_external_id(connector_id, subtask_id)
        assert record is not None
        assert str(record.parent_external_record_id) == new_parent_id, (
            f"parent_external_record_id should be {new_parent_id}, got {record.parent_external_record_id}"
        )
        incoming = await graph_provider.get_record_incoming_relations(
            connector_id, subtask_id, "PARENT_CHILD",
        )
        assert new_parent_id in incoming and old_parent_id not in incoming, (
            f"PARENT_CHILD into sub-task: expect new parent {new_parent_id} in {incoming!r}, "
            f"old {old_parent_id} absent"
        )

        logger.info("TC-MOVE-001 passed: %s reparented %s -> %s", subtask_key, old_parent_key, new_parent_key)

    @pytest.mark.order(11)
    async def test_tc_move_002_story_epic_reparent(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-MOVE-002: reparent story/task under another Epic; after sync, graph parent + PARENT_CHILD match Jira."""
        connector_id = jira_connector["connector_id"]
        story_key = jira_connector.get("seed_story_under_epic_key")
        old_epic_key = jira_connector.get("seed_epic_key")
        new_epic_key = jira_connector.get("move_target_epic_key")
        if not (story_key and old_epic_key and new_epic_key):
            pytest.skip("Middle-level issue / Epic / move-target Epic not seeded — skipping epic reparent test")

        story_resp = await jira_datasource.get_issue(issueIdOrKey=story_key, fields="parent")
        assert story_resp.status == 200
        story_id = str(story_resp.json()["id"])
        old_epic_id = str(jira_connector["seed_epic_id"])
        new_epic_id = str(jira_connector["move_target_epic_id"])

        before_count = await graph_provider.count_records(connector_id)

        edit_resp = await jira_datasource.edit_issue(
            issueIdOrKey=story_key, fields={"parent": {"key": new_epic_key}},
        )
        if edit_resp.status not in (200, 204):
            # Classic projects: try epic-link custom field. Discover field id via get_fields.
            try:
                fields_resp = await jira_datasource.get_fields()
                epic_link_field = None
                if fields_resp.status == 200:
                    for f in fields_resp.json() or []:
                        if str(f.get("name", "")).strip().lower() == "epic link":
                            epic_link_field = f.get("id")
                            break
                if not epic_link_field:
                    pytest.skip(
                        f"Story-Epic reparent failed (HTTP {edit_resp.status}) and no Epic Link field — skipping"
                    )
                retry = await jira_datasource.edit_issue(
                    issueIdOrKey=story_key, fields={epic_link_field: new_epic_key},
                )
                if retry.status not in (200, 204):
                    pytest.skip(f"Story-Epic reparent rejected via epic-link too (HTTP {retry.status}) — skipping")
            except Exception as e:
                pytest.skip(f"Story-Epic reparent unsupported in this workspace: {e}")

        await wait_until_jira_condition(
            check_fn=lambda: check_issue_parent_bool(jira_datasource, story_key, new_epic_key),
            description=f"TC-MOVE-002: {story_key} parent == {new_epic_key}",
            timeout=300,
        )

        _restart_sync(pipeshub_client, connector_id)
        after_count = await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, timeout=240,
        )
        assert after_count == before_count, "Record count should be stable after story reparent"

        record = await graph_provider.get_record_by_external_id(connector_id, story_id)
        assert record is not None
        assert str(record.parent_external_record_id) == new_epic_id, (
            f"Story.parent_external_record_id should be {new_epic_id}, got {record.parent_external_record_id}"
        )
        incoming = await graph_provider.get_record_incoming_relations(
            connector_id, story_id, "PARENT_CHILD",
        )
        assert new_epic_id in incoming and old_epic_id not in incoming, (
            f"PARENT_CHILD into story: expect new epic {new_epic_id} in {incoming!r}, old {old_epic_id} absent"
        )

        logger.info("TC-MOVE-002 passed: %s reparented epic %s -> %s", story_key, old_epic_key, new_epic_key)


# =============================================================================
# TestJiraValidation — entity properties + relationship audit (already-synced state)
# =============================================================================


class TestJiraValidation:
    """Entity / relationship validation against the fixture's initial sync output."""

    @pytest.mark.order(2)
    async def test_tc_jira_001_user_properties(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-001: synced user exists; USER_APP_RELATION matches Jira user fetch (connector-style).

        Compares graph ``USER_APP_RELATION`` edges to the count from the same paginated
        ``get_all_users(query='')`` + active + ``emailAddress`` rules as ``JiraCloudConnector._fetch_users``.
        """
        connector_id = jira_connector["connector_id"]
        test_email = os.getenv("JIRA_TEST_EMAIL")
        if not test_email:
            pytest.skip("JIRA_TEST_EMAIL not set")

        account_id = jira_connector.get("lead_account_id")
        if not account_id:
            pytest.skip("jira_connector missing lead_account_id (fixture setup failed?)")

        await connector_assertions.assert_user_exists(
            connector_id=connector_id, source_user_id=account_id, email=test_email,
        )
        jira_users_with_email = await count_jira_users_with_visible_email(jira_datasource)
        rel_count = await graph_provider.count_user_app_relation_edges(connector_id)
        assert rel_count == jira_users_with_email, (
            f"USER_APP_RELATION count {rel_count} != Jira _fetch_users-style count "
            f"({jira_users_with_email}) (connector {connector_id})"
        )

        await assert_user_app_edge(
            account_id, connector_id=connector_id, graph_provider=graph_provider,
        )

        logger.info("TC-JIRA-001 passed: %d users validated", rel_count)

    @pytest.mark.order(3)
    async def test_tc_jira_002_group_properties(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-002: graph user-group count matches Jira bulk API; fixture group has one member edge + YAML ``app_user_group``."""
        connector_id = jira_connector["connector_id"]

        jira_group_total = await count_jira_site_groups_bulk(jira_datasource)
        graph_group_total = await graph_provider.count_user_groups(connector_id)
        assert graph_group_total == jira_group_total, (
            f"Graph UserGroup/Group count {graph_group_total} != Jira bulk group count "
            f"{jira_group_total} (connector {connector_id})"
        )

        group_id = jira_connector.get("test_group_id")
        group_name = jira_connector.get("test_group_name")
        if not (group_id and group_name):
            logger.info("TC-JIRA-002 passed: %d groups (no fixture group)", graph_group_total)
            return

        graph_ug = await connector_assertions.assert_group_exists(
            connector_id=connector_id, external_group_id=group_id, name=group_name,
        )
        member_edges = await graph_provider.count_group_members(connector_id, group_id)
        assert member_edges == 1, (
            f"Fixture group {group_name} should have exactly 1 member edge in graph, got {member_edges}"
        )

        expected_ug = JiraExpected.user_group(
            name=group_name,
            source_user_group_id=str(group_id),
            connector_id=connector_id,
        )
        ug_skip = frozenset({"created_at", "updated_at", "source_created_at", "source_updated_at"})
        assert_graph_entity_matches(
            expected_ug, graph_ug, entity="app_user_group", skip_compare=ug_skip,
        )

        lead_account_id = jira_connector.get("lead_account_id")
        if lead_account_id:
            perm_edges = build_permission_expectations([
                PermissionExpectation(
                    from_kind="user",
                    from_external_id=lead_account_id,
                    to_kind="group",
                    to_external_id=str(group_id),
                    connector_id=connector_id,
                    entity_type="USER",
                    role="READER",
                ),
            ])
            await assert_graph_edges(graph_provider, perm_edges)

        logger.info("TC-JIRA-002 passed: %d groups, fixture group %s validated", graph_group_total, group_name)

    @pytest.mark.order(5)
    async def test_tc_jira_003_project_record_group(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-JIRA-003: project synced as RecordGroup with correct properties."""
        connector_id = jira_connector["connector_id"]
        project_key = jira_connector["project_key"]
        project_id = jira_connector["project_id"]

        proj_resp = await jira_datasource.get_project(projectIdOrKey=project_key)
        assert proj_resp.status == 200, f"get_project failed: HTTP {proj_resp.status}"
        proj_data = proj_resp.json()

        rg = await graph_provider.get_record_group_by_external_id(connector_id, project_id)
        assert rg is not None, f"Project (id={project_id}) missing as RecordGroup"

        expected_rg = JiraExpected.record_group(
            proj_data,
            connector_id=connector_id,
            project_key=project_key,
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

        # Linkage: first seeded issue belongs to this project.
        first_key = jira_connector["seed_issue_keys"][0]
        first_resp = await jira_datasource.get_issue(issueIdOrKey=first_key, fields="summary")
        first_id = str(first_resp.json()["id"])
        first_record = await connector_assertions.assert_record_exists(connector_id, first_id)
        assert first_record.external_record_group_id == project_id, (
            f"Issue {first_key} should belong to project {project_id}; got {first_record.external_record_group_id}"
        )
        logger.info("TC-JIRA-003 passed: project %s validated as RecordGroup", project_key)

    @pytest.mark.order(6)
    async def test_tc_jira_004_issue_properties(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-004: seed issue has correct TICKET record properties."""
        connector_id = jira_connector["connector_id"]
        project_id = jira_connector["project_id"]
        target_key = jira_connector["seed_issue_keys"][0]
        base_url = (os.getenv("JIRA_TEST_BASE_URL") or "").rstrip("/")

        issue_resp = await jira_datasource.get_issue(issueIdOrKey=target_key, fields="summary")
        assert issue_resp.status == 200
        issue_data = issue_resp.json()
        target_id = str(issue_data["id"])
        summary = (issue_data.get("fields") or {}).get("summary") or ""

        expected = RecordAssertion(
            external_record_id=target_id,
            record_type=RecordType.TICKET.value,
            mime_type="application/blocks",
            record_name=f"[{target_key}] {summary}",
            external_record_group_id=project_id,
        )
        record = await connector_assertions.assert_record_exists(connector_id, target_id, expected)

        assert record.weburl is not None, "Issue should have weburl"
        assert record.weburl.startswith(base_url), (
            f"weburl '{record.weburl}' should start with base_url '{base_url}'"
        )
        assert f"/browse/{target_key}" in record.weburl, (
            f"weburl '{record.weburl}' should contain '/browse/{target_key}'"
        )
        assert record.source_created_at is not None

        record_edges = build_record_edge_expectations(record, connector_id)
        await assert_graph_edges(graph_provider, record_edges)

        logger.info("TC-JIRA-004 passed: issue %s validated", target_key)


# =============================================================================
# TestJiraIndexing — graph ``indexing_status`` COMPLETED for seed issue
# =============================================================================


class TestJiraIndexing:
    """Indexing pipeline: seed TICKET reaches ``COMPLETED`` in graph."""

    @pytest.mark.order(7)
    async def test_tc_jira_idx_001_seed_issue_indexing_completed(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-JIRA-IDX-001: first seed issue reaches ``indexing_status == COMPLETED``.

        Assumes the deployment already has whatever models/indexing the pipeline needs.
        If the graph shows ``AUTO_INDEX_OFF`` once, triggers ``reindex_record`` (same API
        as Confluence ITs) and keeps polling.
        """
        connector_id = jira_connector["connector_id"]
        project_id = jira_connector["project_id"]
        key = jira_connector["seed_issue_keys"][0]
        issue_resp = await jira_datasource.get_issue(issueIdOrKey=key, fields="summary")
        assert issue_resp.status == 200, f"get_issue {key!r} failed: HTTP {issue_resp.status}"
        external_id = str(issue_resp.json()["id"])
        rec = await wait_until_record_indexing_completed(
            graph_provider,
            connector_id,
            external_id,
            timeout=JIRA_INDEXING_WAIT_SEC,
            description=f"TC-JIRA-IDX-001 seed issue {key}",
            pipeshub_client=pipeshub_client,
        )
        assert rec.indexing_status == ProgressStatus.COMPLETED.value
        assert rec.record_type == RecordType.TICKET, (
            f"Expected TICKET record_type, got {rec.record_type!r}"
        )
        assert rec.connector_id == connector_id
        assert rec.external_record_group_id == project_id, (
            f"Issue {key} should belong to project {project_id}; got {rec.external_record_group_id!r}"
        )
        logger.info("TC-JIRA-IDX-001 passed: %s indexing completed", key)


# =============================================================================
# TestJiraAttachments — FILE record + attachment edge
# =============================================================================


class TestJiraAttachments:
    """Jira FILE-record validation."""

    @pytest.mark.order(14)
    async def test_tc_jira_attach_001_attachment_as_file_record(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-JIRA-ATTACH-001: attachment synced as FILE with parent TICKET; ATTACHMENT or PARENT_CHILD edge."""
        attachment_id = jira_connector.get("seed_attachment_id")
        issue_key = jira_connector.get("seed_attachment_issue_key")
        if not (attachment_id and issue_key):
            pytest.skip("Attachment not seeded — skipping")
        connector_id = jira_connector["connector_id"]
        project_id = jira_connector["project_id"]

        issue_resp = await jira_datasource.get_issue(
            issueIdOrKey=issue_key,
            fields=["summary", "attachment"],
        )
        assert issue_resp.status == 200
        issue_payload = issue_resp.json()
        issue_id = str(issue_payload["id"])
        fields = issue_payload.get("fields") or {}

        meta = JiraExpected.attachment_metadata(fields, str(attachment_id))
        assert meta is not None, (
            f"Attachment id={attachment_id} not found on issue {issue_key}"
        )
        att_filename, att_mime, att_size, att_created_ms = meta

        external_id = f"attachment_{attachment_id}"
        expected = RecordAssertion(
            external_record_id=external_id,
            record_type=RecordType.FILE.value,
            mime_type=att_mime,
            parent_external_record_id=issue_id,
            external_record_group_id=project_id,
            is_dependent_node=True,
        )
        await connector_assertions.assert_record_exists(
            connector_id, external_id, expected,
        )

        parent_ticket = await graph_provider.get_record_by_external_id(connector_id, issue_id)
        assert parent_ticket is not None, (
            f"Parent TICKET record missing for issue id={issue_id} (key={issue_key})"
        )

        expected_file = JiraExpected.file_record(
            attachment_id=str(attachment_id),
            filename=att_filename,
            mime_type=att_mime,
            file_size=att_size,
            created_at=att_created_ms,
            issue_id=issue_id,
            issue_key=issue_key,
            project_id=project_id,
            connector_id=connector_id,
            parent_node_id=parent_ticket.id,
        )
        typed = await graph_provider.get_typed_record_by_external_id(connector_id, external_id)
        assert typed is not None, f"Typed FILE record missing for {external_id!r}"
        assert isinstance(typed, FileRecord), f"Expected FileRecord, got {type(typed).__name__}"
        await assert_graph_entity_with_edges(
            expected_file, typed,
            entity="file_record",
            connector_id=connector_id,
            graph_provider=graph_provider,
        )

        logger.info("TC-JIRA-ATTACH-001 passed")


# =============================================================================
# TestJiraEdges — comprehensive edge inventory audit
# =============================================================================


class TestJiraEdges:
    """One-shot audit of every edge category the connector should emit."""

    @pytest.mark.order(20)
    async def test_tc_jira_edges_001_edge_inventory(
        self,
        jira_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-EDGES-001: structural edge invariants after incremental tests (strict)."""
        connector_id = jira_connector["connector_id"]

        records = await graph_provider.count_records(connector_id)
        ticket_count = await graph_provider.count_records_by_type(connector_id, RecordType.TICKET.value)
        file_count = await graph_provider.count_records_by_type(connector_id, RecordType.FILE.value)

        assert ticket_count == jira_connector["expected_ticket_count"] + 1, (
            f"TICKET count should be baseline + 1 (INCR-001); "
            f"expected {jira_connector['expected_ticket_count'] + 1}, got {ticket_count}"
        )
        assert file_count == jira_connector["expected_file_count"]
        assert records == jira_connector["expected_total_records"] + 1, (
            f"Total records baseline + 1; expected {jira_connector['expected_total_records'] + 1}, got {records}"
        )

        rg_edges = await graph_provider.count_record_group_edges(connector_id)
        assert rg_edges == records, (
            f"BELONGS_TO record→group {rg_edges} must equal records {records}"
        )
        assert rg_edges == jira_connector["expected_record_group_edges"] + 1, (
            f"Record→group edges should grow by 1 after INCR-001; "
            f"expected {jira_connector['expected_record_group_edges'] + 1}, got {rg_edges}"
        )

        group_hier = await graph_provider.count_group_hierarchy_edges(connector_id)
        assert group_hier == 0, f"Jira projects should not nest; got {group_hier} hierarchy edges"

        pc = await graph_provider.count_parent_child_edges(connector_id)
        assert pc == jira_connector["expected_parent_child_edges"], (
            f"PARENT_CHILD edges {pc} must match baseline {jira_connector['expected_parent_child_edges']}"
        )

        attach = await graph_provider.count_record_relation_edges(connector_id, "ATTACHMENT")
        assert attach == jira_connector["expected_attachment_edges"]

        inherit = await graph_provider.count_inherit_permissions_edges(connector_id)
        assert inherit == records, f"INHERIT_PERMISSIONS {inherit} must equal records {records}"
        assert inherit == jira_connector["expected_inherit_edges"] + 1, (
            f"INHERIT edges should grow by 1 after INCR-001; "
            f"expected {jira_connector['expected_inherit_edges'] + 1}, got {inherit}"
        )

        perms = await graph_provider.count_permission_edges(connector_id)
        assert perms == inherit, (
            f"Permission aggregate {perms} must equal INHERIT count {inherit} for this connector"
        )
        assert perms == jira_connector["expected_permission_aggregate"] + 1, (
            f"Permission aggregate should grow by 1 with new record; "
            f"expected {jira_connector['expected_permission_aggregate'] + 1}, got {perms}"
        )

        ug_perm = await graph_provider.count_user_to_group_permission_edges(connector_id)
        ur_perm = await graph_provider.count_user_to_role_permission_edges(connector_id)
        rg_perm = await graph_provider.count_permission_edges_to_record_groups(connector_id)
        assert ug_perm == jira_connector["expected_permission_user_group_edges"]
        assert ur_perm == jira_connector["expected_permission_user_role_edges"]
        assert rg_perm == jira_connector["expected_permission_to_record_group_edges"]

        app_edges = await graph_provider.count_app_record_group_edges(connector_id)
        rgs = await graph_provider.count_record_groups(connector_id)
        exp_rg = jira_connector["expected_record_groups"]
        assert app_edges == rgs == exp_rg, (
            f"App→RecordGroup edges {app_edges}, record groups {rgs}, expected {exp_rg}"
        )

        logger.info("TC-JIRA-EDGES-001 passed")


# =============================================================================
# TestJiraBrowseProjectPermissions — BROWSE_PROJECTS scheme vs graph
# =============================================================================


class TestJiraBrowseProjectPermissions:
    """``BROWSE_PROJECTS`` permission scheme holders vs ``PERMISSION → RecordGroup`` edges."""

    @pytest.mark.order(21)
    async def test_tc_browse_001_default_scheme_matches_graph(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-BROWSE-001: live Jira scheme preview matches graph for the INTTEST RecordGroup."""
        connector_id = jira_connector["connector_id"]
        project_key = jira_connector["project_key"]
        project_id = jira_connector["project_id"]
        expected = await preview_jira_browse_projects_permission_edges_to_record_group(
            jira_datasource,
            project_key=project_key,
        )
        actual = await graph_provider.count_permission_edges_to_record_groups(
            connector_id,
            project_id,
        )
        assert actual == expected, (
            f"TC-BROWSE-001: PERMISSION→RecordGroup for project {project_id!r}: "
            f"graph={actual} jira_preview={expected}"
        )


