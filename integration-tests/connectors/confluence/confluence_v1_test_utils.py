"""
Confluence v1 REST helpers for Confluence connector integration tests.

Asserts Confluence Cloud state (content/search, content/{id}) so failures
distinguish API issues from graph/sync issues. Lives next to
``confluence_integration_test.py`` and ``conftest.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from app.sources.external.confluence.confluence import ConfluenceDataSource  # type: ignore[import-not-found]

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
