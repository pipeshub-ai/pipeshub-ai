# pyright: ignore-file

"""Jira connector fixtures.

Mirrors the Confluence pattern:
- session-scoped `jira_datasource` (skips if creds missing)
- module-scoped `jira_connector` that creates a project, seeds issues,
  registers a Pipeshub connector, runs an initial sync, then tears down.
"""

import logging
import os
import uuid
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from app.sources.client.jira.jira import (  # type: ignore[import-not-found]
    JiraApiKeyConfig,
    JiraClient,
)
from app.sources.external.jira.jira import JiraDataSource  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    async_wait_for_stable_record_count,
    wait_until_graph_condition,
)

logger = logging.getLogger("jira-conftest")

# Stable test-project identity. The fixture reuses the existing project if it
# already exists (e.g. a previous run failed teardown) and tears it down at the
# end with `enableUndo=False` so it's permanently purged — no Jira trash residue.
_TEST_PROJECT_KEY = "INTTEST"
_TEST_PROJECT_NAME = "Pipeshub Integration Test"
_TEST_PROJECT_DESCRIPTION = "Automated integration test project"

# Standard Scrum/Kanban basic template — exposes Story / Task / Bug / Sub-task issue types.
_PROJECT_TEMPLATE_KEY = "com.pyxis.greenhopper.jira:gh-simplified-basic"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def jira_datasource() -> JiraDataSource:
    """Session-scoped Jira datasource using API-token Basic auth."""
    base_url = os.getenv("JIRA_TEST_BASE_URL")
    email = os.getenv("JIRA_TEST_EMAIL")
    api_token = os.getenv("JIRA_TEST_API_TOKEN")

    if not base_url or not email or not api_token:
        pytest.skip(
            "Jira credentials not set "
            "(JIRA_TEST_BASE_URL, JIRA_TEST_EMAIL, JIRA_TEST_API_TOKEN). "
            "The API token must belong to a Project Administrator account so the "
            "fixture can create projects, issues, sub-tasks, attachments, and clean up."
        )

    config = JiraApiKeyConfig(base_url=base_url, email=email, api_key=api_token)
    client = JiraClient.build_with_config(config)
    return JiraDataSource(client)


def _resolve_subtask_issuetype_name(create_meta_json: Dict[str, Any]) -> str:
    """Find the sub-task issue-type name in a project's createmeta payload.

    Defaults to "Sub-task" if the createmeta doesn't expose `subtask: true`. Some
    workspaces rename it (e.g. "Subtask"), so prefer the dynamic lookup.
    """
    projects = create_meta_json.get("projects") or []
    for project in projects:
        for itype in project.get("issuetypes") or []:
            if itype.get("subtask"):
                return str(itype.get("name") or "Sub-task")
    return "Sub-task"


def _resolve_seed_issue_types(create_meta_json: Dict[str, Any]) -> list[str]:
    """Pick 3 createable non-subtask issue types for seed data.

    Jira workspaces can have different issue type schemes (e.g. no "Story").
    Prefer common names, then fill from any remaining createable non-subtask
    types so fixture setup remains stable across tenants.
    """
    projects = create_meta_json.get("projects") or []
    available: list[str] = []
    for project in projects:
        for itype in project.get("issuetypes") or []:
            name = str(itype.get("name") or "").strip()
            if not name:
                continue
            if itype.get("subtask"):
                continue
            if name not in available:
                available.append(name)

    preferred = ["Story", "Task", "Bug"]
    selected: list[str] = [name for name in preferred if name in available]

    for name in available:
        if len(selected) >= 3:
            break
        if name not in selected:
            selected.append(name)

    # Last resort fallback if createmeta is sparse/unavailable.
    fallback = ["Task", "Bug", "Task"]
    while len(selected) < 3:
        selected.append(fallback[len(selected)])

    return selected[:3]


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def jira_connector(
    jira_datasource: JiraDataSource,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Module-scoped Jira connector with full lifecycle.

    Yields a dict with: project_key, project_id, lead_account_id, seed_issue_keys,
    subtask_issuetype_name, connector_id, uploaded_count, full_sync_count.
    """
    base_url = os.getenv("JIRA_TEST_BASE_URL")
    email = os.getenv("JIRA_TEST_EMAIL")
    api_token = os.getenv("JIRA_TEST_API_TOKEN")

    project_key = _TEST_PROJECT_KEY
    connector_name = f"jira-test-{uuid.uuid4().hex[:8]}"
    state: Dict[str, Any] = {
        "project_key": project_key,
        "connector_name": connector_name,
        "seed_issue_keys": [],
    }

    # ========== SETUP ==========
    logger.info("SETUP: Creating Jira project '%s'", project_key)

    # 1. Resolve current user's accountId (required for projectLead).
    me_resp = await jira_datasource.get_current_user()
    if me_resp.status >= 400:
        raise RuntimeError(
            f"Failed to fetch current Jira user: HTTP {me_resp.status}. "
            f"Check JIRA_TEST_EMAIL/JIRA_TEST_API_TOKEN."
        )
    lead_account_id = me_resp.json().get("accountId")
    if not lead_account_id:
        raise RuntimeError("Jira /myself response missing accountId")
    state["lead_account_id"] = lead_account_id

    # 2. Create or reuse the project.
    try:
        existing = await jira_datasource.get_project(projectIdOrKey=project_key)
        if existing.status == 200:
            state["project_id"] = str(existing.json().get("id", ""))
            logger.info("SETUP: Reusing existing project '%s' (id=%s)", project_key, state["project_id"])
        else:
            raise ValueError("project not found")
    except Exception:
        create_resp = await jira_datasource.create_project(
            key=project_key,
            name=_TEST_PROJECT_NAME,
            description=_TEST_PROJECT_DESCRIPTION,
            leadAccountId=lead_account_id,
            projectTypeKey="software",
            projectTemplateKey=_PROJECT_TEMPLATE_KEY,
            assigneeType="PROJECT_LEAD",
        )
        if create_resp.status not in (200, 201):
            raise RuntimeError(
                f"Failed to create Jira project '{project_key}': HTTP {create_resp.status} "
                f"body={create_resp.text() if hasattr(create_resp, 'text') else ''}"
            )
        proj_data = create_resp.json()
        state["project_id"] = str(proj_data.get("id", ""))
        logger.info("SETUP: Created project '%s' (id=%s)", project_key, state["project_id"])

    # 3. Resolve sub-task issuetype name (some workspaces rename it).
    create_meta_json: Dict[str, Any] = {}
    try:
        meta_resp = await jira_datasource.get_create_issue_meta(
            projectKeys=project_key,
            expand="projects.issuetypes",
        )
        if meta_resp.status == 200:
            create_meta_json = meta_resp.json() or {}
            subtask_name = _resolve_subtask_issuetype_name(create_meta_json)
        else:
            subtask_name = "Sub-task"
    except Exception as e:
        logger.warning("SETUP: createmeta fetch failed (%s); defaulting to 'Sub-task'", e)
        subtask_name = "Sub-task"
    state["subtask_issuetype_name"] = subtask_name

    # 4. Seed 3 issues so the initial sync has something to index.
    seed_types = _resolve_seed_issue_types(create_meta_json)
    seed_titles = [
        f"InitTest{seed_types[0].replace(' ', '')}-{uuid.uuid4().hex[:6]}",
        f"InitTest{seed_types[1].replace(' ', '')}-{uuid.uuid4().hex[:6]}",
        f"InitTest{seed_types[2].replace(' ', '')}-{uuid.uuid4().hex[:6]}",
    ]
    logger.info("SETUP: Selected seed issue types: %s", seed_types)
    for title, itype in zip(seed_titles, seed_types):
        resp = await jira_datasource.create_issue(
            fields={
                "project": {"key": project_key},
                "summary": title,
                "issuetype": {"name": itype},
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": f"Seed {itype} for integration test."}],
                        }
                    ],
                },
            }
        )
        if resp.status not in (200, 201):
            error_body = ""
            if hasattr(resp, "text"):
                try:
                    error_body = resp.text()
                except Exception:
                    error_body = ""
            logger.error(
                "SETUP: Failed to create %s '%s': HTTP %s body=%s",
                itype,
                title,
                resp.status,
                error_body,
            )
            continue
        issue_key = resp.json().get("key")
        if issue_key:
            state["seed_issue_keys"].append(issue_key)
    if len(state["seed_issue_keys"]) < 3:
        raise RuntimeError(
            f"Expected 3 seed issues; created {len(state['seed_issue_keys'])}. "
            "Check API token permissions (must be Project Admin)."
        )
    state["uploaded_count"] = len(state["seed_issue_keys"])
    logger.info("SETUP: Seeded %d issues: %s", state["uploaded_count"], state["seed_issue_keys"])

    # 5. Register the connector through the Pipeshub control plane.
    config: Dict[str, Any] = {
        "auth": {
            "authType": "API_TOKEN",
            "baseUrl": base_url,
            "email": email,
            "apiToken": api_token,
        }
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

    # 6. Wait for the initial sync to absorb every seed issue.
    async def _initial_ok() -> bool:
        return await graph_provider.count_records(connector_id) >= state["uploaded_count"]

    await wait_until_graph_condition(
        connector_id,
        check=_initial_ok,
        timeout=240,
        poll_interval=10,
        description="initial sync",
    )

    full_count = await async_wait_for_stable_record_count(
        graph_provider,
        connector_id,
        stability_checks=3,
        interval=5,
        max_rounds=20,
    )

    # Verification cycle — same as Confluence: avoid racing against an in-flight sync
    # when the first test starts toggling.
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    verified_count = await async_wait_for_stable_record_count(
        graph_provider,
        connector_id,
        stability_checks=3,
        interval=5,
        max_rounds=20,
    )
    if verified_count != full_count:
        logger.info(
            "SETUP: Verification sync adjusted record count %d -> %d",
            full_count,
            verified_count,
        )
    state["full_sync_count"] = verified_count

    yield state

    # ========== TEARDOWN ==========
    logger.info("TEARDOWN: Cleaning up connector %s and project '%s'", connector_id, project_key)

    # Disable connector and assert graph cleanup BEFORE deleting Jira data —
    # otherwise an in-flight sync can revive deleted issues.
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

    # Delete the project — `enableUndo=False` skips Jira trash and purges fully.
    # Project deletion cascades to all issues / sub-tasks / attachments inside it,
    # so we don't need a separate per-issue delete loop (and it avoids needing
    # the "Delete Issues" project permission on the test account).
    try:
        del_resp = await jira_datasource.delete_project(
            projectIdOrKey=project_key, enableUndo=False
        )
        if del_resp.status not in (200, 202, 204):
            logger.warning(
                "TEARDOWN: delete_project returned HTTP %s (project may need manual cleanup)",
                del_resp.status,
            )
        else:
            logger.info("TEARDOWN: Permanently deleted project '%s'", project_key)
    except Exception as e:
        logger.warning("TEARDOWN: Failed to delete project '%s': %s", project_key, e)
