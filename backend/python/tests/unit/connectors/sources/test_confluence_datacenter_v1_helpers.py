"""Unit tests for Confluence Data Center v1 migration helpers (no live API)."""

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
