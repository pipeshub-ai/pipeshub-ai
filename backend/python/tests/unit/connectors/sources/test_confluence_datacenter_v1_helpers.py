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
