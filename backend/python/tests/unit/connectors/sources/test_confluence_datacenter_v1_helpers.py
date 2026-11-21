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
