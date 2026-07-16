# pyright: ignore-file

"""
Jira Connector – Integration Tests (pre-provisioned, read-only + self-cleaning mutations)
=========================================================================================

Scope comes from ``JIRA_TEST_PROJECT_KEYS`` (env); fixture issue keys from ``constants.py``.
Counts are BELONGS_TO-guarded (see the IT graph providers).

  order 1  TC-SYNC-001        — full sync baseline vs fixture snapshot + live JQL
  order 2  TC-JIRA-001        — users / USER_APP_RELATION
  order 3  TC-JIRA-002        — site groups count
  order 4  TC-JIRA-ROLE-001   — all primary-project AppRoles + synced User→Role members
  order 5  TC-JIRA-003        — project RecordGroup
  order 6  TC-JIRA-004        — reference issue TICKET properties
  order 7  TC-JIRA-IDX-001    — reference issue indexing COMPLETED
  order 8  TC-INCR-001        — create + incremental + delete/full-sync cleanup
  order 9  TC-UPDATE-001      — edit + restore
  order 10 TC-JIRA-HIER-001   — Epic↔child and Task↔sub-task PARENT_CHILD
  order 11 TC-JIRA-ENTITY-001 — CREATED_BY/REPORTED_BY/ASSIGNED_TO entityRelations
  order 12 TC-JIRA-LINKS-001  — outward issuelinks → RECORD_RELATION
  order 13 TC-JIRA-ATTACH-001 — attachment FILE record
  order 14 TC-JIRA-BLOCKS-001 — streamed application/blocks expected snapshot
  order 15 TC-BROWSE-001      — BROWSE_PROJECTS scheme → PERMISSION→RecordGroup
  order 16 TC-FILTER-001      — in [A,B,(C)]
  order 17 TC-FILTER-002      — not_in [A] (primary absent)
  order 18 TC-FILTER-DATE-001 — created after/before windows
  order 19 TC-FILTER-003      — empty = all (last)
"""

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import ProgressStatus  # type: ignore[import-not-found]  # noqa: E402
from app.connectors.utils.value_mapper import map_relationship_type  # type: ignore[import-not-found]  # noqa: E402
from app.models.entities import FileRecord, RecordType  # type: ignore[import-not-found]  # noqa: E402
from app.sources.external.jira.jira import JiraDataSource  # type: ignore[import-not-found]  # noqa: E402
from helper.assertions import ConnectorAssertions  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import wait_for_sync_completion  # noqa: E402
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from validation.graph_entity_validator import (  # noqa: E402
    assert_graph_entity_matches,
    assert_graph_entity_with_edges,
    assert_user_app_edge,
)
from connectors.jira.constants import (  # noqa: E402
    JIRA_FILTER_DATE_CUT_MS,
    JIRA_INDEXING_WAIT_SEC,
    JIRA_USERS_GROUP_NAME,
)
from connectors.jira.jira_block_utils import (  # noqa: E402
    bootstrap_expected,
    load_expected,
    normalize_blocks_container,
    parse_connector_blocks_via_processor,
)
from connectors.jira.jira_expected import JiraExpected  # noqa: E402
from connectors.jira.jira_test_utils import (  # noqa: E402
    assert_jira_issues_match_graph_records,
    check_issue_exists_bool,
    count_jira_group_synced_members,
    count_jira_site_groups_bulk,
    count_jira_users_with_visible_email,
    get_jira_issue_updated_ms,
    jira_api_call_with_retry,
    parse_jira_timestamp,
    preview_jira_browse_projects_permission_edges_to_record_group,
    preview_jira_role_member_counts,
    search_issues_jql,
    wait_until_jira_condition,
    wait_until_record_indexing_completed,
)

logger = logging.getLogger("jira-lifecycle-test")


def _adf(text: str) -> dict[str, Any]:
    """Minimal Atlassian Document Format paragraph."""
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


def _restart_sync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    """Toggle off/on to trigger an incremental sync (see original note on the trailing wait)."""
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    pipeshub_client.wait(8)


async def _apply_filter_full_sync(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    filters: dict[str, Any],
    *,
    timeout: int = 300,
) -> int:
    """Set a full filter payload then force a full sync (wipes+recreates sync edges).

    A full sync is required for scope *narrowing* to be reflected: it strips
    ``BELONGS_TO`` connector-wide and recreates it only for in-scope entities, so
    projects that left the filter drop out of BELONGS_TO-guarded counts.

    Changing the filter sets the backend ``pendingFullSync`` flag, so the re-enable that
    ``update_connector_filters_sync_safe`` performs is itself a full sync — no separate
    ``resync`` is needed (that would just run a redundant second full sync).
    """
    pipeshub_client.update_connector_filters_sync_safe(connector_id, filters=filters)
    return await wait_for_sync_completion(
        pipeshub_client, graph_provider, connector_id, timeout=timeout,
    )


def _sync_filters(**values: Any) -> dict[str, Any]:
    """Wrap filter fields into the connector's ``config.filters.sync.values`` shape.

    The connector reads ``config.filters.sync.values.<key>`` (see load_connector_filters);
    the filters-sync endpoint stores the request ``filters`` verbatim, so the payload must
    already carry the ``sync.values`` nesting — a flat ``{"project_keys": ...}`` is written
    to the wrong path and silently ignored.
    """
    return {"sync": {"values": values}}


def _pk(operator: str, values: list[str]) -> dict[str, Any]:
    return {"operator": operator, "type": "list", "value": values}


pytestmark = [
    pytest.mark.integration,
    pytest.mark.jira,
    pytest.mark.asyncio(loop_scope="session"),
]


# =============================================================================
# TestJiraConnector — full sync, incremental, update
# =============================================================================


class TestJiraConnector:
    """Sync-pipeline tests: full sync baseline, incremental create, field update."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: validate the graph after the fixture's full sync (snapshot + live JQL)."""
        connector_id = jira_connector["connector_id"]
        primary_key = jira_connector["primary_key"]

        ticket_count = await graph_provider.count_records_by_type(connector_id, RecordType.TICKET.value)
        file_count = await graph_provider.count_records_by_type(connector_id, RecordType.FILE.value)
        total = await graph_provider.count_records(connector_id)

        # Independent (live Jira): TICKET / FILE counts, attachment + parent-child edges.
        assert ticket_count == jira_connector["expected_ticket_count"], (
            f"graph TICKET {ticket_count} != Jira JQL {jira_connector['expected_ticket_count']}"
        )
        assert file_count == jira_connector["expected_file_count"], (
            f"graph FILE {file_count} != Jira attachments {jira_connector['expected_file_count']}"
        )
        assert total == jira_connector["expected_total_records"], (
            f"graph records {total} != Jira tickets+attachments {jira_connector['expected_total_records']}"
        )
        attach = await graph_provider.count_record_relation_edges(connector_id, "ATTACHMENT")
        assert attach == jira_connector["expected_attachment_edges"], (
            f"ATTACHMENT edges {attach} != Jira attachments {jira_connector['expected_attachment_edges']}"
        )
        pc = await graph_provider.count_parent_child_edges(connector_id)
        assert pc == jira_connector["expected_parent_child_edges"], (
            f"PARENT_CHILD {pc} != Jira issues-with-parent {jira_connector['expected_parent_child_edges']}"
        )

        # Independent: record-group count from filter scope (primary only).
        app_edges = await graph_provider.count_app_record_group_edges(connector_id)
        rgs = await graph_provider.count_record_groups(connector_id)
        assert app_edges == rgs == jira_connector["expected_record_groups"]

        # Structural graph-consistency invariants (graph self-consistency, no Jira dependency).
        assert total == ticket_count + file_count, (
            f"records {total} != tickets {ticket_count} + files {file_count} (unexpected record types)"
        )
        rg_edges = await graph_provider.count_record_group_edges(connector_id)
        assert rg_edges == total, f"every record needs one BELONGS_TO→RecordGroup ({rg_edges} != {total})"
        inherit = await graph_provider.count_inherit_permissions_edges(connector_id)
        assert inherit == total, f"every record needs one INHERIT_PERMISSIONS edge ({inherit} != {total})"
        perms = await graph_provider.count_permission_edges(connector_id)
        assert perms == inherit, (
            f"Jira records carry no direct PERMISSION edges (inherit-only): perms {perms} != inherit {inherit}"
        )

        # App metadata document.
        graph_app = await graph_provider.get_app_metadata_by_connector_id(connector_id)
        assert graph_app is not None, f"apps document missing for connector {connector_id}"
        expected_app = JiraExpected.app_metadata_for_full_sync_baseline(jira_connector)
        app_skip = frozenset({
            "created_at_timestamp", "updated_at_timestamp", "auth_type", "is_active",
            "is_agent_active", "is_configured", "is_authenticated", "created_by",
            "updated_by", "status", "is_locked",
        })
        assert_graph_entity_matches(expected_app, graph_app, entity="app_metadata", skip_compare=app_skip)

        # Live reconciliation (concurrency-tolerant): JQL count == graph TICKET count.
        await assert_jira_issues_match_graph_records(
            jira_datasource, graph_provider, connector_id, primary_key, phase="TC-SYNC-001",
        )

        # Sanity: a known in-scope ticket carries a live BELONGS_TO edge (guard regression tripwire).
        ref_id = jira_connector.get("reference_issue_id")
        if ref_id:
            rec = await graph_provider.get_record_by_external_id(connector_id, ref_id)
            assert rec is not None and str(rec.external_record_group_id) == jira_connector["primary_project_id"]

        logger.info("TC-SYNC-001 passed: %d records", total)

    @pytest.mark.order(8)
    async def test_tc_incr_001_incremental_sync_new_issue(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: create one issue, incremental sync picks it up (assert by external id); cleanup."""
        connector_id = jira_connector["connector_id"]
        primary_key = jira_connector["primary_key"]
        issue_type = jira_connector.get("default_issue_type") or "Task"
        base_url = (os.getenv("JIRA_TEST_BASE_URL") or "").rstrip("/")

        title = f"PHIT-IncrTest-{uuid.uuid4().hex[:8]}"
        new_id: str | None = None
        try:
            # create_issue: retry 429 only; 5xx/timeout/transport → fail (no duplicate ticket).
            resp = await jira_api_call_with_retry(
                jira_datasource.create_issue,
                fields={
                    "project": {"key": primary_key},
                    "summary": title,
                    "issuetype": {"name": issue_type},
                    "description": _adf("Incremental sync test issue."),
                },
                context="TC-INCR-001 create_issue",
                retry_server_errors=False,
            )
            assert resp.status in (200, 201), f"create '{title}' failed: HTTP {resp.status}"
            data = resp.json()
            new_key = data["key"]
            new_id = str(data["id"])

            await wait_until_jira_condition(
                check_fn=lambda: check_issue_exists_bool(jira_datasource, new_key),
                description=f"TC-INCR-001: new issue fetchable ({new_key})",
                timeout=120,
            )

            _restart_sync(pipeshub_client, connector_id)
            await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=240)

            # Assert the delta by external id (parallel-safe), not a global count delta.
            actual = await graph_provider.get_typed_record_by_external_id(connector_id, new_id)
            assert actual is not None, f"typed TICKET record missing for external id {new_id}"
            expected = await JiraExpected.ticket_record(
                new_key, connector_id=connector_id, datasource=jira_datasource,
                site_base_url=base_url or None,
            )
            await assert_graph_entity_with_edges(
                expected, actual, entity="ticket_record",
                connector_id=connector_id, graph_provider=graph_provider,
            )
            await graph_provider.assert_record_paths_or_names_contain(connector_id, [title])
            logger.info("TC-INCR-001 passed: %s synced", new_key)
        finally:
            if new_id:
                try:
                    del_resp = await jira_api_call_with_retry(
                        jira_datasource.delete_issue, issueIdOrKey=new_id,
                        context="TC-INCR-001 delete_issue", retry_server_errors=True,
                    )
                    # 404 (already gone) is acceptable.
                    if getattr(del_resp, "status", 204) not in (200, 202, 204, 404):
                        logger.warning("TC-INCR-001 cleanup: delete HTTP %s", del_resp.status)
                    pipeshub_client.resync_connector(connector_id, full_sync=True)
                    await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=240)
                except Exception as e:
                    logger.warning("TC-INCR-001 cleanup failed: %s", e)

    @pytest.mark.order(9)
    async def test_tc_update_001_content_and_summary_revision(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: edit reference issue summary+description; version += 1; revision = Jira updated ms; restore."""
        connector_id = jira_connector["connector_id"]
        target_key = jira_connector.get("reference_issue_key")
        if not target_key:
            pytest.skip("No reference issue discovered on primary — skipping")
        target_id = jira_connector["reference_issue_id"]

        # Capture original summary + description to restore.
        orig_resp = await jira_datasource.get_issue(issueIdOrKey=target_key, fields="summary,description")
        assert orig_resp.status == 200
        orig_fields = (orig_resp.json() or {}).get("fields") or {}
        orig_summary = orig_fields.get("summary") or ""
        orig_description = orig_fields.get("description")

        record_before = await graph_provider.get_record_by_external_id(connector_id, target_id)
        assert record_before is not None, f"Issue {target_key} not in graph"
        old_version = int(record_before.version)

        new_summary = f"PHIT-Edited-{uuid.uuid4().hex[:8]}"
        try:
            edit_resp = await jira_api_call_with_retry(
                jira_datasource.edit_issue, issueIdOrKey=target_key,
                fields={"summary": new_summary, "description": _adf("Edited via TC-UPDATE-001.")},
                context="TC-UPDATE-001 edit_issue", retry_server_errors=True,
            )
            assert edit_resp.status in (200, 204), f"edit_issue failed: HTTP {edit_resp.status}"
            pipeshub_client.wait(5)

            _restart_sync(pipeshub_client, connector_id)
            await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=240)

            record_after = await graph_provider.get_record_by_external_id(connector_id, target_id)
            assert record_after is not None, "Record missing after sync"
            assert record_after.version == old_version + 1, (
                f"Expected version {old_version + 1}, got {record_after.version}"
            )
            jira_updated_ms = await get_jira_issue_updated_ms(jira_datasource, target_key)
            assert str(record_after.external_revision_id) == str(jira_updated_ms)
            assert new_summary in (record_after.record_name or "")
            logger.info("TC-UPDATE-001 passed: version %s -> %s", old_version, record_after.version)
        finally:
            restore_fields: dict[str, Any] = {"summary": orig_summary}
            if orig_description is not None:
                restore_fields["description"] = orig_description
            try:
                await jira_api_call_with_retry(
                    jira_datasource.edit_issue, issueIdOrKey=target_key, fields=restore_fields,
                    context="TC-UPDATE-001 restore", retry_server_errors=True,
                )
            except Exception as e:
                logger.warning("TC-UPDATE-001 restore failed: %s", e)


# =============================================================================
# TestJiraValidation — read-only entity / relationship validation
# =============================================================================


class TestJiraValidation:
    """Entity / relationship validation against the fixture's initial sync output."""

    @pytest.mark.order(2)
    async def test_tc_jira_001_user_properties(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-001: synced user exists; USER_APP_RELATION == connector-style visible-email count."""
        connector_id = jira_connector["connector_id"]
        account_id = jira_connector.get("lead_account_id")
        if not account_id:
            pytest.skip("lead_account_id missing")

        jira_users_with_email = await count_jira_users_with_visible_email(jira_datasource)
        rel_count = await graph_provider.count_user_app_relation_edges(connector_id)
        assert rel_count == jira_users_with_email, (
            f"USER_APP_RELATION {rel_count} != Jira visible-email users {jira_users_with_email}"
        )
        await assert_user_app_edge(account_id, connector_id=connector_id, graph_provider=graph_provider)
        logger.info("TC-JIRA-001 passed: %d users", rel_count)

    @pytest.mark.order(3)
    async def test_tc_jira_002_group_properties(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-002: graph user-group count == Jira bulk count; the site users group's
        members have User→Group edges."""
        connector_id = jira_connector["connector_id"]
        jira_group_total = await count_jira_site_groups_bulk(jira_datasource)
        graph_group_total = await graph_provider.count_user_groups(connector_id)
        assert graph_group_total == jira_group_total, (
            f"Graph UserGroup count {graph_group_total} != Jira bulk {jira_group_total}"
        )

        # User→Group edges for the site's default users group (its members = synced users).
        if JIRA_USERS_GROUP_NAME:
            gid, expected_members = await count_jira_group_synced_members(
                jira_datasource, JIRA_USERS_GROUP_NAME,
            )
            if gid:
                graph_members = await graph_provider.count_user_to_group_permission_edges(connector_id, gid)
                assert graph_members == expected_members, (
                    f"group {JIRA_USERS_GROUP_NAME!r}: graph User→Group {graph_members} != "
                    f"Jira synced members {expected_members}"
                )
                logger.info(
                    "TC-JIRA-002: group %s validated (%d member edges)",
                    JIRA_USERS_GROUP_NAME, expected_members,
                )
        logger.info("TC-JIRA-002 passed: %d groups", graph_group_total)

    @pytest.mark.order(4)
    async def test_tc_jira_role_001_project_roles(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-ROLE-001: every primary-project AppRole (incl. projectLead) exists; per-role synced
        User→Role members match Jira (user actors + group actors expanded to members)."""
        connector_id = jira_connector["connector_id"]
        primary_key = jira_connector["primary_key"]

        # role name (for the AppRole entity match), keyed by source_role_id.
        roles_resp = await jira_datasource.get_project_roles(projectIdOrKey=primary_key)
        assert roles_resp.status == 200, f"get_project_roles failed: HTTP {roles_resp.status}"
        role_names: dict[str, str] = {}
        for role_name, role_url in (roles_resp.json() or {}).items():
            if role_name == "atlassian-addons-project-access":
                continue
            try:
                role_id = int(str(role_url).rstrip("/").split("/")[-1])
            except (TypeError, ValueError):
                continue
            role_names[f"{primary_key}_{role_id}"] = f"{primary_key} - {role_name}"
        role_names[f"{primary_key}_projectLead"] = f"{primary_key} - Project Lead"

        # Per-role expected synced-member counts (user actors + group actors → members, deduped).
        expected_counts = await preview_jira_role_member_counts(
            jira_datasource, project_key=primary_key, lead_account_id=jira_connector["lead_account_id"],
        )

        total_members = 0
        for source_role_id, name in role_names.items():
            actual_role = await graph_provider.get_app_role_by_external_id(connector_id, source_role_id)
            assert actual_role is not None, f"AppRole {source_role_id!r} missing in graph"
            expected_role = JiraExpected.app_role(
                name=name, source_role_id=source_role_id, connector_id=connector_id,
            )
            assert_graph_entity_matches(
                expected_role, actual_role, entity="app_role",
                skip_compare=frozenset({
                    "id", "org_id", "created_at", "updated_at",
                    "source_created_at", "source_updated_at",
                }),
            )
            expected_members = expected_counts.get(source_role_id, 0)
            graph_members = await graph_provider.count_user_to_role_permission_edges(connector_id, source_role_id)
            assert graph_members == expected_members, (
                f"role {source_role_id}: graph User→Role {graph_members} != "
                f"Jira-expanded (user+group actors) {expected_members}"
            )
            total_members += expected_members

        logger.info("TC-JIRA-ROLE-001 passed: %d roles, %d role members", len(role_names), total_members)

    @pytest.mark.order(5)
    async def test_tc_jira_003_project_record_group(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-JIRA-003: primary project synced as RecordGroup; reference issue belongs to it."""
        connector_id = jira_connector["connector_id"]
        primary_key = jira_connector["primary_key"]
        project_id = jira_connector["primary_project_id"]

        proj_resp = await jira_datasource.get_project(projectIdOrKey=primary_key)
        assert proj_resp.status == 200
        rg = await graph_provider.get_record_group_by_external_id(connector_id, project_id)
        assert rg is not None, f"Project (id={project_id}) missing as RecordGroup"

        expected_rg = JiraExpected.record_group(proj_resp.json(), connector_id=connector_id, project_key=primary_key)
        rg_skip = frozenset({
            "created_at", "updated_at", "source_created_at", "source_updated_at", "web_url", "description",
        })
        await assert_graph_entity_with_edges(
            expected_rg, rg, entity="record_group", connector_id=connector_id,
            graph_provider=graph_provider, skip_compare=rg_skip,
        )

        ref_key = jira_connector.get("reference_issue_key")
        if ref_key:
            first_record = await connector_assertions.assert_record_exists(
                connector_id, jira_connector["reference_issue_id"],
            )
            assert first_record.external_record_group_id == project_id
        logger.info("TC-JIRA-003 passed: %s validated", primary_key)

    @pytest.mark.order(6)
    async def test_tc_jira_004_issue_properties(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-004: reference issue has correct TICKET record properties + edges."""
        connector_id = jira_connector["connector_id"]
        target_key = jira_connector.get("reference_issue_key")
        if not target_key:
            pytest.skip("No reference issue discovered on primary — skipping")
        target_id = jira_connector["reference_issue_id"]
        base_url = (os.getenv("JIRA_TEST_BASE_URL") or "").rstrip("/")

        actual = await graph_provider.get_typed_record_by_external_id(connector_id, target_id)
        assert actual is not None, f"typed TICKET record missing for external id {target_id}"
        expected = await JiraExpected.ticket_record(
            target_key, connector_id=connector_id, datasource=jira_datasource,
            site_base_url=base_url or None,
        )
        await assert_graph_entity_with_edges(
            expected, actual, entity="ticket_record",
            connector_id=connector_id, graph_provider=graph_provider,
        )
        logger.info("TC-JIRA-004 passed: %s", target_key)

    @pytest.mark.order(11)
    async def test_tc_jira_entity_001_ticket_user_relations(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-ENTITY-001: CREATED_BY / REPORTED_BY / ASSIGNED_TO for the reference issue (synced users only)."""
        connector_id = jira_connector["connector_id"]
        target_key = jira_connector.get("reference_issue_key")
        if not target_key:
            pytest.skip("No reference issue discovered on primary — skipping")
        target_id = jira_connector["reference_issue_id"]

        resp = await jira_datasource.get_issue(issueIdOrKey=target_key, fields="creator,reporter,assignee")
        assert resp.status == 200
        fields = (resp.json() or {}).get("fields") or {}

        # edgeType -> (accountId, email) from the issue actor
        actors = {
            "CREATED_BY": fields.get("creator") or {},
            "REPORTED_BY": fields.get("reporter") or {},
            "ASSIGNED_TO": fields.get("assignee") or {},
        }
        asserted = 0
        for edge_type, actor in actors.items():
            account_id = actor.get("accountId")
            email = (actor.get("emailAddress") or "").strip()
            if not account_id or not email:
                continue  # unassigned or private-email user → no edge emitted
            related = await graph_provider.get_record_outgoing_entity_relations(connector_id, target_id, edge_type)
            assert account_id in related, (
                f"{edge_type}: expected user {account_id} in {related!r} for issue {target_key}"
            )
            asserted += 1
        if asserted == 0:
            pytest.skip("No creator/reporter/assignee with visible email on reference issue")
        logger.info("TC-JIRA-ENTITY-001 passed: %d relations", asserted)

    @pytest.mark.order(12)
    async def test_tc_jira_links_001_outward_issue_links(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-LINKS-001: outward issuelinks on the link-source ticket → mapped RECORD_RELATION edges."""
        connector_id = jira_connector["connector_id"]
        source_key = jira_connector.get("link_source_issue_key")
        if not source_key:
            pytest.skip("JIRA_LINK_SOURCE_ISSUE_KEY unset / not in primary — skipping")

        resp = await jira_datasource.get_issue(issueIdOrKey=source_key, fields="issuelinks")
        assert resp.status == 200
        payload = resp.json()
        source_id = str(payload["id"])
        links = (payload.get("fields") or {}).get("issuelinks") or []

        # Expected (target_external_id, relationshipType) for OUTWARD links only.
        expected: dict[str, set[str]] = {}
        for link in links:
            if not isinstance(link, dict) or "outwardIssue" not in link:
                continue
            outward = link.get("outwardIssue") or {}
            target_id = outward.get("id")
            if not target_id:
                continue
            ltype = link.get("type") or {}
            raw_tag = ltype.get("outward", ltype.get("name", ""))
            mapped = map_relationship_type(raw_tag)
            rel = mapped.value if hasattr(mapped, "value") else "RELATED"
            expected.setdefault(rel, set()).add(str(target_id))

        if not expected:
            pytest.skip(f"No outward issuelinks on {source_key} — skipping")

        for rel, targets in expected.items():
            actual = set(await graph_provider.get_record_outgoing_relations(connector_id, source_id, rel))
            assert targets <= actual, (
                f"{rel}: expected outgoing targets {targets} ⊆ graph {actual} for {source_key}"
            )
        logger.info("TC-JIRA-LINKS-001 passed: %s", dict((k, len(v)) for k, v in expected.items()))


# =============================================================================
# TestJiraIndexing
# =============================================================================


class TestJiraIndexing:
    @pytest.mark.order(7)
    async def test_tc_jira_idx_001_reference_issue_indexing_completed(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-JIRA-IDX-001: reference issue reaches indexing_status == COMPLETED."""
        connector_id = jira_connector["connector_id"]
        project_id = jira_connector["primary_project_id"]
        key = jira_connector.get("reference_issue_key")
        if not key:
            pytest.skip("No reference issue discovered on primary — skipping")
        external_id = jira_connector["reference_issue_id"]

        rec = await wait_until_record_indexing_completed(
            graph_provider, connector_id, external_id,
            timeout=JIRA_INDEXING_WAIT_SEC,
            description=f"TC-JIRA-IDX-001 {key}", pipeshub_client=pipeshub_client,
        )
        assert rec.indexing_status == ProgressStatus.COMPLETED.value
        assert rec.record_type == RecordType.TICKET
        assert rec.external_record_group_id == project_id
        assert rec.virtual_record_id
        logger.info("TC-JIRA-IDX-001 passed: %s", key)


# =============================================================================
# TestJiraHierarchy — read-only PARENT_CHILD (replaces MOVE-001/002)
# =============================================================================


class TestJiraHierarchy:
    @pytest.mark.order(10)
    async def test_tc_jira_hier_001_parent_child(
        self,
        jira_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-HIER-001: Epic↔child and Task↔sub-task hierarchy built as PARENT_CHILD (read-only)."""
        connector_id = jira_connector["connector_id"]
        checked = 0

        # Epic ↔ child
        epic_id = jira_connector.get("epic_id")
        child_id = jira_connector.get("epic_child_id")
        if epic_id and child_id:
            child = await graph_provider.get_record_by_external_id(connector_id, child_id)
            assert child is not None and str(child.parent_external_record_id) == str(epic_id)
            incoming = await graph_provider.get_record_incoming_relations(connector_id, child_id, "PARENT_CHILD")
            assert str(epic_id) in incoming, f"PARENT_CHILD epic {epic_id} → child {child_id} missing ({incoming!r})"
            checked += 1

        # Task ↔ sub-task
        parent_id = jira_connector.get("subtask_parent_id")
        subtask_id = jira_connector.get("subtask_id")
        if parent_id and subtask_id:
            st = await graph_provider.get_record_by_external_id(connector_id, subtask_id)
            assert st is not None and str(st.parent_external_record_id) == str(parent_id)
            incoming = await graph_provider.get_record_incoming_relations(connector_id, subtask_id, "PARENT_CHILD")
            assert str(parent_id) in incoming, f"PARENT_CHILD task {parent_id} → subtask {subtask_id} missing"
            checked += 1

        if checked == 0:
            pytest.skip("Neither Epic↔child nor Task↔sub-task discovered on primary — pre-provision both")
        logger.info("TC-JIRA-HIER-001 passed: %d hierarchy shapes", checked)


# =============================================================================
# TestJiraAttachments
# =============================================================================


class TestJiraAttachments:
    @pytest.mark.order(13)
    async def test_tc_jira_attach_001_attachment_as_file_record(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-ATTACH-001: discovered attachment synced as FILE with parent TICKET + edges."""
        attachment_id = jira_connector.get("attachment_id")
        issue_key = jira_connector.get("attachment_issue_key")
        if not (attachment_id and issue_key):
            pytest.skip("No attachment discovered on primary — skipping")
        connector_id = jira_connector["connector_id"]
        project_id = jira_connector["primary_project_id"]
        issue_id = jira_connector["attachment_issue_id"]

        meta = jira_connector["attachment_meta"]
        att_filename = meta.get("filename", "unknown")
        att_mime = meta.get("mimeType", "application/octet-stream")
        att_size = int(meta.get("size", 0) or 0)
        att_created_ms = parse_jira_timestamp(meta.get("created")) if meta.get("created") else 0

        external_id = f"attachment_{attachment_id}"
        parent_ticket = await graph_provider.get_record_by_external_id(connector_id, issue_id)
        assert parent_ticket is not None, f"Parent TICKET missing for issue id={issue_id}"

        expected_file = JiraExpected.file_record(
            attachment_id=str(attachment_id), filename=att_filename, mime_type=att_mime,
            file_size=att_size, created_at=att_created_ms, issue_id=issue_id, issue_key=issue_key,
            project_id=project_id, connector_id=connector_id, parent_node_id=parent_ticket.id,
        )
        typed = await graph_provider.get_typed_record_by_external_id(connector_id, external_id)
        assert typed is not None and isinstance(typed, FileRecord)
        await assert_graph_entity_with_edges(
            expected_file, typed, entity="file_record",
            connector_id=connector_id, graph_provider=graph_provider,
        )
        logger.info("TC-JIRA-ATTACH-001 passed")


# =============================================================================
# TestJiraBlocks — streamed application/blocks expected snapshot (read-only)
# =============================================================================


class TestJiraBlocks:
    @pytest.mark.order(14)
    async def test_tc_jira_blocks_001_streamed_blocks_expected(
        self,
        jira_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-JIRA-BLOCKS-001: stream the frozen blocks ticket, run it through the production
        block parser (``process_blocks``), and deep-equal the FINAL parsed blocks vs the expected snapshot.

        Validates the full path — Jira ADF → connector markdown block-groups (streamed) → parser
        → fine-grained typed blocks — the same output the indexing pipeline produces, mirroring the
        parser IT (which validates markdown → blocks) but for a connector record.
        """
        connector_id = jira_connector["connector_id"]
        blocks_key = jira_connector.get("blocks_issue_key")
        external_id = jira_connector.get("blocks_issue_external_id")
        if not (blocks_key and external_id):
            pytest.skip("JIRA_BLOCKS_ISSUE_KEY unset / not in primary / not synced — skipping")

        # External Jira issue id → internal graph record id (what stream_record expects).
        record = await graph_provider.get_record_by_external_id(connector_id, external_id)
        assert record is not None, f"Blocks issue {blocks_key} not synced"

        resp = pipeshub_client.stream_record(record.id)
        assert resp.status_code == 200, f"stream_record HTTP {resp.status_code}"
        content_type = (resp.headers.get("content-type") or "").lower()
        assert "application/blocks" in content_type, f"unexpected content-type {content_type!r}"

        # Parse the connector's block-groups into final typed blocks (in-process, parser-IT style).
        parsed = await parse_connector_blocks_via_processor(resp.content)
        actual = normalize_blocks_container(parsed)
        if os.getenv("JIRA_BLOCKS_BOOTSTRAP") == "1":
            bootstrap_expected(actual)  # local regeneration only; still compared below
        expected = load_expected()
        assert actual == expected, "Parsed blocks do not match expected snapshot"
        logger.info("TC-JIRA-BLOCKS-001 passed")


# =============================================================================
# TestJiraBrowseProjectPermissions
# =============================================================================


class TestJiraBrowseProjectPermissions:
    @pytest.mark.order(15)
    async def test_tc_browse_001_default_scheme_matches_graph(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-BROWSE-001: live BROWSE_PROJECTS scheme preview == graph PERMISSION→RecordGroup for primary."""
        connector_id = jira_connector["connector_id"]
        primary_key = jira_connector["primary_key"]
        project_id = jira_connector["primary_project_id"]

        expected = await preview_jira_browse_projects_permission_edges_to_record_group(
            jira_datasource, project_key=primary_key,
        )
        actual = await graph_provider.count_permission_edges_to_record_groups(connector_id, project_id)
        assert actual == expected, (
            f"PERMISSION→RecordGroup for {project_id!r}: graph={actual} jira_preview={expected}"
        )
        logger.info("TC-BROWSE-001 passed")


# =============================================================================
# TestJiraFilters — project-key + date filters (each: set filter → full sync → assert)
# =============================================================================


class TestJiraFilters:
    """Filter scope tests. Each sets its full filter payload + a full sync; no restore.

    Full sync wipes+recreates BELONGS_TO connector-wide, so scope narrowing (not_in / date)
    correctly drops out-of-scope records from BELONGS_TO-guarded counts.
    """

    @pytest.mark.order(16)
    async def test_tc_filter_001_in_multiple_projects(
        self,
        jira_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-001: in [all IT keys] → each project present as a RecordGroup."""
        connector_id = jira_connector["connector_id"]
        keys = jira_connector["project_keys"]
        project_id_by_key = jira_connector["project_id_by_key"]

        await _apply_filter_full_sync(
            pipeshub_client, graph_provider, connector_id, _sync_filters(project_keys=_pk("in", keys)),
        )
        rgs = await graph_provider.count_record_groups(connector_id)
        assert rgs == len(keys), f"expected {len(keys)} RecordGroups, got {rgs}"
        for key in keys:
            rg = await graph_provider.get_record_group_by_external_id(connector_id, project_id_by_key[key])
            assert rg is not None, f"project {key} RecordGroup absent under in{keys}"
        logger.info("TC-FILTER-001 passed: %d projects", len(keys))

    @pytest.mark.order(17)
    async def test_tc_filter_002_not_in_primary(
        self,
        jira_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-002: not_in [primary] → primary absent, other IT projects present."""
        connector_id = jira_connector["connector_id"]
        keys = jira_connector["project_keys"]
        primary_key = jira_connector["primary_key"]
        project_id_by_key = jira_connector["project_id_by_key"]

        await _apply_filter_full_sync(
            pipeshub_client, graph_provider, connector_id, _sync_filters(project_keys=_pk("not_in", [primary_key])),
        )
        primary_rg = await graph_provider.get_record_group_by_external_id(
            connector_id, project_id_by_key[primary_key],
        )
        # BELONGS_TO-guarded: primary lost its RG→App edge on the narrowing full sync.
        app_rg_edges = await graph_provider.count_app_record_group_edges(connector_id)
        assert app_rg_edges == len(keys) - 1, (
            f"expected {len(keys) - 1} in-scope RecordGroups, got {app_rg_edges}; primary_rg={primary_rg!r}"
        )
        for key in keys:
            if key == primary_key:
                continue
            rg = await graph_provider.get_record_group_by_external_id(connector_id, project_id_by_key[key])
            assert rg is not None, f"non-excluded project {key} should still be present"
        logger.info("TC-FILTER-002 passed: primary excluded")

    @pytest.mark.order(18)
    async def test_tc_filter_date_001_created_windows(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-DATE-001: created filter partitions primary at ``JIRA_FILTER_DATE_CUT_MS``.

        Expected id sets come from live Jira ``created`` at that cut (``>=`` / ``<=``, matching
        connector JQL). Only ``project_keys: in [primary]`` + created window — no modified filter.
        """
        connector_id = jira_connector["connector_id"]
        primary_key = jira_connector["primary_key"]
        cut = JIRA_FILTER_DATE_CUT_MS

        issues = await search_issues_jql(
            jira_datasource, f'project = "{primary_key}"', ["created"],
        )
        created_by_id = {
            str(it["id"]): parse_jira_timestamp((it.get("fields") or {}).get("created"))
            for it in issues if (it.get("fields") or {}).get("created")
        }
        expected_created_after = {i for i, c in created_by_id.items() if c >= cut}
        expected_created_before = {i for i, c in created_by_id.items() if c <= cut}

        if not expected_created_after or not expected_created_before:
            pytest.fail(
                "TC-FILTER-DATE-001 setup: primary needs tickets on BOTH sides of "
                "JIRA_FILTER_DATE_CUT_MS by ``created``. Re-provision the "
                "'IT Date Filter New' group and recompute the cut."
            )

        def _dt(start: int | None, end: int | None) -> dict[str, Any]:
            op = "is_after" if end is None else "is_before"
            return {"type": "datetime", "operator": op, "value": {"start": start, "end": end}}

        async def _count() -> int:
            return await graph_provider.count_records_by_type(connector_id, RecordType.TICKET.value)

        async def _assert_scope(expected_ids: set[str], label: str) -> None:
            count = await _count()
            assert count == len(expected_ids), f"{label}: expected {len(expected_ids)} tickets, got {count}"
            for external_id in expected_ids:
                rec = await graph_provider.get_record_by_external_id(connector_id, external_id)
                assert rec is not None, f"{label}: ticket {external_id} should be in scope but is absent"

        await _apply_filter_full_sync(
            pipeshub_client, graph_provider, connector_id,
            _sync_filters(project_keys=_pk("in", [primary_key]), created=_dt(cut, None)),
        )
        await _assert_scope(expected_created_after, "created_after(cut)")

        await _apply_filter_full_sync(
            pipeshub_client, graph_provider, connector_id,
            _sync_filters(project_keys=_pk("in", [primary_key]), created=_dt(None, cut)),
        )
        await _assert_scope(expected_created_before, "created_before(cut)")
        logger.info("TC-FILTER-DATE-001 passed")

    @pytest.mark.order(19)
    async def test_tc_filter_003_empty_all(
        self,
        jira_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-003 (last): empty project_keys → all visible projects; ≥ every configured IT key."""
        connector_id = jira_connector["connector_id"]
        keys = jira_connector["project_keys"]
        project_id_by_key = jira_connector["project_id_by_key"]

        await _apply_filter_full_sync(
            pipeshub_client, graph_provider, connector_id, _sync_filters(project_keys=_pk("in", [])),
        )
        rgs = await graph_provider.count_record_groups(connector_id)
        assert rgs >= len(keys), f"empty=all should sync ≥ {len(keys)} RecordGroups, got {rgs}"
        for key in keys:
            rg = await graph_provider.get_record_group_by_external_id(connector_id, project_id_by_key[key])
            assert rg is not None, f"configured IT project {key} must be present under empty=all"
        logger.info("TC-FILTER-003 passed: %d RecordGroups", rgs)
