# pyright: ignore-file

"""Jira connector fixtures (Linear-style, read-only against pre-provisioned projects).

- session-scoped ``jira_datasource`` (skips if creds missing)
- module-scoped ``jira_connector`` that syncs pre-existing IT projects selected via the
  ``project_keys`` filter, discovers reference issue / hierarchy / attachment shapes
  read-only, waits once for sync, snapshots ``expected_*`` from BELONGS_TO-guarded graph
  counts, then tears down the connector only.

No project/group is created or deleted here. The only Jira writes are hygiene: setup
sweeps IT artifacts leaked by crashed runs (age-gated, see ``sweep_stale_jira_artifacts``)
and teardown deletes whatever this run created and did not get to delete itself. Scope
comes from ``JIRA_TEST_PROJECT_KEYS`` (env); fixture issue keys come from ``constants.py``.
See ``README.md`` for the shared-site contract every test follows.
"""

import logging
import os
import uuid
from typing import Any, AsyncGenerator, Callable, Optional

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
    JIRA_IT_RUN_ID,
    JIRA_LINK_SOURCE_ISSUE_KEY,
    JIRA_REFERENCE_ISSUE_KEY,
)
from connectors.jira.jira_test_utils import (  # type: ignore[import-not-found]
    can_delete_issues_in,
    derive_jira_scope_counts,
    discover_attachment,
    discover_epic_and_child,
    discover_task_and_subtask,
    issue_exists_in_project,
    jira_api_call_with_retry,
    pick_mutation_project,
    preview_jira_user_group_and_role_permission_edge_totals,
    reap_own_artifacts,
    sweep_stale_jira_artifacts,
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
) -> Optional[str]:
    """Return a createable non-subtask issue type (prefer 'Task'), or None if the account
    cannot create issues in ``project_key`` (createmeta lists no project / no types)."""
    try:
        meta_resp = await jira_api_call_with_retry(
            jira_datasource.get_create_issue_meta,
            projectKeys=[project_key], expand="projects.issuetypes",
            context=f"createmeta {project_key}",
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
        logger.warning("SETUP: createmeta fetch failed for %s (%s)", project_key, e)
    return None


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def jira_connector(
    jira_datasource: JiraDataSource,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
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
    state: dict[str, Any] = {
        "project_keys": project_keys,
        "primary_key": primary_key,
        "primary_project_id": None,
        "connector_name": connector_name,
        "connector_id": None,
        "run_id": JIRA_IT_RUN_ID,
        "lead_account_id": None,
        "default_issue_type": None,
        "mutation_key": None,
        "mutation_project_id": None,
        "mutation_issue_type": None,
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

    # ========== SETUP (read-only against Jira, plus artifact hygiene) ==========
    logger.info(
        "SETUP: Jira IT projects=%s (primary=%s, run_id=%s)", project_keys, primary_key, JIRA_IT_RUN_ID,
    )

    # 1. Resolve each project id (with lead expanded); fail if a configured project is missing.
    project_id_by_key: dict[str, str] = {}
    for key in project_keys:
        resp = await jira_api_call_with_retry(
            jira_datasource.get_project, projectIdOrKey=key, expand="lead",
            context=f"get_project {key}",
        )
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

    # 2. Where the mutation tests write. Prefer the secondary project so the primary stays
    #    read-only for the whole suite; it must be create+delete-able (see pick_mutation_project).
    issue_type_by_key = {
        key: await _resolve_default_issue_type(jira_datasource, key) for key in project_keys
    }
    can_delete_by_key = {key: await can_delete_issues_in(jira_datasource, key) for key in project_keys}
    state["default_issue_type"] = issue_type_by_key.get(primary_key) or "Task"
    mutation_key, mutation_issue_type = pick_mutation_project(
        project_keys, issue_type_by_key, can_delete_by_key,
    )
    state["mutation_key"] = mutation_key
    state["mutation_project_id"] = project_id_by_key[mutation_key]
    state["mutation_issue_type"] = mutation_issue_type
    if not can_delete_by_key.get(mutation_key):
        raise RuntimeError(
            f"SETUP: the IT account cannot delete issues in {mutation_key!r} (nor in any "
            f"secondary project) — mutation tests would leak tickets. DELETE_ISSUES by project: "
            f"{can_delete_by_key}"
        )
    if mutation_key == primary_key:
        logger.warning(
            "SETUP: no secondary project is create+delete-able (%s) — mutation tests will "
            "write to the primary %s (artifact exclusion still applies)",
            can_delete_by_key, primary_key,
        )
    else:
        logger.info("SETUP: mutation tests write to %s (%s)", mutation_key, mutation_issue_type)

    # 2b. Reap tickets leaked by earlier cancelled/crashed runs — only where the account can
    #     delete. A project without DELETE_ISSUES can hold nothing this suite created (the
    #     picker above never writes there), so it is not scanned and never logged; anything
    #     stranded in such a project by an earlier configuration is simply invisible here.
    #     Age-gated so a run still going on this shared site is never touched; best-effort.
    deletable_keys = [key for key in project_keys if can_delete_by_key.get(key)]
    state["deletable_keys"] = deletable_keys
    swept = await sweep_stale_jira_artifacts(jira_datasource, deletable_keys)
    if swept:
        logger.warning("SETUP: swept %d leaked IT artifact(s) from earlier runs", swept)

    # 3. Resolve the pinned reference issue + discover hierarchy/attachment shapes on primary (read-only).
    if JIRA_REFERENCE_ISSUE_KEY and await issue_exists_in_project(
        jira_datasource, JIRA_REFERENCE_ISSUE_KEY, primary_key
    ):
        state["reference_issue_key"] = JIRA_REFERENCE_ISSUE_KEY
        ref_resp = await jira_api_call_with_retry(
            jira_datasource.get_issue, issueIdOrKey=JIRA_REFERENCE_ISSUE_KEY, fields="summary",
            context=f"get_issue {JIRA_REFERENCE_ISSUE_KEY}",
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
        blocks_resp = await jira_api_call_with_retry(
            jira_datasource.get_issue, issueIdOrKey=JIRA_BLOCKS_ISSUE_KEY, fields="summary",
            context=f"get_issue {JIRA_BLOCKS_ISSUE_KEY}",
        )
        if blocks_resp.status == 200:
            state["blocks_issue_external_id"] = str(blocks_resp.json()["id"])
    if JIRA_LINK_SOURCE_ISSUE_KEY and await issue_exists_in_project(
        jira_datasource, JIRA_LINK_SOURCE_ISSUE_KEY, primary_key
    ):
        state["link_source_issue_key"] = JIRA_LINK_SOURCE_ISSUE_KEY

    # 4. Register connector with the primary filter baked into create-config (active on first sync).
    config: dict[str, Any] = {
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
    state["expected_parent_child_edges"] = scope["parent_child"]
    state["expected_attachment_edges"] = scope["file"]  # one ATTACHMENT edge per attachment/FILE
    state["expected_record_groups"] = 1  # only the primary project is in scope

    # Permission edges: reconcile the Jira API preview against the graph (independent gate), then
    # store the PREVIEW totals so TC-JIRA-ROLE-001 validates graph vs an independent source.
    #
    # One retry: the connector writes a group with empty members when its member fetch is
    # throttled, and on this shared site a 429 during the first sync is exactly what a
    # concurrent run causes. A second sync refreshes the membership; only a repeat
    # mismatch is a real defect.
    ug_exp, ur_exp = await preview_jira_user_group_and_role_permission_edge_totals(
        jira_datasource, project_key=primary_key, lead_account_id=state["lead_account_id"],
    )
    readings: list[tuple[int, int]] = []
    for attempt in range(2):
        graph_ug = await graph_provider.count_user_to_group_permission_edges(connector_id)
        graph_ur = await graph_provider.count_user_to_role_permission_edges(connector_id)
        readings.append((graph_ug, graph_ur))
        if (ug_exp, ur_exp) == (graph_ug, graph_ur):
            break
        if attempt == 0:
            logger.warning(
                "SETUP: permission preview != graph on first sync "
                "(user→group %d vs %d; user→role %d vs %d) — resyncing once",
                ug_exp, graph_ug, ur_exp, graph_ur,
            )
            pipeshub_client.resync_connector(connector_id, full_sync=False)
            await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=240)
    else:
        raise RuntimeError(
            "SETUP: Jira permission preview != graph after a resync — "
            f"user→group preview={ug_exp} graph={readings[-1][0]}; "
            f"user→role preview={ur_exp} graph={readings[-1][1]} (first reading {readings[0]})."
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
        # ========== TEARDOWN (own artifacts, then connector + graph — never the project) ==========
        connector_id = state.get("connector_id")
        try:
            reaped = await reap_own_artifacts(jira_datasource, state.get("deletable_keys") or [])
            if reaped:
                logger.warning("TEARDOWN: reaped %d artifact(s) this run left behind", reaped)
        except Exception as e:
            logger.warning("TEARDOWN: artifact reap failed (run %s): %s", JIRA_IT_RUN_ID, e)
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
