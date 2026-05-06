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
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from app.sources.external.jira.jira import (
    JiraDataSource,  # type: ignore[import-not-found]
)
from connectors.jira.constants import (  # type: ignore[import-not-found]
    JIRA_TEST_SETTLE_WAIT_SEC,
)

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger("jira-test-utils")


class JiraAuthError(RuntimeError):
    """Raised when a Jira polling check hits HTTP 401/403 — fail fast on bad creds."""


def _raise_on_auth_error(status: int, context: str) -> None:
    """Re-raise auth-class errors so polling loops don't mask credential problems."""
    if status in (401, 403):
        raise JiraAuthError(
            f"{context}: Jira returned HTTP {status} (auth/permission). "
            f"Check JIRA_TEST_EMAIL / JIRA_TEST_API_TOKEN."
        )


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
    graph_provider: "GraphProviderProtocol",
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


async def assert_jira_issue_in_jql_search(
    datasource: JiraDataSource,
    project_key: str,
    issue_key: str,
    *,
    context: str,
) -> None:
    """Assert an issue is returned by JQL ``project = X AND key = Y`` — distinguishes
    'not yet visible to JQL' from 'not synced to graph'."""
    jql = f'project = "{project_key}" AND key = "{issue_key}"'
    resp = await datasource.search_and_reconsile_issues_using_jql_post(
        jql=jql,
        maxResults=10,
        fields=["summary"],
    )
    if resp.status != 200:
        _raise_on_auth_error(resp.status, context)
        raise AssertionError(
            f"{context}: Jira JQL lookup failed for issue_key={issue_key!r} "
            f"project_key={project_key!r}: HTTP {resp.status}"
        )
    keys = {(it.get("key") or "") for it in (resp.json() or {}).get("issues") or []}
    if issue_key not in keys:
        raise AssertionError(
            f"{context}: Issue {issue_key!r} not in JQL results for project {project_key!r} "
            f"(got {sorted(keys)})."
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
    return _iso_to_epoch_ms(raw)


def _iso_to_epoch_ms(iso_str: str) -> int:
    """Best-effort ISO-8601 → epoch ms. Handles the Jira ``+0000`` (no colon) format."""
    from datetime import datetime, timezone

    s = iso_str.strip()
    # Normalise ``+0000`` → ``+00:00`` for fromisoformat.
    if len(s) >= 5 and (s[-5] in "+-") and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Fallback: try without microseconds.
        dt = datetime.strptime(iso_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


async def get_jira_issue_summary(
    datasource: JiraDataSource, issue_key: str
) -> str:
    """Return ``fields.summary`` for the given issue."""
    resp = await datasource.get_issue(issueIdOrKey=issue_key, fields="summary")
    if resp.status != 200:
        _raise_on_auth_error(resp.status, "get_jira_issue_summary")
        raise AssertionError(
            f"get_jira_issue_summary failed for issue_key={issue_key!r}: HTTP {resp.status}"
        )
    return str(((resp.json() or {}).get("fields") or {}).get("summary") or "")


async def get_jira_issue_parent_key(
    datasource: JiraDataSource, issue_key: str
) -> Optional[str]:
    """Return the ``fields.parent.key`` for the given issue (None if no parent)."""
    resp = await datasource.get_issue(issueIdOrKey=issue_key, fields="parent")
    if resp.status != 200:
        _raise_on_auth_error(resp.status, "get_jira_issue_parent_key")
        raise AssertionError(
            f"get_jira_issue_parent_key failed for issue_key={issue_key!r}: HTTP {resp.status}"
        )
    parent = ((resp.json() or {}).get("fields") or {}).get("parent")
    if not isinstance(parent, dict):
        return None
    return parent.get("key")


async def get_jira_issue_attachment_ids(
    datasource: JiraDataSource, issue_key: str
) -> set[str]:
    """Return the set of attachment ids on an issue."""
    resp = await datasource.get_issue(issueIdOrKey=issue_key, fields="attachment")
    if resp.status != 200:
        _raise_on_auth_error(resp.status, "get_jira_issue_attachment_ids")
        raise AssertionError(
            f"get_jira_issue_attachment_ids failed for issue_key={issue_key!r}: HTTP {resp.status}"
        )
    attachments = ((resp.json() or {}).get("fields") or {}).get("attachment") or []
    return {str(a.get("id")) for a in attachments if a.get("id")}


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


async def check_issue_in_jql_search_bool(
    datasource: JiraDataSource, project_key: str, issue_key: str
) -> bool:
    """Non-assertion variant of ``assert_jira_issue_in_jql_search``."""
    jql = f'project = "{project_key}" AND key = "{issue_key}"'
    resp = await datasource.search_and_reconsile_issues_using_jql_post(
        jql=jql,
        maxResults=10,
        fields=["summary"],
    )
    if resp.status != 200:
        _raise_on_auth_error(resp.status, "check_issue_in_jql_search_bool")
        return False
    keys = {(it.get("key") or "") for it in (resp.json() or {}).get("issues") or []}
    return issue_key in keys


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


async def check_issue_updated_after_bool(
    datasource: JiraDataSource, issue_key: str, threshold_ms: int
) -> bool:
    """True when the issue's ``fields.updated`` is strictly greater than ``threshold_ms``."""
    try:
        actual = await get_jira_issue_updated_ms(datasource, issue_key)
    except JiraAuthError:
        raise
    except Exception:
        return False
    return actual > threshold_ms


async def check_issue_summary_bool(
    datasource: JiraDataSource, issue_key: str, expected: str
) -> bool:
    """True when the issue's ``fields.summary`` equals ``expected``."""
    try:
        actual = await get_jira_issue_summary(datasource, issue_key)
    except JiraAuthError:
        raise
    except Exception:
        return False
    return actual == expected


async def check_issue_parent_bool(
    datasource: JiraDataSource, issue_key: str, expected_parent_key: str
) -> bool:
    """True when the issue's ``fields.parent.key`` equals ``expected_parent_key``."""
    try:
        actual = await get_jira_issue_parent_key(datasource, issue_key)
    except JiraAuthError:
        raise
    except Exception:
        return False
    return actual == expected_parent_key


async def check_issue_count_in_project_bool(
    datasource: JiraDataSource, project_key: str, expected_count: int
) -> bool:
    """True when JQL count for the project equals ``expected_count``."""
    try:
        actual = await count_jira_project_issues_via_jql(datasource, project_key)
    except JiraAuthError:
        raise
    except Exception:
        return False
    return actual == expected_count


async def check_attachment_present_bool(
    datasource: JiraDataSource, issue_key: str, attachment_id: str
) -> bool:
    """True when ``attachment_id`` is currently attached to the issue."""
    try:
        ids = await get_jira_issue_attachment_ids(datasource, issue_key)
    except JiraAuthError:
        raise
    except Exception:
        return False
    return str(attachment_id) in ids
