"""Neo4j's browse lists every child the batch check admits: the page statement hands back the children its rules leave undecided, the check is
asked about them, and the page is read again only when it admits one (a folder and the App alike, one statement when nothing is undecided). A
real graph is in tests/permissions/test_conformance_kh.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _payload(undecided: list[str]) -> list[dict]:
    return [{"rows": [], "total": 0, "nRecord": 0, "nFolder": 0, "nGroup": 0, "crumbNodes": [], "crumbEdges": [], "khUndecided": undecided}]


def _provider(*answers: list[dict], admits: set[str]) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider._kh_listing_state_ready = AsyncMock(return_value=True)
    provider._kh_v3_connector_and_size = AsyncMock(return_value=("app1", 0))
    provider._kh_v3_chain_tops = AsyncMock(return_value={})
    provider._kh_v3_scoped_start_access = AsyncMock(return_value=(True, [], []))
    provider._kh_v3_app_declared_scope = AsyncMock(return_value=[])
    provider._kh_query = AsyncMock(side_effect=list(answers))
    provider.check_access = AsyncMock(side_effect=lambda _u, _o, node_ids=(), **_: AccessCheck(node_ids=frozenset(set(node_ids) & admits)))
    return provider


async def _browse(provider: Neo4jProvider, start: str | None) -> None:
    await provider.get_knowledge_hub_connector_page_v3(
        "app1", "org1", ["u1"], ["app1"], ["g9"], flatten=False, start_id=start, start_type="recordGroup" if start else "app")


async def test_a_folder_is_read_again_with_the_undecided_children_the_check_admits() -> None:
    provider = _provider(_payload(["k1", "k2"]), _payload(["k2"]), admits={"k1"})
    await _browse(provider, "g1")
    first, second = provider._kh_query.await_args_list
    assert "AS khUndecided" in first.args[0] and second.args[0] == first.args[0]
    # a row's own children are read only where its rules select none: a row with an arrow is asked about itself alone
    assert "CASE WHEN built IS NULL OR built.hasChildren THEN [] ELSE" in first.args[0]
    assert set(provider.check_access.await_args_list[0].kwargs["node_ids"]) == {"k1", "k2"}
    assert second.kwargs["parameters"]["kh_placed"] == ["k1"]


async def test_a_folder_whose_undecided_children_the_check_refuses_is_read_once() -> None:
    provider = _provider(_payload(["k2"]), admits=set())
    await _browse(provider, "g1")
    assert provider._kh_query.await_count == 1


async def test_the_app_reads_its_undecided_children_in_its_own_page_statement() -> None:
    """No statement of its own for the undecided children: the App's page statement returns them, as a folder's does."""
    provider = _provider(_payload([]), admits=set())
    await _browse(provider, None)
    statement, = (c.args[0] for c in provider._kh_query.await_args_list)
    assert "AS khUndecided" in statement and "CALL (app, allNodes)" in statement
    assert "CASE WHEN built.hasChildren THEN [] ELSE" in statement
    provider.check_access.assert_not_awaited()

    provider = _provider(_payload(["k1", "k2"]), _payload([]), admits={"k1"})
    await _browse(provider, None)
    first, second = provider._kh_query.await_args_list
    assert second.args[0] == first.args[0] and second.kwargs["parameters"]["kh_placed"] == ["k1"]


async def test_past_the_cap_the_browse_asks_about_the_first_children_and_logs_it() -> None:
    cap = Neo4jProvider._KH_UNDECIDED_MAX
    provider = _provider(_payload([f"k{i:04d}" for i in range(cap + 1)]), admits=set())
    await _browse(provider, "g1")
    assert len(provider.check_access.await_args_list[0].kwargs["node_ids"]) == cap
    assert any("more than %d children" in c.args[0] for c in provider.logger.warning.call_args_list)
