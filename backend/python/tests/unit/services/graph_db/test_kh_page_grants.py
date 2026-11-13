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
    assert cross == {"probe": [OTHER], "held": ["x"], "common": [], "scoped": {}}


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
