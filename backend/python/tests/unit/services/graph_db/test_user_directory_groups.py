"""``get_user_directory_groups``: the groups a PipesHub login belongs to via synced directory accounts.

Run against mocked clients, so they pin the query shape callers depend on
(the AUTHENTICATED_AS hop, org scoping, the app filter, bounded output) and the
row mapping, not the database's behaviour.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ROW = {"id": "k1", "externalId": "entra-g1", "name": "Finance", "connectorId": "c1", "appName": "ONEDRIVE"}


def _neo4j(rows) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=rows)
    return p


def _arango(rows) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = AsyncMock(return_value=rows)
    return p


class TestNeo4j:
    async def test_maps_rows(self) -> None:
        assert await _neo4j([ROW]).get_user_directory_groups("u1", "o1") == [ROW]

    async def test_missing_fields_become_empty_strings(self) -> None:
        rows = [{"id": "k", "externalId": None, "name": None, "connectorId": "c", "appName": None}]
        result = await _neo4j(rows).get_user_directory_groups("u1", "o1")
        assert result == [{"id": "k", "externalId": "", "name": "", "connectorId": "c", "appName": ""}]

    async def test_unknown_user_is_an_empty_list(self) -> None:
        assert await _neo4j([]).get_user_directory_groups("nobody", "o1") == []

    async def test_walks_the_authenticated_as_hop_scoped_to_the_org(self) -> None:
        p = _neo4j([])
        await p.get_user_directory_groups("u1", "o1", app_names=["ONEDRIVE"], limit=25)
        query, params = p.client.execute_query.call_args.args[0], p.client.execute_query.call_args.kwargs["parameters"]
        assert "AUTHENTICATED_AS" in query
        assert "PERMISSION {type: 'USER'}" in query
        assert "g:Group {orgId: $org_id}" in query
        assert "LIMIT $limit" in query
        assert params == {"user_id": "u1", "org_id": "o1", "app_names": ["ONEDRIVE"], "limit": 25}

    async def test_a_linked_account_contributes_only_its_connectors_groups(self) -> None:
        p = _neo4j([])
        await p.get_user_directory_groups("u1", "o1")
        query = p.client.execute_query.call_args.args[0]
        assert "g.connectorId = link.connectorId" in query

    async def test_an_empty_app_filter_stays_an_empty_filter(self) -> None:
        p = _neo4j([])
        await p.get_user_directory_groups("u1", "o1", app_names=[])
        assert p.client.execute_query.call_args.kwargs["parameters"]["app_names"] == []

    async def test_no_app_filter_binds_null_and_limit_is_at_least_one(self) -> None:
        p = _neo4j([])
        await p.get_user_directory_groups("u1", "o1", limit=0)
        params = p.client.execute_query.call_args.kwargs["parameters"]
        assert params["app_names"] is None and params["limit"] == 1

    async def test_errors_propagate_so_a_resolver_can_tell_unknown_from_none(self) -> None:
        p = _neo4j([])
        p.client.execute_query = AsyncMock(side_effect=RuntimeError("db down"))
        with pytest.raises(RuntimeError):
            await p.get_user_directory_groups("u1", "o1")


class TestArango:
    async def test_maps_rows(self) -> None:
        assert await _arango([ROW]).get_user_directory_groups("u1", "o1") == [ROW]

    async def test_query_follows_the_same_contract(self) -> None:
        p = _arango([])
        await p.get_user_directory_groups("u1", "o1", app_names=["ONEDRIVE"], limit=25)
        query, bind = p.http_client.execute_aql.call_args.args
        assert "authenticatedAs" in query or "AUTHENTICATED" in query.upper()
        assert "edge.type == 'USER'" in query
        assert "g.orgId == @org_id" in query
        assert "LIMIT @limit" in query
        assert bind == {"user_id": "u1", "org_id": "o1", "app_names": ["ONEDRIVE"], "limit": 25}

    async def test_a_linked_account_contributes_only_its_connectors_groups(self) -> None:
        p = _arango([])
        await p.get_user_directory_groups("u1", "o1")
        assert "g.connectorId == link.connectorId" in p.http_client.execute_aql.call_args.args[0]

    async def test_an_empty_app_filter_stays_an_empty_filter(self) -> None:
        p = _arango([])
        await p.get_user_directory_groups("u1", "o1", app_names=[])
        assert p.http_client.execute_aql.call_args.args[1]["app_names"] == []

    async def test_the_two_backends_return_the_same_shape(self) -> None:
        assert await _neo4j([ROW]).get_user_directory_groups("u", "o") == await _arango([ROW]).get_user_directory_groups("u", "o")


class TestInterfaceDefault:
    async def test_a_backend_without_a_directory_returns_nothing(self) -> None:
        class Bare(IGraphDBProvider):  # pragma: no cover - only the default method is used
            pass

        assert await Bare.get_user_directory_groups(object(), "u", "o") == []
