# pyright: ignore-file

"""
Jira Connector – Integration Tests (pre-provisioned, read-only + self-cleaning mutations)
=========================================================================================

Scope comes from ``JIRA_TEST_PROJECT_KEYS`` (env); fixture issue keys from ``constants.py``.
Counts are BELONGS_TO-guarded (see the IT graph providers).

Every CI leg, every PR and the nightly cron share ONE Jira site, and different PRs run at
the same time. The primary project is therefore never written to by this suite: the two
mutation tests (orders 8, 9) each create a throw-away connector scoped to the *secondary*
IT project and assert by external id, so nothing another run does can reach an assertion
here. ``README.md`` in this directory is the contract for adding tests.

  order 1  TC-SYNC-001        — full sync baseline vs fixture snapshot + live JQL
  order 2  TC-JIRA-001        — users / USER_APP_RELATION
  order 3  TC-JIRA-002        — site groups count
  order 4  TC-JIRA-ROLE-001   — all primary-project AppRoles + synced User→Role members
  order 5  TC-JIRA-003        — project RecordGroup
  order 6  TC-JIRA-004        — reference issue TICKET properties
  order 7  TC-JIRA-IDX-001    — reference issue indexing COMPLETED; then manual indexing on
  order 8  TC-INCR-001        — dedicated connector: create + incremental (by id) + delete
  order 9  TC-UPDATE-001      — dedicated connector: create + edit + revision (by id) + delete
  order 10 TC-JIRA-HIER-001   — Epic↔child and Task↔sub-task PARENT_CHILD
  order 11 TC-JIRA-ENTITY-001 — CREATED_BY/REPORTED_BY/ASSIGNED_TO entityRelations
  order 12 TC-JIRA-LINKS-001  — outward issuelinks → RECORD_RELATION
  order 13 TC-JIRA-ATTACH-001 — attachment FILE record
  order 14 TC-JIRA-BLOCKS-001 — streamed application/blocks expected snapshot
  order 15 TC-BROWSE-001      — BROWSE_PROJECTS scheme → PERMISSION→RecordGroup
  order 16 TC-FILTER-001      — in [A,B,(C)]
  order 17 TC-FILTER-002      — not_in [A] (primary absent)
  order 18 TC-FILTER-DATE-001 — created after/before windows
  order 19 TC-FILTER-003      — empty = all (last shared-connector)
  order 20 TC-JIRA-PH-001     — placeholder ancestors: minted → swept → promoted
"""

import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import ProgressStatus  # type: ignore[import-not-found]  # noqa: E402
from app.connectors.sources.atlassian.jira_cloud.connector import (  # type: ignore[import-not-found]  # noqa: E402
    PLACEHOLDER_REVISION_PREFIX,
)
from app.connectors.utils.value_mapper import map_relationship_type  # type: ignore[import-not-found]  # noqa: E402
from app.models.entities import FileRecord, RecordType  # type: ignore[import-not-found]  # noqa: E402
from app.sources.external.jira.jira import JiraDataSource  # type: ignore[import-not-found]  # noqa: E402
from helper.assertions import ConnectorAssertions  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    apply_filter_full_sync,
    count_owned_records,
    wait_for_record_by_external_id,
    wait_for_sync_completion,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from validation.graph_entity_validator import (  # noqa: E402
    assert_graph_entity_matches,
    assert_graph_entity_with_edges,
    assert_user_app_edge,
)
from connectors.jira.constants import (  # noqa: E402
    JIRA_FILTER_DATE_CUT_MS,
    JIRA_INDEXING_WAIT_SEC,
    JIRA_IT_ARTIFACT_PREFIX,
    JIRA_IT_RUN_ID,
    JIRA_PH_CHILD_KEY,
    JIRA_PH_CREATED_CUT_MS,
    JIRA_USERS_GROUP_NAME,
    artifact_summary,
)
from connectors.jira.jira_block_utils import (  # noqa: E402
    assert_snapshot_source_unchanged,
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
    fetch_ancestor_chain,
    get_jira_issue_updated_ms,
    issue_exists_in_project,
    jira_api_call_with_retry,
    jira_artifacts,
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


async def _delete_artifact_issue(
    jira_datasource: JiraDataSource, *, issue_id: str, issue_key: str, context: str,
) -> None:
    """Delete a ticket this run created and confirm it is gone before moving on.

    The connector that synced it is a throw-away and is destroyed right after, so there is
    nothing to resync. Confirming the 404 matters for the *other* runs: their syncs stop
    seeing the ticket as soon as it is really gone, not when the delete was merely accepted.

    Failures are logged, not raised: this runs in a ``finally`` and would otherwise mask a
    real assertion failure from the test body. A ticket that survives here is still reaped
    by the fixture teardown (registry) or a later run's sweep (age gate).
    """
    try:
        del_resp = await jira_api_call_with_retry(
            jira_datasource.delete_issue, issueIdOrKey=issue_id,
            context=f"{context} delete_issue", retry_server_errors=True,
        )
        status = getattr(del_resp, "status", 204)
        if status not in (200, 202, 204, 404):
            logger.warning("%s cleanup: delete HTTP %s — leaving for teardown reap", context, status)
            return

        async def _gone() -> bool:
            return not await check_issue_exists_bool(jira_datasource, issue_id)

        await wait_until_jira_condition(
            check_fn=_gone, description=f"{context}: {issue_key} deleted", timeout=60, poll_interval=5,
        )
        jira_artifacts.release(issue_id)
    except Exception as e:
        logger.error("%s cleanup failed — %s left for teardown reap: %s", context, issue_key, e)


def _jira_auth_config() -> dict[str, str]:
    """API-token auth block for a connector, from the suite's existing env vars."""
    base_url = (os.getenv("JIRA_TEST_BASE_URL") or "").rstrip("/")
    email = os.getenv("JIRA_TEST_EMAIL") or ""
    api_token = os.getenv("JIRA_TEST_API_TOKEN") or ""
    if not (base_url and email and api_token):
        pytest.fail("JIRA_TEST_BASE_URL / JIRA_TEST_EMAIL / JIRA_TEST_API_TOKEN must be set")
    return {"authType": "API_TOKEN", "baseUrl": base_url, "email": email, "apiToken": api_token}


@asynccontextmanager
async def _dedicated_connector(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    *,
    name: str,
    filters: dict[str, Any],
    min_records: int | None = None,
    timeout: int = 240,
) -> AsyncIterator[str]:
    """A throw-away Jira connector: create → first sync → yield id → delete + graph clean.

    Used by every test that needs its own scope or its own sync history (the mutation tests,
    the placeholder test) so the shared fixture connector is never mutated and the test's
    assertions read a graph only this test's connector writes to.
    """
    instance = pipeshub_client.create_connector(
        connector_type="Jira",
        instance_name=name,
        scope="team",
        config={"auth": _jira_auth_config(), "filters": filters},
        auth_type="API_TOKEN",
    )
    connector_id = instance.connector_id
    assert connector_id, "dedicated connector must have an id"
    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, min_records=min_records, timeout=timeout,
        )
        yield connector_id
    finally:
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
            pipeshub_client.delete_connector(connector_id)
            pipeshub_client.wait(25)
            await graph_provider.assert_all_records_cleaned(
                connector_id,
                timeout=int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300")),
            )
        except Exception as e:
            logger.error("dedicated connector %s (%s) cleanup: leaked: %s", name, connector_id, e)


# Indexing is proven once, in TC-JIRA-IDX-001, which then switches the shared connector over
# to this. Every later test only reads the graph, so the ~8 syncs they trigger have no reason
# to run records through extraction/embedding. ``FilterCollection.from_dict`` drops any entry
# missing ``operator``/``type``, so both are required for the switch to take effect.
_MANUAL_INDEXING = {
    "values": {"enable_manual_sync": {"operator": "is", "type": "boolean", "value": True}},
}


def _sync_filters(**values: Any) -> dict[str, Any]:
    """Build the connector's full ``config.filters`` payload: sync scope + manual indexing.

    The connector reads ``config.filters.sync.values.<key>`` (see load_connector_filters);
    the filters-sync endpoint stores the request ``filters`` verbatim, so the payload must
    already carry the ``sync.values`` nesting — a flat ``{"project_keys": ...}`` is written
    to the wrong path and silently ignored. "Verbatim" also means the indexing block has to
    be repeated on every call: omitting it would silently switch auto-indexing back on.
    """
    return {"sync": {"values": values}, "indexing": _MANUAL_INDEXING}


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

        ticket_count = await graph_provider.count_records_by_type(connector_id, RecordType.TICKET.value, scoped=True)
        file_count = await graph_provider.count_records_by_type(connector_id, RecordType.FILE.value, scoped=True)
        total = await graph_provider.count_records(connector_id, scoped=True)

        # TICKET count is not asserted here: the live-Jira reconciliation at the end of this
        # test compares the same thing as id sets, which survives a concurrently running leg
        # creating mutation tickets in this shared project and names the offender on failure.
        # Attachments and parents only ever hang off pre-provisioned tickets, so FILE and
        # PARENT_CHILD counts need no such tolerance.
        assert file_count == jira_connector["expected_file_count"], (
            f"graph FILE {file_count} != synced Jira attachments "
            f"(excl. new inline images) {jira_connector['expected_file_count']}"
        )
        attach = await graph_provider.count_record_relation_edges(connector_id, "ATTACHMENT")
        assert attach == jira_connector["expected_attachment_edges"], (
            f"ATTACHMENT edges {attach} != synced Jira attachments "
            f"{jira_connector['expected_attachment_edges']}"
        )
        pc = await graph_provider.count_parent_child_edges(connector_id)
        assert pc == jira_connector["expected_parent_child_edges"], (
            f"PARENT_CHILD {pc} != Jira issues-with-parent {jira_connector['expected_parent_child_edges']}"
        )

        # Independent: record-group count from filter scope (primary only).
        app_edges = await graph_provider.count_app_record_group_edges(connector_id)
        rgs = await graph_provider.count_record_groups(connector_id, scoped=True)
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
            "updated_by", "last_synced_by", "status", "is_locked",
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
        """TC-INCR-001: create one issue, incremental sync picks it up (assert by external id); cleanup.

        Runs on its own connector scoped to the mutation project (the secondary IT project
        when one is configured), so the shared fixture connector and the primary project are
        untouched — see the module docstring for why that is what makes the suite safe under
        concurrent runs.
        """
        mutation_key = jira_connector["mutation_key"]
        issue_type = jira_connector["mutation_issue_type"]
        base_url = (os.getenv("JIRA_TEST_BASE_URL") or "").rstrip("/")

        title = artifact_summary("IncrTest")
        new_id: str | None = None
        new_key: str | None = None
        async with _dedicated_connector(
            pipeshub_client, graph_provider,
            name=f"jira-incr-{JIRA_IT_RUN_ID}-{uuid.uuid4().hex[:6]}",
            filters=_sync_filters(project_keys=_pk("in", [mutation_key])),
        ) as connector_id:
            try:
                # create_issue: retry 429 only; 5xx/timeout/transport → fail (no duplicate ticket).
                resp = await jira_api_call_with_retry(
                    jira_datasource.create_issue,
                    fields={
                        "project": {"key": mutation_key},
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
                jira_artifacts.register(new_id, new_key)

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
                if new_id and new_key:
                    await _delete_artifact_issue(
                        jira_datasource, issue_id=new_id, issue_key=new_key, context="TC-INCR-001",
                    )

    @pytest.mark.order(9)
    async def test_tc_update_001_content_and_summary_revision(
        self,
        jira_connector: dict[str, Any],
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: edit a test-owned ticket; version += 1; revision = Jira updated ms.

        Same isolation as TC-INCR-001: own connector, mutation project, by-id assertions.
        The ticket is created here rather than reusing the pinned reference issue — editing
        a shared ticket made concurrent runs assert against each other's summary.
        """
        mutation_key = jira_connector["mutation_key"]
        issue_type = jira_connector["mutation_issue_type"]

        target_key: str | None = None
        target_id: str | None = None
        async with _dedicated_connector(
            pipeshub_client, graph_provider,
            name=f"jira-upd-{JIRA_IT_RUN_ID}-{uuid.uuid4().hex[:6]}",
            filters=_sync_filters(project_keys=_pk("in", [mutation_key])),
        ) as connector_id:
            try:
                resp = await jira_api_call_with_retry(
                    jira_datasource.create_issue,
                    fields={
                        "project": {"key": mutation_key},
                        "summary": artifact_summary("UpdTest"),
                        "issuetype": {"name": issue_type},
                        "description": _adf("Update test issue."),
                    },
                    context="TC-UPDATE-001 create_issue",
                    retry_server_errors=False,
                )
                assert resp.status in (200, 201), f"create failed: HTTP {resp.status}"
                data = resp.json()
                target_key = data["key"]
                target_id = str(data["id"])
                jira_artifacts.register(target_id, target_key)

                await wait_until_jira_condition(
                    check_fn=lambda: check_issue_exists_bool(jira_datasource, target_key),
                    description=f"TC-UPDATE-001: new issue fetchable ({target_key})",
                    timeout=120,
                )

                _restart_sync(pipeshub_client, connector_id)
                await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=240)

                record_before = await wait_for_record_by_external_id(
                    graph_provider, connector_id, target_id,
                    timeout=120, description="TC-UPDATE-001 baseline record",
                )
                old_version = int(record_before.version)

                new_summary = artifact_summary("Edited")
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
                if target_id and target_key:
                    await _delete_artifact_issue(
                        jira_datasource, issue_id=target_id, issue_key=target_key, context="TC-UPDATE-001",
                    )


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
        roles_resp = await jira_api_call_with_retry(
            jira_datasource.get_project_roles, projectIdOrKey=primary_key,
            context="TC-JIRA-ROLE-001 get_project_roles",
        )
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

        proj_resp = await jira_api_call_with_retry(
            jira_datasource.get_project, projectIdOrKey=primary_key,
            context="TC-JIRA-003 get_project",
        )
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
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-004: reference issue has correct TICKET record properties + edges.

        The reference ticket is shared and read-only to this suite, but nothing stops an
        outside editor (a Jira UI session, another IT run) from changing it after the
        fixture synced. That drifts ``record_name`` / ``updated_at`` / revision against a
        snapshot this test did not take, which says nothing about the connector — so on
        drift, resync once and compare against the state both sides then agree on.
        """
        connector_id = jira_connector["connector_id"]
        target_key = jira_connector.get("reference_issue_key")
        if not target_key:
            pytest.skip("No reference issue discovered on primary — skipping")
        target_id = jira_connector["reference_issue_id"]
        base_url = (os.getenv("JIRA_TEST_BASE_URL") or "").rstrip("/")

        async def _live_and_graph() -> tuple[Any, Any]:
            live = await JiraExpected.ticket_record(
                target_key, connector_id=connector_id, datasource=jira_datasource,
                site_base_url=base_url or None,
            )
            graph = await graph_provider.get_typed_record_by_external_id(connector_id, target_id)
            assert graph is not None, f"typed TICKET record missing for external id {target_id}"
            return live, graph

        expected, actual = await _live_and_graph()
        # Bounded: two resyncs absorb an edit that lands between the resync and the
        # re-read; anything beyond that is someone actively editing the frozen ticket.
        for attempt in range(2):
            if str(expected.external_revision_id) == str(actual.external_revision_id):
                break
            logger.warning(
                "TC-JIRA-004: %s changed in Jira after the fixture sync "
                "(live revision %s != graph %s) — resyncing (attempt %d/2)",
                target_key, expected.external_revision_id, actual.external_revision_id, attempt + 1,
            )
            _restart_sync(pipeshub_client, connector_id)
            await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=240)
            expected, actual = await _live_and_graph()

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

        resp = await jira_api_call_with_retry(
            jira_datasource.get_issue, issueIdOrKey=target_key, fields="creator,reporter,assignee",
            context=f"TC-JIRA-ENTITY-001 get_issue {target_key}",
        )
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
            # The Jira accountId lands on the user NODE (as ``userId``) only for users the connector
            # creates fresh; when the actor already exists as a pipeshub user (e.g. the connector owner
            # in CI, where the ticket creator's email == the pipeshub account) the node keeps its native
            # userId and the accountId sits on the userAppRelation edge instead. So match on whatever
            # identity the node actually carries, resolved by the actor's email.
            expected_ids = {account_id}
            user_doc = await graph_provider.graph_find_user_by_email(email)
            if user_doc:
                expected_ids |= {
                    str(user_doc[k]) for k in ("sourceUserId", "userId", "_key", "id") if user_doc.get(k)
                }
            assert expected_ids & set(related), (
                f"{edge_type}: none of {sorted(expected_ids)} (for {email}) in {related!r} "
                f"for issue {target_key}"
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

        resp = await jira_api_call_with_retry(
            jira_datasource.get_issue, issueIdOrKey=source_key, fields="issuelinks",
            context=f"TC-JIRA-LINKS-001 get_issue {source_key}",
        )
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
        """TC-JIRA-IDX-001: reference issue reaches indexing_status == COMPLETED.

        Last test that needs the indexing pipeline, so it hands the connector over to manual
        indexing on the way out (see ``_sync_filters``).
        """
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

        # Every later test asserts on the graph only. Records synced from here on get
        # AUTO_INDEX_OFF and publish no indexing event — which also keeps their counts from
        # drifting after the connector reports IDLE.
        await apply_filter_full_sync(
            pipeshub_client, graph_provider, connector_id,
            _sync_filters(project_keys=_pk("in", [jira_connector["primary_key"]])),
        )
        logger.info("TC-JIRA-IDX-001: connector switched to manual indexing")


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
        """TC-JIRA-ATTACH-001: discovered non-inline attachment synced as FILE with parent TICKET + edges."""
        attachment_id = jira_connector.get("attachment_id")
        issue_key = jira_connector.get("attachment_issue_key")
        if not (attachment_id and issue_key):
            pytest.skip(
                "No non-inline attachment discovered on primary "
                "(inline images are not synced as FILE) — skipping"
            )
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
        jira_datasource: JiraDataSource,
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-JIRA-BLOCKS-001: stream the frozen blocks ticket, run it through the production
        block parser (``process_blocks``), and deep-equal the FINAL parsed blocks vs the expected snapshot.

        Validates the full path — Jira rendered HTML → connector HTML block-groups (streamed) →
        HTML parser → fine-grained typed blocks — the same output the indexing pipeline produces.

        The snapshot records the ticket's ``updated`` revision it was taken from; a mismatch
        there is reported as "the frozen ticket was edited", not as a parser diff.
        """
        connector_id = jira_connector["connector_id"]
        blocks_key = jira_connector.get("blocks_issue_key")
        external_id = jira_connector.get("blocks_issue_external_id")
        if not (blocks_key and external_id):
            pytest.skip("JIRA_BLOCKS_ISSUE_KEY unset / not in primary / not synced — skipping")

        live_updated_ms = await get_jira_issue_updated_ms(jira_datasource, blocks_key)
        expected, meta = load_expected()
        if os.getenv("JIRA_BLOCKS_BOOTSTRAP") != "1":
            assert_snapshot_source_unchanged(meta, issue_key=blocks_key, live_updated_ms=live_updated_ms)

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
            # Local regeneration only; still compared below.
            bootstrap_expected(
                actual,
                meta={"issue_key": blocks_key, "issue_updated_ms": live_updated_ms},
            )
            expected, _ = load_expected()
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

        await apply_filter_full_sync(
            pipeshub_client, graph_provider, connector_id, _sync_filters(project_keys=_pk("in", keys)),
        )
        rgs = await graph_provider.count_record_groups(connector_id, scoped=True)
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

        await apply_filter_full_sync(
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

        Validates correctness of the connector's created-date filter by verifying
        each synced ticket against Jira's REST API (by ID — strongly consistent),
        rather than comparing against JQL search results which are eventually
        consistent and cause intermittent flakes from ghost tickets.
        """
        connector_id = jira_connector["connector_id"]
        primary_key = jira_connector["primary_key"]
        cut = JIRA_FILTER_DATE_CUT_MS

        # Pre-flight: query Jira to confirm the cut partitions the project. IT artifacts are
        # excluded — a concurrently running leg's mutation tickets are created "now", i.e.
        # always on the after-cut side, and are deleted again within the same run.
        issues = await search_issues_jql(
            jira_datasource, f'project = "{primary_key}"', ["created", "summary"],
        )
        created_by_id = {
            str(it["id"]): parse_jira_timestamp((it.get("fields") or {}).get("created"))
            for it in issues
            if (it.get("fields") or {}).get("created")
            and JIRA_IT_ARTIFACT_PREFIX not in ((it.get("fields") or {}).get("summary") or "")
        }
        preflight_after = {i for i, c in created_by_id.items() if c >= cut}
        preflight_before = {i for i, c in created_by_id.items() if c <= cut}

        if not preflight_after or not preflight_before:
            pytest.fail(
                "TC-FILTER-DATE-001 setup: primary needs tickets on BOTH sides of "
                "JIRA_FILTER_DATE_CUT_MS by ``created``. Re-provision the "
                "'IT Date Filter New' group and recompute the cut."
            )

        def _dt(start: int | None, end: int | None) -> dict[str, Any]:
            op = "is_after" if end is None else "is_before"
            return {"type": "datetime", "operator": op, "value": {"start": start, "end": end}}

        async def _count() -> int:
            return await graph_provider.count_records_by_type(connector_id, RecordType.TICKET.value, scoped=True)

        async def _verify_filter(preflight_ids: set[str], label: str, *, is_after: bool) -> None:
            """Verify correctness via REST API by ID (strongly consistent).

            1. Re-verify each pre-flight ticket via GET /issue/{id} (no search index).
            2. Assert each verified live ticket is present in the graph.
            3. Assert graph count does not exceed live count by more than 1
               (tolerates a single ghost ticket from Jira search index lag), plus any
               IT-artifact tickets a concurrently running leg has in flight.
            """
            count = await _count()
            assert count > 0, f"{label}: no scoped tickets after sync"

            # A concurrent leg's mutation tickets are created "now", so they sync into the
            # after-cut scope. Counted unscoped, which can only over-state them — the
            # tolerance is never too tight.
            all_tickets = await graph_provider.count_records_by_type(
                connector_id, RecordType.TICKET.value,
            )
            owned_tickets = await count_owned_records(
                graph_provider, connector_id,
                prefix=JIRA_IT_ARTIFACT_PREFIX, record_type=RecordType.TICKET.value,
            )
            artifacts = all_tickets - owned_tickets

            live_ids: set[str] = set()
            for eid in preflight_ids:
                resp = await jira_api_call_with_retry(
                    jira_datasource.get_issue, issueIdOrKey=eid, fields="created",
                    context=f"TC-FILTER-DATE-001 get_issue {eid}",
                )
                if resp.status != 200:
                    continue
                created_ms = parse_jira_timestamp(
                    (resp.json().get("fields") or {}).get("created")
                )
                matches = (created_ms >= cut) if is_after else (created_ms <= cut)
                if matches:
                    live_ids.add(eid)

            assert live_ids, f"{label}: no live tickets verified via REST API"

            for eid in live_ids:
                rec = await graph_provider.get_record_by_external_id(connector_id, eid)
                assert rec is not None, (
                    f"{label}: ticket {eid} exists in Jira and matches filter but is absent from graph"
                )

            assert count <= len(live_ids) + 1 + artifacts, (
                f"{label}: graph has {count} scoped tickets but only {len(live_ids)} "
                f"verified live — difference exceeds ghost tolerance of 1 "
                f"(+{artifacts} IT artifacts)"
            )

        # ── created >= cut ──
        await apply_filter_full_sync(
            pipeshub_client, graph_provider, connector_id,
            _sync_filters(project_keys=_pk("in", [primary_key]), created=_dt(cut, None)),
        )
        await _verify_filter(preflight_after, "created_after(cut)", is_after=True)

        # ── created <= cut ──
        await apply_filter_full_sync(
            pipeshub_client, graph_provider, connector_id,
            _sync_filters(project_keys=_pk("in", [primary_key]), created=_dt(None, cut)),
        )
        await _verify_filter(preflight_before, "created_before(cut)", is_after=False)
        logger.info("TC-FILTER-DATE-001 passed")

    @pytest.mark.order(19)
    async def test_tc_filter_003_empty_all(
        self,
        jira_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-003 (last shared-connector): empty project_keys → all visible projects."""
        connector_id = jira_connector["connector_id"]
        keys = jira_connector["project_keys"]
        project_id_by_key = jira_connector["project_id_by_key"]

        await apply_filter_full_sync(
            pipeshub_client, graph_provider, connector_id, _sync_filters(project_keys=_pk("in", [])),
        )
        rgs = await graph_provider.count_record_groups(connector_id, scoped=True)
        assert rgs >= len(keys), f"empty=all should sync ≥ {len(keys)} RecordGroups, got {rgs}"
        for key in keys:
            rg = await graph_provider.get_record_group_by_external_id(connector_id, project_id_by_key[key])
            assert rg is not None, f"configured IT project {key} must be present under empty=all"
        logger.info("TC-FILTER-003 passed: %d RecordGroups", rgs)


# =============================================================================
# TestJiraPlaceholders — parent stubs: minted, swept, promoted
# =============================================================================


def _jira_ph_env() -> tuple[str, str, str, str]:
    """Return ``(base_url, email, api_token, primary_key)`` or fail the test."""
    base_url = (os.getenv("JIRA_TEST_BASE_URL") or "").rstrip("/")
    email = os.getenv("JIRA_TEST_EMAIL") or ""
    api_token = os.getenv("JIRA_TEST_API_TOKEN") or ""
    primary_key = next(
        (k.strip() for k in (os.getenv("JIRA_TEST_PROJECT_KEYS") or "").split(",") if k.strip()),
        "",
    )
    if not (base_url and email and api_token and primary_key):
        pytest.fail(
            "TC-JIRA-PH-001: JIRA_TEST_BASE_URL / EMAIL / API_TOKEN / PROJECT_KEYS must be set"
        )
    return base_url, email, api_token, primary_key


async def _jira_ph_out_of_window_ancestors(
    datasource: JiraDataSource, child_key: str, cut: int,
) -> tuple[str, list[str]]:
    """Resolve child id + ancestor ids with ``created <= cut``. Requires depth >= 2."""
    child_resp = await jira_api_call_with_retry(
        datasource.get_issue, issueIdOrKey=child_key, fields="created",
        context=f"ph child {child_key}",
    )
    assert child_resp.status == 200, f"get_issue({child_key}) HTTP {child_resp.status}"
    child = child_resp.json() or {}
    child_id = str(child.get("id") or "")
    assert child_id, f"{child_key} has no id"
    if parse_jira_timestamp((child.get("fields") or {}).get("created")) <= cut:
        pytest.fail(f"TC-JIRA-PH-001: {child_key} created must be after cut={cut}")

    ancestors: list[str] = []
    for ancestor in await fetch_ancestor_chain(datasource, child_key):
        created_ms = parse_jira_timestamp((ancestor.get("fields") or {}).get("created"))
        ancestor_id = str(ancestor.get("id") or "")
        if not ancestor_id:
            continue
        if created_ms > cut:
            break
        ancestors.append(ancestor_id)

    if len(ancestors) < 2:
        pytest.fail(
            f"TC-JIRA-PH-001: {child_key} has {len(ancestors)} ancestor(s) with "
            f"created <= cut={cut}; >= 2 required (BFS proof)"
        )
    return child_id, ancestors


class TestJiraPlaceholders:
    """Placeholder ancestor lifecycle driven by the ``created`` sync filter."""

    @pytest.mark.order(20)
    async def test_tc_jira_ph_001_placeholder_sweep_and_promotion(
        self,
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-PH-001: dedicated connector — stubs swept, then promoted on widen.

        Dedicated connector required: narrowing the shared fixture after a full sync
        finds ancestors already as real records, so no stubs are minted. Depth >= 2
        proves BFS (grandparent only appears after the parent stub is swept).
        """
        cut = JIRA_PH_CREATED_CUT_MS
        _base_url, _email, _api_token, primary_key = _jira_ph_env()

        if not await issue_exists_in_project(jira_datasource, JIRA_PH_CHILD_KEY, primary_key):
            pytest.fail(
                f"TC-JIRA-PH-001: {JIRA_PH_CHILD_KEY!r} not in {primary_key!r} — "
                "update constants or provision Sub-task → parent → grandparent"
            )

        child_id, ancestors = await _jira_ph_out_of_window_ancestors(
            jira_datasource, JIRA_PH_CHILD_KEY, cut,
        )

        stub_node_ids: dict[str, str] = {}
        # Phase 1 — first sync already narrowed so parents mint as stubs
        async with _dedicated_connector(
            pipeshub_client, graph_provider,
            name=f"jira-ph-{JIRA_IT_RUN_ID}-{uuid.uuid4().hex[:6]}",
            filters=_sync_filters(
                project_keys=_pk("in", [primary_key]),
                created={
                    "type": "datetime",
                    "operator": "is_after",
                    "value": {"start": cut, "end": None},
                },
            ),
            min_records=1,
        ) as connector_id:
            child = await graph_provider.get_typed_record_by_external_id(connector_id, child_id)
            assert child is not None and child.is_placeholder is False

            for depth, ancestor_id in enumerate(ancestors, start=1):
                stub = await graph_provider.get_typed_record_by_external_id(
                    connector_id, ancestor_id,
                )
                assert stub is not None, (
                    f"phase1: ancestor {ancestor_id} (depth {depth}) absent — BFS stalled"
                )
                assert stub.is_placeholder is True, f"phase1: {ancestor_id} must stay a stub"
                assert stub.external_revision_id and str(stub.external_revision_id).startswith(
                    PLACEHOLDER_REVISION_PREFIX
                ), f"phase1: {ancestor_id} unswept (revision={stub.external_revision_id!r})"
                stub_node_ids[ancestor_id] = stub.id

            # Phase 2 — widen filter; stubs promote in place
            await apply_filter_full_sync(
                pipeshub_client, graph_provider, connector_id,
                _sync_filters(project_keys=_pk("in", [primary_key])),
            )

            for ancestor_id in ancestors:
                promoted = await graph_provider.get_typed_record_by_external_id(
                    connector_id, ancestor_id,
                )
                assert promoted is not None
                assert promoted.is_placeholder is False, f"phase2: {ancestor_id} not promoted"
                assert promoted.id == stub_node_ids[ancestor_id], (
                    f"phase2: {ancestor_id} replaced instead of promoted in place"
                )
                assert promoted.external_revision_id and not str(
                    promoted.external_revision_id
                ).startswith(PLACEHOLDER_REVISION_PREFIX), (
                    f"phase2: {ancestor_id} still has stub revision "
                    f"{promoted.external_revision_id!r}"
                )

            logger.info("TC-JIRA-PH-001 passed: %d ancestor(s) swept + promoted", len(ancestors))
