"""`Neo4jProvider.list_kb_records`'s filter query must aggregate folder
records before matching root records. In a single pipeline the planner
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
    p.client.execute_query = AsyncMock(return_value=[{"total": 0, "records": [], "filters": {}}])
    p.get_user_kb_permission = AsyncMock(return_value="OWNER")
    return p


def _queries_with_root_match(provider: Neo4jProvider) -> list[str]:
    queries = [c.args[0] if c.args else c.kwargs["query"] for c in provider.client.execute_query.await_args_list]
    return [q for q in queries if ROOT_MATCH in q]


@pytest.mark.asyncio
async def test_folder_records_are_aggregated_before_the_root_match(provider: Neo4jProvider) -> None:
    await provider.list_kb_records("kb-1", "user-1", "org-1", skip=0, limit=10)

    queries = _queries_with_root_match(provider)
    assert len(queries) == 1
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
    main = next(q for q in queries if "$skip" in q)
    assert "ORDER BY record.createdAtTimestamp DESC, record.id" in main


@pytest.mark.asyncio
async def test_files_are_matched_for_the_page_only(provider: Neo4jProvider) -> None:
    """Matching every record's file and building its map before ORDER BY, plus
    a separate count query doing the same, cost ~5 s a page on a 100k-record KB."""
    await provider.list_kb_records("kb-1", "user-1", "org-1", skip=0, limit=10)
    queries = [c.args[0] if c.args else c.kwargs["query"] for c in provider.client.execute_query.await_args_list]
    paged = [q for q in queries if "$skip" in q]
    assert len(paged) == 1
    assert not [q for q in queries if "count(result)" in q]
    main = paged[0]
    assert main.index("rows[$skip..") < main.index("IS_OF_TYPE")


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


@pytest.mark.asyncio
async def test_negative_bounds_do_not_slice_from_the_end(provider: Neo4jProvider) -> None:
    """`rows[0..-5]` is every row but the last five; SKIP/LIMIT used to reject it."""
    await provider.list_kb_records("kb-1", "user-1", "org-1", skip=-20, limit=-5)
    calls = provider.client.execute_query.await_args_list
    params = next(c.kwargs.get("parameters") or c.args[1] for c in calls if "$skip" in (c.args[0] if c.args else c.kwargs["query"]))
    assert (params["skip"], params["limit"]) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args", [
    ("get_kb_children", ("kb-1",)),
    ("get_folder_children", ("kb-1", "folder-1")),
])
async def test_folders_sharing_a_name_keep_their_page_order(provider: Neo4jProvider, method: str, args: tuple) -> None:
    """A failed restore rename leaves two folders with one name; sorting by name
    alone let them swap across a page boundary."""
    provider.client.execute_query = AsyncMock(return_value=[])
    provider.get_document = AsyncMock(return_value={"id": "kb-1", "_key": "kb-1"})
    await getattr(provider, method)(*args, skip=0, limit=10)
    queries = [c.args[0] if c.args else c.kwargs["query"] for c in provider.client.execute_query.await_args_list]
    folder_sorts = [line.strip() for q in queries for line in q.splitlines() if "recordName ASC" in line and "ORDER BY" in line]
    assert folder_sorts
    assert all(s.endswith(".id") for s in folder_sorts), folder_sorts
