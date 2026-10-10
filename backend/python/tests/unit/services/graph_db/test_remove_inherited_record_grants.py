"""``remove_inherited_record_grants`` in both graph providers, against a stubbed
driver: it removes the grants of a record of the named connectors and types only
when one of its grantees holds a grant on a live group the record belongs to and
inherits from, and fails when any such grant is left. Arango pages through the
records instead of holding every grant in memory. A real graph is in
tests/integration/graph_permissions/test_mailbox_record_grants.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

CONNECTORS, TYPES = ["OUTLOOK"], ["MAIL", "FILE"]

# Record key -> whether its grants go; two grants each.
RECORDS = {"r1": True, "r2": False, "r3": True, "r4": True, "r5": False}


def _arango(*, left: int = 0) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = MagicMock()

    async def run(query: str, bind: dict | None = None, **_kwargs: object) -> list:
        bind = bind or {}
        if "REMOVE e IN @@perm" in query:
            return [f"records/{k}" for k in bind["keys"] for _ in range(2)]
        if query.lstrip().startswith("RETURN LENGTH("):
            return [left]
        after = [k for k in sorted(RECORDS) if k > bind["after"]][: bind["batch"]]
        return [[k, RECORDS[k]] for k in after]

    provider.http_client.execute_aql = AsyncMock(side_effect=run)
    return provider


def _neo4j(*, left: int = 0) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()

    async def run(query: str, **_kwargs: object) -> list[dict]:
        if "IN TRANSACTIONS" in query:
            return [{"removed": 6, "records": 3}]
        return [{"remaining": left}]

    provider.client.execute_query = AsyncMock(side_effect=run)
    return provider


def _arango_calls(provider: ArangoHTTPProvider) -> list[tuple[str, dict]]:
    return [(c.args[0], c.args[1]) for c in provider.http_client.execute_aql.await_args_list]


@pytest.mark.asyncio
async def test_arango_pages_through_the_records_and_never_holds_every_grant() -> None:
    provider = _arango()
    assert await provider.remove_inherited_record_grants(CONNECTORS, TYPES, batch_size=2) == {
        "removed": 6, "records": 3,
    }
    calls = _arango_calls(provider)
    pages = [(q, b) for q, b in calls if "@after" in q]
    assert [b["after"] for _, b in pages] == ["", "r2", "r4"], "a short page is the last"
    assert all(b["batch"] == 2 for _, b in pages)
    for page, bind in pages:
        assert "FILTER r._key > @after" in page and "SORT r._key" in page and "LIMIT @batch" in page
        assert "FILTER r.connectorName IN @connectors AND r.recordType IN @types" in page
        assert bind["connectors"] == CONNECTORS and bind["types"] == TYPES
    removes = [b["keys"] for q, b in calls if "REMOVE e IN @@perm" in q]
    assert removes == [["r1"], ["r3", "r4"]], "each page's hits, removed before the next page is read"


def _assert_arango_guard(query: str) -> None:
    # A live group the record belongs to and inherits from ...
    assert 'IS_SAME_COLLECTION("recordGroups", i._to)' in query
    assert "g != null AND g.isDeleted != true" in query
    assert "FILTER b._from == r._id AND b._to == g._id" in query
    # ... that one of the record's own grantees holds a grant on.
    assert "FILTER q._to == g._id AND q._from == p._from" in query


@pytest.mark.asyncio
async def test_arango_removes_grants_only_where_the_group_repeats_a_grantee() -> None:
    provider = _arango()
    await provider.remove_inherited_record_grants(CONNECTORS, TYPES, batch_size=2)
    calls = _arango_calls(provider)

    page = next(q for q, _ in calls if "@after" in q)
    removes = [q for q, _ in calls if "REMOVE e IN @@perm" in q]
    (check,) = [q for q, _ in calls if q.lstrip().startswith("RETURN LENGTH(")]
    for query in [page, check, *removes]:
        _assert_arango_guard(query)
    # Re-checked when removed: decided per record before any of its grants go.
    for remove in removes:
        assert remove.index("LET ids") < remove.index("REMOVE e IN @@perm")
        assert "FILTER e._to IN ids" in remove


@pytest.mark.asyncio
async def test_neo4j_removes_grants_only_where_the_group_repeats_a_grantee() -> None:
    provider = _neo4j()
    assert await provider.remove_inherited_record_grants(CONNECTORS, TYPES, batch_size=2) == {
        "removed": 6, "records": 3,
    }
    (write, rescan) = [c.args[0] for c in provider.client.execute_query.await_args_list]
    for statement in (write, rescan):
        assert "MATCH (r:Record)" in statement
        assert "r.connectorName IN $connectors AND r.recordType IN $types" in statement
        assert "(r)<-[:PERMISSION]-(x)-[:PERMISSION]->(g:RecordGroup)<-[:INHERIT_PERMISSIONS]-(r)" in statement
        assert "NOT coalesce(g.isDeleted, false)" in statement
        assert "EXISTS { (r)-[:BELONGS_TO]->(g) }" in statement
    # One row per record: its grants are decided together, before any of them goes.
    assert "CALL (r) {" in write and "DELETE p" in write and "IN TRANSACTIONS OF 2 ROWS" in write
    assert "DELETE" not in rescan


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
async def test_a_grant_left_behind_fails_the_removal(make) -> None:
    with pytest.raises(RuntimeError, match="left on inheriting records"):
        await make(left=1).remove_inherited_record_grants(CONNECTORS, TYPES)
