"""
Linear API helpers for Linear connector integration tests.

Linear's GraphQL API is consistent (no Atlassian-style search-index lag), so
visibility checks are simple — ``issue(id=...)`` resolves immediately after
``issueCreate``. The polling helper still exists for parity with the Confluence/
Jira utilities and to handle transient errors gracefully.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.sources.external.linear.linear import (  # type: ignore[import-not-found]
    LinearDataSource,
)
from connectors.linear.constants import (  # type: ignore[import-not-found]
    LINEAR_TEST_SETTLE_WAIT_SEC,
)

logger = logging.getLogger("linear-test-utils")


class LinearAuthError(RuntimeError):
    """Raised when a Linear polling check hits an auth-class error — fail fast."""


def _is_auth_failure(message: Optional[str]) -> bool:
    if not message:
        return False
    lower = message.lower()
    return any(s in lower for s in ("authentication", "unauthorized", "forbidden", "token"))


# =============================================================================
# Single-issue lookups
# =============================================================================


async def get_linear_issue(
    datasource: LinearDataSource, issue_id: str
) -> Optional[Dict[str, Any]]:
    """Return the issue payload for ``issue_id`` or ``None`` if not found."""
    resp = await datasource.issue(id=issue_id)
    if not resp.success:
        if _is_auth_failure(resp.message):
            raise LinearAuthError(
                f"get_linear_issue: Linear auth failure for {issue_id!r}: {resp.message}"
            )
        return None
    return ((resp.data or {}).get("issue")) or None


async def list_team_issue_ids(
    datasource: LinearDataSource, team_id: str, page_size: int = 100
) -> List[str]:
    """Page through issues for ``team_id`` and return their ids."""
    ids: List[str] = []
    after: Optional[str] = None
    while True:
        resp = await datasource.issues(
            first=page_size,
            after=after,
            filter={"team": {"id": {"eq": team_id}}},
        )
        if not resp.success:
            if _is_auth_failure(resp.message):
                raise LinearAuthError(
                    f"list_team_issue_ids: Linear auth failure for team={team_id!r}: {resp.message}"
                )
            raise RuntimeError(
                f"Linear issues query failed for team={team_id!r}: {resp.message}"
            )
        block = (resp.data or {}).get("issues") or {}
        nodes = block.get("nodes") or []
        for n in nodes:
            ext_id = n.get("id")
            if ext_id:
                ids.append(str(ext_id))
        page_info = block.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break
    return ids


# =============================================================================
# Polling helper (parity with Confluence / Jira test utils)
# =============================================================================


async def wait_until_linear_condition(
    check_fn: Callable[[], Awaitable[bool]],
    *,
    timeout: int = LINEAR_TEST_SETTLE_WAIT_SEC,
    poll_interval: int = 10,
    description: str = "Linear API condition",
) -> None:
    """Poll ``check_fn`` until truthy or ``timeout`` elapses.

    Auth-class errors propagate immediately so credential problems fail fast
    instead of looping for the full timeout.
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
        except LinearAuthError:
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
    datasource: LinearDataSource, issue_id: str
) -> bool:
    """True if the issue is fetchable via ``issue(id=...)``."""
    try:
        issue = await get_linear_issue(datasource, issue_id)
    except LinearAuthError:
        raise
    except Exception:
        return False
    return issue is not None
