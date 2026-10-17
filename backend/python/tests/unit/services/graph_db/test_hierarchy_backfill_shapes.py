"""The shapes ``backfill_hierarchy`` writes, in both graph providers.

A graph written before the permission rework has no hierarchy edge from an App or a
record group to what it holds. One shape is easy to miss: a record nested under a
record of ANOTHER group (a Jira story under an epic of another project). It inherits
from its own group, but its only hierarchy edge comes from the other group's record,
so nothing reaches it; it has to hang off its own group as well.

Each test runs the real provider method against a stubbed driver and reads what it
sent. A real graph is in tests/integration/graph_permissions/test_hierarchy_backfill.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

SHAPES = ["collection_roots", "groups", "group_roots", "nested_inheritance", "cross_group_children"]
CROSS = "cross_group_children"


def _neo4j(*, left: int = 0) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()

    async def run(query: str, **_kwargs: object) -> list[dict]:
        if "IN TRANSACTIONS" in query:
            return [{"n": 3 if "(pr:Record)" in query else 0}]
        return [{"n": left if "(pr:Record)" in query else 0}]

    provider.client.execute_query = AsyncMock(side_effect=run)
    return provider


def _neo4j_statements(provider: Neo4jProvider) -> tuple[str, str]:
    """The cross-group shape's write and its re-scan."""
    sent = [c.args[0] for c in provider.client.execute_query.await_args_list if "(pr:Record)" in c.args[0]]
    (write,) = [q for q in sent if "IN TRANSACTIONS" in q]
    (rescan,) = [q for q in sent if "IN TRANSACTIONS" not in q]
    return write, rescan


def _arango(*, left: bool = False) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = MagicMock()
    pairs = [[f"recordGroups/g{i}", f"records/c{i}"] for i in range(3)]
    scans: list[str] = []

    async def run(query: str, bind: dict | None = None, **_kwargs: object) -> list:
        if "FOR pr IN @pairs" in query:
            return [1] * len((bind or {})["pairs"])
        cross = "FOR own IN @@ip" in query
        first = query not in scans
        scans.append(query)
        if not cross:
            return []
        return pairs if first or left else []

    provider.http_client.execute_aql = AsyncMock(side_effect=run)
    return provider


def _arango_statements(provider: ArangoHTTPProvider) -> tuple[str, list[tuple[str, dict]]]:
    """The cross-group shape's scan, and every write with what it was bound to."""
    calls = [(c.args[0], c.args[1] if len(c.args) > 1 else {}) for c in provider.http_client.execute_aql.await_args_list]
    scan = next(q for q, _ in calls if "FOR own IN @@ip" in q)
    return scan, [(q, b) for q, b in calls if "FOR pr IN @pairs" in q]


def test_both_providers_write_the_same_shapes() -> None:
    assert list(Neo4jProvider._BACKFILL_SHAPES) == SHAPES
    assert list(ArangoHTTPProvider._BACKFILL_PAIRS) == SHAPES


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
async def test_the_result_counts_every_shape(make) -> None:
    result = await make().backfill_hierarchy(batch_size=2)

    assert list(result["added"]) == SHAPES
    assert result["added"][CROSS] == 3
    assert sum(result["added"].values()) == 3


@pytest.mark.asyncio
async def test_neo4j_hangs_a_cross_group_child_off_its_own_group() -> None:
    provider = _neo4j()
    await provider.backfill_hierarchy(batch_size=2)
    write, rescan = _neo4j_statements(provider)

    for statement in (write, rescan):
        assert "MATCH (child:Record)-[:INHERIT_PERMISSIONS]->(parent:RecordGroup)" in statement
        assert "WHERE NOT coalesce(child.isDeleted, false)" in statement
        assert "EXISTS { MATCH (child)-[:BELONGS_TO]->(parent) }" in statement
        assert "EXISTS { MATCH (:Record)-[h:NODE_RELATION]->(child) WHERE h.relationshipType IN $hierarchy }" in statement
        assert "NOT EXISTS { MATCH (parent)-[h:NODE_RELATION]->(child) WHERE h.relationshipType IN $hierarchy }" in statement
        # A parent record inside the group is nested_inheritance's child, not this shape's.
        assert "EXISTS { MATCH (pr)-[:INHERIT_PERMISSIONS]->(parent) }" in statement
        assert "EXISTS { MATCH (pr)-[:BELONGS_TO]->(parent) }" in statement
    assert "MERGE (parent)-[e:NODE_RELATION {relationshipType: 'PARENT_CHILD'}]->(child)" in write
    assert "MERGE (child)-[e:INHERIT_PERMISSIONS]->(parent)" not in write
    assert "IN TRANSACTIONS OF 2 ROWS" in write


@pytest.mark.asyncio
async def test_arango_hangs_a_cross_group_child_off_its_own_group() -> None:
    provider = _arango()
    await provider.backfill_hierarchy(batch_size=2)
    scan, writes = _arango_statements(provider)

    assert 'IS_SAME_COLLECTION("records", own._from) AND IS_SAME_COLLECTION("recordGroups", own._to)' in scan
    assert "child.isDeleted != true" in scan
    assert "FILTER b._from == own._from AND b._to == own._to" in scan
    assert "FILTER h._from == own._to AND h._to == own._from AND h.relationshipType IN @hierarchy" in scan
    # A parent record inside the group is nested_inheritance's child, not this shape's.
    assert "FILTER i._from == pr AND i._to == own._to" in scan
    assert "FILTER b._from == pr AND b._to == own._to" in scan
    assert "RETURN DISTINCT [own._to, own._from]" in scan

    assert [len(bind["pairs"]) for _, bind in writes] == [2, 1], "three pairs in batches of two"
    for write, _ in writes:
        assert 'INSERT {_from: pr[0], _to: pr[1], relationshipType: "PARENT_CHILD",' in write
        assert "INTO @@nr" in write and "INTO @@ip" not in write
        # Re-checked when written, so a second run, or a sync in between, adds nothing.
        assert "FILTER h._from == pr[0] AND h._to == pr[1] AND h.relationshipType IN @hierarchy" in write


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make", [lambda: _neo4j(left=2), lambda: _arango(left=True)], ids=["neo4j", "arango"],
)
async def test_a_cross_group_child_left_unwritten_fails_the_backfill(make) -> None:
    with pytest.raises(RuntimeError, match=f"{CROSS} edge"):
        await make().backfill_hierarchy()
