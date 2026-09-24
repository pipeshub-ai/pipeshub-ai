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
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from app.config.constants.arangodb import ProgressStatus  # type: ignore[import-not-found]
from app.connectors.sources.linear.connector import (  # type: ignore[import-not-found]
    PLACEHOLDER_SWEEP_MAX_DEPTH,
)
from app.models.entities import Record  # type: ignore[import-not-found]
from app.sources.external.linear.linear import (
    LinearDataSource,  # type: ignore[import-not-found]
)
from connectors.linear.constants import (  # type: ignore[import-not-found]
    LINEAR_FROZEN_ISSUE_IDENTIFIERS,
    LINEAR_INDEXING_WAIT_SEC,
    LINEAR_IT_ARTIFACT_PREFIX,
    LINEAR_IT_RUN_ID,
    LINEAR_IT_STALE_ARTIFACT_AGE_SEC,
    LINEAR_TEST_SETTLE_WAIT_SEC,
)
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    owned_record_external_ids,
)

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


# Linear reports throttling as a GraphQL error ("Rate limit exceeded", code RATELIMITED),
# not as a message carrying "429"; gateway failures come back as non-JSON bodies that the
# client surfaces as "Request failed: ..."; and a read timeout (``asyncio.TimeoutError``,
# whose ``str`` is empty) reaches the datasource, which wraps it as "Failed to execute
# <op>: ". All of them are worth a retry on a workspace that N concurrent runs (and their
# connectors) are hitting at once.
_TRANSIENT_MARKERS = (
    "429", "500", "502", "503", "504",
    "rate limit", "ratelimited", "timeout", "timed out", "temporarily",
    "request failed", "failed to execute", "failed to fetch", "unexpected mimetype",
    "service unavailable", "bad gateway", "internal server error",
)
LINEAR_RETRY_MAX_DELAY_SEC = 60


def _is_transient_error(response: Any) -> bool:
    """Return True if the response looks like a transient Linear API failure."""
    msg = (getattr(response, "message", "") or "").lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


async def _api_call_with_retry(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    context: str,
    max_retries: int = 4,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> Any:
    """Call ``fn`` and retry on transient Linear API errors (rate limit, 5xx, transport).

    A transport exception is retried like a transient response: the GraphQL client only
    converts ``aiohttp.ClientError``, so a read timeout escapes as ``asyncio.TimeoutError``.
    Auth failures raise ``LinearAuthError`` at once; anything else non-transient (an unknown
    id, a validation error) raises ``RuntimeError`` without retrying.
    """
    last_error = ""
    attempts = 0
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            response = await fn(*args, **kwargs)
        except LinearAuthError:
            raise
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
        else:
            if response.success:
                return response
            _raise_on_auth_error(response, context)
            last_error = getattr(response, "message", "") or ""
            if not _is_transient_error(response):
                break
        if attempt == max_retries:
            break
        delay = min(base_delay * (2 ** attempt), LINEAR_RETRY_MAX_DELAY_SEC)
        logger.warning(
            "%s: transient error (attempt %d/%d), retrying in %.1fs: %s",
            context, attempt + 1, max_retries + 1, delay, last_error,
        )
        await asyncio.sleep(delay)
    raise RuntimeError(f"{context} failed after {attempts} attempt(s): {last_error}")


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
    """Return issue IDs belonging to ``team_id``, excluding IT artifacts."""
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
            if issue_id and LINEAR_IT_ARTIFACT_PREFIX not in (node.get("title") or ""):
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
    """Count issues belonging to ``team_id``, excluding IT artifacts."""
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


async def resolve_issue_by_identifier(
    datasource: LinearDataSource,
    identifier: str,
) -> Optional[Dict[str, Any]]:
    """Return the issue JSON for a human identifier (``ENG-2``) or a UUID.

    Linear's ``issue(id:)`` accepts either form. Transient failures are retried; an
    unknown identifier yields ``None``.
    """
    try:
        resp = await _api_call_with_retry(
            datasource.issue, id=identifier, context=f"resolve_issue_by_identifier({identifier})",
        )
    except RuntimeError:
        return None
    issue = (resp.data or {}).get("issue")
    return issue if issue and issue.get("id") else None


async def fetch_ancestor_chain(
    datasource: LinearDataSource,
    issue_id: str,
    *,
    max_depth: int = PLACEHOLDER_SWEEP_MAX_DEPTH,
) -> List[Dict[str, Any]]:
    """Return ancestor issues for ``issue_id``, nearest parent first.

    Walked one hop at a time: ``issue(id:)`` exposes only a single level of
    ``parent``, so reaching the grandparent needs a second call. Returns the full
    issue nodes rather than ids so callers can read ``updatedAt`` without a second
    round-trip per ancestor. ``max_depth`` mirrors the connector's own sweep cap so
    the walk cannot claim ancestors the sweep would never reach.
    """
    chain: List[Dict[str, Any]] = []
    seen: Set[str] = {issue_id}

    resp = await _api_call_with_retry(
        datasource.issue, id=issue_id,
        context=f"fetch_ancestor_chain({issue_id})",
    )
    current = (resp.data or {}).get("issue") or {}

    for _ in range(max_depth):
        parent_id = (current.get("parent") or {}).get("id")
        if not parent_id or parent_id in seen:
            break
        seen.add(parent_id)

        resp = await _api_call_with_retry(
            datasource.issue, id=parent_id,
            context=f"fetch_ancestor_chain({parent_id})",
        )
        current = (resp.data or {}).get("issue") or {}
        if not current.get("id"):
            break
        chain.append(current)

    return chain


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


async def check_issue_trashed_bool(
    datasource: LinearDataSource,
    issue_id: str,
) -> bool:
    """True once Linear reports the issue trashed, or can no longer resolve it.

    ``issueDelete`` moves an issue to the trash rather than removing it: ``issue(id)`` keeps
    returning it with ``trashed=True`` (and it drops out of every active listing), so
    "gone" has to be read from that flag, not from a lookup failure. A transient failure
    reads as "not yet" so a poller keeps going.
    """
    try:
        resp = await datasource.issue(id=issue_id)
    except Exception:
        return False
    if not resp.success:
        return "not found" in (getattr(resp, "message", "") or "").lower()
    issue = (resp.data or {}).get("issue")
    if not issue or not issue.get("id"):
        return True
    return bool(issue.get("trashed"))


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
    """Assert every live issue in the filtered teams reached the graph.

    Inclusion, not equality: the graph legitimately holds *more* TICKETs than the API reports,
    because the connector mints placeholder stubs for referenced parents. Compared as id sets
    so a failure names the missing issues, and IT artifacts are skipped on both sides — a
    concurrently running leg shares this workspace and its mutation issues come and go.
    """
    api_ids: Set[str] = set()
    for tid in team_ids:
        api_ids |= await fetch_linear_team_issue_ids(datasource, tid)

    graph_ids = await owned_record_external_ids(
        graph_provider, connector_id, prefix=LINEAR_IT_ARTIFACT_PREFIX, record_type="TICKET",
    )
    missing = api_ids - graph_ids
    if missing:
        raise AssertionError(
            f"{phase}: {len(missing)} live Linear issue(s) absent from the graph for "
            f"connector {connector_id} (IT artifacts excluded): {sorted(missing)}"
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


# =============================================================================
# Artifact ownership — this run's issues, and leftovers from crashed runs
# =============================================================================
#
# Every leg, every PR and the nightly cron share one Linear workspace. The mutation tests
# delete what they create, but a cancelled CI run (``cancel-in-progress``) SIGTERMs pytest
# before any ``finally`` runs, so leaks are inevitable and something has to reap them.
# Ownership is encoded in the title (``LinearIT-<run_id>-<Kind>-<hex>``, see
# ``constants.artifact_title``); the registry tracks what this process created so its own
# teardown deletes exactly that, and the sweep reaps anything old enough that no live run
# can still own it. Deleting means trashing: the issue stays fetchable by id with
# ``trashed=True`` and leaves every active listing, which is all the suite needs.


class LinearArtifactRegistry:
    """Issue ids this run created and has not yet confirmed trashed."""

    def __init__(self) -> None:
        self._issues: dict[str, str] = {}

    def register(self, issue_id: str, title: str) -> None:
        self._issues[str(issue_id)] = title

    def release(self, issue_id: str) -> None:
        self._issues.pop(str(issue_id), None)

    def is_registered(self, issue_id: str) -> bool:
        return str(issue_id) in self._issues

    def drain(self) -> list[tuple[str, str]]:
        """Return and forget every outstanding ``(issue_id, title)``."""
        items = list(self._issues.items())
        self._issues.clear()
        return items

    def __len__(self) -> int:
        return len(self._issues)


linear_artifacts = LinearArtifactRegistry()

# The exact shape ``constants.artifact_title`` produces. Ownership for deletion is decided
# by THIS, not by the ``LinearIT-`` marker alone: the marker also sits on legacy artifacts
# (``LinearIT-IncrTest-<hex>``) that an older suite version left in the trash, and a
# marker-only rule would happily delete a fixture someone renamed by hand.
ARTIFACT_TITLE_RE = re.compile(
    rf"^{re.escape(LINEAR_IT_ARTIFACT_PREFIX)}[0-9a-f]{{8}}-[A-Za-z]+-[0-9a-f]{{8}}$"
)


def is_run_artifact_title(title: str) -> bool:
    """True only for titles in the current ``LinearIT-<run_id>-<Kind>-<hex>`` form."""
    return bool(ARTIFACT_TITLE_RE.fullmatch((title or "").strip()))


def pick_mutation_team(team_ids: List[str]) -> str:
    """Where the mutation tests write.

    The *secondary* filtered team when one is configured, so the primary — home of every
    pinned fixture and every baseline — stays read-only for the whole suite. A single-team
    setup falls back to the primary; artifact exclusion still protects its baselines.
    """
    return team_ids[1] if len(team_ids) > 1 else team_ids[0]


async def create_artifact_issue(
    datasource: LinearDataSource,
    *,
    team_id: str,
    title: str,
    context: str,
) -> str:
    """Create a test-owned issue and register it; returns the issue id.

    A retried ``issueCreate`` whose first attempt actually succeeded leaves a twin behind;
    the teardown sweep on this run id (``reap_own_artifacts``) is what catches that.
    """
    resp = await _api_call_with_retry(
        datasource.issueCreate, input={"teamId": team_id, "title": title},
        context=f"{context} issueCreate",
    )
    issue = ((resp.data or {}).get("issueCreate") or {}).get("issue") or {}
    issue_id = issue.get("id")
    assert issue_id, f"{context}: issueCreate returned no issue id"
    linear_artifacts.register(issue_id, title)
    logger.info("%s: created %s (%s)", context, issue.get("identifier"), title)
    return str(issue_id)


async def delete_artifact_issue(
    datasource: LinearDataSource,
    *,
    issue_id: str,
    context: str,
    timeout: int = 60,
) -> bool:
    """Trash a test-owned issue, wait until Linear confirms it, release it from the registry.

    Idempotent: an id already released (deleted earlier in the same test) is skipped, so
    a ``finally`` can call this unconditionally. Never raises — it runs where an exception
    would mask the test body's real failure — and returns False when the issue could not
    be confirmed trashed, leaving it registered for the teardown reap.
    """
    if not linear_artifacts.is_registered(issue_id):
        return True
    try:
        await _api_call_with_retry(
            datasource.issueDelete, id=issue_id, context=f"{context} issueDelete",
        )
    except Exception as e:
        if "not found" not in str(e).lower():
            logger.warning("%s: issueDelete %s failed: %s", context, issue_id, e)
            return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if await check_issue_trashed_bool(datasource, issue_id):
            linear_artifacts.release(issue_id)
            logger.info("%s: issue %s confirmed trashed", context, issue_id)
            return True
        await asyncio.sleep(3)
    logger.warning("%s: issue %s not confirmed trashed within %ds", context, issue_id, timeout)
    return False


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def stale_artifact_filter(
    team_ids: List[str],
    *,
    min_age_sec: float = LINEAR_IT_STALE_ARTIFACT_AGE_SEC,
    only_run_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """``issues`` filter selecting *candidate* artifacts for the sweep.

    Structured filters only — ``title.startsWith`` on the marker (or on this run's stem)
    and, without ``only_run_id``, ``createdAt`` older than ``min_age_sec``. The age gate is
    what keeps the sweep safe under concurrency: a younger issue may belong to a run that is
    still asserting on it. ``createdAt`` is immutable so the cut never drifts. Linear does
    not accept relative durations here, so the cut is an absolute UTC instant. Ownership is
    still decided in Python from the full title (``is_run_artifact_title``).
    """
    stem = f"{LINEAR_IT_ARTIFACT_PREFIX}{only_run_id}-" if only_run_id else LINEAR_IT_ARTIFACT_PREFIX
    issue_filter: Dict[str, Any] = {
        "team": {"id": {"in": list(team_ids)}},
        "title": {"startsWith": stem},
    }
    if not only_run_id:
        cut = (now or datetime.now(timezone.utc)) - timedelta(seconds=min_age_sec)
        issue_filter["createdAt"] = {"lt": _iso_utc(cut)}
    return issue_filter


async def sweep_stale_linear_artifacts(
    datasource: LinearDataSource,
    team_ids: List[str],
    *,
    min_age_sec: float = LINEAR_IT_STALE_ARTIFACT_AGE_SEC,
    only_run_id: Optional[str] = None,
) -> int:
    """Trash leaked IT issues in ``team_ids``; return how many were trashed.

    Trashing requires ALL of: the title is in the exact run-id artifact form
    (``is_run_artifact_title``), it starts with the requested stem, the identifier is not a
    frozen fixture, and the issue has neither parent nor children (an artifact is always
    flat — this is what shields the placeholder chain whatever its titles say). Already
    trashed issues never show up: the query runs without ``includeArchived``. Any failure
    is logged and skipped: reaping is best-effort hygiene and must never fail the run that
    performs it.
    """
    if not team_ids:
        return 0
    stem = f"{LINEAR_IT_ARTIFACT_PREFIX}{only_run_id}-" if only_run_id else LINEAR_IT_ARTIFACT_PREFIX
    issue_filter = stale_artifact_filter(team_ids, min_age_sec=min_age_sec, only_run_id=only_run_id)

    candidates: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        for _ in range(50):
            resp = await _api_call_with_retry(
                datasource.issues, first=100, after=cursor, filter=issue_filter,
                context="SWEEP issues",
            )
            data = (resp.data or {}).get("issues", {})
            candidates.extend(data.get("nodes", []))
            page_info = data.get("pageInfo", {})
            if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
                break
            cursor = page_info["endCursor"]
    except Exception as e:
        logger.warning("SWEEP: artifact search failed (%s) — skipping: %s", issue_filter, e)
        return 0

    deleted = 0
    for issue in candidates:
        title = (issue.get("title") or "").strip()
        issue_id = str(issue.get("id") or "")
        identifier = str(issue.get("identifier") or "")
        if not issue_id or not title.startswith(stem) or not is_run_artifact_title(title):
            continue
        if identifier in LINEAR_FROZEN_ISSUE_IDENTIFIERS:
            logger.error(
                "SWEEP: refusing to delete frozen fixture %s even though its title is %r "
                "— restore its real title", identifier, title,
            )
            continue
        if issue.get("parent") or ((issue.get("children") or {}).get("nodes")):
            logger.error(
                "SWEEP: refusing to delete %s (%r) — it is part of a hierarchy, artifacts never are",
                identifier, title,
            )
            continue
        if issue.get("trashed"):
            continue
        try:
            await _api_call_with_retry(
                datasource.issueDelete, id=issue_id, context=f"SWEEP delete {identifier}",
            )
        except LinearAuthError:
            logger.debug("SWEEP: no permission to delete %s — skipping", identifier)
            continue
        except Exception as e:
            logger.warning("SWEEP: delete %s (%s) failed: %s", identifier, title, e)
            continue
        logger.warning(
            "SWEEP: trashed leaked IT artifact %s (%s, created %s)",
            identifier, title, issue.get("createdAt"),
        )
        deleted += 1
    return deleted


async def reap_own_artifacts(
    datasource: LinearDataSource, team_ids: List[str],
) -> int:
    """Teardown hygiene: trash everything this run still owns.

    Registry first (ids we know), then a title sweep on this run id — the sweep also
    catches an issue whose ``issueCreate`` succeeded after the registry line was never
    reached (interrupt between the two, or a retried create that made a twin).
    """
    deleted = 0
    for issue_id, title in linear_artifacts.drain():
        try:
            await _api_call_with_retry(
                datasource.issueDelete, id=issue_id, context=f"teardown delete {title}",
            )
            deleted += 1
        except Exception as e:
            if "not found" in str(e).lower():
                deleted += 1
            else:
                logger.warning("TEARDOWN: delete %s (%s) failed: %s", issue_id, title, e)
    deleted += await sweep_stale_linear_artifacts(
        datasource, team_ids, only_run_id=LINEAR_IT_RUN_ID,
    )
    return deleted
