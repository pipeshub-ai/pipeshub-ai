"""Identifier lookup returns the issue that was asked for, or nothing.

Before #3914 the Jira browse-URL pattern was ``.*/browse/PP-8.*``, so looking
up PP-8 through ``/api/v1/knowledge-graph/lookup`` (the MCP
``pipeshub_get_record_content`` lookup mode) returned PP-80 when that row came
first, and an agent then read the wrong ticket. The provider tests in
``tests/unit/services/graph_db/test_jira_issue_key_lookup.py`` pin the regex;
these drive the whole resolver over the real providers, with each database
client faked to apply the bound regex the way that database does (Neo4j ``=~``
matches the whole string, Arango ``REGEX_TEST`` searches) and the near
neighbours listed first.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.catalog import ConnectorCatalog, ConnectorInfo
from app.agents.actions.knowledge_graph.resolver import RecordResolver
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

if TYPE_CHECKING:
    from collections.abc import Iterator

SITE = "https://acme.atlassian.net"
Provider = Neo4jProvider | ArangoHTTPProvider


def _ticket(key: str) -> dict[str, Any]:
    return {
        "_key": f"rec-{key}",
        "recordName": f"{key} summary",
        "recordType": "TICKET",
        "connectorName": "JIRA",
        "webUrl": f"{SITE}/browse/{key}",
    }


PP_8, PP_80, PP_81 = _ticket("PP-8"), _ticket("PP-80"), _ticket("PP-81")
NEIGHBOURS_FIRST = [PP_80, PP_81, PP_8]


def _exact_weburl(rows: list[dict[str, Any]]) -> AsyncMock:
    async def lookup(url: str, org_id: str | None = None) -> dict[str, Any] | None:
        return next((r for r in rows if r["webUrl"] == url), None)

    return AsyncMock(side_effect=lookup)


def _grant_every_node(provider: Provider) -> None:
    async def access(node_id: str, **_: object) -> dict[str, Any]:
        return {"id": node_id, "nodeType": "record"}

    provider.get_knowledge_hub_node_access = AsyncMock(side_effect=access)


def _neo4j(rows: list[dict[str, Any]]) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())

    async def execute_query(query: str, parameters: dict[str, Any], txn_id: str | None = None) -> list[dict]:
        pattern = parameters["browse_pattern_regex"]
        return [{"record": row} for row in rows if re.fullmatch(pattern, row["webUrl"])][:1]

    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(side_effect=execute_query)
    provider._neo4j_to_arango_node = MagicMock(side_effect=lambda node, _collection: node)  # type: ignore[method-assign]
    provider.get_record_by_weburl = _exact_weburl(rows)  # type: ignore[method-assign]
    _grant_every_node(provider)
    return provider


def _arango(rows: list[dict[str, Any]]) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), AsyncMock())

    async def execute_aql(query: str, bind_vars: dict[str, Any], txn_id: str | None = None) -> list[dict]:
        assert "REGEX_TEST(record.webUrl, @browse_pattern_regex)" in query
        pattern = bind_vars["browse_pattern_regex"]
        return [{"record": row, "ticket": None} for row in rows if re.search(pattern, row["webUrl"])][:1]

    provider.http_client = MagicMock()
    provider.http_client.execute_aql = AsyncMock(side_effect=execute_aql)
    provider._create_typed_record_from_arango = MagicMock(side_effect=lambda record, _ticket: record)  # type: ignore[method-assign]
    provider.get_record_by_weburl = _exact_weburl(rows)  # type: ignore[method-assign]
    _grant_every_node(provider)
    return provider


@pytest.fixture(autouse=True)
def record_passthrough() -> Iterator[None]:
    with patch(
        "app.services.graph_db.neo4j.neo4j_provider.Record.from_arango_base_record",
        side_effect=lambda data: data,
    ):
        yield


MakeProvider = Callable[[list[dict[str, Any]]], Provider]
PROVIDERS: list[MakeProvider] = [_neo4j, _arango]


def _resolver(provider: Provider) -> RecordResolver:
    return RecordResolver(
        graph_provider=provider,
        catalog=ConnectorCatalog([ConnectorInfo(id="conn-jira", name="Jira Cloud", type="JIRA")]),
        org_id="org-1",
        user_id="user-1",
        user_key="user-key-1",
        folder_mime_types=[],
    )


@pytest.mark.parametrize("make_provider", PROVIDERS, ids=["neo4j", "arango"])
@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("PP-8", PP_8),
        ("PP-80", PP_80),
        ("PP-81", PP_81),
        (f"{SITE}/browse/PP-8", PP_8),
        (f"{SITE}/browse/PP-8?focusedCommentId=1", PP_8),
    ],
)
async def test_each_key_resolves_to_exactly_its_own_issue(
    make_provider: MakeProvider, identifier: str, expected: dict[str, Any],
) -> None:
    result = await _resolver(make_provider(NEIGHBOURS_FIRST)).resolve_many([identifier])

    assert [m.id for m in result.matches] == [expected["_key"]]
    assert result.not_found_identifiers == []
    assert result.ambiguous is False


@pytest.mark.parametrize("make_provider", PROVIDERS, ids=["neo4j", "arango"])
async def test_missing_key_is_not_found_rather_than_a_near_neighbour(
    make_provider: MakeProvider,
) -> None:
    result = await _resolver(make_provider([PP_80, PP_81])).resolve_many(["PP-8"])

    assert result.matches == []
    assert result.not_found_identifiers == ["PP-8"]


@pytest.mark.parametrize("make_provider", PROVIDERS, ids=["neo4j", "arango"])
@pytest.mark.parametrize("identifier", ["PP-8.*", "PP-8.", "PP-8\\d", "PP-[0-9]+", "PP-8|PP-80"])
async def test_regex_metacharacters_never_widen_the_match(
    make_provider: MakeProvider, identifier: str,
) -> None:
    result = await _resolver(make_provider(NEIGHBOURS_FIRST)).resolve_many([identifier])

    assert result.matches == []
    assert result.not_found_identifiers == [identifier]
