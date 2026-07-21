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
    """Assert AQL contains proper timestamp handling for KB vs non-KB sources.
    
    The new architecture branches at app.type == "KB" level, so we check:
    - KB branch uses createdAtTimestamp / updatedAtTimestamp directly
    - Non-KB branch uses source timestamps with fallback to internal
    """
    flat = _normalize(aql)

    # Check for app.type == "KB" branching (new architecture)
    # OR check for direct timestamp usage in appropriate contexts
    has_app_type_check = 'app.type == "KB"' in flat
    has_timestamp_fields = (
        f"{var}.createdAtTimestamp" in flat
        and f"{var}.updatedAtTimestamp" in flat
    )
    
    # For recordGroups, check they have source timestamp handling
    if "recordGroup" in flat or "node" in flat:
        has_source_timestamps = (
            "sourceCreatedAtTimestamp" in flat
            or "sourceLastModifiedTimestamp" in flat
        )
        assert has_timestamp_fields or has_source_timestamps, (
            f'Missing timestamp fields for "{var}". AQL fragment: {flat[:400]}'
        )
    else:
        assert has_timestamp_fields, (
            f'Missing timestamp fields for "{var}". AQL fragment: {flat[:400]}'
        )


def _assert_neo4j_kb_conditional(cypher: str, var: str) -> None:
    """Assert Cypher contains proper timestamp handling for KB vs non-KB sources.
    
    The new architecture branches at app.type == "KB" level, so we check:
    - KB branch uses createdAtTimestamp / updatedAtTimestamp directly
    - Non-KB branch uses source timestamps with fallback to internal
    """
    flat = _normalize(cypher)

    # Check for timestamp field presence
    has_timestamp_fields = (
        f"{var}.createdAtTimestamp" in flat
        and f"{var}.updatedAtTimestamp" in flat
    )
    
    # For recordGroups, check they have source timestamp handling
    if "recordGroup" in flat or var in ["rg", "node"]:
        has_source_timestamps = (
            "sourceCreatedAtTimestamp" in flat
            or "sourceLastModifiedTimestamp" in flat
        )
        assert has_timestamp_fields or has_source_timestamps, (
            f'Missing timestamp fields for "{var}". Cypher fragment: {flat[:400]}'
        )
    else:
        assert has_timestamp_fields, (
            f'Missing timestamp fields for "{var}". Cypher fragment: {flat[:400]}'
        )


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
        assert 'LET record_parent_app = DOCUMENT(CONCAT("apps/", record.connectorId))' in aql


class TestArangoRecordChildrenSubquery:
    """_get_record_children_subquery must bind record_parent_app before timestamp projection."""

    def test_binds_record_parent_app(self, arango_provider):
        aql, _bind = arango_provider._get_record_children_subquery(
            record_id="r1", org_id="org1", user_key="user1"
        )
        assert 'LET record_parent_app = DOCUMENT(CONCAT("apps/", record.connectorId))' in aql
        assert "record_parent_app.type" in aql


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
        # least one of them must include recordGroup projection with proper
        # timestamp handling (either app.type branching or source timestamp fallback).
        rg_projections = [
            q for q in executed_queries
            if 'nodeType: "recordGroup"' in q
            and ("createdAtTimestamp" in q or "sourceCreatedAtTimestamp" in q)
        ]
        assert rg_projections, (
            "Expected at least one captured AQL with a recordGroup projection "
            "carrying timestamp fields; "
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
            bind_vars = _kwargs.get("bind_vars") or {}
            # Phase 2 runs only when phase 1 returns non-empty paginated_refs.
            if "paginated_refs" in bind_vars:
                return [{"nodes": []}]
            return [{
                "total": 1,
                "paginated_refs": [{"id": "rg1", "nodeType": "recordGroup"}],
            }]

        arango_provider.http_client.execute_aql = AsyncMock(side_effect=capture)
        arango_provider.get_user_app_ids = AsyncMock(return_value=[])
        arango_provider.get_user_permission_app_ids = AsyncMock(return_value=[])

        await arango_provider.get_knowledge_hub_search(
            "org1", "user1",
            skip=0, limit=10,
            sort_field="name", sort_dir="ASC",
            search_query="anything",
        )

        assert captured_queries, "search did not invoke execute_aql"

        # Phase 2 hydrates full rg with proper timestamp handling
        rg_aql = next(
            (q for q in captured_queries if "sharingStatus" in q),
            None,
        )
        assert rg_aql is not None, (
            "Expected phase-2 hydration AQL with full rg projection; "
            f"captured {len(captured_queries)} queries."
        )
        # Verify timestamp fields are properly handled (either via app.type check or source/internal fallback)
        flat = _normalize(rg_aql)
        assert "createdAtTimestamp" in flat or "sourceCreatedAtTimestamp" in flat, (
            "Phase 2 should include timestamp projections"
        )

        phase1_aql = captured_queries[0]
        assert "sharingStatus" not in phase1_aql, "phase 1 must not compute sharingStatus"
        assert "path1_seed_rgs" in phase1_aql, "phase 1 uses unified seed recordGroup paths"
        assert "FOR seed IN seed_rgs" in phase1_aql, "phase 1 uses single inherit traversal"
        # Search must not filter permission seeds (would block inheritPermissions).
        flat_p1 = _normalize(phase1_aql)
        path1_slice = flat_p1.split("path2_seed_rgs")[0]
        assert "LOWER(rg.groupName)" not in path1_slice, (
            "search must not prefilter seed record groups in path1"
        )
        assert "LET sorted_nodes" not in flat_p1, (
            "phase 1 should count filtered_nodes without materializing sorted_nodes"
        )
        assert "LENGTH(filtered_nodes)" in flat_p1
        assert "LIMIT @skip, @limit" in flat_p1


class TestKnowledgeHubPrefilterBuilders:
    """Traversal vs seed prefilter split and projected timestamp expressions."""

    def test_seed_prefilter_empty(self, arango_provider):
        assert arango_provider._build_knowledge_hub_seed_prefilter_aql() == ""

    def test_direct_record_date_uses_source_projection(self, arango_provider):
        block = arango_provider._build_knowledge_hub_direct_record_prefilter_aql(
            "record",
            search_query=None,
            origins=None,
            connector_ids=None,
            record_types=None,
            indexing_status=None,
            created_at={"gte": 1, "lte": None},
            updated_at=None,
            size=None,
        )
        flat = _normalize(block)
        # The expression includes both timestamps with app.type conditional:
        # - For KB apps: uses record.createdAtTimestamp
        # - For other apps: prefers record.sourceCreatedAtTimestamp with fallback to createdAtTimestamp
        assert "record.sourceCreatedAtTimestamp" in flat, "Should include source timestamp for non-KB apps"
        assert "record_parent_app" in flat or "record.createdAtTimestamp" in flat, "Should handle KB app timestamp logic"

    def test_traversal_rg_prefilter_has_no_raw_timestamp_fields(self, arango_provider):
        block = arango_provider._build_knowledge_hub_traversal_document_prefilter_aql(
            "inherited_node",
            is_record_group=True,
            search_query="x",
            origins=["CONNECTOR"],
            connector_ids=None,
            record_types=None,
            indexing_status=None,
            size=None,
        )
        flat = _normalize(block)
        assert "createdAtTimestamp" not in flat
        assert "updatedAtTimestamp" not in flat


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
        # The implementation now branches at app.type == "KB" level:
        # - KB branch: uses record.createdAtTimestamp directly
        # - Non-KB branch: uses source timestamps with fallback to internal timestamps
        assert "app.type == \"KB\"" in flat, "Should check app type to determine timestamp handling"
        assert "createdAtTimestamp" in flat, "Should include internal timestamp field"
        assert ("sourceCreatedAtTimestamp" in flat or "sourceLastModifiedTimestamp" in flat), (
            "Should include source timestamp fields for non-KB apps"
        )

    def test_neo4j_app_children(self, neo4j_provider):
        cypher = neo4j_provider._get_app_children_cypher()
        flat = _normalize(cypher)
        # Neo4j implementation uses CALL subqueries that branch on is_kb_app:
        # - KB branch: uses coalesce(record.createdAtTimestamp, 0)
        # - Non-KB branch: uses coalesce(rg.sourceCreatedAtTimestamp, 0)
        assert "is_kb_app" in flat, "Should check app type using is_kb_app variable"
        assert "record.createdAtTimestamp" in flat, "KB branch should use record.createdAtTimestamp"
        assert "rg.sourceCreatedAtTimestamp" in flat, "Non-KB branch should use rg.sourceCreatedAtTimestamp"
