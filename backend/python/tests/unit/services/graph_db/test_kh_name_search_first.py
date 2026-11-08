"""A global name search reads the matching nodes, not the whole connector.

The listing used to build every visible node of the connector (a walk of the
whole tree, 512k nodes on one benchmark connector, 8 s and 250 MB of
transaction memory per page) and apply the name last. These pin the shape that
replaced it on both stores: the names are matched first, and only the matches
are checked for visibility. That the rows are the same is proven on real
stores in tests/integration/graph_permissions/test_kh_name_search_first.py.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

APP = "app1"
ORG = "org1"


def _neo4j(candidates: list[str]) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()

    async def execute(query: str, parameters: dict | None = None, txn_id: str | None = None,
                      **_kwargs: object) -> list[dict]:
        if "AS kh_name_matches" in query:
            return [{"ids": candidates[: parameters["kh_cap"]]}]
        return [{"rows": [], "total": 0, "nRecord": 0, "nFolder": 0, "nGroup": 0}]

    provider.client.execute_query = AsyncMock(side_effect=execute)
    provider._kh_listing_state_ready = AsyncMock(return_value=True)
    provider._kh_scope_mode = AsyncMock(return_value="off")
    return provider


async def _statements(provider: Neo4jProvider, filters: dict | None, **overrides) -> list[tuple[str, dict]]:
    kwargs = {"flatten": True, "filters": filters, "name_parents": False}
    kwargs.update(overrides)
    await provider.get_knowledge_hub_connector_page_v3(APP, ORG, ["uk1"], [APP], ["g1"], **kwargs)
    return [(c.args[0], c.kwargs.get("parameters") or {}) for c in provider.client.execute_query.await_args_list]


def _listing(statements: list[tuple[str, dict]]) -> tuple[str, dict]:
    return next((q, p) for q, p in statements if "$kh_limit" in q or "kh_limit" in str(p.get("kh_lists", "")))


@pytest.mark.asyncio
async def test_neo4j_a_name_search_matches_names_before_it_lists() -> None:
    provider = _neo4j(["r1", "r2"])
    statements = await _statements(provider, {"search_query": "Shipping"})

    match, params = statements[0]
    assert "AS kh_name_matches" in match
    assert "CONTAINS $kh_search" in match and params["kh_search"] == "shipping"
    assert "$org_id" in match

    listing, listing_params = _listing(statements)
    # The matches are the listing's only nodes: no walk down the whole connector.
    assert "AS regionA" not in listing
    assert "collect(DISTINCT na)" not in listing
    assert "UNWIND $kh_lists.kh_candidates" in listing
    assert listing_params["kh_lists"]["kh_candidates"] == ["r1", "r2"]


@pytest.mark.asyncio
async def test_neo4j_each_match_is_placed_by_walking_up_to_the_app() -> None:
    listing, _ = _listing(await _statements(_neo4j(["r1"]), {"search_query": "x"}))
    # The walk from a match ends at the App, which the pattern leaves unbound:
    # bound, the planner can start from the App and search its whole tree.
    assert "<-[" in listing and "WHERE kh_wta = app" in listing
    assert "(app)\n" not in listing


@pytest.mark.asyncio
async def test_neo4j_a_depth_bounded_name_search_takes_the_walk_that_applies_the_bound() -> None:
    statements = await _statements(_neo4j(["r1"]), {"search_query": "x"}, max_depth=2)
    assert not any("AS kh_name_matches" in q for q, _ in statements)
    listing = _listing(statements)[0]
    assert "AS regionA" in listing and "[kh_x IN allNodes WHERE kh_x IN within] AS allNodes" in listing


@pytest.mark.asyncio
async def test_neo4j_a_name_search_deeper_than_50_takes_the_walk_of_that_depth() -> None:
    statements = await _statements(_neo4j(["r1"]), {"search_query": "x"}, max_depth=60)
    assert not any("AS kh_name_matches" in q for q, _ in statements)
    assert "{1,60}" in _listing(statements)[0]


@pytest.mark.asyncio
async def test_neo4j_without_a_name_the_listing_walks_the_connector() -> None:
    statements = await _statements(_neo4j(["r1"]), None)
    assert not any("AS kh_name_matches" in q for q, _ in statements)
    assert "AS regionA" in _listing(statements)[0]


@pytest.mark.asyncio
async def test_neo4j_too_many_matches_fall_back_to_the_full_listing(monkeypatch) -> None:
    monkeypatch.setattr(IGraphDBProvider, "_KH_NAME_FIRST_MAX", 2)
    statements = await _statements(_neo4j(["r1", "r2", "r3"]), {"search_query": "x"})
    assert any("AS kh_name_matches" in q for q, _ in statements)
    assert "AS regionA" in _listing(statements)[0]


@pytest.mark.asyncio
async def test_neo4j_no_match_lists_nothing_without_walking() -> None:
    statements = await _statements(_neo4j([]), {"search_query": "x"})
    listing, params = _listing(statements)
    assert "AS regionA" not in listing
    assert params["kh_lists"]["kh_candidates"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"flatten": False},
    {"start_id": "g1", "start_type": "recordGroup"},
])
async def test_neo4j_browsing_is_unchanged(overrides) -> None:
    provider = _neo4j(["r1"])
    provider._kh_v3_connector_and_size = AsyncMock(return_value=(APP, 0))
    provider._kh_v3_chain_tops = AsyncMock(return_value={})
    provider._kh_v3_scoped_start_access = AsyncMock(return_value=(True, [], []))
    statements = await _statements(provider, {"search_query": "x"}, **overrides)
    assert not any("AS kh_name_matches" in q for q, _ in statements)


@pytest.mark.asyncio
async def test_neo4j_the_matches_are_read_once_for_concurrent_pages() -> None:
    """The global search runs one page per connector at once; the names are
    matched across the org, so the pages share one read of them."""
    import asyncio

    provider = _neo4j(["r1"])
    await asyncio.gather(*(
        provider.get_knowledge_hub_connector_page_v3(
            app, ORG, ["uk1"], [APP, "app2"], [], flatten=True,
            filters={"search_query": "x"}, name_parents=False, user_key=None,
        ) for app in (APP, "app2")
    ))
    reads = [c for c in provider.client.execute_query.await_args_list if "AS kh_name_matches" in c.args[0]]
    assert len(reads) == 1


# ------------------------------------------------------------------ arango

def _arango(candidates: list[str]) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), MagicMock())
    provider.http_client = MagicMock()
    walked: list[tuple[str, dict]] = []

    async def run(name: str, bind: dict, transaction: str | None = None) -> list:
        walked.append((name, bind))
        if name == "app":
            return [{"handle": f"apps/{APP}", "opens": False}]
        if name == "name_matches":
            return candidates[: bind["kh_cap"]]
        return []

    provider._kh_v3_run = AsyncMock(side_effect=run)
    provider._walked = walked
    provider.http_client.execute_aql = AsyncMock(return_value=[])
    provider._kh_v3_page_result = AsyncMock(return_value={"rows": []})
    return provider


async def _arango_steps(provider: ArangoHTTPProvider, filters: dict | None, **overrides) -> list[tuple[str, dict]]:
    await provider.get_knowledge_hub_connector_page_v3(
        APP, ORG, ["uk1"], [APP], ["g1"], flatten=True, filters=filters, name_parents=False, **overrides,
    )
    return provider._walked


@pytest.mark.asyncio
async def test_arango_a_name_search_walks_only_towards_the_matches() -> None:
    steps = await _arango_steps(_arango(["records/r1"]), {"search_query": "Shipping"})
    names = [name for name, _ in steps]
    assert names[1] == "name_matches"
    assert steps[1][1]["kh_search"] == "shipping"
    # The App walk is the restricted one: it reads only the matches and their ancestors.
    assert "step_app" not in names
    assert "step_app_within" in names or "step_up" in names


@pytest.mark.asyncio
async def test_arango_without_a_name_the_walk_is_unchanged() -> None:
    names = [name for name, _ in await _arango_steps(_arango(["records/r1"]), None)]
    assert "name_matches" not in names
    assert "step_app" in names


@pytest.mark.asyncio
@pytest.mark.parametrize(("max_depth", "name_first"), [(2, True), (50, True), (60, False)])
async def test_arango_a_name_search_deeper_than_50_takes_the_full_walk(max_depth, name_first) -> None:
    """The name-restricted walks look at most 50 hops up from a match."""
    names = [name for name, _ in await _arango_steps(
        _arango(["records/r1"]), {"search_query": "x"}, max_depth=max_depth,
    )]
    assert ("name_matches" in names) is name_first
    assert ("step_app" in names) is not name_first


@pytest.mark.asyncio
async def test_arango_too_many_matches_fall_back(monkeypatch) -> None:
    monkeypatch.setattr(IGraphDBProvider, "_KH_NAME_FIRST_MAX", 1)
    names = [name for name, _ in await _arango_steps(_arango(["records/r1", "records/r2"]), {"search_query": "x"})]
    assert "name_matches" in names and "step_app" in names


@pytest.mark.asyncio
async def test_neo4j_the_name_search_page_is_sliced_from_sorted_matches() -> None:
    """ORDER BY + LIMIT over the collected rows keeps the whole row list on each
    row it holds (745 MB for 201 rows of 5650 matches on the benchmark graph);
    the matches are few enough to sort and slice."""
    listing, _ = _listing(await _statements(_neo4j(["r1"]), {"search_query": "x"}))
    assert "CALL (rows)" not in listing
    assert "rows[0..$kh_limit] AS page" in listing

    full, _ = _listing(await _statements(_neo4j(["r1"]), None))
    assert "CALL (rows)" in full
