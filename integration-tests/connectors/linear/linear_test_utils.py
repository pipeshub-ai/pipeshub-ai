# pyright: ignore-file

"""
Linear GraphQL helpers for Linear connector integration tests.

Mirrors the Jira v1 helper pattern: polling helpers, issue/project counting,
timestamp parsing, and user-pool counting for graph reconciliation.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from app.config.constants.arangodb import ProgressStatus  # type: ignore[import-not-found]
from app.models.entities import Record  # type: ignore[import-not-found]
from app.sources.external.linear.linear import (
    LinearDataSource,  # type: ignore[import-not-found]
)
from connectors.linear.constants import (  # type: ignore[import-not-found]
    LINEAR_INDEXING_WAIT_SEC,
    LINEAR_TEST_SETTLE_WAIT_SEC,
)
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]

logger = logging.getLogger("linear-test-utils")


class LinearAuthError(RuntimeError):
    """Raised when a Linear polling check hits an auth failure — fail fast on bad creds."""


def _raise_on_auth_error(response: Any, context: str) -> None:
    if not response.success:
        msg = getattr(response, "message", "") or ""
        if "unauthorized" in msg.lower() or "forbidden" in msg.lower():
            raise LinearAuthError(
                f"{context}: Linear returned auth error: {msg}. "
                "Check LINEAR_TEST_API_TOKEN."
            )


_TRANSIENT_STATUS_CODES = {"429", "500", "502", "503", "504"}


def _is_transient_error(response: Any) -> bool:
    """Return True if the response looks like a transient Linear API failure."""
    msg = getattr(response, "message", "") or ""
    for code in _TRANSIENT_STATUS_CODES:
        if code in msg:
            return True
    return False


async def _api_call_with_retry(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    context: str,
    max_retries: int = 3,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> Any:
    """Call ``fn`` and retry on transient Linear API errors (503, 429, etc.)."""
    last_response = None
    for attempt in range(max_retries + 1):
        response = await fn(*args, **kwargs)
        if response.success:
            return response
        _raise_on_auth_error(response, context)
        last_response = response
        if not _is_transient_error(response) or attempt == max_retries:
            break
        delay = base_delay * (2 ** attempt)
        logger.warning(
            "%s: transient error (attempt %d/%d), retrying in %.1fs: %s",
            context, attempt + 1, max_retries + 1,
            delay, getattr(response, "message", ""),
        )
        await asyncio.sleep(delay)
    raise RuntimeError(f"{context} failed after {max_retries + 1} attempts: {getattr(last_response, 'message', '')}")


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def parse_linear_timestamp(timestamp_str: str | None) -> int:
    """Parse a Linear ISO-8601 timestamp to epoch milliseconds.

    Linear format: ``2025-01-01T12:00:00.000Z``.
    Returns 0 for None/empty/unparseable input.
    """
    if not timestamp_str:
        return 0
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        return 0


# ---------------------------------------------------------------------------
# Issue counting & discovery
# ---------------------------------------------------------------------------


async def fetch_linear_team_issue_ids(
    datasource: LinearDataSource,
    team_id: str,
    *,
    page_size: int = 100,
    max_pages: int = 200,
) -> set[str]:
    """Return all issue IDs belonging to ``team_id`` via paginated GraphQL query."""
    issue_ids: set[str] = set()
    cursor: Optional[str] = None

    for _ in range(max_pages):
        response = await _api_call_with_retry(
            datasource.issues,
            first=page_size, after=cursor,
            filter={"team": {"id": {"eq": team_id}}},
            context=f"fetch_linear_team_issue_ids({team_id})",
        )
        issues_data = response.data.get("issues", {}) if response.data else {}
        nodes = issues_data.get("nodes", [])
        for node in nodes:
            issue_id = node.get("id")
            if issue_id:
                issue_ids.add(issue_id)

        page_info = issues_data.get("pageInfo", {})
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            break
        cursor = page_info["endCursor"]

    return issue_ids


async def count_linear_team_issues(
    datasource: LinearDataSource,
    team_id: str,
    *,
    page_size: int = 100,
    max_pages: int = 200,
) -> int:
    """Count all issues belonging to ``team_id`` via paginated GraphQL query."""
    return len(
        await fetch_linear_team_issue_ids(
            datasource, team_id, page_size=page_size, max_pages=max_pages,
        )
    )


async def count_linear_team_projects(
    datasource: LinearDataSource,
    team_id: str,
    *,
    page_size: int = 50,
    max_pages: int = 100,
) -> int:
    """Count projects accessible to ``team_id``."""
    total = 0
    cursor: Optional[str] = None

    for _ in range(max_pages):
        response = await _api_call_with_retry(
            datasource.projects,
            first=page_size, after=cursor,
            filter={"accessibleTeams": {"some": {"id": {"eq": team_id}}}},
            context=f"count_linear_team_projects({team_id})",
        )
        data = response.data.get("projects", {}) if response.data else {}
        nodes = data.get("nodes", [])
        total += len(nodes)

        page_info = data.get("pageInfo", {})
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            break
        cursor = page_info["endCursor"]

    return total


async def fetch_first_issue_in_team(
    datasource: LinearDataSource,
    team_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the first issue (by updatedAt DESC) in the given team, or None."""
    response = await _api_call_with_retry(
        datasource.issues,
        first=1,
        filter={"team": {"id": {"eq": team_id}}},
        context=f"fetch_first_issue_in_team({team_id})",
    )
    nodes = (response.data or {}).get("issues", {}).get("nodes", [])
    return nodes[0] if nodes else None


# ---------------------------------------------------------------------------
# FILE extraction (mirrors LinearConnector._extract_file_urls_from_markdown)
# ---------------------------------------------------------------------------


def extract_file_urls_from_markdown(
    markdown_text: str,
    *,
    exclude_images: bool = True,
) -> List[Dict[str, str]]:
    """Extract ``uploads.linear.app`` file URLs from markdown (connector parity)."""
    if not markdown_text:
        return []

    file_urls: List[Dict[str, str]] = []
    seen_urls: Set[str] = set()

    image_urls: Set[str] = set()
    if exclude_images:
        image_pattern = r"!\[([^\]]*)\]\(([^)]+)\)"
        for match in re.finditer(image_pattern, markdown_text):
            url = match.group(2).strip()
            if "uploads.linear.app" in url:
                image_urls.add(url)

    link_pattern = r"\[([^\]]+)\]\(([^)]+)\)"
    for match in re.finditer(link_pattern, markdown_text):
        url = match.group(2).strip()
        if "uploads.linear.app" in url and url not in seen_urls:
            if exclude_images and url in image_urls:
                continue
            seen_urls.add(url)
            link_text = match.group(1) or ""
            filename = link_text or url.split("?")[0].split("/")[-1]
            file_urls.append({"url": url, "filename": filename, "alt_text": link_text})

    return file_urls


async def fetch_unique_projects_for_teams(
    datasource: LinearDataSource,
    team_ids: List[str],
    *,
    page_size: int = 50,
    max_pages: int = 100,
) -> List[Dict[str, Any]]:
    """Return deduplicated projects accessible to any of ``team_ids``."""
    seen: Set[str] = set()
    projects: List[Dict[str, Any]] = []

    for team_id in team_ids:
        cursor: Optional[str] = None
        for _ in range(max_pages):
            response = await _api_call_with_retry(
                datasource.projects,
                first=page_size, after=cursor,
                filter={"accessibleTeams": {"some": {"id": {"eq": team_id}}}},
                context=f"fetch_unique_projects_for_teams({team_id})",
            )
            data = (response.data or {}).get("projects", {})
            nodes = data.get("nodes", [])
            for node in nodes:
                pid = node.get("id")
                if pid and pid not in seen:
                    seen.add(pid)
                    projects.append(node)

            page_info = data.get("pageInfo", {})
            if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
                break
            cursor = page_info["endCursor"]

    return projects


async def _paginate_attachments(
    datasource: LinearDataSource,
    *,
    page_size: int = 100,
    max_pages: int = 200,
) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    cursor: Optional[str] = None

    for _ in range(max_pages):
        response = await _api_call_with_retry(
            datasource.attachments,
            first=page_size, after=cursor,
            context="_paginate_attachments",
        )
        data = (response.data or {}).get("attachments", {})
        batch = data.get("nodes", [])
        nodes.extend(batch)
        page_info = data.get("pageInfo", {})
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            break
        cursor = page_info["endCursor"]

    return nodes


async def _paginate_documents(
    datasource: LinearDataSource,
    *,
    page_size: int = 50,
    max_pages: int = 200,
) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    cursor: Optional[str] = None

    for _ in range(max_pages):
        response = await _api_call_with_retry(
            datasource.documents,
            first=page_size, after=cursor,
            context="_paginate_documents",
        )
        data = (response.data or {}).get("documents", {})
        batch = data.get("nodes", [])
        nodes.extend(batch)
        page_info = data.get("pageInfo", {})
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            break
        cursor = page_info["endCursor"]

    return nodes


async def _paginate_team_issues(
    datasource: LinearDataSource,
    team_id: str,
    *,
    page_size: int = 100,
    max_pages: int = 200,
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    cursor: Optional[str] = None

    for _ in range(max_pages):
        response = await _api_call_with_retry(
            datasource.issues,
            first=page_size, after=cursor,
            filter={"team": {"id": {"eq": team_id}}},
            context=f"_paginate_team_issues({team_id})",
        )
        data = (response.data or {}).get("issues", {})
        batch = data.get("nodes", [])
        issues.extend(batch)
        page_info = data.get("pageInfo", {})
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            break
        cursor = page_info["endCursor"]

    return issues


def _issue_team_id(issue_data: Dict[str, Any]) -> Optional[str]:
    team = issue_data.get("team") or {}
    team_id = team.get("id")
    return str(team_id) if team_id else None


async def count_linear_scope_links(
    datasource: LinearDataSource,
    team_ids: List[str],
) -> int:
    """Count LINK records the connector would create: issue attachments + project external links."""
    team_set = set(team_ids)
    link_ids: Set[str] = set()

    for attachment in await _paginate_attachments(datasource):
        issue = attachment.get("issue") or {}
        team = (issue.get("team") or {})
        if team.get("id") in team_set and attachment.get("id"):
            link_ids.add(str(attachment["id"]))

    for project in await fetch_unique_projects_for_teams(datasource, team_ids):
        project_id = project.get("id")
        if not project_id:
            continue
        resp = await datasource.project(id=project_id)
        if not resp.success:
            continue
        proj = (resp.data or {}).get("project") or {}
        for link in (proj.get("externalLinks") or {}).get("nodes", []):
            if link.get("id"):
                link_ids.add(str(link["id"]))

    return len(link_ids)


async def count_linear_scope_webpages(
    datasource: LinearDataSource,
    team_ids: List[str],
) -> int:
    """Count WEBPAGE records: issue-attached documents + project documents."""
    team_set = set(team_ids)
    document_ids: Set[str] = set()

    for document in await _paginate_documents(datasource):
        doc_id = document.get("id")
        if not doc_id:
            continue
        issue = document.get("issue")
        if issue:
            team = (issue.get("team") or {})
            if team.get("id") in team_set:
                document_ids.add(str(doc_id))

    for project in await fetch_unique_projects_for_teams(datasource, team_ids):
        project_id = project.get("id")
        if not project_id:
            continue
        resp = await datasource.project(id=project_id)
        if not resp.success:
            continue
        proj = (resp.data or {}).get("project") or {}
        for doc in (proj.get("documents") or {}).get("nodes", []):
            if doc.get("id"):
                document_ids.add(str(doc["id"]))

    return len(document_ids)


async def count_linear_scope_files(
    datasource: LinearDataSource,
    team_ids: List[str],
) -> int:
    """Count unique FILE URLs extracted from issue/project markdown (connector parity)."""
    file_urls: Set[str] = set()

    for team_id in team_ids:
        for issue in await _paginate_team_issues(datasource, team_id):
            description = issue.get("description") or ""
            for file_info in extract_file_urls_from_markdown(description, exclude_images=True):
                file_urls.add(file_info["url"])
            comments = (issue.get("comments") or {}).get("nodes", [])
            for comment in comments:
                body = comment.get("body") or ""
                for file_info in extract_file_urls_from_markdown(body, exclude_images=True):
                    file_urls.add(file_info["url"])

    for project in await fetch_unique_projects_for_teams(datasource, team_ids):
        project_id = project.get("id")
        if not project_id:
            continue
        resp = await datasource.project(id=project_id)
        if not resp.success:
            continue
        content = ((resp.data or {}).get("project") or {}).get("content") or ""
        for file_info in extract_file_urls_from_markdown(content, exclude_images=True):
            file_urls.add(file_info["url"])

    return len(file_urls)


async def fetch_first_attachment_in_teams(
    datasource: LinearDataSource,
    team_ids: List[str],
) -> Optional[Dict[str, Any]]:
    """Return the first issue attachment whose parent issue belongs to ``team_ids``."""
    team_set = set(team_ids)
    for attachment in await _paginate_attachments(datasource):
        issue = attachment.get("issue") or {}
        team = (issue.get("team") or {})
        if team.get("id") in team_set and attachment.get("id"):
            return attachment
    return None


async def fetch_first_document_in_teams(
    datasource: LinearDataSource,
    team_ids: List[str],
) -> Optional[Dict[str, Any]]:
    """Return the first document synced by the connector (issue- or project-attached)."""
    team_set = set(team_ids)

    for document in await _paginate_documents(datasource):
        issue = document.get("issue")
        if issue:
            team = (issue.get("team") or {})
            if team.get("id") in team_set and document.get("id"):
                return document

    for project in await fetch_unique_projects_for_teams(datasource, team_ids):
        project_id = project.get("id")
        if not project_id:
            continue
        try:
            resp = await _api_call_with_retry(
                datasource.project, id=project_id,
                context=f"fetch_first_document_in_teams:project({project_id})",
            )
        except RuntimeError:
            continue
        proj = (resp.data or {}).get("project") or {}
        nodes = (proj.get("documents") or {}).get("nodes", [])
        if nodes:
            doc = dict(nodes[0])
            doc["_parent_project_id"] = project_id
            doc["_parent_team_id"] = team_ids[0]
            return doc

    return None


async def fetch_first_file_in_teams(
    datasource: LinearDataSource,
    team_ids: List[str],
) -> Optional[Dict[str, Any]]:
    """Return metadata for the first FILE URL found in issue/project markdown."""
    for team_id in team_ids:
        for issue in await _paginate_team_issues(datasource, team_id):
            issue_id = issue.get("id")
            if not issue_id:
                continue
            weburl = issue.get("url")
            for source in (
                issue.get("description") or "",
                *[
                    (c.get("body") or "")
                    for c in (issue.get("comments") or {}).get("nodes", [])
                ],
            ):
                files = extract_file_urls_from_markdown(source, exclude_images=True)
                if files:
                    return {
                        "url": files[0]["url"],
                        "filename": files[0]["filename"],
                        "parent_external_id": issue_id,
                        "parent_record_type": "TICKET",
                        "team_id": team_id,
                        "parent_weburl": weburl,
                        "parent_created_at": parse_linear_timestamp(issue.get("createdAt")),
                        "parent_updated_at": parse_linear_timestamp(issue.get("updatedAt")),
                    }

    for project in await fetch_unique_projects_for_teams(datasource, team_ids):
        project_id = project.get("id")
        if not project_id:
            continue
        try:
            resp = await _api_call_with_retry(
                datasource.project, id=project_id,
                context=f"fetch_first_file_in_teams:project({project_id})",
            )
        except RuntimeError:
            continue
        proj = (resp.data or {}).get("project") or {}
        content = proj.get("content") or ""
        files = extract_file_urls_from_markdown(content, exclude_images=True)
        if files:
            teams = (proj.get("teams") or {}).get("nodes", [])
            team_id = teams[0]["id"] if teams else team_ids[0]
            return {
                "url": files[0]["url"],
                "filename": files[0]["filename"],
                "parent_external_id": project_id,
                "parent_record_type": "PROJECT",
                "team_id": team_id,
                "parent_weburl": proj.get("url"),
                "parent_created_at": parse_linear_timestamp(proj.get("createdAt")),
                "parent_updated_at": parse_linear_timestamp(proj.get("updatedAt")),
            }

    return None


async def assert_linear_dependent_counts_match_graph(
    datasource: LinearDataSource,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    team_ids: List[str],
    *,
    phase: str,
) -> None:
    """Assert graph FILE/LINK/WEBPAGE counts match API-derived connector expectations."""
    expected_links = await count_linear_scope_links(datasource, team_ids)
    expected_webpages = await count_linear_scope_webpages(datasource, team_ids)
    expected_files = await count_linear_scope_files(datasource, team_ids)

    graph_links = await graph_provider.count_records_by_type(connector_id, "LINK")
    graph_webpages = await graph_provider.count_records_by_type(connector_id, "WEBPAGE")
    graph_files = await graph_provider.count_records_by_type(connector_id, "FILE")

    mismatches: List[str] = []
    if graph_links != expected_links:
        mismatches.append(
            f"LINK: graph={graph_links}, API-expected={expected_links}"
        )
    if graph_webpages != expected_webpages:
        mismatches.append(
            f"WEBPAGE: graph={graph_webpages}, API-expected={expected_webpages}"
        )
    if graph_files != expected_files:
        mismatches.append(
            f"FILE: graph={graph_files}, API-expected={expected_files}"
        )
    if mismatches:
        raise AssertionError(
            f"{phase}: dependent record count mismatch for connector {connector_id}: "
            + "; ".join(mismatches)
        )


async def fetch_first_project_in_team(
    datasource: LinearDataSource,
    team_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the first project accessible to the given team, or None."""
    response = await _api_call_with_retry(
        datasource.projects,
        first=1,
        filter={"accessibleTeams": {"some": {"id": {"eq": team_id}}}},
        context=f"fetch_first_project_in_team({team_id})",
    )
    nodes = (response.data or {}).get("projects", {}).get("nodes", [])
    return nodes[0] if nodes else None


# ---------------------------------------------------------------------------
# Issue lookups
# ---------------------------------------------------------------------------


async def get_linear_issue_updated_ms(
    datasource: LinearDataSource,
    issue_id: str,
) -> int:
    """Return ``updatedAt`` as epoch milliseconds for the given issue."""
    resp = await _api_call_with_retry(
        datasource.issue, id=issue_id,
        context=f"get_linear_issue_updated_ms({issue_id})",
    )
    issue = (resp.data or {}).get("issue", {})
    raw = issue.get("updatedAt")
    return parse_linear_timestamp(raw)


async def check_issue_exists_bool(
    datasource: LinearDataSource,
    issue_id: str,
) -> bool:
    """True if the issue is fetchable via ``issue(id)``."""
    try:
        resp = await datasource.issue(id=issue_id)
    except Exception:
        return False
    if not resp.success:
        return False
    issue = (resp.data or {}).get("issue")
    return issue is not None and bool(issue.get("id"))


# ---------------------------------------------------------------------------
# Graph reconciliation
# ---------------------------------------------------------------------------


async def assert_linear_issues_match_graph_records(
    datasource: LinearDataSource,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    team_ids: List[str],
    *,
    phase: str,
) -> None:
    """Assert graph TICKET count >= Linear API issue count for the filtered teams.

    The graph legitimately has more TICKET nodes than the API issue count because the
    connector creates placeholder stubs for parent/related issues referenced by synced
    issues. The connector's team_ids filter already scopes all graph records to the
    correct teams, so we only need to verify the graph captured at least every issue
    the API reports.
    """
    api_total = 0
    for tid in team_ids:
        api_total += await count_linear_team_issues(datasource, tid)

    graph_ticket_count = await graph_provider.count_records_by_type(connector_id, "TICKET")
    if graph_ticket_count < api_total:
        raise AssertionError(
            f"{phase}: graph TICKET count ({graph_ticket_count}) < "
            f"Linear API issue count ({api_total}) for connector {connector_id}. "
            "Expected graph count to be >= API count (placeholders are additive)."
        )


# ---------------------------------------------------------------------------
# User counting (mirrors connector _fetch_users pool rules)
# ---------------------------------------------------------------------------


async def count_linear_users_with_email(
    datasource: LinearDataSource,
    *,
    page_size: int = 50,
    max_pages: int = 500,
) -> int:
    """Count active Linear users that have an email address.

    Mirrors ``LinearConnector._fetch_users``: active + non-empty email.
    """
    count = 0
    cursor: Optional[str] = None

    for _ in range(max_pages):
        resp = await _api_call_with_retry(
            datasource.users, first=page_size, after=cursor,
            context="count_linear_users_with_email",
        )
        users_data = (resp.data or {}).get("users", {})
        nodes = users_data.get("nodes", [])
        if not nodes:
            break
        for u in nodes:
            if not u.get("active", True):
                continue
            if not (u.get("email") or "").strip():
                continue
            count += 1
        page_info = users_data.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break

    return count


# ---------------------------------------------------------------------------
# Team metadata fetching
# ---------------------------------------------------------------------------


async def fetch_teams_by_ids(
    datasource: LinearDataSource,
    team_ids: List[str],
    *,
    page_size: int = 50,
) -> List[Dict[str, Any]]:
    """Fetch team metadata for specific team IDs."""
    all_teams: List[Dict[str, Any]] = []
    cursor: Optional[str] = None

    while True:
        resp = await _api_call_with_retry(
            datasource.teams,
            first=page_size, after=cursor,
            filter={"id": {"in": team_ids}},
            context="fetch_teams_by_ids",
        )
        data = (resp.data or {}).get("teams", {})
        nodes = data.get("nodes", [])
        if not nodes:
            break
        all_teams.extend(nodes)
        page_info = data.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break

    return all_teams


# ---------------------------------------------------------------------------
# Polling helpers
# ---------------------------------------------------------------------------


async def wait_until_linear_condition(
    check_fn: Callable[[], Awaitable[bool]],
    *,
    timeout: int = LINEAR_TEST_SETTLE_WAIT_SEC,
    poll_interval: int = 15,
    description: str = "Linear API condition",
) -> None:
    """Poll ``check_fn`` until truthy or ``timeout`` elapses.

    Auth-class errors propagate immediately.
    """
    start = time.time()
    deadline = start + timeout
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        try:
            if await check_fn():
                logger.info(
                    "%s satisfied (attempt %d, %.1fs elapsed)",
                    description, attempt, time.time() - start,
                )
                return
        except LinearAuthError:
            raise
        except Exception as e:
            logger.warning(
                "Check failed for %s (attempt %d): %s",
                description, attempt, e,
            )

        remaining = deadline - time.time()
        if remaining <= 0:
            break
        sleep_time = min(poll_interval, remaining)
        logger.info(
            "Waiting for %s (attempt %d, %.0fs remaining)...",
            description, attempt, remaining,
        )
        await asyncio.sleep(sleep_time)

    raise TimeoutError(
        f"Timed out waiting for {description} after {timeout}s ({attempt} attempts)"
    )


# ---------------------------------------------------------------------------
# Indexing helpers
# ---------------------------------------------------------------------------

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
    timeout: int = LINEAR_INDEXING_WAIT_SEC,
    poll_interval: int = 5,
    description: str = "record indexing COMPLETED",
    pipeshub_client: Any | None = None,
) -> Record:
    """Poll the graph until the record reaches ``indexingStatus == COMPLETED``.

    If ``pipeshub_client`` is set and the record hits ``AUTO_INDEX_OFF`` once,
    triggers ``reindex_record`` and continues polling.
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
                    "%s COMPLETED (attempt %d, %.1fs)",
                    description, attempt, time.time() - start,
                )
                return rec
            if last_status in _RECORD_INDEXING_TERMINAL:
                if (
                    last_status == ProgressStatus.AUTO_INDEX_OFF.value
                    and pipeshub_client is not None
                    and not reindexed_after_auto_index_off
                ):
                    logger.info("%s — AUTO_INDEX_OFF, triggering reindex", description)
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
            "%s — status=%s (attempt %d, %.0fs left)",
            description, last_status or "pending", attempt, remaining,
        )
        await asyncio.sleep(sleep_time)

    raise TimeoutError(
        f"Timed out waiting for {description} on externalRecordId={external_record_id!r} "
        f"after {timeout}s (last indexingStatus={last_status!r}, attempts={attempt})"
    )
