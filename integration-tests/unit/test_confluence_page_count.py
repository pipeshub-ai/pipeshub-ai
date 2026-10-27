"""Unit tests for the Confluence page-count check (no live services).

``assert_confluence_pages_match_graph_records`` compares the pages Confluence's
v1 search lists for a space with what the connector stored. The v1 search
asks for ``type=page`` only, while the connector also stores the space's blog
posts and folders as records, so only page records may be counted against it.

Every CI run shares the space, so both sides leave out test pages that are not the
caller's own: other runs create, edit and delete theirs mid-test.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.models.entities import RecordType
from connectors.confluence.confluence_v1_test_utils import (
    assert_confluence_pages_match_graph_records,
    count_confluence_space_pages_v1_search,
    count_run_records,
    wait_for_space_page_count_at_least,
)
from connectors.confluence.constants import is_test_page

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
    """Record nodes as the Neo4j and ArangoDB helpers return them."""

    def __init__(self, types: list[str], names: dict[int, str] | None = None) -> None:
        self.records = [
            {"externalRecordId": str(i), "recordType": t, "recordName": (names or {}).get(i, f"Page {i}")}
            for i, t in enumerate(types)
        ]

    async def fetch_records_by_type(
        self, connector_id: str, record_type: str, *, scoped: bool = False
    ) -> list[dict[str, Any]]:
        return [r for r in self.records if record_type in ("", r["recordType"])]


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


OTHER_RUNS = {
    "30": "Integration Test Page Beta 29ce5c17",
    "31": "Renamed-Integration Test Page Alpha fab987f9",
    "32": "TC-CF-024 Test Page 8793550c",
}


async def test_other_runs_test_pages_are_left_out_on_both_sides() -> None:
    # Our run created page 33; the graph synced other runs' pages 30-32 that the
    # listing no longer returns (deleted by their teardown since).
    titles = {**OTHER_RUNS, "33": "Parent Page 1a2b3c4d"}
    names = {int(k): v for k, v in titles.items()}
    await assert_confluence_pages_match_graph_records(
        _Datasource([str(i) for i in range(30)] + ["33"], titles),  # type: ignore[arg-type]
        _Graph(_space(34), names),  # type: ignore[arg-type]
        CONNECTOR, "SPACE", phase="after sync", own_ids=["33"],
    )
    assert await count_run_records(_Graph(_space(34), names), CONNECTOR, own_ids=["33"]) == 33  # type: ignore[arg-type]
    assert await count_run_records(_Graph(_space(34), names), CONNECTOR) == 32  # type: ignore[arg-type]


@pytest.mark.parametrize("title", [
    "Integration Test Page Alpha 79ca476e",
    "Renamed-Integration Test Page Alpha 79ca476e",
    "Parent Page c1422314",
    "TC-CF-024 Test Page af8511fc",
    "PipesHub IT matrix renamed mxe572db5a99",
])
def test_pages_the_tests_create_are_test_pages(title: str) -> None:
    assert is_test_page(title)


@pytest.mark.parametrize("title", [
    "Page-1-DO-NOT-DELETE",
    "Folder-Page-1-DO-NOT-DELETE",
    "InitTestPage1-6c8712",
    "connsim-golden page 20261014-015322 flat",
])
def test_the_spaces_permanent_pages_are_not(title: str) -> None:
    assert not is_test_page(title)
