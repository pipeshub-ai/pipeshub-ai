"""
Jira REST helpers for Jira connector integration tests.

Mirrors the Confluence v1 helper pattern: polling helpers + bool variants for use
inside ``wait_until_jira_condition``. Behavioural difference from the Confluence
helpers: ``check_*_bool`` re-raises HTTP 401/403 (auth-class) errors instead of
swallowing them to ``False``, so credential problems fail fast.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from app.config.constants.arangodb import ProgressStatus  # type: ignore[import-not-found]
from app.connectors.sources.atlassian.jira_cloud.connector import (  # type: ignore[import-not-found]
    PLACEHOLDER_SWEEP_MAX_DEPTH,
)
from app.models.entities import Record  # type: ignore[import-not-found]
from app.sources.external.jira.jira import (
    JiraDataSource,  # type: ignore[import-not-found]
)
from connectors.jira.constants import (  # type: ignore[import-not-found]
    JIRA_FROZEN_ISSUE_KEYS,
    JIRA_INDEXING_WAIT_SEC,
    JIRA_IT_ARTIFACT_PREFIX,
    JIRA_IT_RUN_ID,
    JIRA_IT_STALE_ARTIFACT_AGE_SEC,
    JIRA_TEST_SETTLE_WAIT_SEC,
)
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    owned_record_external_ids,
)

logger = logging.getLogger("jira-test-utils")

_JIRA_GROUP_PAGE = 50
_JIRA_GROUP_MEMBER_PAGE = 50
# Jira lists these in bulk groups but ``/group/member`` returns 404 (add-on pseudo-group).
_GROUP_NAMES_SKIP_MEMBER_FETCH: frozenset[str] = frozenset({"atlassian-addons"})


class JiraAuthError(RuntimeError):
    """Raised when a Jira polling check hits HTTP 401/403 — fail fast on bad creds."""


def _raise_on_auth_error(status: int, context: str) -> None:
    """Re-raise auth-class errors so polling loops don't mask credential problems."""
    if status in (401, 403):
        raise JiraAuthError(
            f"{context}: Jira returned HTTP {status} (auth/permission). "
            f"Check JIRA_TEST_EMAIL / JIRA_TEST_API_TOKEN."
        )


async def collect_jira_synced_users_for_connector_edges(
    datasource: JiraDataSource,
    *,
    page_size: int = 50,
    max_pages: int = 500,
) -> tuple[set[str], set[str]]:
    """Users the Jira connector can link via ``User→Group`` / ``User→Role`` edges.

    Mirrors :meth:`JiraCloudConnector._fetch_users`: paginated ``get_all_users('')``,
    **active** users only, with non-empty ``emailAddress`` (private / missing email ⇒
    excluded — no PERMISSION edges are created for them).

    Returns:
        ``(emails_lower, account_ids)`` — one accountId per user with visible email.

    Raises:
        JiraAuthError: On HTTP 401/403 from Jira.
        RuntimeError: On other non-success HTTP status or unparseable payload.
    """
    emails_lower: set[str] = set()
    account_ids: set[str] = set()
    seen_account_ids: set[str] = set()
    start_at = 0

    for _ in range(max_pages):
        resp = await jira_api_call_with_retry(
            datasource.get_all_users,
            query="",
            startAt=start_at,
            maxResults=page_size,
            context="collect_jira_synced_users_for_connector_edges",
        )
        if resp.status in (401, 403):
            _raise_on_auth_error(resp.status, "collect_jira_synced_users_for_connector_edges")
        if resp.status != 200:
            raise RuntimeError(
                f"get_all_users (users/search) failed: HTTP {resp.status} startAt={start_at}"
            )
        payload = resp.json()
        if isinstance(payload, list):
            batch_users = payload
        elif isinstance(payload, dict):
            batch_users = payload.get("values") or []
        else:
            raise RuntimeError(
                f"get_all_users: expected list or dict, got {type(payload).__name__}"
            )
        if not batch_users:
            break
        for u in batch_users:
            if not u.get("active", True):
                continue
            aid = u.get("accountId")
            if not aid or aid in seen_account_ids:
                continue
            email = (u.get("emailAddress") or "").strip()
            if not email:
                continue
            seen_account_ids.add(aid)
            account_ids.add(aid)
            emails_lower.add(email.lower())
        if len(batch_users) < page_size:
            break
        start_at += page_size

    return emails_lower, account_ids


async def count_jira_users_with_visible_email(
    datasource: JiraDataSource,
    *,
    page_size: int = 50,
    max_pages: int = 500,
) -> int:
    """Count users returned by :func:`collect_jira_synced_users_for_connector_edges`."""
    _, accounts = await collect_jira_synced_users_for_connector_edges(
        datasource, page_size=page_size, max_pages=max_pages,
    )
    return len(accounts)


async def _jira_fetch_all_groups(datasource: JiraDataSource) -> list[dict[str, Any]]:
    """Paginated ``/rest/api/3/group/bulk`` — same page size as ``JiraCloudConnector``."""
    groups: list[dict[str, Any]] = []
    start_at = 0
    while True:
        resp = await jira_api_call_with_retry(
            datasource.bulk_get_groups,
            startAt=start_at,
            maxResults=_JIRA_GROUP_PAGE,
            context="_jira_fetch_all_groups",
        )
        if resp.status in (401, 403):
            _raise_on_auth_error(resp.status, "_jira_fetch_all_groups")
        if resp.status != 200:
            raise RuntimeError(f"bulk_get_groups failed: HTTP {resp.status} startAt={start_at}")
        data = resp.json() or {}
        batch = data.get("values") or []
        if not batch:
            break
        groups.extend(batch)
        if data.get("isLast") or len(batch) < _JIRA_GROUP_PAGE:
            break
        start_at += len(batch)
    return groups


async def count_jira_group_synced_members(
    datasource: JiraDataSource, group_name: str
) -> tuple[Optional[str], int]:
    """Return ``(groupId, synced-member count)`` for ``group_name``.

    Mirrors the connector's ``User→Group`` edge creation in ``_sync_user_groups``: one edge per
    group member who is in the synced user pool (active + visible email). Returns ``(None, 0)``
    if the group is not found.
    """
    all_groups = await _jira_fetch_all_groups(datasource)
    group = next(
        (g for g in all_groups if g.get("name") == group_name and g.get("groupId")), None,
    )
    if not group:
        return None, 0
    synced_emails, _ = await collect_jira_synced_users_for_connector_edges(datasource)
    member_emails = await _jira_fetch_group_member_emails_with_visible_address(datasource, group_name)
    synced = {e.lower() for e in member_emails if e.lower() in synced_emails}
    return str(group.get("groupId")), len(synced)


async def count_jira_site_groups_bulk(datasource: JiraDataSource) -> int:
    """Count the groups the connector actually syncs into ``Group`` nodes.

    Mirrors ``_sync_user_groups``: skip bulk groups missing ``groupId``/``name``, and skip
    Atlassian-managed Connect/app groups (``atlassian-addons*``) that the connector never
    writes as ``Group`` nodes.
    """
    groups = await _jira_fetch_all_groups(datasource)
    return sum(
        1
        for g in groups
        if g.get("groupId")
        and g.get("name")
        and not str(g.get("name")).startswith("atlassian-addons")
    )


async def _jira_fetch_group_member_emails_with_visible_address(
    datasource: JiraDataSource,
    group_name: str,
) -> list[str]:
    """Member emails from ``/rest/api/3/group/member`` (inactive excluded)."""
    if group_name in _GROUP_NAMES_SKIP_MEMBER_FETCH:
        return []
    out: list[str] = []
    start_at = 0
    while True:
        resp = await jira_api_call_with_retry(
            datasource.get_users_from_group,
            groupname=group_name,
            includeInactiveUsers=False,
            startAt=start_at,
            maxResults=_JIRA_GROUP_MEMBER_PAGE,
            context=f"group members {group_name!r}",
        )
        if resp.status in (401, 403):
            _raise_on_auth_error(resp.status, "_jira_fetch_group_member_emails_with_visible_address")
        if resp.status != 200:
            logger.warning(
                "group member fetch failed for %r: HTTP %s", group_name, resp.status,
            )
            break
        data = resp.json() or {}
        batch = data.get("values") or []
        if not batch:
            break
        for m in batch:
            e = (m.get("emailAddress") or "").strip()
            if e:
                out.append(e)
        if data.get("isLast") or len(batch) < _JIRA_GROUP_MEMBER_PAGE:
            break
        start_at += len(batch)
    return out


async def build_jira_groups_members_map_for_synced_users(
    datasource: JiraDataSource,
    synced_emails_lower: set[str],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Build ``group_id`` / ``group_name`` → synced-member emails (lowercase), like the connector.

    Returns:
        ``(all_groups, groups_members_map)`` where map values are lists of emails (lowercase)
        that intersect ``synced_emails_lower`` (same cardinality the connector uses per group).
    """
    all_groups = await _jira_fetch_all_groups(datasource)
    mapping: dict[str, list[str]] = {}
    for g in all_groups:
        gid = g.get("groupId")
        name = g.get("name")
        if not gid or not name:
            continue
        raw = await _jira_fetch_group_member_emails_with_visible_address(datasource, str(name))
        synced_members = [e.lower() for e in raw if e.lower() in synced_emails_lower]
        gid_s, name_s = str(gid), str(name)
        mapping[gid_s] = synced_members
        mapping[name_s] = synced_members
    return all_groups, mapping


def _sum_user_group_permission_edges_from_map(
    all_groups: list[dict[str, Any]],
    groups_members_map: dict[str, list[str]],
) -> int:
    """One ``User→Group`` edge per synced member per Jira group (connector batch semantics)."""
    total = 0
    seen_gid: set[str] = set()
    for g in all_groups:
        gid = g.get("groupId")
        if not gid:
            continue
        gid_s = str(gid)
        if gid_s in seen_gid:
            continue
        seen_gid.add(gid_s)
        total += len(groups_members_map.get(gid_s, []))
    return total


async def preview_jira_user_group_and_role_permission_edge_totals(
    datasource: JiraDataSource,
    *,
    project_key: str,
    lead_account_id: str,
) -> tuple[int, int]:
    """Expected global ``User→Group`` and ``User→Role`` PERMISSION counts for this site.

    Mirrors ``JiraCloudConnector._sync_user_groups`` membership filtering and
    ``_sync_project_roles`` / ``_sync_project_lead_roles`` actor expansion: only users
    with visible email in the connector user list receive edges.

    Returns:
        ``(expected_user_group_edges, expected_user_role_edges)``
    """
    synced_emails, synced_accounts = await collect_jira_synced_users_for_connector_edges(
        datasource,
    )
    all_groups, groups_members_map = await build_jira_groups_members_map_for_synced_users(
        datasource, synced_emails,
    )
    ug_total = _sum_user_group_permission_edges_from_map(all_groups, groups_members_map)

    roles_resp = await jira_api_call_with_retry(
        datasource.get_project_roles, projectIdOrKey=project_key,
        context=f"project roles {project_key!r}",
    )
    if roles_resp.status in (401, 403):
        _raise_on_auth_error(roles_resp.status, "preview_jira_user_group_and_role_permission_edge_totals")
    if roles_resp.status != 200:
        raise RuntimeError(
            f"get_project_roles failed for {project_key!r}: HTTP {roles_resp.status}",
        )
    roles_dict = roles_resp.json() or {}
    if not isinstance(roles_dict, dict):
        raise RuntimeError("get_project_roles: expected JSON object mapping")

    ur_total = 0
    for role_name, role_url in roles_dict.items():
        if role_name == "atlassian-addons-project-access":
            continue
        try:
            role_id = int(str(role_url).rstrip("/").split("/")[-1])
        except (TypeError, ValueError):
            continue
        rresp = await jira_api_call_with_retry(
            datasource.get_project_role,
            projectIdOrKey=project_key,
            id=role_id,
            excludeInactiveUsers=True,
            context=f"project role {project_key}/{role_name}",
        )
        if rresp.status != 200:
            continue
        role_data = rresp.json() or {}
        actors = role_data.get("actors") or []
        member_slots = 0
        for actor in actors:
            atype = actor.get("type", "")
            if atype == "atlassian-user-role-actor":
                au = actor.get("actorUser") or {}
                acc = au.get("accountId")
                em = (au.get("emailAddress") or "").strip().lower()
                ok = (acc and acc in synced_accounts) or (em and em in synced_emails)
                if ok:
                    member_slots += 1
            elif atype == "atlassian-group-role-actor":
                gname = actor.get("name") or actor.get("displayName")
                gid = actor.get("groupId")
                group_members: list[str] = []
                if gid and str(gid) in groups_members_map:
                    group_members = groups_members_map[str(gid)]
                elif gname and str(gname) in groups_members_map:
                    group_members = groups_members_map[str(gname)]
                member_slots += len(group_members)
        ur_total += member_slots

    if lead_account_id in synced_accounts:
        ur_total += 1

    return ug_total, ur_total


async def preview_jira_role_member_counts(
    datasource: JiraDataSource,
    *,
    project_key: str,
    lead_account_id: str,
) -> dict[str, int]:
    """Per-role distinct synced ``User→Role`` member counts, keyed by ``source_role_id``.

    Mirrors ``_sync_project_roles`` actor expansion, including **group actors**
    (``atlassian-group-role-actor``): a group actor contributes all of its synced members
    as ``User→Role`` edges (connector.py: ``member_users.extend(group_members)``). Counts are
    de-duplicated per role (by email, falling back to accountId) to match the graph, where the
    per-(user, role) edge is upserted once even if a user is both a direct and a group actor.

    Also includes the synthetic ``{project_key}_projectLead`` role (1 if the lead is synced).
    """
    synced_emails, synced_accounts = await collect_jira_synced_users_for_connector_edges(datasource)
    _all_groups, groups_members_map = await build_jira_groups_members_map_for_synced_users(
        datasource, synced_emails,
    )

    roles_resp = await jira_api_call_with_retry(
        datasource.get_project_roles, projectIdOrKey=project_key,
        context=f"project roles {project_key!r}",
    )
    if roles_resp.status in (401, 403):
        _raise_on_auth_error(roles_resp.status, "preview_jira_role_member_counts")
    if roles_resp.status != 200:
        raise RuntimeError(f"get_project_roles({project_key!r}) failed: HTTP {roles_resp.status}")

    counts: dict[str, int] = {}
    for role_name, role_url in (roles_resp.json() or {}).items():
        if role_name == "atlassian-addons-project-access":
            continue
        try:
            role_id = int(str(role_url).rstrip("/").split("/")[-1])
        except (TypeError, ValueError):
            continue
        source_role_id = f"{project_key}_{role_id}"
        rresp = await jira_api_call_with_retry(
            datasource.get_project_role,
            projectIdOrKey=project_key, id=role_id, excludeInactiveUsers=True,
            context=f"project role {project_key}/{role_name}",
        )
        if rresp.status != 200:
            counts[source_role_id] = 0
            continue
        members: set[str] = set()
        for actor in (rresp.json() or {}).get("actors") or []:
            atype = actor.get("type", "")
            if atype == "atlassian-user-role-actor":
                au = actor.get("actorUser") or {}
                acc = au.get("accountId")
                em = (au.get("emailAddress") or "").strip().lower()
                if em and em in synced_emails:
                    members.add(em)
                elif acc and acc in synced_accounts:
                    members.add(f"acct:{acc}")
            elif atype == "atlassian-group-role-actor":
                gname = actor.get("name") or actor.get("displayName")
                gid = actor.get("groupId")
                group_members: list[str] = []
                if gid and str(gid) in groups_members_map:
                    group_members = groups_members_map[str(gid)]
                elif gname and str(gname) in groups_members_map:
                    group_members = groups_members_map[str(gname)]
                members.update(group_members)  # synced, lowercased emails
        counts[source_role_id] = len(members)

    counts[f"{project_key}_projectLead"] = 1 if (lead_account_id and lead_account_id in synced_accounts) else 0
    return counts


async def jira_fetch_application_roles_to_groups_mapping(
    datasource: JiraDataSource,
) -> dict[str, list[dict[str, str]]]:
    """Mirror ``JiraCloudConnector._fetch_application_roles_to_groups_mapping`` (no cache)."""
    mapping: dict[str, list[dict[str, str]]] = {}
    resp = await jira_api_call_with_retry(
        datasource.get_all_application_roles, context="application roles",
    )
    if resp.status in (401, 403):
        _raise_on_auth_error(resp.status, "jira_fetch_application_roles_to_groups_mapping")
    if resp.status != 200:
        raise RuntimeError(f"get_all_application_roles failed: HTTP {resp.status}")
    roles_data = resp.json()
    if not isinstance(roles_data, list):
        raise RuntimeError(
            f"get_all_application_roles: expected list, got {type(roles_data).__name__}",
        )
    for role in roles_data:
        role_key = role.get("key")
        group_details = role.get("groupDetails") or []
        if role_key and group_details:
            mapping[str(role_key)] = [
                {"groupId": str(g.get("groupId")), "name": g.get("name")}
                for g in group_details
                if g.get("groupId")
            ]
    return mapping


async def preview_jira_browse_projects_permission_edges_to_record_group(
    datasource: JiraDataSource,
    *,
    project_key: str,
) -> int:
    """Count resolvable ``PERMISSION → RecordGroup`` edges for ``BROWSE_PROJECTS``.

    Aligns with ``JiraCloudConnector._fetch_project_permission_scheme`` and
    ``DataEntitiesProcessor.on_new_record_groups`` (user needs visible email in
    the synced user pool; group id must appear in bulk groups; org and project
    roles always resolve).
    """
    synced_emails, _synced_accounts = await collect_jira_synced_users_for_connector_edges(
        datasource,
    )
    all_groups = await _jira_fetch_all_groups(datasource)
    synced_group_ids = {str(g.get("groupId")) for g in all_groups if g.get("groupId")}
    app_roles_mapping = await jira_fetch_application_roles_to_groups_mapping(datasource)

    scheme_resp = await jira_api_call_with_retry(
        datasource.get_assigned_permission_scheme,
        projectKeyOrId=project_key,
        expand="all",
        context=f"permission scheme {project_key!r}",
    )
    if scheme_resp.status in (401, 403):
        _raise_on_auth_error(
            scheme_resp.status, "preview_jira_browse_projects_permission_edges_to_record_group",
        )
    if scheme_resp.status != 200:
        raise RuntimeError(
            f"get_assigned_permission_scheme({project_key!r}) failed: HTTP {scheme_resp.status}",
        )
    scheme_data = scheme_resp.json() or {}
    scheme_id = scheme_data.get("id")
    if scheme_id is None:
        return 0

    grants_resp = await jira_api_call_with_retry(
        datasource.get_permission_scheme_grants,
        schemeId=int(scheme_id),
        expand="all",
        context=f"permission grants (scheme {scheme_id})",
    )
    if grants_resp.status in (401, 403):
        _raise_on_auth_error(
            grants_resp.status, "preview_jira_browse_projects_permission_edges_to_record_group",
        )
    if grants_resp.status != 200:
        raise RuntimeError(
            f"get_permission_scheme_grants({scheme_id}) failed: HTTP {grants_resp.status}",
        )
    grants_data = grants_resp.json() or {}
    permission_grants = grants_data.get("permissions") or []
    if not isinstance(permission_grants, list):
        return 0

    seen_holders: set[str] = set()
    edge_slots = 0

    for grant in permission_grants:
        if grant.get("permission") != "BROWSE_PROJECTS":
            continue
        holder = grant.get("holder") or {}
        holder_type = holder.get("type")
        holder_param = holder.get("parameter")
        holder_value = holder.get("value")
        holder_key = f"{holder_type}:{holder_value or holder_param}"
        if holder_key in seen_holders:
            continue
        seen_holders.add(holder_key)

        if holder_type == "group" and holder_value:
            if str(holder_value) in synced_group_ids:
                edge_slots += 1
        elif holder_type == "applicationRole":
            role_key = holder_param
            if role_key and role_key in app_roles_mapping:
                for group_info in app_roles_mapping[role_key]:
                    group_id = group_info.get("groupId")
                    if not group_id:
                        continue
                    gkey = f"group:{group_id}"
                    if gkey in seen_holders:
                        continue
                    seen_holders.add(gkey)
                    if str(group_id) in synced_group_ids:
                        edge_slots += 1
            else:
                edge_slots += 1
        elif holder_type == "user" and holder_param:
            user_data = holder.get("user") or {}
            user_email = (user_data.get("emailAddress") or "").strip().lower()
            if user_email in synced_emails:
                edge_slots += 1
        elif holder_type == "anyone":
            edge_slots += 1
        elif holder_type == "projectRole":
            project_role = holder.get("projectRole") or {}
            role_name = project_role.get("name", f"Role_{holder_param}")
            if role_name == "atlassian-addons-project-access":
                continue
            edge_slots += 1
        elif holder_type == "projectLead":
            edge_slots += 1
        elif holder_type in ("groupCustomField", "userCustomField", "sd.customer.portal.only"):
            continue

    return edge_slots


# =============================================================================
# JQL counting + match
# =============================================================================


async def assert_jira_issues_match_graph_records(
    datasource: JiraDataSource,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    project_key: str,
    *,
    phase: str,
) -> None:
    """Assert the project's live issues and the graph's TICKETs are the same set.

    Sets, not counts, so a failure names the offending issues. IT artifacts are skipped on
    both sides: a concurrently running leg shares this Jira site, so its mutation tickets are
    live for a few minutes and may or may not have landed inside our sync window.

    The graph side is *not* ``BELONGS_TO``-guarded, so call this only before a narrowing
    filter sync — records that lost their edge would otherwise read as unexpected extras.
    """
    live = await fetch_jira_project_issue_ids(datasource, project_key)
    graph_ids = await owned_record_external_ids(
        graph_provider, connector_id, prefix=JIRA_IT_ARTIFACT_PREFIX, record_type="TICKET",
    )
    missing = live - graph_ids
    extra = graph_ids - live
    if missing or extra:
        raise AssertionError(
            f"{phase}: graph TICKETs != live Jira issues for connector {connector_id} "
            f"project_key={project_key!r} (IT artifacts excluded from both sides). "
            f"missing_from_graph={sorted(missing)} unexpected_in_graph={sorted(extra)}"
        )


async def fetch_jira_project_issue_ids(datasource: JiraDataSource, project_key: str) -> set[str]:
    """Live issue ids for ``project_key``, excluding IT artifacts."""
    issues = await search_issues_jql(datasource, f'project = "{project_key}"', ["summary"])
    return {str(it["id"]) for it in issues if it.get("id") and not is_jira_it_artifact(it)}


def is_jira_it_artifact(issue: dict[str, Any]) -> bool:
    """True if a search-result issue was created by an integration test."""
    return JIRA_IT_ARTIFACT_PREFIX in ((issue.get("fields") or {}).get("summary") or "")


# =============================================================================
# Single-issue lookups (assertion helpers)
# =============================================================================


async def get_jira_issue_updated_ms(
    datasource: JiraDataSource, issue_key: str
) -> int:
    """Return ``fields.updated`` as epoch milliseconds. Matches ``external_revision_id``."""
    resp = await jira_api_call_with_retry(
        datasource.get_issue, issueIdOrKey=issue_key, fields="updated",
        context=f"get_jira_issue_updated_ms({issue_key})",
    )
    if resp.status != 200:
        _raise_on_auth_error(resp.status, "get_jira_issue_updated_ms")
        raise AssertionError(
            f"get_jira_issue_updated_ms failed for issue_key={issue_key!r}: HTTP {resp.status}"
        )
    fields = (resp.json() or {}).get("fields") or {}
    raw = fields.get("updated")
    if not raw:
        raise AssertionError(
            f"get_jira_issue_updated_ms: issue {issue_key!r} missing fields.updated"
        )
    # Jira returns an ISO-8601 string (e.g. "2024-01-15T10:30:45.123+0000").
    # Convert to epoch ms via a tolerant parser — the connector uses the same
    # epoch-ms representation in ``external_revision_id``.
    return parse_jira_timestamp(raw)


def parse_jira_timestamp(timestamp_str: str | None) -> int:
    """Parse a Jira ISO-8601 timestamp to epoch milliseconds.

    Handles ``Z`` suffix, ``+0000`` (no colon) offsets, and multiple strptime
    fallbacks. Returns 0 for None/empty/unparseable input.
    """
    if not timestamp_str:
        return 0
    normalized = timestamp_str.replace("Z", "+00:00")
    normalized = re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", normalized)
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        normalized_strptime = re.sub(r"([+-])(\d{2}):(\d{2})$", r"\1\2\3", normalized)
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                dt = datetime.strptime(normalized_strptime, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                continue
    return 0


# =============================================================================
# Polling helpers (bool variants — re-raise on auth errors)
# =============================================================================


async def wait_until_jira_condition(
    check_fn: Callable[[], Awaitable[bool]],
    *,
    timeout: int = JIRA_TEST_SETTLE_WAIT_SEC,
    poll_interval: int = 15,
    description: str = "Jira API condition",
) -> None:
    """Poll ``check_fn`` until truthy or ``timeout`` elapses.

    Auth-class errors (raised as ``JiraAuthError``) propagate immediately so
    bad credentials fail fast instead of looping for the full timeout.
    """
    start = time.time()
    deadline = start + timeout
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        try:
            if await check_fn():
                logger.info(
                    "✅ %s (attempt %d, %.1fs elapsed)",
                    description, attempt, time.time() - start,
                )
                return
        except JiraAuthError:
            # Don't swallow auth errors — they will never resolve by waiting.
            raise
        except Exception as e:
            logger.warning(
                "⏳ Check failed for %s (attempt %d): %s",
                description, attempt, e,
            )

        remaining = deadline - time.time()
        if remaining <= 0:
            break
        sleep_time = min(poll_interval, remaining)
        logger.info(
            "⏳ Waiting for %s (attempt %d, %.0fs remaining, sleeping %ds)...",
            description, attempt, remaining, sleep_time,
        )
        await asyncio.sleep(sleep_time)

    raise TimeoutError(
        f"Timed out waiting for {description} after {timeout}s ({attempt} attempts)"
    )


async def check_issue_exists_bool(
    datasource: JiraDataSource, issue_key: str
) -> bool:
    """True if the issue is fetchable via ``get_issue`` (direct lookup, not JQL search).

    Atlassian's enhanced JQL endpoint (``/rest/api/3/search/jql``) has high
    indexing latency on fresh projects — sometimes 10+ minutes after issue
    creation. ``GET /rest/api/3/issue/{key}`` does not depend on the search
    index and resolves immediately, so prefer this for "is the issue created
    in Jira yet" polling.
    """
    try:
        resp = await datasource.get_issue(issueIdOrKey=issue_key, fields="summary")
    except Exception:
        return False
    if resp is None:
        return False
    if resp.status in (401, 403):
        _raise_on_auth_error(resp.status, "check_issue_exists_bool")
    return resp.status == 200


# Terminal indexing statuses (pipeline will not advance past these).
_RECORD_INDEXING_TERMINAL: frozenset[str] = frozenset(
    {
        ProgressStatus.COMPLETED.value,
        ProgressStatus.FAILED.value,
        ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
        ProgressStatus.EMPTY.value,
        ProgressStatus.AUTO_INDEX_OFF.value,
        ProgressStatus.ENABLE_MULTIMODAL_MODELS.value,
    }
)


async def wait_until_record_indexing_completed(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    external_record_id: str,
    *,
    timeout: int = JIRA_INDEXING_WAIT_SEC,
    poll_interval: int = 5,
    description: str = "record indexing COMPLETED",
    pipeshub_client: Any | None = None,
) -> Record:
    """Poll the graph until the connector record reaches ``indexingStatus == COMPLETED``.

    Reads ``Record.indexing_status`` via :meth:`GraphProviderProtocol.get_record_by_external_id`.
    Requires a working indexing stack and models configured on the backend so the
    pipeline can reach ``COMPLETED``.

    If ``pipeshub_client`` is set and the record hits ``AUTO_INDEX_OFF`` once, calls
    ``POST .../reindex`` for the graph record's internal ``id`` (same as Confluence ITs)
    and continues polling so auto-index can run again.

    Raises:
        AssertionError: If a terminal non-COMPLETED status is observed.
        TimeoutError: If COMPLETED is not reached within ``timeout`` seconds.
    """
    start = time.time()
    deadline = start + timeout
    attempt = 0
    last_status: str | None = None
    reindexed_after_auto_index_off = False

    while time.time() < deadline:
        attempt += 1
        rec = await graph_provider.get_record_by_external_id(connector_id, external_record_id)
        if rec is not None:
            last_status = rec.indexing_status
            if last_status == ProgressStatus.COMPLETED.value:
                logger.info(
                    "✅ %s COMPLETED (attempt %d, %.1fs)",
                    description, attempt, time.time() - start,
                )
                return rec
            if last_status in _RECORD_INDEXING_TERMINAL:
                if (
                    last_status == ProgressStatus.AUTO_INDEX_OFF.value
                    and pipeshub_client is not None
                    and not reindexed_after_auto_index_off
                ):
                    logger.info("🔄 %s — AUTO_INDEX_OFF, triggering reindex", description)
                    pipeshub_client.reindex_record(rec.id)
                    reindexed_after_auto_index_off = True
                    await asyncio.sleep(8)
                    continue
                raise AssertionError(
                    f"{description}: record {external_record_id!r} reached terminal "
                    f"indexingStatus={last_status!r} (expected COMPLETED)"
                )
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        sleep_time = min(poll_interval, remaining)
        logger.info(
            "⏳ %s — status=%s (attempt %d, %.0fs left)",
            description, last_status or "pending", attempt, remaining,
        )
        await asyncio.sleep(sleep_time)

    raise TimeoutError(
        f"Timed out waiting for {description} on externalRecordId={external_record_id!r} "
        f"after {timeout}s (last indexingStatus={last_status!r}, attempts={attempt})"
    )


# =============================================================================
# Idempotency-aware Jira write/read retry (mirrors Linear _api_call_with_retry,
# but HTTP-status based and split by write idempotency)
# =============================================================================

_JIRA_TRANSIENT_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


async def jira_api_call_with_retry(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    context: str,
    retry_server_errors: bool = True,
    max_retries: int = 4,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> Any:
    """Call a ``JiraDataSource`` method with idempotency-aware retry.

    - HTTP 429 is always retried (rate-limited → request rejected before execution, safe).
      The wait honours Jira's ``Retry-After`` when present (see ``retry_delay_seconds``):
      every CI leg and every PR shares this site, so under parallel runs a 429 is the
      expected failure mode, and backing off for less than Jira asked just burns retries.
    - ``retry_server_errors=True`` (reads / ``edit`` / ``delete`` / restore — idempotent):
      also retry 5xx responses and transport/timeout exceptions, then return the last
      response (caller asserts the status).
    - ``retry_server_errors=False`` (``create_issue`` — non-idempotent): retry 429 only;
      a 5xx response is returned as-is so the caller's status assertion fails, and a
      transport/timeout exception is re-raised immediately — never silently recreates a
      possibly-created ticket (a lost 201 must not become a duplicate).

    Auth-class 401/403 propagate immediately as ``JiraAuthError``.
    """
    last_resp: Any = None
    for attempt in range(max_retries + 1):
        try:
            resp = await fn(*args, **kwargs)
        except Exception as e:
            if retry_server_errors and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "%s: transport error (attempt %d/%d), retrying in %.1fs: %s",
                    context, attempt + 1, max_retries + 1, delay, e,
                )
                await asyncio.sleep(delay)
                continue
            raise
        status = getattr(resp, "status", None)
        if status in (401, 403):
            _raise_on_auth_error(status, context)
        if status is None or status < 400:
            return resp
        retryable = status == 429 or (retry_server_errors and status in _JIRA_TRANSIENT_STATUS)
        if not retryable or attempt == max_retries:
            return resp
        last_resp = resp
        delay = retry_delay_seconds(resp, attempt, base_delay=base_delay)
        logger.warning(
            "%s: HTTP %s (attempt %d/%d), retrying in %.1fs",
            context, status, attempt + 1, max_retries + 1, delay,
        )
        await asyncio.sleep(delay)
    return last_resp


# Never wait longer than this on a single retry, whatever Retry-After says: a runaway
# value would stall the leg past its timeout instead of failing the one call.
JIRA_RETRY_MAX_DELAY_SEC = 60.0


def retry_delay_seconds(resp: Any, attempt: int, *, base_delay: float = 2.0) -> float:
    """Seconds to sleep before retrying ``resp``: exponential backoff, floored by ``Retry-After``.

    Jira Cloud sends ``Retry-After`` in seconds on 429. Waiting less than that is pointless —
    the next attempt is rejected the same way — so the header is a floor, not a replacement,
    and the result is capped at ``JIRA_RETRY_MAX_DELAY_SEC``. A missing or malformed header
    falls back to plain backoff.
    """
    delay = base_delay * (2 ** attempt)
    headers = getattr(resp, "headers", None) or {}
    raw = None
    if isinstance(headers, dict):
        raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is not None:
        try:
            delay = max(delay, float(raw))
        except (TypeError, ValueError):
            pass
    return min(delay, JIRA_RETRY_MAX_DELAY_SEC)


# =============================================================================
# Read-only discovery helpers (pre-provisioned project shapes)
# =============================================================================


async def search_issues_jql(
    datasource: JiraDataSource,
    jql: str,
    fields: list[str],
    *,
    page_size: int = 100,
    max_pages: int = 100,
) -> list[dict[str, Any]]:
    """Page issues matching ``jql`` (enhanced ``/search/jql`` endpoint), returning full issue dicts."""
    out: list[dict[str, Any]] = []
    next_token: Optional[str] = None
    for _ in range(max_pages):
        resp = await jira_api_call_with_retry(
            datasource.search_and_reconsile_issues_using_jql_post,
            jql=jql, maxResults=page_size, fields=fields, nextPageToken=next_token,
            context=f"JQL search {jql!r}",
        )
        if resp.status != 200:
            _raise_on_auth_error(resp.status, "search_issues_jql")
            raise RuntimeError(f"JQL search failed ({jql!r}): HTTP {resp.status}")
        data = resp.json() or {}
        issues = data.get("issues") or []
        out.extend(issues)
        next_token = data.get("nextPageToken")
        if data.get("isLast") or not next_token or not issues:
            break
    return out


async def issue_exists_in_project(
    datasource: JiraDataSource, issue_key: str, project_key: str
) -> bool:
    """True if ``issue_key`` exists and belongs to ``project_key``."""
    resp = await jira_api_call_with_retry(
        datasource.get_issue, issueIdOrKey=issue_key, fields="project",
        context=f"issue_exists_in_project({issue_key})",
    )
    if resp.status in (401, 403):
        _raise_on_auth_error(resp.status, "issue_exists_in_project")
    if resp.status != 200:
        return False
    proj = ((resp.json() or {}).get("fields") or {}).get("project") or {}
    return str(proj.get("key")) == str(project_key)


async def fetch_ancestor_chain(
    datasource: JiraDataSource,
    issue_id_or_key: str,
    *,
    max_depth: int = PLACEHOLDER_SWEEP_MAX_DEPTH,
) -> list[dict[str, Any]]:
    """Return ancestor issues for ``issue_id_or_key``, nearest parent first.

    Walked one hop at a time via ``fields.parent``. Returns full issue payloads
    (not ids) so callers can read ``created`` without a second round-trip per
    ancestor. ``max_depth`` mirrors the connector's own sweep cap so the walk
    cannot claim ancestors the sweep would never reach.
    """
    chain: list[dict[str, Any]] = []
    seen: set[str] = {str(issue_id_or_key)}

    resp = await jira_api_call_with_retry(
        datasource.get_issue,
        issueIdOrKey=issue_id_or_key,
        fields="parent,created,updated,summary",
        context=f"fetch_ancestor_chain({issue_id_or_key})",
    )
    if resp.status != 200:
        return chain
    current = resp.json() or {}
    if current.get("id"):
        seen.add(str(current["id"]))
    if current.get("key"):
        seen.add(str(current["key"]))

    for _ in range(max_depth):
        parent = ((current.get("fields") or {}).get("parent")) or {}
        parent_id = parent.get("id")
        parent_key = parent.get("key")
        if not parent_id:
            break
        parent_token = str(parent_id)
        if parent_token in seen or (parent_key and str(parent_key) in seen):
            break
        seen.add(parent_token)
        if parent_key:
            seen.add(str(parent_key))

        resp = await jira_api_call_with_retry(
            datasource.get_issue,
            issueIdOrKey=parent_token,
            fields="parent,created,updated,summary",
            context=f"fetch_ancestor_chain({parent_token})",
        )
        if resp.status != 200:
            break
        current = resp.json() or {}
        if not current.get("id"):
            break
        chain.append(current)

    return chain


async def discover_epic_and_child(
    datasource: JiraDataSource, project_key: str
) -> Optional[tuple[str, str, str, str]]:
    """Find an Epic (hierarchyLevel 1) with a level-0 child under it.

    Returns ``(epic_key, epic_id, child_key, child_id)`` or None.
    """
    issues = await search_issues_jql(
        datasource, f'project = "{project_key}"', ["issuetype", "parent"],
    )
    epic_id_to_key: dict[str, str] = {}
    for it in issues:
        f = it.get("fields") or {}
        if (f.get("issuetype") or {}).get("hierarchyLevel") == 1:
            epic_id_to_key[str(it.get("id"))] = it.get("key")
    if not epic_id_to_key:
        return None
    for it in issues:
        f = it.get("fields") or {}
        parent = f.get("parent") or {}
        pid = str(parent.get("id")) if parent.get("id") else None
        if pid and pid in epic_id_to_key and (f.get("issuetype") or {}).get("hierarchyLevel") == 0:
            return (epic_id_to_key[pid], pid, it.get("key"), str(it.get("id")))
    return None


async def discover_task_and_subtask(
    datasource: JiraDataSource, project_key: str
) -> Optional[tuple[str, str, str, str]]:
    """Find a sub-task (hierarchyLevel -1) and its parent task.

    Returns ``(parent_key, parent_id, subtask_key, subtask_id)`` or None.
    """
    issues = await search_issues_jql(
        datasource, f'project = "{project_key}"', ["issuetype", "parent"],
    )
    for it in issues:
        f = it.get("fields") or {}
        if (f.get("issuetype") or {}).get("hierarchyLevel") == -1:
            parent = f.get("parent") or {}
            if parent.get("id"):
                return (parent.get("key"), str(parent.get("id")), it.get("key"), str(it.get("id")))
    return None


def _wiki_attachment_filenames(text: str) -> set[str]:
    """Filenames from Jira wiki ``!file.ext|...!`` markup (mirrors connector helper)."""
    filenames: set[str] = set()
    for match in re.finditer(r"!([^!]+)!", text):
        filename_part = match.group(1).split("|", 1)[0].strip()
        if filename_part:
            filenames.add(filename_part.lower())
    return filenames


def _adf_media_filenames(node: Any) -> set[str]:
    """ADF ``media`` / ``mediaInline`` ``alt`` filenames (+ nested wiki refs)."""
    filenames: set[str] = set()
    if isinstance(node, dict):
        if node.get("type") in ("media", "mediaInline"):
            alt = (node.get("attrs") or {}).get("alt")
            if isinstance(alt, str) and alt.strip():
                filenames.add(alt.strip().lower())
        for value in node.values():
            if isinstance(value, (dict, list)):
                filenames |= _adf_media_filenames(value)
            elif isinstance(value, str):
                filenames |= _wiki_attachment_filenames(value)
    elif isinstance(node, list):
        for item in node:
            filenames |= _adf_media_filenames(item)
    return filenames


def _attachment_ids_from_strings(node: Any) -> set[str]:
    """Attachment ids embedded in HTML/URL strings inside ADF or free text."""
    from app.connectors.sources.atlassian.core.html_utils import (  # type: ignore[import-not-found]
        extract_attachment_ids,
    )

    ids: set[str] = set()
    if isinstance(node, dict):
        for value in node.values():
            if isinstance(value, str):
                ids |= extract_attachment_ids(value)
            elif isinstance(value, (dict, list)):
                ids |= _attachment_ids_from_strings(value)
    elif isinstance(node, list):
        for item in node:
            ids |= _attachment_ids_from_strings(item)
    return ids


def _inline_refs_from_body(body: Any) -> tuple[set[str], set[str]]:
    """Return ``(filenames_lower, attachment_ids)`` referenced as inline media in a body."""
    from app.connectors.sources.atlassian.core.html_utils import (  # type: ignore[import-not-found]
        extract_attachment_ids,
    )

    filenames: set[str] = set()
    attachment_ids: set[str] = set()
    if isinstance(body, dict):
        filenames |= _adf_media_filenames(body)
        attachment_ids |= _attachment_ids_from_strings(body)
    elif isinstance(body, str):
        filenames |= _wiki_attachment_filenames(body)
        attachment_ids |= extract_attachment_ids(body)
    return filenames, attachment_ids


def resolve_inline_image_attachment_ids(issue_fields: dict[str, Any]) -> set[str]:
    """Image attachment ids embedded in description/comment (sync-path parity).

    Mirrors ``JiraCloudConnector._resolve_inline_image_attachment_ids``. Comment bodies
    are only considered when ``comment`` is present on ``issue_fields`` (issue search
    normally omits it).
    """
    filenames: set[str] = set()
    referenced_ids: set[str] = set()

    desc_names, desc_ids = _inline_refs_from_body(issue_fields.get("description"))
    filenames |= desc_names
    referenced_ids |= desc_ids

    comment_field = issue_fields.get("comment")
    if isinstance(comment_field, dict):
        for comment in comment_field.get("comments") or []:
            if not isinstance(comment, dict):
                continue
            c_names, c_ids = _inline_refs_from_body(comment.get("body"))
            filenames |= c_names
            referenced_ids |= c_ids

    if not filenames and not referenced_ids:
        return set()

    inline_ids: set[str] = set()
    for attachment in issue_fields.get("attachment") or []:
        if not isinstance(attachment, dict):
            continue
        attachment_id = attachment.get("id")
        if attachment_id is None:
            continue
        mime = (attachment.get("mimeType") or "").lower()
        if not mime.startswith("image/"):
            continue
        filename = (attachment.get("filename") or "").strip().lower()
        att_id = str(attachment_id)
        if att_id in referenced_ids or (filename and filename in filenames):
            inline_ids.add(att_id)
    return inline_ids


def count_synced_file_attachments(issue_fields: dict[str, Any]) -> int:
    """Attachments that become FILE records on a fresh sync (excludes new inline images)."""
    attachments = issue_fields.get("attachment") or []
    if not attachments:
        return 0
    inline_ids = resolve_inline_image_attachment_ids(issue_fields)
    count = 0
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        attachment_id = attachment.get("id")
        if attachment_id is None:
            continue
        if str(attachment_id) in inline_ids:
            continue
        count += 1
    return count


def first_synced_file_attachment(
    issue_fields: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """First attachment that would be created as a FILE on a fresh sync, or None."""
    attachments = issue_fields.get("attachment") or []
    if not attachments:
        return None
    inline_ids = resolve_inline_image_attachment_ids(issue_fields)
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        attachment_id = attachment.get("id")
        if attachment_id is None:
            continue
        if str(attachment_id) in inline_ids:
            continue
        return attachment
    return None


async def discover_attachment(
    datasource: JiraDataSource, project_key: str
) -> Optional[tuple[str, str, dict[str, Any]]]:
    """Find an attachment that syncs as a FILE (skips new inline images).

    Returns ``(issue_key, issue_id, attachment)`` or None when the project only has
    inline-image attachments (no FileRecord created on fresh sync).
    """
    issues = await search_issues_jql(
        datasource,
        f'project = "{project_key}"',
        ["attachment", "description"],
    )
    for it in issues:
        fields = it.get("fields") or {}
        attachment = first_synced_file_attachment(fields)
        if attachment is not None:
            return (it.get("key"), str(it.get("id")), attachment)
    return None


async def derive_jira_scope_counts(
    datasource: JiraDataSource, project_key: str
) -> dict[str, int]:
    """Single enumeration of a project's issues → independent expected counts (live Jira, not graph).

    Mirrors the connector's sync-path record creation:
      - ``ticket``: one TICKET record per issue.
      - ``file``: one FILE record per attachment that is *not* a new inline image
        (``_fetch_issue_attachments`` skips image attachments referenced in description;
        comment bodies are not on the issue-search payload, same as production sync).
      - ``parent_child``: one PARENT_CHILD edge per issue with a ``fields.parent`` (sub-task /
        epic child; attachments use ATTACHMENT, not PARENT_CHILD).

    IT artifacts are skipped: this project is shared with any concurrently running leg,
    whose in-flight tickets would otherwise land in the baseline.
    """
    issues = await search_issues_jql(
        datasource,
        f'project = "{project_key}"',
        ["parent", "attachment", "description", "summary"],
    )
    ticket = 0
    files = 0
    parent_child = 0
    for it in issues:
        if is_jira_it_artifact(it):
            continue
        ticket += 1
        f = it.get("fields") or {}
        if f.get("parent"):
            parent_child += 1
        files += count_synced_file_attachments(f)
    return {"ticket": ticket, "file": files, "parent_child": parent_child}


# =============================================================================
# Artifact ownership — this run's tickets, and leftovers from crashed runs
# =============================================================================
#
# Every leg, every PR and the nightly cron share one Jira site. The mutation tests are
# self-cleaning, but a cancelled CI run (``cancel-in-progress``) SIGTERMs pytest before
# any ``finally`` runs, and a failed delete is logged rather than raised — so leaks are
# inevitable and something has to reap them. Ownership is encoded in the summary
# (``PHIT-<run_id>-...``, see ``constants.artifact_summary``); the registry tracks what
# this process created so its own teardown can delete exactly that, and the sweep
# reaps anything old enough that no live run can still own it.


class JiraArtifactRegistry:
    """Issue ids this run created and has not yet confirmed deleted."""

    def __init__(self) -> None:
        self._issues: dict[str, str] = {}

    def register(self, issue_id: str, issue_key: str) -> None:
        self._issues[str(issue_id)] = issue_key

    def release(self, issue_id: str) -> None:
        self._issues.pop(str(issue_id), None)

    def drain(self) -> list[tuple[str, str]]:
        """Return and forget every outstanding ``(issue_id, issue_key)``."""
        items = list(self._issues.items())
        self._issues.clear()
        return items

    def __len__(self) -> int:
        return len(self._issues)


jira_artifacts = JiraArtifactRegistry()

# The exact shape ``constants.artifact_summary`` produces. Ownership for deletion is decided
# by THIS, not by the ``PHIT-`` marker alone: the marker also sits on legacy artifacts
# (``PHIT-IncrTest-<hex>``) and, historically, on a frozen ticket that an old test renamed
# in place. Nothing but the current suite ever produces the run-id form.
ARTIFACT_SUMMARY_RE = re.compile(
    rf"^{re.escape(JIRA_IT_ARTIFACT_PREFIX)}[0-9a-f]{{8}}-[A-Za-z]+-[0-9a-f]{{8}}$"
)


def is_run_artifact_summary(summary: str) -> bool:
    """True only for summaries in the current ``PHIT-<run_id>-<Kind>-<hex>`` form."""
    return bool(ARTIFACT_SUMMARY_RE.fullmatch((summary or "").strip()))


def can_delete_issues_in(datasource: JiraDataSource, project_key: str) -> Awaitable[bool]:
    """Whether the IT account holds ``DELETE_ISSUES`` in ``project_key``.

    A mutation test must be able to delete what it creates; ``CREATE_ISSUES`` alone leaves
    permanent leaks (and the sweep cannot remove them either).
    """

    async def _probe() -> bool:
        resp = await jira_api_call_with_retry(
            datasource.get_my_permissions,
            projectKey=project_key, permissions="DELETE_ISSUES",
            context=f"mypermissions {project_key}",
        )
        if resp.status != 200:
            return False
        perms = (resp.json() or {}).get("permissions") or {}
        return bool((perms.get("DELETE_ISSUES") or {}).get("havePermission"))

    return _probe()


def pick_mutation_project(
    project_keys: list[str],
    issue_type_by_key: dict[str, Optional[str]],
    can_delete_by_key: dict[str, bool],
) -> tuple[str, str]:
    """Choose where the mutation tests create tickets: ``(project_key, issue_type)``.

    Prefer the *secondary* IT project so the primary — the source of every baseline and
    live-vs-graph reconciliation — stays read-only for the whole suite. A project qualifies
    only if the account can both create *and delete* there. Falls back to the primary
    (artifact exclusion still protects its baselines) when no secondary qualifies.
    """
    for key in project_keys[1:] + project_keys[:1]:
        issue_type = issue_type_by_key.get(key)
        if issue_type and can_delete_by_key.get(key):
            return key, issue_type
    return project_keys[0], issue_type_by_key.get(project_keys[0]) or "Task"


# How far back a teardown looks for its own run's tickets. A leg is ~15 min; a day is
# generous and keeps the candidate page small.
OWN_RUN_LOOKBACK_MIN = 24 * 60


def stale_artifact_jql(
    project_keys: list[str],
    *,
    min_age_sec: float = JIRA_IT_STALE_ARTIFACT_AGE_SEC,
    only_run_id: Optional[str] = None,
) -> str:
    """JQL selecting *candidate* tickets for the artifact sweep — by ``created`` only.

    Deliberately no ``summary ~`` clause: Jira's text index lags its structured index by
    minutes for fresh issues (verified: ``key = X`` and ``created >= ...`` return a
    seconds-old ticket while ``summary ~`` returns nothing), so a text clause would hide
    exactly the tickets a teardown is trying to reap. Ownership is decided in Python from
    the full summary (``is_run_artifact_summary``) over this small, reliable candidate set.

    Without ``only_run_id``: everything in the IT projects created at least ``min_age_sec``
    ago — the age gate keeps this safe under concurrency, a younger ticket may belong to a
    run still asserting on it. With ``only_run_id``: everything created in the last
    ``OWN_RUN_LOOKBACK_MIN`` minutes (a teardown reaping its own, regardless of age).
    ``created`` is immutable so neither cut drifts; JQL relative dates are minute-granular,
    so the age is rounded *up* — never reaps anything younger than asked.
    """
    projects = ", ".join(f'"{k}"' for k in project_keys)
    if only_run_id:
        window = f'created >= "-{OWN_RUN_LOOKBACK_MIN}m"'
    else:
        minutes = max(1, -(-int(min_age_sec) // 60))
        window = f'created <= "-{minutes}m"'
    return f"project in ({projects}) AND {window} ORDER BY created ASC"


async def sweep_stale_jira_artifacts(
    datasource: JiraDataSource,
    project_keys: list[str],
    *,
    min_age_sec: float = JIRA_IT_STALE_ARTIFACT_AGE_SEC,
    only_run_id: Optional[str] = None,
) -> int:
    """Delete leaked IT tickets; return how many were deleted.

    Deletion requires ALL of: the summary is in the exact run-id artifact form
    (``is_run_artifact_summary``), it starts with the requested stem, and the key is not a
    frozen fixture. The JQL only narrows candidates by ``created`` (see
    ``stale_artifact_jql`` for why not by summary); the ``PHIT-`` marker alone is never
    proof of ownership (see ``ARTIFACT_SUMMARY_RE``). A 404 on delete means another run's
    sweep got there first and is not an error. Any other failure is logged and skipped:
    reaping is best-effort hygiene and must never fail the run that performs it.
    """
    if not project_keys:
        # Nothing the account can delete in — nothing to scan, nothing to say.
        return 0
    stem = f"{JIRA_IT_ARTIFACT_PREFIX}{only_run_id}-" if only_run_id else JIRA_IT_ARTIFACT_PREFIX
    jql = stale_artifact_jql(project_keys, min_age_sec=min_age_sec, only_run_id=only_run_id)
    try:
        candidates = await search_issues_jql(datasource, jql, ["summary", "created"])
    except Exception as e:
        logger.warning("SWEEP: artifact search failed (%s) — skipping: %s", jql, e)
        return 0

    deleted = 0
    for issue in candidates:
        summary = (issue.get("fields") or {}).get("summary") or ""
        issue_id = str(issue.get("id") or "")
        issue_key = str(issue.get("key") or "")
        if not issue_id or not summary.startswith(stem) or not is_run_artifact_summary(summary):
            continue
        if issue_key in JIRA_FROZEN_ISSUE_KEYS:
            logger.error(
                "SWEEP: refusing to delete frozen fixture %s even though its summary is %r "
                "— restore its real summary", issue_key, summary,
            )
            continue
        try:
            resp = await jira_api_call_with_retry(
                datasource.delete_issue, issueIdOrKey=issue_id,
                context=f"SWEEP delete {issue.get('key')}", retry_server_errors=True,
            )
        except JiraAuthError:
            # No delete right on this project (callers pre-filter, but permissions can
            # change under us). Not actionable from a test run — say it once, quietly.
            logger.debug("SWEEP: no permission to delete %s — skipping", issue.get("key"))
            continue
        except Exception as e:
            logger.warning("SWEEP: delete %s (%s) failed: %s", issue.get("key"), summary, e)
            continue
        status = getattr(resp, "status", 204)
        if status in (200, 202, 204, 404):
            logger.warning(
                "SWEEP: deleted leaked IT artifact %s (%s, created %s)",
                issue.get("key"), summary, (issue.get("fields") or {}).get("created"),
            )
            deleted += 1
        else:
            logger.warning("SWEEP: delete %s returned HTTP %s", issue.get("key"), status)
    return deleted


async def reap_own_artifacts(
    datasource: JiraDataSource, project_keys: list[str],
) -> int:
    """Teardown hygiene: delete everything this run still owns.

    Registry first (ids we know), then a summary sweep on this run id — the sweep also
    catches a ticket whose ``create_issue`` returned 201 after the registry line was never
    reached (interrupt between the two).
    """
    deleted = 0
    for issue_id, issue_key in jira_artifacts.drain():
        try:
            resp = await jira_api_call_with_retry(
                datasource.delete_issue, issueIdOrKey=issue_id,
                context=f"teardown delete {issue_key}", retry_server_errors=True,
            )
            if getattr(resp, "status", 204) in (200, 202, 204, 404):
                deleted += 1
        except Exception as e:
            logger.warning("TEARDOWN: delete %s failed: %s", issue_key, e)
    deleted += await sweep_stale_jira_artifacts(
        datasource, project_keys, only_run_id=JIRA_IT_RUN_ID,
    )
    return deleted

