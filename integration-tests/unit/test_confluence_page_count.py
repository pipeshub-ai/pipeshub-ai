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
from connectors.confluence.constants import ARTIFACT_PREFIX, RUN_PREFIX
from connectors.confluence.confluence_v1_test_utils import (
    assert_confluence_pages_match_graph_records,
    count_confluence_space_pages_v1_search,
    wait_for_space_page_count_at_least,
)

pytestmark = pytest.mark.unit

CONNECTOR = "conn-1"


class _Resp:
    status = 200

    def __init__(self, ids: list[str], titles: dict[str, str] | None = None) -> None:
        self._ids = ids
        self._titles = titles or {}

    def json(self) -> dict[str, Any]:
        return {
            "results": [{"id": i, "title": self._titles.get(i, f"Page {i}")} for i in self._ids],
            "_links": {},
        }


class _Datasource:
    """v1 content/search: one page of results, pages only."""

    def __init__(self, page_ids: list[str], titles: dict[str, str] | None = None) -> None:
        self.page_ids = page_ids
        self.titles = titles

    async def get_pages_v1(self, **_: Any) -> _Resp:
        return _Resp(self.page_ids, self.titles)


class _Graph:
    """Record nodes by recordType, counted the way the Neo4j and ArangoDB helpers count them."""

    def __init__(self, types: list[str], names: list[str] | None = None) -> None:
        self.types = types
        self.names = names or [f"Record {i}" for i in range(len(types))]

    async def fetch_records_by_type(
        self, connector_id: str, record_type: str, *, scoped: bool = False
    ) -> list[dict[str, Any]]:
        return [
            {"recordType": t, "recordName": n}
            for t, n in zip(self.types, self.names)
            if not record_type or t == record_type
        ]

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


async def test_another_runs_pages_are_left_out_on_both_sides() -> None:
    # The arango and neo4j jobs share the space: the other run's page is listed by
    # Confluence but never reached this graph (or was deleted after it did).
    titles = {"0": f"{RUN_PREFIX}ours", "1": f"{ARTIFACT_PREFIX}0therrun-theirs"}
    graph = _Graph([RecordType.CONFLUENCE_PAGE.value] * 2, names=[f"{RUN_PREFIX}ours", "Home"])
    await assert_confluence_pages_match_graph_records(
        _Datasource(["0", "1", "2"], titles), graph,  # type: ignore[arg-type]
        CONNECTOR, "SPACE", phase="after sync",
    )


class _LaggingDatasource:
    """The space-wide listing leaves out pages just created, then catches up."""

    def __init__(self, listings: list[int]) -> None:
        self.listings = listings
        self.reads = 0

    async def get_pages_v1(self, **_: Any) -> _Resp:
        count = self.listings[min(self.reads, len(self.listings) - 1)]
        self.reads += 1
        return _Resp([str(i) for i in range(count)])


async def test_one_read_right_after_creating_two_pages_can_count_one() -> None:
    # Nightly 37826872241: 35 pages, two created and both found by id, and the
    # next listing counted 36.
    assert await count_confluence_space_pages_v1_search(_LaggingDatasource([36, 37]), "SPACE") == 36  # type: ignore[arg-type]


async def test_the_count_is_read_again_until_the_new_pages_are_listed() -> None:
    source = _LaggingDatasource([36, 36, 37])
    count = await wait_for_space_page_count_at_least(source, "SPACE", 37, timeout=5, poll_interval=0)  # type: ignore[arg-type]
    assert count == 37
    assert source.reads == 3


async def test_a_count_that_never_gets_there_is_returned_at_the_deadline() -> None:
    source = _LaggingDatasource([36])
    assert await wait_for_space_page_count_at_least(source, "SPACE", 37, timeout=0, poll_interval=0) == 36  # type: ignore[arg-type]
    assert source.reads == 1
