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
from app.models.entities import Record  # type: ignore[import-not-found]
from app.sources.external.jira.jira import (
    JiraDataSource,  # type: ignore[import-not-found]
)
from connectors.jira.constants import (  # type: ignore[import-not-found]
    JIRA_INDEXING_WAIT_SEC,
    JIRA_TEST_SETTLE_WAIT_SEC,
)
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]

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
        resp = await datasource.get_all_users(
            query="",
            startAt=start_at,
            maxResults=page_size,
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
        resp = await datasource.bulk_get_groups(
            startAt=start_at,
            maxResults=_JIRA_GROUP_PAGE,
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

    ``_sync_user_groups`` skips any bulk group missing ``groupId`` or ``name``
    (connector.py: ``if not group_id or not group_name: continue``), so mirror that filter —
    otherwise a site group with incomplete metadata (e.g. some team-managed access groups)
    makes the graph count trail the raw bulk count by one.
    """
    groups = await _jira_fetch_all_groups(datasource)
    return sum(1 for g in groups if g.get("groupId") and g.get("name"))


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
        resp = await datasource.get_users_from_group(
            groupname=group_name,
            includeInactiveUsers=False,
            startAt=start_at,
            maxResults=_JIRA_GROUP_MEMBER_PAGE,
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

    roles_resp = await datasource.get_project_roles(projectIdOrKey=project_key)
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
        rresp = await datasource.get_project_role(
            projectIdOrKey=project_key,
            id=role_id,
            excludeInactiveUsers=True,
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

    roles_resp = await datasource.get_project_roles(projectIdOrKey=project_key)
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
        rresp = await datasource.get_project_role(
            projectIdOrKey=project_key, id=role_id, excludeInactiveUsers=True,
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
    resp = await datasource.get_all_application_roles()
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

    scheme_resp = await datasource.get_assigned_permission_scheme(
        projectKeyOrId=project_key,
        expand="all",
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

    grants_resp = await datasource.get_permission_scheme_grants(
        schemeId=int(scheme_id),
        expand="all",
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


async def count_jira_project_issues_via_jql(
    datasource: JiraDataSource, project_key: str
) -> int:
    """Count issues in ``project_key`` via JQL (paginated).

    Uses the enhanced ``/rest/api/3/search/jql`` endpoint. The legacy
    ``/rest/api/3/search`` endpoint was retired by Atlassian in May 2025 and now
    returns HTTP 410 Gone. The new endpoint uses cursor-based pagination
    (``nextPageToken`` / ``isLast``) rather than ``startAt`` / ``total``.
    """
    jql = f'project = "{project_key}"'
    total_seen = 0
    next_token: Optional[str] = None
    page_size = 100

    while True:
        resp = await datasource.search_and_reconsile_issues_using_jql_post(
            jql=jql,
            maxResults=page_size,
            fields=["summary"],
            nextPageToken=next_token,
        )
        if resp.status != 200:
            _raise_on_auth_error(resp.status, "count_jira_project_issues_via_jql")
            raise RuntimeError(
                f"Jira JQL search failed for project={project_key!r}: HTTP {resp.status}"
            )
        data = resp.json() or {}
        issues = data.get("issues") or []
        total_seen += len(issues)
        next_token = data.get("nextPageToken")
        # New endpoint signals end-of-page via ``isLast`` or absence of ``nextPageToken``.
        if data.get("isLast") or not next_token:
            return total_seen
        if not issues:
            return total_seen


async def assert_jira_issues_match_graph_records(
    datasource: JiraDataSource,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    project_key: str,
    *,
    phase: str,
) -> None:
    """Assert JQL issue count for the project equals graph TICKET-record count for the connector."""
    api_count = await count_jira_project_issues_via_jql(datasource, project_key)
    graph_ticket_count = await graph_provider.count_records_by_type(connector_id, "TICKET")
    if api_count != graph_ticket_count:
        raise AssertionError(
            f"{phase}: Jira JQL issue count ({api_count}) != "
            f"graph TICKET count ({graph_ticket_count}) for connector {connector_id} "
            f"project_key={project_key!r}"
        )


# =============================================================================
# Single-issue lookups (assertion helpers)
# =============================================================================


async def get_jira_issue_updated_ms(
    datasource: JiraDataSource, issue_key: str
) -> int:
    """Return ``fields.updated`` as epoch milliseconds. Matches ``external_revision_id``."""
    resp = await datasource.get_issue(issueIdOrKey=issue_key, fields="updated")
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
    max_retries: int = 3,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> Any:
    """Call a ``JiraDataSource`` method with idempotency-aware retry.

    - HTTP 429 is always retried (rate-limited → request rejected before execution, safe).
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
        delay = base_delay * (2 ** attempt)
        logger.warning(
            "%s: HTTP %s (attempt %d/%d), retrying in %.1fs",
            context, status, attempt + 1, max_retries + 1, delay,
        )
        await asyncio.sleep(delay)
    return last_resp


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
        resp = await datasource.search_and_reconsile_issues_using_jql_post(
            jql=jql, maxResults=page_size, fields=fields, nextPageToken=next_token,
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
    resp = await datasource.get_issue(issueIdOrKey=issue_key, fields="project")
    if resp.status in (401, 403):
        _raise_on_auth_error(resp.status, "issue_exists_in_project")
    if resp.status != 200:
        return False
    proj = ((resp.json() or {}).get("fields") or {}).get("project") or {}
    return str(proj.get("key")) == str(project_key)


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


async def discover_attachment(
    datasource: JiraDataSource, project_key: str
) -> Optional[tuple[str, str, dict[str, Any]]]:
    """Find the first issue with an attachment. Returns ``(issue_key, issue_id, attachment)`` or None."""
    issues = await search_issues_jql(
        datasource, f'project = "{project_key}"', ["attachment"],
    )
    for it in issues:
        atts = (it.get("fields") or {}).get("attachment") or []
        if atts:
            return (it.get("key"), str(it.get("id")), atts[0])
    return None


async def derive_jira_scope_counts(
    datasource: JiraDataSource, project_key: str
) -> dict[str, int]:
    """Single enumeration of a project's issues → independent expected counts (live Jira, not graph).

    Mirrors the connector's sync-path record creation:
      - ``ticket``: one TICKET record per issue.
      - ``file``: one FILE record per attachment. ``_fetch_issue_attachments`` creates a
        FileRecord for **every** ``fields.attachment`` entry — no inline-image filtering on the
        sync path (that only affects the streamed blocks) — so this is the exact FILE count.
      - ``parent_child``: one PARENT_CHILD edge per issue with a ``fields.parent`` (sub-task /
        epic child; attachments use ATTACHMENT, not PARENT_CHILD).
    """
    issues = await search_issues_jql(
        datasource, f'project = "{project_key}"', ["parent", "attachment"],
    )
    ticket = len(issues)
    files = 0
    parent_child = 0
    for it in issues:
        f = it.get("fields") or {}
        if f.get("parent"):
            parent_child += 1
        files += len(f.get("attachment") or [])
    return {"ticket": ticket, "file": files, "parent_child": parent_child}

