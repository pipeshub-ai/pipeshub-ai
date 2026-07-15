# pyright: ignore-file

"""Jira connector fixtures (Linear-style, read-only against pre-provisioned projects).

- session-scoped ``jira_datasource`` (skips if creds missing)
- module-scoped ``jira_connector`` that syncs pre-existing IT projects selected via the
  ``project_keys`` filter, discovers reference issue / hierarchy / attachment shapes
  read-only, waits once for sync, snapshots ``expected_*`` from BELONGS_TO-guarded graph
  counts, then tears down the connector only.

No project/issue/group is created or deleted during fixture setup/teardown. Scope comes
from ``JIRA_TEST_PROJECT_KEYS`` (env); fixture issue keys come from ``constants.py``.
"""

import logging
import os
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, Optional

import pytest
import pytest_asyncio

from app.sources.client.jira.jira import (  # type: ignore[import-not-found]
    JiraApiKeyConfig,
    JiraClient,
)
from app.sources.external.jira.jira import JiraDataSource  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.assertions import ConnectorAssertions  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from connectors.jira.constants import (  # type: ignore[import-not-found]
    JIRA_BLOCKS_ISSUE_KEY,
    JIRA_LINK_SOURCE_ISSUE_KEY,
    JIRA_REFERENCE_ISSUE_KEY,
)
from connectors.jira.jira_test_utils import (  # type: ignore[import-not-found]
    derive_jira_scope_counts,
    discover_attachment,
    discover_epic_and_child,
    discover_task_and_subtask,
    issue_exists_in_project,
    preview_jira_user_group_and_role_permission_edge_totals,
)

logger = logging.getLogger("jira-conftest")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def jira_datasource() -> JiraDataSource:
    """Session-scoped Jira datasource using API-token Basic auth."""
    base_url = os.getenv("JIRA_TEST_BASE_URL")
    email = os.getenv("JIRA_TEST_EMAIL")
    api_token = os.getenv("JIRA_TEST_API_TOKEN")

    if not base_url or not email or not api_token:
        pytest.skip(
            "Jira credentials not set "
            "(JIRA_TEST_BASE_URL, JIRA_TEST_EMAIL, JIRA_TEST_API_TOKEN)."
        )

    config = JiraApiKeyConfig(base_url=base_url, email=email, api_key=api_token)
    client = JiraClient.build_with_config(config)
    return JiraDataSource(client)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def connector_assertions(graph_provider: GraphProviderProtocol):
    """Generic assertions helper - works for any connector."""
    return ConnectorAssertions(graph_provider)


def _parse_project_keys() -> list[str]:
    raw = os.getenv("JIRA_TEST_PROJECT_KEYS", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


async def _resolve_default_issue_type(
    jira_datasource: JiraDataSource, project_key: str
) -> str:
    """Return a createable non-subtask issue type for INCR (prefer 'Task')."""
    try:
        meta_resp = await jira_datasource.get_create_issue_meta(
            projectKeys=[project_key], expand="projects.issuetypes",
        )
        if meta_resp.status == 200:
            for project in (meta_resp.json() or {}).get("projects") or []:
                names = [
                    str(it.get("name"))
                    for it in project.get("issuetypes") or []
                    if not it.get("subtask")
                ]
                for preferred in ("Task", "Story", "Bug"):
                    if preferred in names:
                        return preferred
                if names:
                    return names[0]
    except Exception as e:
        logger.warning("SETUP: createmeta fetch failed (%s); defaulting to 'Task'", e)
    return "Task"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def jira_connector(
    jira_datasource: JiraDataSource,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Module-scoped Jira connector: sync pre-provisioned projects, discover references, snapshot.

    Yields a state dict with project/primary ids, discovered reference/hierarchy/attachment
    keys, pinned constant keys (validated), and ``expected_*`` snapshot counts. Setup is
    read-only against Jira; teardown removes the connector + graph only (never the project).
    """
    base_url = os.getenv("JIRA_TEST_BASE_URL")
    email = os.getenv("JIRA_TEST_EMAIL")
    api_token = os.getenv("JIRA_TEST_API_TOKEN")

    project_keys = _parse_project_keys()
    if not project_keys:
        pytest.skip(
            "JIRA_TEST_PROJECT_KEYS not set. Provide comma-separated dedicated IT "
            "project keys (primary first)."
        )
    primary_key = project_keys[0]

    connector_name = f"jira-test-{uuid.uuid4().hex[:8]}"
    state: Dict[str, Any] = {
        "project_keys": project_keys,
        "primary_key": primary_key,
        "primary_project_id": None,
        "connector_name": connector_name,
        "connector_id": None,
        "lead_account_id": None,
        "default_issue_type": None,
        "reference_issue_key": None,
        "reference_issue_id": None,
        "blocks_issue_key": None,
        "link_source_issue_key": None,
        "epic_key": None, "epic_id": None,
        "epic_child_key": None, "epic_child_id": None,
        "subtask_parent_key": None, "subtask_parent_id": None,
        "subtask_key": None, "subtask_id": None,
        "attachment_issue_key": None, "attachment_issue_id": None,
        "attachment_id": None, "attachment_meta": None,
    }

    # ========== SETUP (read-only against Jira) ==========
    logger.info("SETUP: Jira IT projects=%s (primary=%s)", project_keys, primary_key)

    # 1. Resolve each project id (with lead expanded); fail if a configured project is missing.
    project_id_by_key: Dict[str, str] = {}
    for key in project_keys:
        resp = await jira_datasource.get_project(projectIdOrKey=key, expand="lead")
        if resp.status != 200:
            raise RuntimeError(
                f"SETUP: project {key!r} not resolvable (HTTP {resp.status}); "
                "check JIRA_TEST_PROJECT_KEYS."
            )
        proj = resp.json() or {}
        project_id_by_key[key] = str(proj.get("id", ""))
        if key == primary_key:
            # Lead must come from the PROJECT (mirrors `_sync_project_lead_roles` →
            # project.lead.accountId), not the API-token account: the setup permission
            # preview adds a User→Role edge for the project lead.
            lead = proj.get("lead") or {}
            state["lead_account_id"] = lead.get("accountId")
    state["project_id_by_key"] = project_id_by_key
    state["primary_project_id"] = project_id_by_key[primary_key]
    if not state["lead_account_id"]:
        logger.warning("SETUP: primary project %s has no lead.accountId", primary_key)

    # 2. Default issue type on primary (for INCR).
    state["default_issue_type"] = await _resolve_default_issue_type(jira_datasource, primary_key)

    # 3. Resolve the pinned reference issue + discover hierarchy/attachment shapes on primary (read-only).
    if JIRA_REFERENCE_ISSUE_KEY and await issue_exists_in_project(
        jira_datasource, JIRA_REFERENCE_ISSUE_KEY, primary_key
    ):
        state["reference_issue_key"] = JIRA_REFERENCE_ISSUE_KEY
        ref_resp = await jira_datasource.get_issue(
            issueIdOrKey=JIRA_REFERENCE_ISSUE_KEY, fields="summary",
        )
        if ref_resp.status == 200:
            state["reference_issue_id"] = str(ref_resp.json()["id"])

    epic = await discover_epic_and_child(jira_datasource, primary_key)
    if epic:
        state["epic_key"], state["epic_id"], state["epic_child_key"], state["epic_child_id"] = epic
    subtask = await discover_task_and_subtask(jira_datasource, primary_key)
    if subtask:
        (state["subtask_parent_key"], state["subtask_parent_id"],
         state["subtask_key"], state["subtask_id"]) = subtask
    attachment = await discover_attachment(jira_datasource, primary_key)
    if attachment:
        state["attachment_issue_key"], state["attachment_issue_id"], meta = attachment
        state["attachment_id"] = str(meta.get("id"))
        state["attachment_meta"] = meta

    # Pinned constant keys: validate they belong to primary, else leave None (dependent TC skips).
    if JIRA_BLOCKS_ISSUE_KEY and await issue_exists_in_project(
        jira_datasource, JIRA_BLOCKS_ISSUE_KEY, primary_key
    ):
        state["blocks_issue_key"] = JIRA_BLOCKS_ISSUE_KEY
        blocks_resp = await jira_datasource.get_issue(
            issueIdOrKey=JIRA_BLOCKS_ISSUE_KEY, fields="summary",
        )
        if blocks_resp.status == 200:
            state["blocks_issue_external_id"] = str(blocks_resp.json()["id"])
    if JIRA_LINK_SOURCE_ISSUE_KEY and await issue_exists_in_project(
        jira_datasource, JIRA_LINK_SOURCE_ISSUE_KEY, primary_key
    ):
        state["link_source_issue_key"] = JIRA_LINK_SOURCE_ISSUE_KEY

    # 4. Register connector with the primary filter baked into create-config (active on first sync).
    config: Dict[str, Any] = {
        "auth": {
            "authType": "API_TOKEN",
            "baseUrl": base_url,
            "email": email,
            "apiToken": api_token,
        },
        "filters": {
            "sync": {
                "values": {
                    "project_keys": {
                        "operator": "in",
                        "type": "list",
                        "value": [primary_key],
                    }
                }
            }
        },
    }
    instance = pipeshub_client.create_connector(
        connector_type="Jira",
        instance_name=connector_name,
        scope="team",
        config=config,
        auth_type="API_TOKEN",
    )
    assert instance.connector_id, "Connector must have a valid ID"
    connector_id = instance.connector_id
    state["connector_id"] = connector_id

    pipeshub_client.toggle_sync(connector_id, enable=True)
    full_count = await wait_for_sync_completion(
        pipeshub_client, graph_provider, connector_id, min_records=1, timeout=240,
    )
    state["full_sync_count"] = full_count
    # Jira-vs-graph reconciliation lives in TC-SYNC-001 (the test), not here — the fixture only
    # produces the snapshot. The permission-preview gate below is kept: it is the sole check of
    # permission-edge correctness against an independent source (the Jira API).

    # 5. Derive expected_* from LIVE Jira / filter scope (independent of the graph) so TC-SYNC-001
    #    validates the sync (Jira → graph), not the graph against itself. One enumeration yields
    #    ticket / file / parent-child counts, each matching the connector's sync-path record model.
    scope = await derive_jira_scope_counts(jira_datasource, primary_key)
    state["expected_ticket_count"] = scope["ticket"]
    state["expected_file_count"] = scope["file"]
    state["expected_total_records"] = scope["ticket"] + scope["file"]
    state["expected_parent_child_edges"] = scope["parent_child"]
    state["expected_attachment_edges"] = scope["file"]  # one ATTACHMENT edge per attachment/FILE
    state["expected_record_groups"] = 1  # only the primary project is in scope

    # Permission edges: reconcile the Jira API preview against the graph (independent gate), then
    # store the PREVIEW totals so TC-JIRA-ROLE-001 validates graph vs an independent source.
    graph_ug = await graph_provider.count_user_to_group_permission_edges(connector_id)
    graph_ur = await graph_provider.count_user_to_role_permission_edges(connector_id)
    ug_exp, ur_exp = await preview_jira_user_group_and_role_permission_edge_totals(
        jira_datasource, project_key=primary_key, lead_account_id=state["lead_account_id"],
    )
    if ug_exp != graph_ug or ur_exp != graph_ur:
        raise RuntimeError(
            "SETUP: Jira permission preview != graph — "
            f"user→group preview={ug_exp} graph={graph_ug}; user→role preview={ur_exp} graph={graph_ur}."
        )
    state["expected_permission_user_group_edges"] = ug_exp
    state["expected_permission_user_role_edges"] = ur_exp

    logger.info(
        "SETUP done: %d Jira tickets, %d RGs; ref=%s epic=%s subtask=%s attach=%s",
        state["expected_ticket_count"],
        state["expected_record_groups"], state["reference_issue_key"],
        state["epic_key"], state["subtask_key"], state["attachment_id"],
    )

    try:
        yield state
    finally:
        # ========== TEARDOWN (connector + graph only — never the project) ==========
        connector_id = state.get("connector_id")
        logger.info("TEARDOWN: cleaning connector %s", connector_id)
        if connector_id:
            try:
                pipeshub_client.toggle_sync(connector_id, enable=False)
                status = pipeshub_client.get_connector_status(connector_id)
                assert not status.get("isActive"), "Connector should be inactive after disable"
            except Exception as e:
                logger.warning("TEARDOWN: disable failed for %s: %s", connector_id, e)
            try:
                pipeshub_client.delete_connector(connector_id)
                pipeshub_client.wait(25)
                cleanup_timeout = int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300"))
                await graph_provider.assert_all_records_cleaned(connector_id, timeout=cleanup_timeout)
            except Exception as e:
                logger.warning("TEARDOWN: delete/clean failed for %s: %s", connector_id, e)
