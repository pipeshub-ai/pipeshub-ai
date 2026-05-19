# pyright: ignore-file

"""Jira connector fixtures.

Mirrors the Confluence pattern:
- session-scoped `jira_datasource` (skips if creds missing)
- module-scoped `jira_connector` that creates a project, seeds issues
  (including Epic / Story-under-Epic / Sub-task / one attachment),
  registers a Pipeshub connector, waits once for sync to finish, then tears down.
"""

import logging
import os
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, Optional

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
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from connectors.jira.jira_test_utils import (  # type: ignore[import-not-found]
    assert_jira_issues_match_graph_records,
    preview_jira_user_group_and_role_permission_edge_totals,
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


def _parse_project_role_id(role_url: str) -> int:
    """Extract numeric role id from Jira project role self URL."""
    tail = (role_url or "").rstrip("/").split("/")[-1]
    return int(tail)


def _role_url_from_api_value(val: Any) -> str:
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return str(val.get("self") or val.get("url") or "")
    return ""


async def _add_user_to_project_role_by_name(
    jira_datasource: JiraDataSource,
    project_key: str,
    lead_account_id: str,
    *role_name_candidates: str,
) -> Optional[tuple[int, str]]:
    """Add current user to the first matching project role. Returns (role_id, role_name) or None."""
    roles_resp = await jira_datasource.get_project_roles(projectIdOrKey=project_key)
    if roles_resp.status != 200:
        logger.warning("SETUP: get_project_roles(%s) HTTP %s", project_key, roles_resp.status)
        return None
    mapping = roles_resp.json() or {}
    for cand in role_name_candidates:
        for role_name, url in mapping.items():
            if str(role_name).strip().lower() == cand.lower():
                rid = _parse_project_role_id(_role_url_from_api_value(url))
                add_resp = await jira_datasource.add_actor_users(
                    projectIdOrKey=project_key,
                    id=rid,
                    user=[lead_account_id],
                )
                if add_resp.status in (200, 201, 204):
                    return rid, str(role_name)
                logger.warning(
                    "SETUP: add_actor_users(%s role=%s id=%s) HTTP %s",
                    project_key, role_name, rid, add_resp.status,
                )
    return None


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


def _resolve_task_issuetype_name(create_meta_json: Dict[str, Any]) -> str:
    """Issue type name to use for generic seed Tasks (createmeta-aware).

    Prefer the literal ``Task`` type when the project exposes it; otherwise any
    non-subtask, non-Epic createable type so software projects without a "Task"
    label still seed.
    """
    task = _find_issuetype(
        create_meta_json,
        lambda it: (
            not it.get("subtask")
            and str(it.get("name", "")).strip().lower() == "task"
        ),
    )
    if task:
        return task
    other = _find_issuetype(
        create_meta_json,
        lambda it: (
            not it.get("subtask")
            and str(it.get("name", "")).strip().lower() != "epic"
        ),
    )
    return other or "Task"


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


def _compute_jira_fixture_expected_graph_counts(state: dict[str, Any]) -> dict[str, int]:
    """Derive expected **record** graph counts from Jira seed topology (no graph reads).

    Covers: ``seed_issue_keys`` (tasks + move-target), optional epics / story-under-epic /
    sub-task, optional FILE attachment, and **one** project ``RecordGroup`` (Jira site
    groups are ``Group`` nodes, not extra ``RecordGroup`` nodes).

    Global ``User→Group`` and ``User→Role`` edges, and edges into ``RecordGroup``, are
    site-dependent; those expectations are set after sync via Jira API preview + one graph
    read (see fixture body below).

    The fixture asserts post-sync ``verified_count`` equals ``expected_total_records`` so a
    wrong record formula fails setup before tests run.
    """
    ticket = len(state["seed_issue_keys"])
    if state.get("seed_epic_key"):
        ticket += 1
    if state.get("move_target_epic_key"):
        ticket += 1
    if state.get("seed_story_under_epic_key"):
        ticket += 1
    if state.get("seed_subtask_key"):
        ticket += 1

    file_n = 1 if state.get("seed_attachment_id") else 0
    total = ticket + file_n

    parent_child = 0
    if state.get("seed_subtask_key"):
        parent_child += 1
    if state.get("seed_story_under_epic_key"):
        parent_child += 1

    attachment = 1 if state.get("seed_attachment_id") else 0

    record_groups = 1
    app_rg_edges = 1
    record_group_edges = total

    inherit = total
    permission_aggregate = total

    return {
        "expected_ticket_count": ticket,
        "expected_file_count": file_n,
        "expected_total_records": total,
        "expected_record_groups": record_groups,
        "expected_parent_child_edges": parent_child,
        "expected_attachment_edges": attachment,
        "expected_record_group_edges": record_group_edges,
        "expected_inherit_edges": inherit,
        "expected_permission_aggregate": permission_aggregate,
        "expected_app_record_group_edges": app_rg_edges,
    }


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

    Record-level ``expected_*`` values come from :func:`_compute_jira_fixture_expected_graph_counts`.
    Permission edge totals (**``User→Group``**, **``User→Role``**) are computed from the same
    Jira APIs / rules as ``JiraCloudConnector`` (visible email + synced user pool), then
    reconciled against ``graph_provider`` so preview and Neo4j stay aligned. Edges into
    ``RecordGroup`` nodes use a single post-sync graph count (scheme resolution is complex).

    Setup asserts post-sync ``verified_count`` equals ``expected_total_records``.

    TC-JIRA-001 compares ``USER_APP_RELATION`` to Jira ``get_all_users('')`` counts separately.
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
        "test_group_id": None,
        "test_group_name": None,
        "test_project_role_id": None,
        "test_project_role_key": None,
        "expected_ticket_count": None,
        "expected_file_count": None,
        "expected_total_records": None,
        "expected_record_groups": None,
        "expected_parent_child_edges": None,
        "expected_attachment_edges": None,
        "expected_record_group_edges": None,
        "expected_inherit_edges": None,
        "expected_permission_aggregate": None,
        "expected_app_record_group_edges": None,
        "expected_permission_user_group_edges": None,
        "expected_permission_user_role_edges": None,
        "expected_permission_to_record_group_edges": None,
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
    existing = await jira_datasource.get_project(projectIdOrKey=project_key)
    if existing.status == 200:
        state["project_id"] = str(existing.json().get("id", ""))
        logger.info("SETUP: Reusing existing project '%s' (id=%s)", project_key, state["project_id"])
    elif existing.status == 404:
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
            body = (create_resp.text() or "")[:400]
            raise RuntimeError(
                f"Failed to create Jira project '{project_key}': HTTP {create_resp.status} body={body!r}"
            )
        proj_data = create_resp.json()
        state["project_id"] = str(proj_data.get("id", ""))
        logger.info("SETUP: Created project '%s' (id=%s)", project_key, state["project_id"])
    else:
        body = (existing.text() or "")[:400]
        raise RuntimeError(
            f"SETUP: get_project({project_key!r}) returned HTTP {existing.status} "
            f"(expected 200 to reuse or 404 to create). body={body!r}"
        )

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

    # 4. Seed 3 Task issues + 1 spare Task (move target).
    task_issuetype = _resolve_task_issuetype_name(create_meta_json)
    state["default_issue_type"] = task_issuetype

    for i in range(3):
        title = f"InitTestTask-{i + 1}-{uuid.uuid4().hex[:6]}"
        resp = await jira_datasource.create_issue(
            fields={
                "project": {"key": project_key},
                "summary": title,
                "issuetype": {"name": task_issuetype},
                "description": _adf("Seed task for integration test."),
            }
        )
        if resp.status not in (200, 201):
            logger.error(
                "SETUP: Failed to create seed task '%s': HTTP %s",
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

    # Spare middle-level issue to use as the move target for sub-task reparenting.
    move_target_resp = await jira_datasource.create_issue(
        fields={
            "project": {"key": project_key},
            "summary": f"InitTestMoveTarget-{uuid.uuid4().hex[:6]}",
            "issuetype": {"name": task_issuetype},
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
        logger.warning("SETUP: Epic issue type not available — TC-MOVE-002 will skip")

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
            "SETUP: Sub-task creation rejected (HTTP %s) — TC-MOVE-001 will skip",
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

    # 8b. Test group (TC-JIRA-002) + user as project-role actor on INTTEST.
    group_name = f"pipeshub-it-{uuid.uuid4().hex[:8]}"
    state["test_group_name"] = group_name
    cg = await jira_datasource.create_group(name=group_name)
    if cg.status not in (200, 201):
        raise RuntimeError(f"SETUP: create_group({group_name!r}) HTTP {cg.status}")
    gj = cg.json() or {}
    gid = gj.get("groupId") or gj.get("id")
    if not gid:
        raise RuntimeError(f"SETUP: create_group response missing groupId: {gj}")
    state["test_group_id"] = str(gid)

    aug = await jira_datasource.add_user_to_group(
        accountId=lead_account_id,
        groupId=state["test_group_id"],
    )
    if aug.status not in (200, 201, 204):
        body = aug.json() or {}
        logger.warning("SETUP: add_user_to_group HTTP %s body=%s (continuing)", aug.status, body)

    dev = await _add_user_to_project_role_by_name(
        jira_datasource, project_key, lead_account_id, "Developers", "Member", "Administrators",
    )
    if dev:
        dev_id, dev_key = dev
        state["test_project_role_id"] = str(dev_id)
        state["test_project_role_key"] = dev_key
    else:
        logger.warning(
            "SETUP: could not add lead to Developers/Member/Administrators on %s — "
            "graph user→role edges for INTTEST may be incomplete",
            project_key,
        )

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

    # Restrict sync to our test project only (deterministic graph vs whole site).
    pipeshub_client.update_connector_filters_sync_safe(
        connector_id,
        filters={
            "project_keys": {
                "operator": "in",
                "type": "list",
                "value": [project_key],
            }
        },
    )

    pipeshub_client.toggle_sync(connector_id, enable=True)

    # 10. Wait for sync to absorb every seeded record (single pass).
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

    await assert_jira_issues_match_graph_records(
        jira_datasource,
        graph_provider,
        connector_id,
        project_key,
        phase="SETUP after sync",
    )

    state["full_sync_count"] = full_count

    # Expected counts from seed topology (not graph snapshots). Reconcile once: sync must
    # have produced exactly this many Record nodes or setup fails loudly.
    derived = _compute_jira_fixture_expected_graph_counts(state)
    if full_count != derived["expected_total_records"]:
        raise RuntimeError(
            f"SETUP: post-sync graph record count {full_count} != seed-derived "
            f"{derived['expected_total_records']}. Update _compute_jira_fixture_expected_graph_counts "
            "or fix connector/sync for this Jira project shape."
        )
    state.update(derived)

    ug_exp, ur_exp = await preview_jira_user_group_and_role_permission_edge_totals(
        jira_datasource,
        project_key=project_key,
        lead_account_id=lead_account_id,
    )
    ug_graph = await graph_provider.count_user_to_group_permission_edges(connector_id)
    ur_graph = await graph_provider.count_user_to_role_permission_edges(connector_id)
    if ug_exp != ug_graph or ur_exp != ur_graph:
        raise RuntimeError(
            "SETUP: Jira API permission preview does not match graph counts — "
            f"user→group preview={ug_exp} graph={ug_graph}; "
            f"user→role preview={ur_exp} graph={ur_graph}. "
            "Update preview_jira_user_group_and_role_permission_edge_totals or investigate sync."
        )
    state["expected_permission_user_group_edges"] = ug_exp
    state["expected_permission_user_role_edges"] = ur_exp
    state["expected_permission_to_record_group_edges"] = (
        await graph_provider.count_permission_edges_to_record_groups(connector_id)
    )

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

    # Jira org cleanup (fixture group) before deleting INTTEST.
    if state.get("test_group_id") and state.get("lead_account_id"):
        try:
            rm = await jira_datasource.remove_user_from_group(
                accountId=state["lead_account_id"],
                groupId=state["test_group_id"],
            )
            if rm.status not in (200, 204):
                logger.warning("TEARDOWN: remove_user_from_group HTTP %s", rm.status)
        except Exception as e:
            logger.warning("TEARDOWN: remove_user_from_group: %s", e)
    if state.get("test_group_id"):
        try:
            rg = await jira_datasource.remove_group(groupId=state["test_group_id"])
            if rg.status not in (200, 204):
                logger.warning("TEARDOWN: remove_group HTTP %s", rg.status)
        except Exception as e:
            logger.warning("TEARDOWN: remove_group: %s", e)

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
