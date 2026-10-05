"""Unit tests for the Confluence page-count check (no live services).

``assert_confluence_pages_match_graph_records`` compares the pages Confluence's
v1 search lists for a space with what the connector stored. The v1 search
asks for ``type=page`` only, while the connector also stores the space's blog
posts and folders as records, so only page records may be counted against it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.models.entities import RecordType
from connectors.confluence.confluence_v1_test_utils import (
    assert_confluence_pages_match_graph_records,
)

pytestmark = pytest.mark.unit

CONNECTOR = "conn-1"


class _Resp:
    status = 200

    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    def json(self) -> dict[str, Any]:
        return {"results": [{"id": i} for i in self._ids], "_links": {}}


class _Datasource:
    """v1 content/search: one page of results, pages only."""

    def __init__(self, page_ids: list[str]) -> None:
        self.page_ids = page_ids

    async def get_pages_v1(self, **_: Any) -> _Resp:
        return _Resp(self.page_ids)


class _Graph:
    """Record nodes by recordType, counted the way the Neo4j and ArangoDB helpers count them."""

    def __init__(self, types: list[str]) -> None:
        self.types = types

    async def count_records(self, connector_id: str, *, scoped: bool = False) -> int:
        return len(self.types)

    async def count_records_by_type(
        self, connector_id: str, record_type: str, *, scoped: bool = False
    ) -> int:
        return sum(1 for t in self.types if t == record_type)


def _space(pages: int) -> list[str]:
    return [RecordType.CONFLUENCE_PAGE.value] * pages + [
        RecordType.CONFLUENCE_BLOGPOST.value,
        RecordType.FILE.value,
    ]


async def test_a_space_with_a_blog_post_and_a_folder_matches_on_pages() -> None:
    await assert_confluence_pages_match_graph_records(
        _Datasource([str(i) for i in range(33)]), _Graph(_space(33)),  # type: ignore[arg-type]
        CONNECTOR, "SPACE", phase="after sync",
    )


async def test_a_page_missing_from_the_graph_is_reported() -> None:
    with pytest.raises(AssertionError, match=r"page count \(33\) != graph page record count \(32\)"):
        await assert_confluence_pages_match_graph_records(
            _Datasource([str(i) for i in range(33)]), _Graph(_space(32)),  # type: ignore[arg-type]
            CONNECTOR, "SPACE", phase="after sync",
        )
