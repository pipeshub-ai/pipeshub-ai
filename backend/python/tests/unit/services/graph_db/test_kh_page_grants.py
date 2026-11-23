"""A Knowledge Hub page of one App counts a grant on a node of any gated App (the walk from the App can pass through one), so it
needs every gated connector's grants or the user to read them. Asked with one connector's grants alone while another connector is
gated, it refuses rather than answer a narrower walk; given the user, it reads the others itself."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import org_context as oc
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.provider import PostgresGraphProvider

APP, OTHER, ORG = "app-a", "app-b", "org-1"


@pytest.mark.parametrize(("gated", "by_connector", "user_key", "refused"), [
    ([APP, OTHER], None, None, True),           # another connector gated and nothing to read its grants with
    ([APP], None, None, False),                 # the App alone: its grants are all a walk can meet
    ([APP, OTHER], {APP: ["g"], OTHER: []}, None, False),
    ([APP, OTHER], None, "user-1", False),
])
def test_one_connectors_grants_alone_are_refused_only_when_another_connector_is_gated(gated, by_connector, user_key, refused) -> None:
    def ask() -> None:
        IGraphDBProvider._kh_v3_require_other_grants(APP, gated, ["g"], by_connector, user_key)
    if refused:
        with pytest.raises(ValueError, match="grants_by_connector or user_key"):
            ask()
    else:
        ask()


@pytest.mark.asyncio
async def test_the_user_reads_the_other_connectors_grants_beside_the_apps_own_given_ones() -> None:
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.get_knowledge_hub_access_v3 = AsyncMock(return_value={"by_connector": {APP: ["stale"], OTHER: ["x"], "app-c": ["y"]}})
    every = await provider._kh_v3_page_grants(APP, ORG, [APP, OTHER], ["g"], None, "user-1", None)
    # The given grants stay the App's own; another gated connector's are read; a connector that is not gated is dropped.
    assert every == {APP: ["g"], OTHER: ["x"]}


@pytest.mark.asyncio
async def test_neo4j_takes_the_other_connectors_grants_the_caller_holds_without_a_read() -> None:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(side_effect=AssertionError("no read"))
    cross = await provider._kh_v3_cross(None, ORG, [APP, OTHER], APP, None, {APP: ["g"], OTHER: ["x"]})
    assert cross == ["x"]


@pytest.mark.asyncio
async def test_neo4j_tests_the_grants_of_a_viewer_past_the_bound_on_each_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """Past the bound no grant is dropped: the request tests the viewer's holders on each node instead (`_kh_cross_by_probe`), the user's
    own on any other connector and a source account's on its own. The read asks for one id past the bound, in id order."""
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock(spec=logging.Logger)
    monkeypatch.setattr(Neo4jProvider, "_KH_CROSS_MAX", 2)
    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(side_effect=[
        [{"user": "user-1", "badges": ["role-1"], "orgs": ["org-node"], "linked": [[OTHER, "source-1"], [OTHER, "source-role"]]}],
        [{"ids": ["x", "y", "z"]}]])
    cross = await provider._kh_v3_cross("user-1", ORG, [APP, OTHER], APP, "txn")
    assert cross == {"probe": [OTHER], "common": ["user-1", "org-node", "role-1"], "scoped": {OTHER: ["source-1", "source-role"]}}
    read = provider.client.execute_query.await_args_list[1]
    assert "ORDER BY id LIMIT $limit" in read.args[0] and read.kwargs["parameters"]["limit"] == 3
    provider.logger.warning.assert_called_once()

    # The caller's own grants past the bound: the holders are read, never the grants.
    provider.client.execute_query = AsyncMock(side_effect=[[{"user": "user-1", "badges": [], "orgs": [], "linked": []}]])
    held = await provider._kh_v3_cross("user-1", ORG, [APP, OTHER], APP, "txn", {OTHER: ["z", "x", "y"]})
    assert held == {"probe": [OTHER], "common": ["user-1"], "scoped": {}}
    assert provider.client.execute_query.await_count == 1


@pytest.mark.asyncio
async def test_neo4j_cuts_the_grants_it_holds_past_the_bound_the_same_way_on_every_read_when_no_user_is_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no user to read holders for, the lowest ids are kept, however they arrive, and the cut is logged."""
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock(spec=logging.Logger)
    monkeypatch.setattr(Neo4jProvider, "_KH_CROSS_MAX", 2)
    first = await provider._kh_v3_cross(None, ORG, [APP, OTHER], APP, None, {OTHER: ["z", "x", "y"]})
    again = await provider._kh_v3_cross(None, ORG, [APP, OTHER], APP, None, {OTHER: ["y", "z", "x"]})
    assert first == again == ["x", "y"]
    assert provider.logger.warning.call_count == 2


@pytest.mark.asyncio
async def test_neo4j_runs_a_statement_past_the_bound_in_the_per_node_form() -> None:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(return_value=[])
    cross = {"probe": [OTHER], "common": ["user-1"], "scoped": {}}
    await provider._kh_query("MATCH (ca) WHERE ca.id IN $grantedIds RETURN ca", {"grantedIds": ["g"], "kh_cross": cross})
    sent = provider.client.execute_query.await_args
    assert "$kh_cross" not in sent.args[0] and "(ca)<-[kh_xp_ca:PERMISSION]-(kh_xg_ca)" in sent.args[0]
    assert sent.kwargs["parameters"]["kh_x"] == cross and "kh_cross" not in sent.kwargs["parameters"]


@pytest.mark.asyncio
@pytest.mark.parametrize("store", ["neo4j", "arango", "postgres"])
async def test_every_store_refuses_a_page_asked_with_one_connectors_grants_alone(store: str) -> None:
    if store == "neo4j":
        provider = Neo4jProvider.__new__(Neo4jProvider)
    elif store == "arango":
        provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    else:
        provider = PostgresGraphProvider.__new__(PostgresGraphProvider)
    provider.logger = MagicMock(spec=logging.Logger)
    with oc.bind(oc.OrgContext(ORG, "user-1", "user", "jwt", "req-1")), \
            pytest.raises(ValueError, match="grants_by_connector or user_key"):
        await provider.get_knowledge_hub_connector_page_v3(APP, ORG, ["user-1"], [APP, OTHER], ["g"], flatten=True)
