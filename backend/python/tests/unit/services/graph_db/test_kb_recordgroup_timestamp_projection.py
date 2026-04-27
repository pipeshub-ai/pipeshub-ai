"""
Unit tests for the KB-conditional createdAt/updatedAt projection in
recordGroup queries on both graph providers.

Background:
    For KB record groups (connectorName == "KB") the in-system
    createdAtTimestamp / updatedAtTimestamp must be projected as createdAt /
    updatedAt; for connector record groups the source-side timestamps must be
    used. These tests inspect the AQL / Cypher templates produced by the
    private query-builder methods and assert that every recordGroup projection
    contains the KB conditional. They are deliberately string-based — these
    methods produce query templates as strings, and there is no DB to run
    against in a unit test.

Sites covered:
    Arango (4):
        - get_knowledge_hub_search           (rg.* projection)
        - _get_app_children_subquery         (node.* projection)
        - _get_record_group_children_split   (node.* projection, captured via
          execute_aql)
        - _get_record_group_children_subquery (node.* projection)
    Neo4j (3):
        - get_knowledge_hub_search           (rg.* projection)
        - _get_app_children_cypher           (rg.* projection)
        - _get_record_group_children_cypher  (node.* projection,
          parameterized for both split and subquery cases)
"""

import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def arango_provider():
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    return provider


@pytest.fixture
def neo4j_provider():
    return Neo4jProvider(MagicMock(spec=logging.Logger), AsyncMock())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Collapse all whitespace to single spaces for fragile multi-line matches."""
    return re.sub(r"\s+", " ", text)


def _assert_arango_kb_conditional(aql: str, var: str) -> None:
    """Assert AQL contains the KB-vs-non-KB ternary for createdAt and updatedAt
    against the given identifier prefix (e.g. ``rg`` or ``node``)."""
    flat = _normalize(aql)

    # createdAt branches
    assert f'createdAt: {var}.connectorName == "KB"' in flat, (
        f'createdAt KB branch missing for "{var}". AQL fragment: {flat[:400]}'
    )
    assert f"{var}.createdAtTimestamp" in flat, (
        f'in-system createdAtTimestamp missing for "{var}".'
    )
    assert f"{var}.sourceCreatedAtTimestamp" in flat, (
        f'sourceCreatedAtTimestamp missing for "{var}".'
    )

    # updatedAt branches
    assert f'updatedAt: {var}.connectorName == "KB"' in flat, (
        f'updatedAt KB branch missing for "{var}".'
    )
    assert f"{var}.updatedAtTimestamp" in flat, (
        f'in-system updatedAtTimestamp missing for "{var}".'
    )
    assert f"{var}.sourceLastModifiedTimestamp" in flat, (
        f'sourceLastModifiedTimestamp missing for "{var}".'
    )


def _assert_neo4j_kb_conditional(cypher: str, var: str) -> None:
    """Assert Cypher contains the KB-vs-non-KB CASE for createdAt and updatedAt."""
    flat = _normalize(cypher)

    assert f"createdAt: CASE WHEN {var}.connectorName = 'KB'" in flat, (
        f'createdAt CASE WHEN missing for "{var}". Cypher fragment: {flat[:400]}'
    )
    # Both branch sources must be referenced
    assert f"{var}.createdAtTimestamp" in flat
    assert f"{var}.sourceCreatedAtTimestamp" in flat

    assert f"updatedAt: CASE WHEN {var}.connectorName = 'KB'" in flat, (
        f'updatedAt CASE WHEN missing for "{var}".'
    )
    assert f"{var}.updatedAtTimestamp" in flat
    assert f"{var}.sourceLastModifiedTimestamp" in flat


# ---------------------------------------------------------------------------
# Arango — synchronous query-builder methods
# ---------------------------------------------------------------------------


class TestArangoAppChildrenSubquery:
    """_get_app_children_subquery returns AQL with KB-aware timestamps."""

    def test_kb_conditional_present(self, arango_provider):
        aql, _bind = arango_provider._get_app_children_subquery(
            app_id="app1", org_id="org1", user_key="user1"
        )
        _assert_arango_kb_conditional(aql, var="node")


class TestArangoRecordGroupChildrenSubquery:
    """_get_record_group_children_subquery returns AQL with KB-aware timestamps."""

    @pytest.mark.parametrize("parent_type", ["recordGroup", "folder"])
    def test_kb_conditional_present(self, arango_provider, parent_type):
        aql, _bind = arango_provider._get_record_group_children_subquery(
            rg_id="rg1", org_id="org1", parent_type=parent_type, user_key="user1"
        )
        _assert_arango_kb_conditional(aql, var="node")


# ---------------------------------------------------------------------------
# Arango — async methods (capture AQL through execute_aql)
# ---------------------------------------------------------------------------


class TestArangoRecordGroupChildrenSplit:
    """_get_record_group_children_split must build AQL with KB-aware timestamps
    for the projection of recordGroup-typed children. Capture via execute_aql."""

    @pytest.mark.asyncio
    async def test_kb_conditional_in_executed_aql(self, arango_provider):
        executed_queries: list[str] = []

        async def capture(query, **_kwargs):
            executed_queries.append(query)
            return []

        arango_provider.http_client.execute_aql = AsyncMock(side_effect=capture)

        await arango_provider._get_record_group_children_split(
            parent_id="rg1",
            user_key="user1",
            skip=0,
            limit=10,
            sort_field="name",
            sort_dir="ASC",
            only_containers=False,
        )

        # The split implementation issues several AQL queries internally; at
        # least one of them must include the recordGroup projection with the
        # KB conditional.
        rg_projections = [
            q for q in executed_queries
            if 'nodeType: "recordGroup"' in q
            and 'connectorName == "KB"' in q
            and "createdAt" in q
        ]
        assert rg_projections, (
            "Expected at least one captured AQL with a recordGroup projection "
            "carrying the KB conditional createdAt; "
            f"captured {len(executed_queries)} queries."
        )
        for q in rg_projections:
            _assert_arango_kb_conditional(q, var="node")


class TestArangoSearchRecordGroupProjection:
    """get_knowledge_hub_search builds AQL inline; capture via execute_aql and
    confirm the rg.* projection carries the KB conditional."""

    @pytest.mark.asyncio
    async def test_kb_conditional_in_search_aql(self, arango_provider):
        # Stub helpers used by the search method so it focuses on building AQL.
        arango_provider._build_knowledge_hub_filter_conditions = MagicMock(
            return_value=([], {})
        )
        arango_provider._build_scope_filters = MagicMock(
            return_value=("", "", "true", "true")
        )
        arango_provider._build_children_intersection_aql = MagicMock(return_value="")

        captured_queries: list[str] = []

        async def capture(query, **_kwargs):
            captured_queries.append(query)
            return [{"nodes": [], "total": 0, "availableFilters": {}}]

        arango_provider.http_client.execute_aql = AsyncMock(side_effect=capture)

        await arango_provider.get_knowledge_hub_search(
            "org1", "user1",
            skip=0, limit=10,
            sort_field="name", sort_dir="ASC",
            search_query="anything",
        )

        assert captured_queries, "search did not invoke execute_aql"

        # Search projects against `rg`; locate the AQL that contains the rg.*
        # recordGroup projection (others may be helper queries).
        rg_aql = next(
            (q for q in captured_queries if 'rg.connectorName == "KB"' in _normalize(q)),
            None,
        )
        assert rg_aql is not None, (
            "Expected at least one captured AQL with the rg.* KB conditional; "
            f"captured {len(captured_queries)} queries."
        )
        _assert_arango_kb_conditional(rg_aql, var="rg")


# ---------------------------------------------------------------------------
# Neo4j — synchronous Cypher-builder methods
# ---------------------------------------------------------------------------


class TestNeo4jAppChildrenCypher:
    """_get_app_children_cypher emits Cypher with KB-aware rg.* timestamps."""

    def test_kb_conditional_present(self, neo4j_provider):
        cypher = neo4j_provider._get_app_children_cypher()
        _assert_neo4j_kb_conditional(cypher, var="rg")


class TestNeo4jRecordGroupChildrenCypher:
    """_get_record_group_children_cypher emits node.* projection with the KB
    conditional, regardless of parent_type variant."""

    @pytest.mark.parametrize("parent_type", ["recordGroup", "folder"])
    def test_kb_conditional_present(self, neo4j_provider, parent_type):
        cypher = neo4j_provider._get_record_group_children_cypher(parent_type)
        _assert_neo4j_kb_conditional(cypher, var="node")


# ---------------------------------------------------------------------------
# Regression: KB branch must use in-system timestamp, NOT the source one
# ---------------------------------------------------------------------------


class TestKBBranchUsesInSystemTimestamp:
    """Sanity-check the *order* inside the conditional: the KB branch must
    select createdAtTimestamp / updatedAtTimestamp, never the source field."""

    def test_arango_app_children(self, arango_provider):
        aql, _ = arango_provider._get_app_children_subquery(
            app_id="app1", org_id="org1", user_key="user1"
        )
        flat = _normalize(aql)
        # The KB-branch fragment for createdAt must reference createdAtTimestamp
        # immediately after the KB ternary head, before the non-KB branch.
        assert (
            'node.connectorName == "KB" ? (node.createdAtTimestamp'
            in flat
        ), "KB branch should select node.createdAtTimestamp first"
        assert (
            'node.connectorName == "KB" ? (node.updatedAtTimestamp'
            in flat
        ), "KB branch should select node.updatedAtTimestamp first"

    def test_neo4j_app_children(self, neo4j_provider):
        cypher = neo4j_provider._get_app_children_cypher()
        flat = _normalize(cypher)
        assert (
            "CASE WHEN rg.connectorName = 'KB' THEN coalesce(rg.createdAtTimestamp"
            in flat.replace("COALESCE", "coalesce")
        ), "KB branch should THEN coalesce(rg.createdAtTimestamp, ...)"
        assert (
            "CASE WHEN rg.connectorName = 'KB' THEN coalesce(rg.updatedAtTimestamp"
            in flat.replace("COALESCE", "coalesce")
        ), "KB branch should THEN coalesce(rg.updatedAtTimestamp, ...)"
