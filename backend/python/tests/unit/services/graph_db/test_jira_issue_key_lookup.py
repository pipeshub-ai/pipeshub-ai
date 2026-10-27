"""get_record_by_issue_key matches the issue key exactly on both graph providers.

The lookup used to match "/browse/{key}" anywhere in the webUrl, so ENG-1 also
matched ENG-12 and ENG-123, and LIMIT 1 returned whichever came first. The Jira
deletion paths then deleted that other issue. The fakes below apply the bound
regex the way each database does (Neo4j ``=~`` matches the whole string, Arango
``REGEX_TEST`` searches) and return the near-miss row first.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.utils import jira_issue_browse_url_regex
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

if TYPE_CHECKING:
    from collections.abc import Iterator

SITE = "https://acme.atlassian.net"
ENG_12 = {"_key": "r-eng-12", "webUrl": f"{SITE}/browse/ENG-12"}
ENG_123 = {"_key": "r-eng-123", "webUrl": f"{SITE}/browse/ENG-123"}
ENG_1 = {"_key": "r-eng-1", "webUrl": f"{SITE}/browse/ENG-1"}


@pytest.mark.parametrize(
    "url",
    [
        f"{SITE}/browse/ENG-1",
        f"{SITE}/browse/ENG-1/",
        f"{SITE}/browse/ENG-1?focusedCommentId=10001",
        f"{SITE}/browse/ENG-1#comment",
        "http://jira.internal:8080/jira/browse/ENG-1",
    ],
)
def test_regex_matches_the_issue_itself(url: str) -> None:
    pattern = jira_issue_browse_url_regex("ENG-1")
    assert re.fullmatch(pattern, url)
    assert re.search(pattern, url)


@pytest.mark.parametrize(
    "url",
    [
        f"{SITE}/browse/ENG-12",
        f"{SITE}/browse/ENG-123",
        f"{SITE}/browse/ENG-10?focusedCommentId=1",
        f"{SITE}/browse/XENG-1",
    ],
)
def test_regex_rejects_other_issues(url: str) -> None:
    pattern = jira_issue_browse_url_regex("ENG-1")
    assert not re.fullmatch(pattern, url)
    assert not re.search(pattern, url)


def test_regex_escapes_the_key() -> None:
    pattern = jira_issue_browse_url_regex("MY_PROJ.X-7")
    assert re.fullmatch(pattern, f"{SITE}/browse/MY_PROJ.X-7")
    assert not re.fullmatch(pattern, f"{SITE}/browse/MY_PROJzX-7")


def _neo4j_with_rows(rows: list[dict[str, Any]]) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())

    async def execute_query(query: str, parameters: dict[str, Any], txn_id: str | None = None) -> list[dict]:
        pattern = parameters.get("browse_pattern_regex", "")
        return [{"record": row} for row in rows if re.fullmatch(pattern, row["webUrl"])][:1]

    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(side_effect=execute_query)
    provider._neo4j_to_arango_node = MagicMock(side_effect=lambda node, _collection: node)  # type: ignore[method-assign]
    return provider


def _arango_with_rows(rows: list[dict[str, Any]]) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), AsyncMock())

    async def execute_aql(query: str, bind_vars: dict[str, Any], txn_id: str | None = None) -> list[dict]:
        assert "REGEX_TEST(record.webUrl, @browse_pattern_regex)" in query
        pattern = bind_vars["browse_pattern_regex"]
        return [{"record": row, "ticket": None} for row in rows if re.search(pattern, row["webUrl"])][:1]

    provider.http_client = MagicMock()
    provider.http_client.execute_aql = AsyncMock(side_effect=execute_aql)
    provider._create_typed_record_from_arango = MagicMock(side_effect=lambda record, _ticket: record)  # type: ignore[method-assign]
    return provider


@pytest.fixture
def record_passthrough() -> Iterator[None]:
    with patch(
        "app.services.graph_db.neo4j.neo4j_provider.Record.from_arango_base_record",
        side_effect=lambda data: data,
    ):
        yield


@pytest.mark.usefixtures("record_passthrough")
class TestNeo4jIssueKeyLookup:
    async def test_eng_1_skips_eng_12_listed_first(self) -> None:
        provider = _neo4j_with_rows([ENG_12, ENG_123, ENG_1])
        assert await provider.get_record_by_issue_key("conn-1", "ENG-1") == ENG_1

    async def test_eng_1_absent_returns_none_not_eng_12(self) -> None:
        provider = _neo4j_with_rows([ENG_12, ENG_123])
        assert await provider.get_record_by_issue_key("conn-1", "ENG-1") is None
        provider.logger.error.assert_not_called()


class TestArangoIssueKeyLookup:
    async def test_eng_1_skips_eng_12_listed_first(self) -> None:
        provider = _arango_with_rows([ENG_12, ENG_123, ENG_1])
        assert await provider.get_record_by_issue_key("conn-1", "ENG-1") == ENG_1

    async def test_eng_1_absent_returns_none_not_eng_12(self) -> None:
        provider = _arango_with_rows([ENG_12, ENG_123])
        assert await provider.get_record_by_issue_key("conn-1", "ENG-1") is None
        provider.logger.error.assert_not_called()
