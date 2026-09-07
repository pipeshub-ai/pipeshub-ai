# pyright: ignore-file

"""Linear connector fixtures.

- session-scoped ``linear_datasource`` (skips if creds missing)
- module-scoped ``linear_connector`` that discovers existing team/issue/project
  data, registers a Pipeshub connector, waits for sync, then tears down.

Setup reads the workspace, picks the team the mutation tests write to, and trashes IT
artifacts leaked by crashed runs (age-gated, see ``sweep_stale_linear_artifacts``);
teardown trashes whatever this run still owns. Nothing else is ever written to the
workspace. See ``README.md`` for the shared-workspace contract.
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
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    count_owned_records,
    wait_for_sync_completion,
)
from connectors.linear.constants import (  # type: ignore[import-not-found]
    LINEAR_IT_ARTIFACT_PREFIX,
    LINEAR_IT_RUN_ID,
    LINEAR_REFERENCE_ISSUE_IDENTIFIER,
)
from connectors.linear.linear_test_utils import (  # type: ignore[import-not-found]
    _api_call_with_retry,
    assert_linear_issues_match_graph_records,
    count_linear_team_issues,
    count_linear_team_projects,
    fetch_first_attachment_in_teams,
    fetch_first_document_in_teams,
    fetch_first_file_in_teams,
    fetch_first_project_in_team,
    fetch_teams_by_ids,
    pick_mutation_team,
    reap_own_artifacts,
    sweep_stale_linear_artifacts,
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
async def connector_assertions(graph_provider: GraphProviderProtocol) -> ConnectorAssertions:
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

    Setup creates nothing in Linear; its only writes are trashing leaked artifacts.
    Teardown trashes this run's leftover artifacts, then removes the Pipeshub connector
    and cleans the graph. The connector syncs exactly once, here — no test resyncs it, so
    the baselines snapshotted below cannot be moved by anything another run does.
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
        "mutation_team_id": None,
        "run_id": LINEAR_IT_RUN_ID,
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
        "expected_ticket_count": 0,
        "expected_project_count": 0,
        "expected_total_records": 0,
        "expected_record_groups": 0,
        "expected_parent_child_edges": 0,
        "expected_record_group_edges": 0,
        "expected_inherit_edges": 0,
    }

    # ========== SETUP (reads, plus artifact hygiene) ==========

    # 1. Resolve current user + organization.
    logger.info("SETUP: Fetching viewer and organization info (run_id=%s)", LINEAR_IT_RUN_ID)
    viewer_resp = await _api_call_with_retry(
        linear_datasource.viewer, context="conftest:viewer",
    )
    viewer_data = (viewer_resp.data or {}).get("viewer", {})
    state["viewer_id"] = viewer_data.get("id")
    state["viewer_email"] = viewer_data.get("email")
    if not state["viewer_id"]:
        raise RuntimeError("Linear viewer response missing id")

    org_resp = await _api_call_with_retry(
        linear_datasource.organization, context="conftest:organization",
    )
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

    # 2b. Where the mutation tests write: the secondary team when configured, so the
    #     primary — every pinned fixture, every baseline — stays read-only all suite long.
    mutation_team_id = pick_mutation_team(team_ids)
    if not any(t.get("id") == mutation_team_id for t in teams):
        raise RuntimeError(
            f"SETUP: mutation team {mutation_team_id!r} is not resolvable; "
            "check LINEAR_TEST_TEAM_IDS."
        )
    state["mutation_team_id"] = mutation_team_id
    if mutation_team_id == state["primary_team_id"]:
        logger.warning(
            "SETUP: single team configured — mutation tests write to the primary %s "
            "(artifact exclusion still applies)", primary.get("key"),
        )
    else:
        mutation_key = next(t.get("key") for t in teams if t.get("id") == mutation_team_id)
        logger.info("SETUP: mutation tests write to team %s", mutation_key)

    # 2c. Trash artifacts leaked by earlier cancelled/crashed runs. Age-gated so a run still
    #     going on this shared workspace is never touched; best-effort.
    swept = await sweep_stale_linear_artifacts(linear_datasource, team_ids)
    if swept:
        logger.warning("SETUP: swept %d leaked IT artifact(s) from earlier runs", swept)

    # 3. Count existing issues + projects per team (API baseline, IT artifacts excluded).
    total_api_tickets = 0
    for tid in team_ids:
        tc = await count_linear_team_issues(linear_datasource, tid)
        pc = await count_linear_team_projects(linear_datasource, tid)
        total_api_tickets += tc
        logger.info("SETUP: Team %s — %d issues, %d projects", tid, tc, pc)

    if total_api_tickets == 0:
        raise RuntimeError(
            f"SETUP: Filtered teams {team_ids} contain zero issues. "
            "Nothing to validate — ensure teams have existing issues."
        )

    # 4. Resolve the pinned reference issue on the primary team (existing data). A fixed
    #    identifier avoids drift across runs vs. "whatever the API returns first".
    ref_resp = await _api_call_with_retry(
        linear_datasource.issue, id=LINEAR_REFERENCE_ISSUE_IDENTIFIER,
        context="conftest:reference_issue",
    )
    ref_issue = (ref_resp.data or {}).get("issue") if ref_resp.success else None
    ref_team_id = (ref_issue.get("team") or {}).get("id") if ref_issue else None
    if ref_issue and ref_team_id == state["primary_team_id"]:
        state["reference_issue_id"] = ref_issue.get("id")
        state["reference_issue_identifier"] = ref_issue.get("identifier")
        logger.info("SETUP: Reference issue found (%s)", state["reference_issue_identifier"])
    else:
        logger.info(
            "SETUP: Pinned reference issue %r not found on primary team %s — "
            "dependent tests will skip",
            LINEAR_REFERENCE_ISSUE_IDENTIFIER, state["primary_team_id"],
        )

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

    # 6. Wait for sync to absorb existing data.
    await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        min_records=1,
        timeout=240,
    )

    # 7. Reconcile API issue IDs vs fully synced graph tickets (excludes placeholders).
    #    The connector's own fetches can be throttled on this shared workspace and lag one
    #    sync behind, so one incremental resync is allowed before this is a failure.
    for attempt in range(2):
        try:
            await assert_linear_issues_match_graph_records(
                linear_datasource,
                graph_provider,
                connector_id,
                team_ids,
                phase="SETUP after sync",
            )
            break
        except AssertionError as e:
            if attempt:
                raise
            logger.warning("SETUP: %s — resyncing once", e)
            pipeshub_client.resync_connector(connector_id, full_sync=False)
            await wait_for_sync_completion(
                pipeshub_client, graph_provider, connector_id, timeout=240,
            )

    # Verify each dependent type synced by checking the reference record exists in graph.
    if state.get("reference_attachment_id"):
        rec = await graph_provider.get_record_by_external_id(
            connector_id, state["reference_attachment_id"])
        assert rec is not None, (
            f"SETUP: reference LINK {state['reference_attachment_id']} missing after sync")

    if state.get("reference_document_id"):
        rec = await graph_provider.get_record_by_external_id(
            connector_id, state["reference_document_id"])
        assert rec is not None, (
            f"SETUP: reference WEBPAGE {state['reference_document_id']} missing after sync")

    if state.get("reference_file_url"):
        rec = await graph_provider.get_record_by_external_id(
            connector_id, state["reference_file_url"])
        assert rec is not None, (
            f"SETUP: reference FILE {state['reference_file_url']} missing after sync")

    # 8. Snapshot expected counts from post-sync graph state. Record baselines count only
    #    records this run owns: a concurrently running leg's mutation issue may be inside
    #    this sync window and gone by the time later tests read the graph, which would
    #    otherwise look like records having disappeared.
    ticket_count = await count_owned_records(
        graph_provider, connector_id, prefix=LINEAR_IT_ARTIFACT_PREFIX, record_type="TICKET",
    )
    total_records = await count_owned_records(
        graph_provider, connector_id, prefix=LINEAR_IT_ARTIFACT_PREFIX,
    )
    project_count = await graph_provider.count_records_by_type(connector_id, "PROJECT")
    link_count = await graph_provider.count_records_by_type(connector_id, "LINK")
    webpage_count = await graph_provider.count_records_by_type(connector_id, "WEBPAGE")
    file_count = await graph_provider.count_records_by_type(connector_id, "FILE")
    record_groups = await graph_provider.count_record_groups(connector_id)
    parent_child_edges = await graph_provider.count_parent_child_edges(connector_id)
    record_group_edges = await graph_provider.count_record_group_edges(connector_id)
    inherit_edges = await graph_provider.count_inherit_permissions_edges(connector_id)

    state["expected_ticket_count"] = ticket_count
    state["expected_project_count"] = project_count
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
        # ========== TEARDOWN (own artifacts, then connector + graph) ==========
        connector_id = state.get("connector_id")
        try:
            reaped = await reap_own_artifacts(linear_datasource, team_ids)
            if reaped:
                logger.warning("TEARDOWN: reaped %d artifact(s) this run left behind", reaped)
        except Exception as e:
            logger.warning("TEARDOWN: artifact reap failed (run %s): %s", LINEAR_IT_RUN_ID, e)
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
