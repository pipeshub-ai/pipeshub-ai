"""
Confluence v1 REST helpers for Confluence connector integration tests.

Asserts Confluence Cloud state (content/search, content/{id}) so failures
distinguish API issues from graph/sync issues. Lives next to
``confluence_integration_test.py`` and ``conftest.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from app.sources.external.confluence.confluence import (
    ConfluenceDataSource,  # type: ignore[import-not-found]
)
from connectors.confluence.constants import (
    CONFLUENCE_TEST_SETTLE_WAIT_SEC,  # type: ignore[import-not-found]
)
from helper.graph_provider_utils import (
    async_poll_until,  # type: ignore[import-not-found]
)

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger("confluence-v1-test-utils")


def _confluence_extract_cursor_from_next_link(next_url: str | None) -> str | None:
    if not next_url:
        return None
    try:
        parsed = urlparse(next_url)
        values = parse_qs(parsed.query).get("cursor", [])
        return values[0] if values else None
    except Exception:
        logger.exception("Failed to parse Confluence cursor from next URL: %s", next_url)
        return None


async def count_confluence_space_pages_v1_search(
    datasource: ConfluenceDataSource,
    space_key: str,
) -> int:
    """Count pages in ``space_key`` via Confluence v1 ``/rest/api/content/search`` (paginated)."""
    cursor: str | None = None
    seen: set[str] = set()
    batch_size = 50

    while True:
        resp = await datasource.get_pages_v1(
            space_key=space_key,
            cursor=cursor,
            limit=batch_size,
            order_by="lastModified",
            sort_order="asc",
        )
        if resp.status != 200:
            raise RuntimeError(
                f"Confluence content/search failed: HTTP {resp.status} {resp.text()[:800]}"
            )
        data = resp.json()
        for item in data.get("results") or []:
            cid = item.get("id")
            if cid is not None:
                seen.add(str(cid))
        next_url = (data.get("_links") or {}).get("next")
        if not next_url:
            break
        cursor = _confluence_extract_cursor_from_next_link(next_url)
        if not cursor:
            break

    return len(seen)


async def assert_confluence_pages_match_graph_records(
    datasource: ConfluenceDataSource,
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    space_key: str,
    *,
    phase: str,
) -> None:
    """Assert v1 page count for the space equals graph Record count for the connector."""
    api_count = await count_confluence_space_pages_v1_search(datasource, space_key)
    graph_count = await graph_provider.count_records(connector_id)
    if api_count != graph_count:
        raise AssertionError(
            f"{phase}: Confluence v1 content/search page count ({api_count}) != "
            f"graph Record count ({graph_count}) for connector {connector_id} "
            f"space_key={space_key!r}"
        )


async def assert_confluence_page_in_v1_space_content_search(
    datasource: ConfluenceDataSource,
    space_key: str,
    page_id: str,
    *,
    context: str,
) -> None:
    """
    Assert a page id is returned by v1 ``/rest/api/content/search`` for the space.

    Call this before graph ``assert_record_exists`` so failures distinguish
    \"not in Confluence v1 search\" from \"not synced to graph\".
    """
    resp = await datasource.get_pages_v1(
        space_key=space_key,
        page_ids=[page_id],
        page_ids_operator="in",
        limit=25,
    )
    if resp.status != 200:
        raise AssertionError(
            f"{context}: Confluence v1 content/search lookup failed for page_id={page_id!r} "
            f"space_key={space_key!r}: HTTP {resp.status} {resp.text()[:600]}"
        )
    results = resp.json().get("results") or []
    found_ids = {str(item.get("id")) for item in results if item.get("id") is not None}
    if page_id not in found_ids:
        raise AssertionError(
            f"{context}: Page id {page_id!r} is not in Confluence v1 content/search results "
            f"for space_key={space_key!r} (got {len(results)} row(s), ids={sorted(found_ids)}). "
            f"The sample page from the Confluence API is not visible to the same v1 search the "
            f"connector uses; graph sync cannot be expected for this id."
        )


async def get_confluence_page_version_number_v1(
    datasource: ConfluenceDataSource,
    page_id: str,
) -> int:
    """
    Return ``version.number`` from Confluence v1 ``GET /wiki/rest/api/content/{id}``.

    Uses ``get_page_content_v1`` (default expand includes ``version``) — same version
    object Confluence documents in the REST API.
    """
    resp = await datasource.get_page_content_v1(str(page_id))
    if resp.status != 200:
        raise AssertionError(
            f"Confluence v1 get content failed for page_id={page_id!r}: "
            f"HTTP {resp.status} {resp.text()[:600]}"
        )
    data = resp.json()
    ver = data.get("version")
    if not isinstance(ver, dict):
        raise AssertionError(
            f"Confluence v1 content for page_id={page_id!r} has no version object; "
            f"top-level keys: {sorted(data.keys())}"
        )
    num = ver.get("number")
    if num is None:
        raise AssertionError(
            f"Confluence v1 content version for page_id={page_id!r} has no number: {ver!r}"
        )
    return int(num)


async def assert_confluence_page_version_number_v1(
    datasource: ConfluenceDataSource,
    page_id: str,
    expected: int,
    *,
    context: str,
) -> None:
    """Assert v1 ``GET /wiki/rest/api/content/{id}`` ``version.number`` equals ``expected``."""
    actual = await get_confluence_page_version_number_v1(datasource, page_id)
    if actual != expected:
        raise AssertionError(
            f"{context}: Confluence v1 version.number for page_id={page_id!r} is {actual}, "
            f"expected {expected}"
        )


async def assert_confluence_page_title_v1(
    datasource: ConfluenceDataSource,
    page_id: str,
    expected_title: str,
    *,
    context: str,
) -> None:
    """Assert v1 content response top-level ``title`` matches ``expected_title``."""
    resp = await datasource.get_page_content_v1(str(page_id), expand="version,space")
    if resp.status != 200:
        raise AssertionError(
            f"{context}: Confluence v1 get content failed for page_id={page_id!r}: "
            f"HTTP {resp.status} {resp.text()[:600]}"
        )
    data = resp.json()
    title = data.get("title")
    if title != expected_title:
        raise AssertionError(
            f"{context}: Confluence v1 title for page_id={page_id!r} is {title!r}, "
            f"expected {expected_title!r}"
        )


async def assert_confluence_page_v1_ancestors_contain_id(
    datasource: ConfluenceDataSource,
    page_id: str,
    ancestor_content_id: str,
    *,
    context: str,
) -> None:
    """Assert v1 content with ``expand=ancestors`` lists ``ancestor_content_id`` among ancestors."""
    resp = await datasource.get_page_content_v1(
        str(page_id),
        expand="version,space,ancestors",
    )
    if resp.status != 200:
        raise AssertionError(
            f"{context}: Confluence v1 get content (ancestors) failed for page_id={page_id!r}: "
            f"HTTP {resp.status} {resp.text()[:600]}"
        )
    data = resp.json()
    ancestors = data.get("ancestors") or []
    if not isinstance(ancestors, list):
        raise AssertionError(
            f"{context}: page_id={page_id!r} has non-list ancestors: {ancestors!r}"
        )
    ids = {str(a.get("id")) for a in ancestors if isinstance(a, dict) and a.get("id") is not None}
    aid = str(ancestor_content_id)
    if aid not in ids:
        raise AssertionError(
            f"{context}: Confluence v1 ancestors for page_id={page_id!r} do not include id={aid!r}; "
            f"ancestor ids={sorted(ids)}"
        )


# ========== Polling Helpers for Intelligent Wait ==========


async def wait_until_confluence_condition(
    check_fn: Callable[[], Awaitable[bool]],
    *,
    timeout: int = CONFLUENCE_TEST_SETTLE_WAIT_SEC,
    poll_interval: int = 30,
    description: str = "Confluence API condition",
) -> None:
    """
    Poll until check_fn returns True or timeout is reached.
    
    Specifically for Confluence API checks with 30s polling interval.
    Uses async_poll_until from graph_provider_utils under the hood.
    
    Args:
        check_fn: Async callable that returns True when condition is met
        timeout: Maximum time to wait in seconds (default: CONFLUENCE_TEST_SETTLE_WAIT_SEC)
        poll_interval: Seconds between checks (default: 30)
        description: Human-readable description for error messages
    
    Raises:
        TimeoutError: If condition not met within timeout
    """
    deadline = time.time() + timeout
    attempt = 0
    
    while time.time() < deadline:
        attempt += 1
        try:
            if await check_fn():
                logger.info(
                    "✅ %s (attempt %d, %.1fs elapsed)",
                    description, attempt, timeout - (deadline - time.time())
                )
                return
        except Exception as e:
            logger.debug(
                "⏳ Check failed for %s (attempt %d): %s",
                description, attempt, e
            )
        
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        
        sleep_time = min(poll_interval, remaining)
        logger.info(
            "⏳ Waiting for %s (attempt %d, %.0fs remaining, sleeping %ds)...",
            description, attempt, remaining, sleep_time
        )
        await asyncio.sleep(sleep_time)
    
    raise TimeoutError(
        f"Timed out waiting for {description} after {timeout}s ({attempt} attempts)"
    )


async def check_page_in_v1_search_bool(
    datasource: ConfluenceDataSource,
    space_key: str,
    page_id: str,
) -> bool:
    """
    Check if page is in v1 content/search results (non-assertion version).
    
    Returns True if page found, False otherwise (including API errors).
    """
    try:
        resp = await datasource.get_pages_v1(
            space_key=space_key,
            page_ids=[page_id],
            page_ids_operator="in",
            limit=25,
        )
        if resp.status != 200:
            return False
        
        results = resp.json().get("results") or []
        found_ids = {str(item.get("id")) for item in results if item.get("id") is not None}
        return page_id in found_ids
    except Exception:
        return False


async def check_version_equals_bool(
    datasource: ConfluenceDataSource,
    page_id: str,
    expected_version: int,
) -> bool:
    """
    Check if page version.number equals expected (non-assertion version).
    
    Returns True if version matches, False otherwise (including API errors).
    """
    try:
        actual_version = await get_confluence_page_version_number_v1(datasource, str(page_id))
        return actual_version == expected_version
    except Exception:
        return False


async def check_page_title_bool(
    datasource: ConfluenceDataSource,
    page_id: str,
    expected_title: str,
) -> bool:
    """
    Check if page title equals expected (non-assertion version).
    
    Returns True if title matches, False otherwise (including API errors).
    """
    try:
        resp = await datasource.get_page_content_v1(str(page_id), expand="version,space")
        if resp.status != 200:
            return False
        
        data = resp.json()
        title = data.get("title")
        return title == expected_title
    except Exception:
        return False


async def check_ancestors_contain_bool(
    datasource: ConfluenceDataSource,
    page_id: str,
    ancestor_content_id: str,
) -> bool:
    """
    Check if page ancestors contain specific ancestor ID (non-assertion version).
    
    Returns True if ancestor found, False otherwise (including API errors).
    """
    try:
        resp = await datasource.get_page_content_v1(
            str(page_id),
            expand="version,space,ancestors",
        )
        if resp.status != 200:
            return False
        
        data = resp.json()
        ancestors = data.get("ancestors") or []
        if not isinstance(ancestors, list):
            return False
        
        ids = {str(a.get("id")) for a in ancestors if isinstance(a, dict) and a.get("id") is not None}
        return str(ancestor_content_id) in ids
    except Exception:
        return False


async def check_page_count_in_space_bool(
    datasource: ConfluenceDataSource,
    space_key: str,
    expected_count: int,
) -> bool:
    """
    Check if page count in space equals expected (non-assertion version).
    
    Returns True if count matches, False otherwise (including API errors).
    """
    try:
        actual_count = await count_confluence_space_pages_v1_search(datasource, space_key)
        return actual_count == expected_count
    except Exception:
        return False


async def check_multiple_pages_in_search_bool(
    datasource: ConfluenceDataSource,
    space_key: str,
    page_ids: list[str],
) -> bool:
    """
    Check if all specified page IDs are in v1 content/search (non-assertion version).
    
    Returns True if all pages found, False otherwise (including API errors).
    """
    try:
        for page_id in page_ids:
            if not await check_page_in_v1_search_bool(datasource, space_key, page_id):
                return False
        return True
    except Exception:
        return False

