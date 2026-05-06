# pyright: ignore-file

"""Jira connector fixtures.

Mirrors the Confluence pattern:
- session-scoped `jira_datasource` (skips if creds missing)
- module-scoped `jira_connector` that creates a project, seeds issues
  (including Epic / Story-under-Epic / Sub-task / one attachment),
  registers a Pipeshub connector, runs an initial sync, then tears down.
"""

import logging
import os
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

import httpx
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
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from connectors.jira.jira_test_utils import (  # type: ignore[import-not-found]
    assert_jira_issues_match_graph_records,
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


def _adf(text: str) -> Dict[str, Any]:
    """Build a minimal Atlassian Document Format paragraph."""
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


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def connector_assertions(graph_provider: GraphProviderProtocol):
    """Generic assertions helper - works for any connector."""
    return ConnectorAssertions(graph_provider)


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


def _find_issuetype(
    create_meta_json: Dict[str, Any],
    predicate: Callable[[Dict[str, Any]], bool],
) -> Optional[str]:
    """Return the first issue-type name matching ``predicate``, or None."""
    for project in create_meta_json.get("projects") or []:
        for itype in project.get("issuetypes") or []:
            if predicate(itype):
                name = itype.get("name")
                if name:
                    return str(name)
    return None


def _resolve_middle_level_issuetype(create_meta_json: Dict[str, Any]) -> Optional[str]:
    """Pick any non-subtask, non-Epic issue type to seed under an Epic.

    The connector's hierarchy detection (and the PARENT_CHILD edge it emits) is
    driven by Jira's ``hierarchyLevel``, not by the issue-type **name** — Epic
    is level 1, Sub-task is level -1, and everything else (Story / Task / Bug /
    Improvement / custom) is level 0 and can be a direct child of an Epic.
    Prefer Story for parity with classic-Atlassian setups, then fall back to
    Task → Bug → whichever non-Epic non-subtask type the workspace exposes.
    """
    for preferred in ("Story", "Task", "Bug"):
        match = _find_issuetype(
            create_meta_json,
            lambda it, p=preferred: (
                not it.get("subtask")
                and str(it.get("name", "")).strip().lower() == p.lower()
            ),
        )
        if match:
            return match
    return _find_issuetype(
        create_meta_json,
        lambda it: (
            not it.get("subtask")
            and str(it.get("name", "")).strip().lower() != "epic"
        ),
    )


def _resolve_seed_issue_types(create_meta_json: Dict[str, Any]) -> List[str]:
    """Pick 3 createable non-subtask issue types for seed data.

    Jira workspaces can have different issue type schemes (e.g. no "Story").
    Prefer common names, then fill from any remaining createable non-subtask
    types so fixture setup remains stable across tenants.
    """
    projects = create_meta_json.get("projects") or []
    available: List[str] = []
    for project in projects:
        for itype in project.get("issuetypes") or []:
            name = str(itype.get("name") or "").strip()
            if not name:
                continue
            if itype.get("subtask"):
                continue
            if name.lower() == "epic":
                # Reserve Epic for hierarchy seeding, not for the basic seed pool.
                continue
            if name not in available:
                available.append(name)

    preferred = ["Story", "Task", "Bug"]
    selected: List[str] = [name for name in preferred if name in available]

    for name in available:
        if len(selected) >= 3:
            break
        if name not in selected:
            selected.append(name)

    fallback = ["Task", "Bug", "Task"]
    while len(selected) < 3:
        selected.append(fallback[len(selected)])

    return selected[:3]


async def _upload_attachment_via_httpx(
    base_url: str,
    email: str,
    api_token: str,
    issue_key: str,
    filename: str,
    content: bytes,
    mime: str,
) -> Optional[Dict[str, Any]]:
    """Upload an attachment via direct httpx multipart POST.

    The auto-generated ``JiraDataSource.add_attachment`` doesn't format multipart
    properly — Atlassian's attachment endpoint requires both ``X-Atlassian-Token:
    no-check`` and a real multipart body. Returns the first attachment object
    from the response, or None on failure (test will skip cleanly).
    """
    url = f"{base_url.rstrip('/')}/rest/api/3/issue/{issue_key}/attachments"
    headers = {"X-Atlassian-Token": "no-check", "Accept": "application/json"}
    files = {"file": (filename, content, mime)}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, auth=(email, api_token), headers=headers, files=files)
        if resp.status_code in (200, 201):
            data = resp.json()
            if isinstance(data, list) and data:
                return data[0]
            return None
        logger.warning(
            "SETUP: Attachment upload to %s returned HTTP %s: %s",
            issue_key,
            resp.status_code,
            resp.text[:300],
        )
    except Exception as e:
        logger.warning("SETUP: Attachment upload to %s failed: %s", issue_key, e)
    return None


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def jira_connector(
    jira_datasource: JiraDataSource,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Module-scoped Jira connector with full lifecycle.

    Yields a dict with: project_key, project_id, lead_account_id, seed_issue_keys,
    subtask_issuetype_name, connector_id, uploaded_count, full_sync_count, plus
    optional hierarchy/attachment ids when the workspace supports them.
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
        "seed_epic_key": None,
        "seed_epic_id": None,
        "move_target_epic_key": None,
        "move_target_epic_id": None,
        "seed_story_under_epic_key": None,
        "seed_story_under_epic_id": None,
        "seed_subtask_key": None,
        "seed_subtask_parent_key": None,
        "move_target_parent_key": None,
        "seed_attachment_issue_key": None,
        "seed_attachment_id": None,
        "seed_attachment_filename": None,
        "seed_attachment_mime": None,
        "seed_attachment_size": None,
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
                f"Failed to create Jira project '{project_key}': HTTP {create_resp.status}"
            )
        proj_data = create_resp.json()
        state["project_id"] = str(proj_data.get("id", ""))
        logger.info("SETUP: Created project '%s' (id=%s)", project_key, state["project_id"])

    # 3. Resolve issue type names from createmeta.
    create_meta_json: Dict[str, Any] = {}
    try:
        meta_resp = await jira_datasource.get_create_issue_meta(
            projectKeys=[project_key],
            expand="projects.issuetypes",
        )
        if meta_resp.status == 200:
            create_meta_json = meta_resp.json() or {}
    except Exception as e:
        logger.warning("SETUP: createmeta fetch failed (%s); using fallbacks", e)

    state["subtask_issuetype_name"] = _resolve_subtask_issuetype_name(create_meta_json)

    epic_issuetype_name = _find_issuetype(
        create_meta_json,
        lambda it: not it.get("subtask") and str(it.get("name", "")).strip().lower() == "epic",
    )
    # Hierarchy semantics in the connector key off Jira's hierarchyLevel, not
    # off the issue-type name. Any non-subtask, non-Epic type is "middle level"
    # and can sit under an Epic — prefer Story when available, otherwise pick
    # whatever standard type this workspace exposes (Task / Bug / Improvement / etc.).
    story_issuetype_name = _resolve_middle_level_issuetype(create_meta_json)

    # 4. Seed 3 standard issues + 1 spare Task (move target).
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
                "description": _adf(f"Seed {itype} for integration test."),
            }
        )
        if resp.status not in (200, 201):
            logger.error(
                "SETUP: Failed to create %s '%s': HTTP %s",
                itype,
                title,
                resp.status,
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

    # Expose the resolved issue types to tests. Hierarchy semantics in the connector
    # are based on Jira's hierarchyLevel, not on the issue-type name — so tests
    # creating ad-hoc level-0 issues should use whatever the workspace exposes,
    # not a hard-coded "Task" / "Story" string.
    state["seed_issue_types"] = list(seed_types)
    state["default_issue_type"] = seed_types[0] if seed_types else "Task"

    # Spare middle-level issue to use as the move target for sub-task reparenting.
    move_target_resp = await jira_datasource.create_issue(
        fields={
            "project": {"key": project_key},
            "summary": f"InitTestMoveTarget-{uuid.uuid4().hex[:6]}",
            "issuetype": {"name": "Task" if "Task" in seed_types else seed_types[0]},
            "description": _adf("Spare task; sub-task move-target."),
        }
    )
    if move_target_resp.status in (200, 201):
        state["move_target_parent_key"] = move_target_resp.json().get("key")
        if state["move_target_parent_key"]:
            state["seed_issue_keys"].append(state["move_target_parent_key"])

    # 5. Create primary Epic + secondary Epic (move target).
    if epic_issuetype_name:
        epic_resp = await jira_datasource.create_issue(
            fields={
                "project": {"key": project_key},
                "summary": f"InitTestEpic-{uuid.uuid4().hex[:6]}",
                "issuetype": {"name": epic_issuetype_name},
                "description": _adf("Primary epic for hierarchy tests."),
            }
        )
        if epic_resp.status in (200, 201):
            epic_data = epic_resp.json()
            state["seed_epic_key"] = epic_data.get("key")
            state["seed_epic_id"] = str(epic_data.get("id", ""))

        epic2_resp = await jira_datasource.create_issue(
            fields={
                "project": {"key": project_key},
                "summary": f"InitTestEpicMoveTarget-{uuid.uuid4().hex[:6]}",
                "issuetype": {"name": epic_issuetype_name},
                "description": _adf("Secondary epic; story-move target."),
            }
        )
        if epic2_resp.status in (200, 201):
            epic2_data = epic2_resp.json()
            state["move_target_epic_key"] = epic2_data.get("key")
            state["move_target_epic_id"] = str(epic2_data.get("id", ""))
    else:
        logger.warning("SETUP: Epic issue type not available — TC-JIRA-HIER-001 / TC-MOVE-002 will skip")

    # 6. Create middle-level issue (Story / Task / Bug / etc.) under primary Epic.
    # Try team-managed `parent` first; fall back to epic-link custom field.
    if state["seed_epic_key"] and story_issuetype_name:
        story_summary = f"InitTest{story_issuetype_name.replace(' ', '')}UnderEpic-{uuid.uuid4().hex[:6]}"
        logger.info(
            "SETUP: Creating %s under Epic %s (hierarchy mid-level seed)",
            story_issuetype_name, state["seed_epic_key"],
        )
        story_resp = await jira_datasource.create_issue(
            fields={
                "project": {"key": project_key},
                "summary": story_summary,
                "issuetype": {"name": story_issuetype_name},
                "parent": {"key": state["seed_epic_key"]},
                "description": _adf(f"{story_issuetype_name} under primary epic."),
            }
        )
        if story_resp.status in (200, 201):
            data = story_resp.json()
            state["seed_story_under_epic_key"] = data.get("key")
            state["seed_story_under_epic_id"] = str(data.get("id", ""))
        else:
            # Try epic-link via customfield_10014 (classic projects).
            try:
                fields_resp = await jira_datasource.get_fields()
                epic_link_field = None
                if fields_resp.status == 200:
                    for f in fields_resp.json() or []:
                        if str(f.get("name", "")).strip().lower() == "epic link":
                            epic_link_field = f.get("id")
                            break
                if epic_link_field:
                    retry_resp = await jira_datasource.create_issue(
                        fields={
                            "project": {"key": project_key},
                            "summary": story_summary,
                            "issuetype": {"name": story_issuetype_name},
                            epic_link_field: state["seed_epic_key"],
                            "description": _adf("Story under primary epic (classic epic-link)."),
                        }
                    )
                    if retry_resp.status in (200, 201):
                        data = retry_resp.json()
                        state["seed_story_under_epic_key"] = data.get("key")
                        state["seed_story_under_epic_id"] = str(data.get("id", ""))
            except Exception as e:
                logger.warning("SETUP: Story-under-Epic via epic-link failed: %s", e)

    # 7. Create Sub-task under one of the seed Tasks (parent = seed_issue_keys[2]).
    subtask_parent_key = state["seed_issue_keys"][2]
    subtask_resp = await jira_datasource.create_issue(
        fields={
            "project": {"key": project_key},
            "summary": f"InitTestSubtask-{uuid.uuid4().hex[:6]}",
            "issuetype": {"name": state["subtask_issuetype_name"]},
            "parent": {"key": subtask_parent_key},
            "description": _adf("Sub-task for hierarchy tests."),
        }
    )
    if subtask_resp.status in (200, 201):
        data = subtask_resp.json()
        state["seed_subtask_key"] = data.get("key")
        state["seed_subtask_parent_key"] = subtask_parent_key
    else:
        logger.warning(
            "SETUP: Sub-task creation rejected (HTTP %s) — TC-JIRA-HIER-002 / TC-MOVE-001 will skip",
            subtask_resp.status,
        )

    # 8. Upload one attachment to seed_issue_keys[0] (uses direct httpx — auto-gen client doesn't multipart).
    attachment_filename = f"jira-test-attachment-{uuid.uuid4().hex[:6]}.txt"
    attachment_content = b"PipesHub Jira integration test attachment payload."
    attachment_mime = "text/plain"
    attachment_target_key = state["seed_issue_keys"][0]
    att_meta = await _upload_attachment_via_httpx(
        base_url=base_url,
        email=email,
        api_token=api_token,
        issue_key=attachment_target_key,
        filename=attachment_filename,
        content=attachment_content,
        mime=attachment_mime,
    )
    if att_meta and att_meta.get("id"):
        state["seed_attachment_issue_key"] = attachment_target_key
        state["seed_attachment_id"] = str(att_meta.get("id"))
        state["seed_attachment_filename"] = att_meta.get("filename") or attachment_filename
        state["seed_attachment_mime"] = att_meta.get("mimeType") or attachment_mime
        state["seed_attachment_size"] = int(att_meta.get("size") or len(attachment_content))
    else:
        logger.warning("SETUP: Attachment upload failed — TC-JIRA-ATTACH-001 will skip")

    state["uploaded_count"] = len(state["seed_issue_keys"])
    logger.info(
        "SETUP: Seeded %d issues: %s (epic=%s, story=%s, subtask=%s, attachment=%s)",
        state["uploaded_count"],
        state["seed_issue_keys"],
        state["seed_epic_key"],
        state["seed_story_under_epic_key"],
        state["seed_subtask_key"],
        state["seed_attachment_id"],
    )

    # 9. Register the connector through the Pipeshub control plane.
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

    # 10. Wait for the initial sync to absorb every seeded record.
    expected_min_records = len(state["seed_issue_keys"])  # tickets only
    if state["seed_epic_key"]:
        expected_min_records += 1
    if state["move_target_epic_key"]:
        expected_min_records += 1
    if state["seed_story_under_epic_key"]:
        expected_min_records += 1
    if state["seed_subtask_key"]:
        expected_min_records += 1
    if state["seed_attachment_id"]:
        expected_min_records += 1  # FILE record

    full_count = await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        min_records=expected_min_records,
        timeout=240,
    )

    try:
        await assert_jira_issues_match_graph_records(
            jira_datasource,
            graph_provider,
            connector_id,
            project_key,
            phase="SETUP after initial sync",
        )
    except AssertionError as e:
        logger.warning("SETUP: post-initial-sync API/graph mismatch (will retry after verification cycle): %s", e)

    # 11. Verification sync — same race-avoidance Confluence uses.
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)

    verified_count = await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        min_records=expected_min_records,
        timeout=240,
    )
    if verified_count != full_count:
        logger.info(
            "SETUP: Verification sync adjusted record count %d -> %d",
            full_count,
            verified_count,
        )

    await assert_jira_issues_match_graph_records(
        jira_datasource,
        graph_provider,
        connector_id,
        project_key,
        phase="SETUP after verification sync",
    )

    state["full_sync_count"] = verified_count

    yield state

    # ========== TEARDOWN ==========
    logger.info("TEARDOWN: Cleaning up connector %s and project '%s'", connector_id, project_key)

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

    # Project deletion cascades to all issues / sub-tasks / attachments.
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
