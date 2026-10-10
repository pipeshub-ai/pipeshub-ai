"""A user group is one node per connector and external id (SERVICENOW-08 /
N4MISC-02), on both providers: the lookup by external id always returns the same
copy of a duplicated group (the oldest), and the merge moves the other copies'
edges onto that one and deletes them.

The behaviour on real databases is in
tests/integration/graph_permissions/test_resync_identity.py; these pin what each
provider sends.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

PAIRS = [{"keeper": "old", "dups": ["new-1", "new-2"]}]


@pytest.fixture
def arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql.return_value = []
    return provider


@pytest.fixture
def neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query.return_value = []
    return provider


def _aql(provider: ArangoHTTPProvider) -> list[tuple[str, dict]]:
    return [(c.args[0], c.kwargs.get("bind_vars") or (c.args[1] if len(c.args) > 1 else {}))
            for c in provider.http_client.execute_aql.await_args_list]


def _cypher(provider: Neo4jProvider) -> list[tuple[str, dict]]:
    return [(c.args[0], c.kwargs.get("parameters") or {}) for c in provider.client.execute_query.await_args_list]


class TestTheLookupIsDeterministic:
    @pytest.mark.asyncio
    async def test_arango_returns_the_oldest_copy(self, arango) -> None:
        await arango.get_user_group_by_external_id("sn-1", "acme")

        query, _ = _aql(arango)[0]
        assert "SORT group.createdAtTimestamp == null ? 0 : group.createdAtTimestamp, group._key" in query
        assert query.index("SORT") < query.index("LIMIT 1")

    @pytest.mark.asyncio
    async def test_neo4j_returns_the_oldest_copy(self, neo4j) -> None:
        await neo4j.get_user_group_by_external_id("sn-1", "acme")

        query, _ = _cypher(neo4j)[0]
        assert "ORDER BY coalesce(g.createdAtTimestamp, 0), g.id" in query
        assert query.index("ORDER BY") < query.index("LIMIT 1")


class TestArangoMerge:
    @pytest.mark.asyncio
    async def test_nothing_duplicated_changes_nothing(self, arango) -> None:
        assert await arango.merge_duplicate_user_groups(["SERVICENOW"]) == {"groups": 0, "removed": 0, "moved": 0}
        assert len(_aql(arango)) == 1

    @pytest.mark.asyncio
    async def test_the_copies_edges_move_to_the_oldest_and_the_copies_go(self, arango) -> None:
        responses = iter([PAIRS] + [[1], [], [1], []] + [[]] * 4 + [[1, 1]] + [[0]])
        arango.http_client.execute_aql.side_effect = lambda *a, **k: next(responses)

        result = await arango.merge_duplicate_user_groups(["SERVICENOW"])

        assert result == {"groups": 1, "removed": 2, "moved": 2}
        statements = _aql(arango)
        find, *moves, delete, check = statements
        assert "SORT c.at, c.key" in find[0] and find[1]["connectors"] == ["SERVICENOW"]
        assert [bind["@c"] for _, bind in moves] == ["permission"] * 4 + ["belongsTo"] * 4
        inserts, removes = moves[0::2], moves[1::2]
        assert all("INSERT MERGE(UNSET(found[0]" in q and "COLLECT k = keeper" in q for q, _ in inserts)
        assert all("FOR x IN @@c FILTER" in q for q, _ in inserts)
        assert all("REMOVE e IN @@c" in q for q, _ in removes)
        assert {"_from", "_to"} == {q.split("FILTER e.")[1].split(" ")[0] for q, _ in removes}
        assert all(bind["pairs"] == PAIRS for _, bind in moves)
        assert "REMOVE {_key: d} IN groups" in delete[0]

    @pytest.mark.asyncio
    async def test_a_copy_left_behind_raises(self, arango) -> None:
        responses = iter([PAIRS] + [[]] * 8 + [[1, 1]] + [[1]])
        arango.http_client.execute_aql.side_effect = lambda *a, **k: next(responses)

        with pytest.raises(RuntimeError, match="left after the merge"):
            await arango.merge_duplicate_user_groups(["SERVICENOW"])


class TestNeo4jMerge:
    @pytest.mark.asyncio
    async def test_nothing_duplicated_changes_nothing(self, neo4j) -> None:
        assert await neo4j.merge_duplicate_user_groups(["SERVICENOW"]) == {"groups": 0, "removed": 0, "moved": 0}
        assert len(_cypher(neo4j)) == 1

    @pytest.mark.asyncio
    async def test_the_copies_edges_move_to_the_oldest_and_the_copies_go(self, neo4j) -> None:
        responses = iter(
            [[{"keeper": "old", "dups": ["new-1", "new-2"]}]]
            + [[{"moved": 1}]] * 4
            + [[{"removed": 2}], [{"left": 0}]]
        )
        neo4j.client.execute_query.side_effect = lambda *a, **k: next(responses)

        result = await neo4j.merge_duplicate_user_groups(["SERVICENOW"])

        assert result == {"groups": 1, "removed": 2, "moved": 4}
        find, *moves, delete, check = _cypher(neo4j)
        assert "ORDER BY coalesce(g.createdAtTimestamp, 0), g.id" in find[0]
        assert find[1] == {"connectors": ["SERVICENOW"]}
        assert [("PERMISSION" in q, "BELONGS_TO" in q) for q, _ in moves] == [(True, False)] * 2 + [(False, True)] * 2
        assert all("SET n = properties(head(rs))" in q and "FOREACH (r IN rs | DELETE r)" in q for q, _ in moves)
        assert all("count(x) AS kept" in q for q, _ in moves)
        assert "CREATE (k)-[n:PERMISSION]->(o)" in moves[0][0] and "CREATE (o)-[n:PERMISSION]->(k)" in moves[1][0]
        assert all(params == {"pairs": PAIRS} for _, params in moves)
        assert "DETACH DELETE d" in delete[0] and delete[1] == {"dups": ["new-1", "new-2"]}

    @pytest.mark.asyncio
    async def test_a_copy_left_behind_raises(self, neo4j) -> None:
        responses = iter([[{"keeper": "old", "dups": ["new-1"]}]] + [[{"moved": 0}]] * 4 + [[{"removed": 0}], [{"left": 1}]])
        neo4j.client.execute_query.side_effect = lambda *a, **k: next(responses)

        with pytest.raises(RuntimeError, match="left after the merge"):
            await neo4j.merge_duplicate_user_groups(["SERVICENOW"])
