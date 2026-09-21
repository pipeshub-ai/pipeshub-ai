"""`Neo4jProvider.list_kb_records` count and filter queries must aggregate
folder records before matching root records. In a single pipeline the planner
re-expands the KB's BELONGS_TO edges once per root record — quadratic in KB
size (14+ minutes for a 12k-record KB)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ROOT_MATCH = "OPTIONAL MATCH (rootRecord:Record)-[:BELONGS_TO]->(kb)"


@pytest.fixture
def provider() -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=[{"total": 0, "filters": {}}])
    p.get_user_kb_permission = AsyncMock(return_value="OWNER")
    return p


def _queries_with_root_match(provider: Neo4jProvider) -> list[str]:
    queries = [c.args[0] if c.args else c.kwargs["query"] for c in provider.client.execute_query.await_args_list]
    return [q for q in queries if ROOT_MATCH in q]


@pytest.mark.asyncio
async def test_folder_records_are_aggregated_before_the_root_match(provider: Neo4jProvider) -> None:
    await provider.list_kb_records("kb-1", "user-1", "org-1", skip=0, limit=10)

    queries = _queries_with_root_match(provider)
    assert len(queries) == 2  # count + filters
    for query in queries:
        before_root = query[: query.index(ROOT_MATCH)]
        assert "collect(DISTINCT folderRecord)" in before_root
        assert "collect(DISTINCT folderRecord) + collect" not in query


@pytest.mark.asyncio
async def test_page_order_has_a_unique_tie_breaker(provider: Neo4jProvider) -> None:
    """Upload batches share one createdAtTimestamp; ordering by it alone made
    SKIP/LIMIT pages repeat 1,103 of 12,441 records and never return others."""
    await provider.list_kb_records("kb-1", "user-1", "org-1", skip=0, limit=10)
    queries = [c.args[0] if c.args else c.kwargs["query"] for c in provider.client.execute_query.await_args_list]
    main = next(q for q in queries if "SKIP $skip" in q)
    assert "ORDER BY result.createdAtTimestamp DESC, result.id" in main


@pytest.mark.asyncio
async def test_root_branch_survives_a_kb_with_no_folders(provider: Neo4jProvider) -> None:
    """The barrier is `WITH kb, collect(...)`. Aggregating over zero rows drops
    every non-grouping variable, so `kb` has to stay in the WITH or a KB whose
    records all sit at the root — what an upload produces — lists as empty.
    """
    await provider.list_kb_records("kb-1", "user-1", "org-1", skip=0, limit=10)

    for query in _queries_with_root_match(provider):
        barrier = query[: query.index(ROOT_MATCH)]
        with_clause = barrier[barrier.rindex("WITH "):]
        assert with_clause.startswith("WITH kb,"), with_clause
        # The OPTIONAL MATCH must carry its own predicate; a WHERE after the
        # barrier would filter the aggregated row away instead of the match.
        assert "collect(DISTINCT folderRecord)" in with_clause
