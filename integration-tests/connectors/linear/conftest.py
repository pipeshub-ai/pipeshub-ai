# pyright: ignore-file

"""Linear connector fixtures.

Read-only against the Linear workspace:
- session-scoped ``linear_datasource`` (skips if creds missing)
- module-scoped ``linear_connector`` that discovers existing team/issue/project
  data, registers a Pipeshub connector, waits for sync, then tears down.

No issues, projects, or teams are created or deleted during fixture setup/teardown.
"""

import logging
import os
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import pytest
import pytest_asyncio

from app.sources.client.linear.linear import (  # type: ignore[import-not-found]
    LinearClient,
    LinearTokenConfig,
)
from app.sources.external.linear.linear import LinearDataSource  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.assertions import ConnectorAssertions  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from connectors.linear.linear_test_utils import (  # type: ignore[import-not-found]
    assert_linear_dependent_counts_match_graph,
    assert_linear_issues_match_graph_records,
    count_linear_scope_files,
    count_linear_scope_links,
    count_linear_scope_webpages,
    count_linear_team_issues,
    count_linear_team_projects,
    fetch_first_attachment_in_teams,
    fetch_first_document_in_teams,
    fetch_first_file_in_teams,
    fetch_first_issue_in_team,
    fetch_first_project_in_team,
    fetch_teams_by_ids,
)

logger = logging.getLogger("linear-conftest")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def linear_datasource() -> LinearDataSource:
    """Session-scoped Linear datasource using personal API key."""
    api_token = os.getenv("LINEAR_TEST_API_TOKEN")

    if not api_token:
        pytest.skip(
            "Linear credentials not set (LINEAR_TEST_API_TOKEN). "
            "The API token must have read access to the target workspace."
        )

    config = LinearTokenConfig(token=api_token)
    client = LinearClient.build_with_config(config)
    return LinearDataSource(client)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def connector_assertions(graph_provider: GraphProviderProtocol):
    """Generic assertions helper — works for any connector."""
    return ConnectorAssertions(graph_provider)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def linear_connector(
    linear_datasource: LinearDataSource,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Module-scoped Linear connector with read-only discovery + sync lifecycle.

    Yields a dict with team metadata, reference issue/project IDs, connector_id,
    and expected graph counts derived from the Linear API snapshot.

    Setup is read-only against Linear (no issue/project creation).
    Teardown only removes the Pipeshub connector and cleans the graph.
    """
    api_token = os.getenv("LINEAR_TEST_API_TOKEN")
    raw_team_ids = os.getenv("LINEAR_TEST_TEAM_IDS", "")

    team_ids = [t.strip() for t in raw_team_ids.split(",") if t.strip()]
    if not team_ids:
        pytest.skip(
            "LINEAR_TEST_TEAM_IDS not set. Provide comma-separated team UUIDs "
            "whose issues should be synced."
        )

    connector_name = f"linear-test-{uuid.uuid4().hex[:8]}"
    state: Dict[str, Any] = {
        "team_ids": team_ids,
        "teams": [],
        "primary_team_id": team_ids[0],
        "primary_team_key": None,
        "connector_id": None,
        "connector_name": connector_name,
        "viewer_id": None,
        "viewer_email": None,
        "organization_url_key": None,
        "reference_issue_id": None,
        "reference_issue_identifier": None,
        "reference_project_id": None,
        "reference_attachment_id": None,
        "reference_attachment_team_id": None,
        "reference_document_id": None,
        "reference_document_parent_id": None,
        "reference_document_parent_type": None,
        "reference_document_team_id": None,
        "reference_file_url": None,
        "reference_file_filename": None,
        "reference_file_parent_id": None,
        "reference_file_parent_type": None,
        "reference_file_team_id": None,
        "reference_file_parent_weburl": None,
        "reference_file_parent_created_at": 0,
        "reference_file_parent_updated_at": 0,
        "full_sync_count": 0,
        "api_ticket_count": 0,
        "api_project_count": 0,
        "api_link_count": 0,
        "api_webpage_count": 0,
        "api_file_count": 0,
        "expected_ticket_count": 0,
        "expected_project_count": 0,
        "expected_link_count": 0,
        "expected_webpage_count": 0,
        "expected_file_count": 0,
        "expected_total_records": 0,
        "expected_record_groups": 0,
        "expected_parent_child_edges": 0,
        "expected_record_group_edges": 0,
        "expected_inherit_edges": 0,
    }

    # ========== SETUP (read-only against Linear) ==========

    # 1. Resolve current user + organization.
    logger.info("SETUP: Fetching viewer and organization info")
    viewer_resp = await linear_datasource.viewer()
    if not viewer_resp.success:
        raise RuntimeError(f"Failed to fetch Linear viewer: {viewer_resp.message}")
    viewer_data = (viewer_resp.data or {}).get("viewer", {})
    state["viewer_id"] = viewer_data.get("id")
    state["viewer_email"] = viewer_data.get("email")
    if not state["viewer_id"]:
        raise RuntimeError("Linear viewer response missing id")

    org_resp = await linear_datasource.organization()
    if not org_resp.success:
        raise RuntimeError(f"Failed to fetch Linear organization: {org_resp.message}")
    org_data = (org_resp.data or {}).get("organization", {})
    state["organization_url_key"] = org_data.get("urlKey")

    # 2. Discover existing team metadata (read-only).
    logger.info("SETUP: Discovering teams %s", team_ids)
    teams = await fetch_teams_by_ids(linear_datasource, team_ids)
    if not teams:
        raise RuntimeError(
            f"SETUP: No teams found for IDs {team_ids}. "
            "Ensure LINEAR_TEST_TEAM_IDS contains valid team UUIDs."
        )
    state["teams"] = [
        {
            "id": t.get("id"),
            "key": t.get("key"),
            "name": t.get("name"),
            "private": t.get("private", False),
        }
        for t in teams
    ]
    primary = next((t for t in teams if t.get("id") == team_ids[0]), teams[0])
    state["primary_team_id"] = primary.get("id")
    state["primary_team_key"] = primary.get("key")

    # 3. Count existing issues + projects per team (API baseline).
    total_api_tickets = 0
    total_api_projects = 0
    for tid in team_ids:
        tc = await count_linear_team_issues(linear_datasource, tid)
        pc = await count_linear_team_projects(linear_datasource, tid)
        total_api_tickets += tc
        total_api_projects += pc
        logger.info("SETUP: Team %s — %d issues, %d projects", tid, tc, pc)

    if total_api_tickets == 0:
        raise RuntimeError(
            f"SETUP: Filtered teams {team_ids} contain zero issues. "
            "Nothing to validate — ensure teams have existing issues."
        )
    state["api_ticket_count"] = total_api_tickets
    state["api_project_count"] = total_api_projects

    state["api_link_count"] = await count_linear_scope_links(linear_datasource, team_ids)
    state["api_webpage_count"] = await count_linear_scope_webpages(linear_datasource, team_ids)
    state["api_file_count"] = await count_linear_scope_files(linear_datasource, team_ids)
    logger.info(
        "SETUP: API baseline — %d links, %d webpages, %d files",
        state["api_link_count"], state["api_webpage_count"], state["api_file_count"],
    )

    # 4. Pick reference issue + project + dependent records (existing data).
    ref_issue = await fetch_first_issue_in_team(linear_datasource, state["primary_team_id"])
    if not ref_issue:
        raise RuntimeError("SETUP: Could not find any issue in primary team")
    state["reference_issue_id"] = ref_issue.get("id")
    state["reference_issue_identifier"] = ref_issue.get("identifier")
    logger.info("SETUP: Reference issue found (%s)", state["reference_issue_identifier"])

    ref_project = await fetch_first_project_in_team(linear_datasource, state["primary_team_id"])
    if ref_project:
        state["reference_project_id"] = ref_project.get("id")
        logger.info("SETUP: Reference project found")
    else:
        logger.info("SETUP: No projects in primary team — TC-LINEAR-005 will skip")

    ref_attachment = await fetch_first_attachment_in_teams(linear_datasource, team_ids)
    if ref_attachment:
        state["reference_attachment_id"] = ref_attachment.get("id")
        attachment_team = (ref_attachment.get("issue") or {}).get("team") or {}
        state["reference_attachment_team_id"] = (
            attachment_team.get("id") or state["primary_team_id"]
        )
        logger.info("SETUP: Reference attachment found")
    else:
        logger.info("SETUP: No attachments in filtered teams — TC-LINEAR-006 will skip")

    ref_document = await fetch_first_document_in_teams(linear_datasource, team_ids)
    if ref_document:
        state["reference_document_id"] = ref_document.get("id")
        issue = ref_document.get("issue")
        if issue and issue.get("id"):
            state["reference_document_parent_id"] = issue.get("id")
            state["reference_document_parent_type"] = "TICKET"
            doc_team = (issue.get("team") or {})
            state["reference_document_team_id"] = doc_team.get("id") or state["primary_team_id"]
        elif ref_document.get("_parent_project_id"):
            state["reference_document_parent_id"] = ref_document.get("_parent_project_id")
            state["reference_document_parent_type"] = "PROJECT"
            state["reference_document_team_id"] = (
                ref_document.get("_parent_team_id") or state["primary_team_id"]
            )
        logger.info("SETUP: Reference document found")
    else:
        logger.info("SETUP: No documents in filtered teams — TC-LINEAR-007 will skip")

    ref_file = await fetch_first_file_in_teams(linear_datasource, team_ids)
    if ref_file:
        state["reference_file_url"] = ref_file.get("url")
        state["reference_file_filename"] = ref_file.get("filename")
        state["reference_file_parent_id"] = ref_file.get("parent_external_id")
        state["reference_file_parent_type"] = ref_file.get("parent_record_type")
        state["reference_file_team_id"] = ref_file.get("team_id")
        state["reference_file_parent_weburl"] = ref_file.get("parent_weburl")
        state["reference_file_parent_created_at"] = ref_file.get("parent_created_at", 0)
        state["reference_file_parent_updated_at"] = ref_file.get("parent_updated_at", 0)
        logger.info("SETUP: Reference file found")
    else:
        logger.info("SETUP: No markdown files in filtered teams — TC-LINEAR-008 will skip")

    # 5. Register the connector with the team_ids filter baked in at creation,
    #    mirroring the Confluence pattern so the filter is active on the very first sync.
    config: Dict[str, Any] = {
        "auth": {
            "authType": "API_TOKEN",
            "apiToken": api_token,
        },
        "filters": {
            "sync": {
                "values": {
                    "team_ids": {
                        "operator": "in",
                        "type": "list",
                        "value": team_ids,
                    }
                }
            }
        },
    }
    instance = pipeshub_client.create_connector(
        connector_type="Linear",
        instance_name=connector_name,
        scope="team",
        config=config,
        auth_type="API_TOKEN",
    )
    assert instance.connector_id, "Connector must have a valid ID"
    connector_id = instance.connector_id
    state["connector_id"] = connector_id

    pipeshub_client.toggle_sync(connector_id, enable=True)

    # 7. Wait for sync to absorb existing data.
    full_count = await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        min_records=1,
        timeout=240,
    )

    # 8. Reconcile API issue IDs vs fully synced graph tickets (excludes placeholders).
    await assert_linear_issues_match_graph_records(
        linear_datasource,
        graph_provider,
        connector_id,
        team_ids,
        phase="SETUP after sync",
    )

    await assert_linear_dependent_counts_match_graph(
        linear_datasource,
        graph_provider,
        connector_id,
        team_ids,
        phase="SETUP after sync",
    )

    state["full_sync_count"] = full_count

    # 9. Compute expected counts from post-sync graph state.
    ticket_count = await graph_provider.count_records_by_type(connector_id, "TICKET")
    project_count = await graph_provider.count_records_by_type(connector_id, "PROJECT")
    link_count = await graph_provider.count_records_by_type(connector_id, "LINK")
    webpage_count = await graph_provider.count_records_by_type(connector_id, "WEBPAGE")
    file_count = await graph_provider.count_records_by_type(connector_id, "FILE")
    total_records = await graph_provider.count_records(connector_id)
    record_groups = await graph_provider.count_record_groups(connector_id)
    parent_child_edges = await graph_provider.count_parent_child_edges(connector_id)
    record_group_edges = await graph_provider.count_record_group_edges(connector_id)
    inherit_edges = await graph_provider.count_inherit_permissions_edges(connector_id)

    state["expected_ticket_count"] = ticket_count
    state["expected_project_count"] = project_count
    state["expected_link_count"] = link_count
    state["expected_webpage_count"] = webpage_count
    state["expected_file_count"] = file_count
    state["expected_total_records"] = total_records
    state["expected_record_groups"] = record_groups
    state["expected_parent_child_edges"] = parent_child_edges
    state["expected_record_group_edges"] = record_group_edges
    state["expected_inherit_edges"] = inherit_edges

    logger.info(
        "SETUP: Sync complete — %d records (%d tickets, %d projects, "
        "%d links, %d webpages, %d files), %d record groups, %d PARENT_CHILD edges",
        total_records, ticket_count, project_count, link_count, webpage_count, file_count,
        record_groups, parent_child_edges,
    )

    try:
        yield state
    finally:
        # ========== TEARDOWN (connector only — no Linear mutations) ==========
        connector_id = state.get("connector_id")
        logger.info("TEARDOWN: Cleaning up connector '%s'", connector_id)

        if connector_id:
            try:
                pipeshub_client.toggle_sync(connector_id, enable=False)
                status = pipeshub_client.get_connector_status(connector_id)
                assert not status.get("isActive"), "Connector should be inactive after disable"
            except Exception as e:
                logger.warning("TEARDOWN: Failed to disable connector %s: %s", connector_id, e)

            try:
                pipeshub_client.delete_connector(connector_id)
                pipeshub_client.wait(25)
                cleanup_timeout = int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300"))
                await graph_provider.assert_all_records_cleaned(connector_id, timeout=cleanup_timeout)
            except Exception as e:
                logger.warning("TEARDOWN: Failed to delete/clean connector %s: %s", connector_id, e)
