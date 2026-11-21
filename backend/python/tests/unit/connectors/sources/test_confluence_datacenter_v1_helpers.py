"""Unit tests for Confluence Data Center v1 migration helpers (no live API)."""

from collections.abc import Awaitable, Callable

import pytest

pytestmark = pytest.mark.confluence_datacenter

from app.connectors.sources.atlassian.confluence_datacenter.connector import (
    ConfluenceDataCenterConnector,
)


class TestConfluenceDataCenterV1Helpers:
    def test_pagination_token_from_next_link_cursor(self) -> None:
        c = object.__new__(ConfluenceDataCenterConnector)
        url = "https://example.com/wiki/rest/api/content/search?cql=type%3Dpage&cursor=abc123"
        assert c._pagination_token_from_next_link(url) == "abc123"

    def test_pagination_token_from_next_link_start(self) -> None:
        c = object.__new__(ConfluenceDataCenterConnector)
        url = "https://example.com/wiki/rest/api/space?limit=25&start=50"
        assert c._pagination_token_from_next_link(url) == "50"

    def test_pagination_token_from_next_link_none(self) -> None:
        c = object.__new__(ConfluenceDataCenterConnector)
        assert c._pagination_token_from_next_link(None) is None


def test_an_empty_page_that_links_onward_is_not_the_end_of_a_listing() -> None:
    from app.connectors.sources.atlassian.core.confluence_access import v1_next_start

    links = {"_links": {"next": "/rest/api/group/eng/member?start=2"}}
    for use_link_offset in (False, True):
        with pytest.raises(ValueError, match="empty page"):
            v1_next_start(links, 1, 0, 200, use_link_offset=use_link_offset)
    assert v1_next_start({"_links": {"base": "x"}}, 1, 0, 200, use_link_offset=False) is None
    assert v1_next_start(links, 0, 1, 200, use_link_offset=False) == 1


@pytest.mark.parametrize("body", [
    pytest.param({"size": 0, "_links": {}}, id="no-results"),
    pytest.param({"results": None}, id="null-results"),
    pytest.param([{"id": "1"}], id="a-list-not-a-page"),
    pytest.param("busy", id="not-json-object"),
    pytest.param({"results": [{"id": "1"}, "x"]}, id="an-item-that-is-not-an-object"),
    pytest.param({"results": [{"id": "1"}], "size": 2}, id="size-says-more"),
    pytest.param({"results": [{"id": "1"}], "_links": []}, id="links-not-an-object"),
    pytest.param({"results": [], "_links": {"next": "/rest/api/x?start=25"}}, id="empty-page-that-links-on"),
    pytest.param({"results": [{}], "size": 1}, id="an-item-without-an-id"),
])
def test_read_page_refuses_what_is_not_a_page_read_in_full(body) -> None:
    from app.connectors.sources.atlassian.core.confluence_access import (
        content_id,
        read_page,
    )
    assert read_page(body, content_id) is None


def test_read_page_reads_a_page_and_its_links() -> None:
    from app.connectors.sources.atlassian.core.confluence_access import (
        group_key,
        read_page,
        user_key,
    )
    page = read_page({"results": [{"name": "eng"}], "size": 1, "_links": {"base": "b", "next": "n"}}, group_key)
    assert (page.results, page.base, page.next_url) == ([{"name": "eng"}], "b", "n")
    assert read_page({"results": []}).results == []
    assert read_page({"results": [{"userKey": ""}]}, user_key) is None, "an empty key is no key"


class _Answer:
    def __init__(self, status: int, body: object = None) -> None:
        self.status, self._body = status, body

    def json(self) -> object:
        return self._body


def _pages(answers: dict[int, object], asked: list[int]) -> Callable[[int, int], Awaitable[_Answer]]:
    async def fetch(start: int, limit: int) -> _Answer:
        asked.append(start)
        answer = answers[start]
        if isinstance(answer, Exception):
            raise answer
        return answer
    return fetch


@pytest.mark.asyncio
async def test_read_all_pages_reads_after_the_rows_whatever_the_link_names() -> None:
    from app.connectors.sources.atlassian.core.confluence_access import (
        content_id,
        read_all_pages,
    )
    asked: list[int] = []
    answers = {0: _Answer(200, {"results": [{"id": "1"}], "_links": {"base": "b", "next": "/x?start=100"}}),
               1: _Answer(200, {"results": [{"id": "2"}], "_links": {"next": "/x?cursor=opaque"}}),
               2: _Answer(200, {"results": [], "_links": {"base": "b"}})}
    assert await read_all_pages(_pages(answers, asked), content_id) == ([{"id": "1"}, {"id": "2"}], "b")
    assert asked == [0, 1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("second", [_Answer(500, {"message": "boom"}), _Answer(200, {"results": None}),
                                    RuntimeError("connection reset"), None])
async def test_read_all_pages_is_unread_when_any_page_is(second) -> None:
    from app.connectors.sources.atlassian.core.confluence_access import read_all_pages
    answers = {0: _Answer(200, {"results": [{"id": "1"}], "_links": {"next": "/x?start=1"}}), 1: second}
    assert await read_all_pages(_pages(answers, [])) is None


@pytest.mark.asyncio
async def test_read_all_pages_of_content_that_is_gone_is_empty() -> None:
    from app.connectors.sources.atlassian.core.confluence_access import read_all_pages
    assert await read_all_pages(_pages({0: _Answer(404, {"message": "No content found"})}, [])) == ([], None)


@pytest.mark.asyncio
async def test_read_all_pages_with_a_404_after_rows_came_back_is_unread() -> None:
    from app.connectors.sources.atlassian.core.confluence_access import read_all_pages
    answers = {0: _Answer(200, {"results": [{"id": "1"}], "_links": {"next": "/x?start=1"}}),
               1: _Answer(404, {"message": "No content found"})}
    assert await read_all_pages(_pages(answers, [])) is None, "it existed a moment ago: the rest is unread, not gone"


@pytest.mark.asyncio
async def test_read_all_pages_follows_the_continuation_a_9x_child_list_names() -> None:
    from app.connectors.sources.atlassian.core.confluence_access import read_all_pages
    asked: list[int] = []
    answers = {0: _Answer(200, {"results": [{"id": "1"}], "start": 0, "limit": 25, "next": "/x?limit=25&start=25"}),
               1: _Answer(200, {"results": [{"id": "2"}], "start": 1, "limit": 25})}
    assert await read_all_pages(_pages(answers, asked)) == ([{"id": "1"}, {"id": "2"}], None)
    assert asked == [0, 1]


@pytest.mark.asyncio
async def test_read_all_pages_reads_on_while_the_total_count_says_more() -> None:
    from app.connectors.sources.atlassian.core.confluence_access import read_all_pages
    answers = {0: _Answer(200, {"results": [{"id": "1"}], "totalCount": 2, "_links": {"base": "b"}}),
               1: _Answer(200, {"results": [{"id": "2"}], "totalCount": 2, "_links": {"base": "b"}})}
    assert await read_all_pages(_pages(answers, [])) == ([{"id": "1"}, {"id": "2"}], "b")


@pytest.mark.asyncio
async def test_a_null_size_is_an_absent_one() -> None:
    from app.connectors.sources.atlassian.core.confluence_access import (
        read_all_pages,
        v1_next_start,
    )
    assert v1_next_start({"results": [{"id": "1"}], "size": None}, 0, 1, 100, use_link_offset=False) is None
    assert await read_all_pages(_pages({0: _Answer(200, {"results": [{"id": "1"}], "size": None})}, [])) == ([{"id": "1"}], None)
