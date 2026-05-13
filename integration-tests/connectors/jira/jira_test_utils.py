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


class JiraAuthError(RuntimeError):
    """Raised when a Jira polling check hits HTTP 401/403 — fail fast on bad creds."""


def _raise_on_auth_error(status: int, context: str) -> None:
    """Re-raise auth-class errors so polling loops don't mask credential problems."""
    if status in (401, 403):
        raise JiraAuthError(
            f"{context}: Jira returned HTTP {status} (auth/permission). "
            f"Check JIRA_TEST_EMAIL / JIRA_TEST_API_TOKEN."
        )


async def count_jira_users_with_visible_email(
    datasource: JiraDataSource,
    *,
    page_size: int = 50,
    max_pages: int = 500,
) -> int:
    """Match :meth:`JiraCloudConnector._fetch_users` — paginated ``get_all_users`` with ``query=''``.

    Same as the connector: ``GET /rest/api/3/users/search`` with empty query, ``maxResults=50``,
    ``startAt`` stepping until a short page or empty batch. Response may be a JSON array or
    ``{\"values\": [...]}``. Counts distinct ``accountId`` for **active** users with non-empty
    ``emailAddress`` (connector skips inactive and users without email).

    Raises:
        JiraAuthError: On HTTP 401/403 from Jira.
        RuntimeError: On other non-success HTTP status or unparseable payload.
    """
    seen_account_ids: set[str] = set()
    count_with_email = 0
    start_at = 0

    for _ in range(max_pages):
        resp = await datasource.get_all_users(
            query="",
            startAt=start_at,
            maxResults=page_size,
        )
        if resp.status in (401, 403):
            _raise_on_auth_error(resp.status, "count_jira_users_with_visible_email")
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
            count_with_email += 1
        if len(batch_users) < page_size:
            break
        start_at += page_size

    logger.info(
        "Jira users (connector-style fetch): %d active users with visible emailAddress "
        "(distinct accountId)",
        count_with_email,
    )
    return count_with_email


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
                    "✅ %s — externalRecordId=%s COMPLETED (attempt %d, %.1fs)",
                    description, external_record_id, attempt, time.time() - start,
                )
                return rec
            if last_status in _RECORD_INDEXING_TERMINAL:
                if (
                    last_status == ProgressStatus.AUTO_INDEX_OFF.value
                    and pipeshub_client is not None
                    and not reindexed_after_auto_index_off
                ):
                    logger.info(
                        "🔄 %s — AUTO_INDEX_OFF on externalRecordId=%s; "
                        "POST reindex (internal record id=%s)",
                        description,
                        external_record_id,
                        rec.id,
                    )
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
            "⏳ %s — externalRecordId=%s status=%s (attempt %d, %.0fs left, sleep %ds)",
            description,
            external_record_id,
            last_status or "(no record yet)",
            attempt,
            remaining,
            sleep_time,
        )
        await asyncio.sleep(sleep_time)

    raise TimeoutError(
        f"Timed out waiting for {description} on externalRecordId={external_record_id!r} "
        f"after {timeout}s (last indexingStatus={last_status!r}, attempts={attempt})"
    )

