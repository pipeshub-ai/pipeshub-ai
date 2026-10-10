"""GATE-NEVER-REMOVED / JPDC-04: a user removed or deactivated at the source loses the connector gate.

The gate edge (userAppRelation) is what lets a PipesHub user reach a connector's
org-wide grants. It used to be only ever upserted, so a departed source user kept
reading every "any logged-in user" project, public team or EEEU site. A connector
whose user sync lists every active source user now withdraws the gate from
anyone missing from that list, on both graph providers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import AppUser
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _user(email: str, **kw) -> AppUser:
    return AppUser(
        app_name=Connectors.LINEAR, connector_id="conn-1", source_user_id=f"src-{email}",
        email=email, full_name=email, **kw,
    )


def _processor(tx_store) -> DataSourceEntitiesProcessor:
    provider = MagicMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    provider.transaction.return_value = ctx
    proc = DataSourceEntitiesProcessor(MagicMock(), provider, AsyncMock())
    proc.org_id = "org-1"
    return proc


@pytest.mark.asyncio
async def test_the_processor_keeps_only_the_listed_users() -> None:
    tx_store = AsyncMock()
    tx_store.remove_app_users_except = AsyncMock(return_value=2)
    proc = _processor(tx_store)

    removed = await proc.remove_app_users_absent_from_source(
        "conn-1", [_user("Ana@Contoso.com"), _user("bob@contoso.com"), _user("ana@contoso.com")],
    )

    assert removed == 2
    tx_store.remove_app_users_except.assert_awaited_once_with(
        "conn-1", ["ana@contoso.com", "bob@contoso.com"],
    )


@pytest.mark.asyncio
async def test_an_empty_listing_removes_nobody() -> None:
    """An empty list is far likelier a failed read than a source with nobody left."""
    tx_store = AsyncMock()
    proc = _processor(tx_store)

    assert await proc.remove_app_users_absent_from_source("conn-1", []) == 0
    tx_store.remove_app_users_except.assert_not_awaited()
    proc.logger.warning.assert_called()


def _neo4j(rows: list) -> Neo4jProvider:
    provider = Neo4jProvider(MagicMock(), MagicMock(), accessible_records_cache=None)
    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(return_value=rows)
    return provider


def _arango(rows: list) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = MagicMock()
    provider.http_client.execute_aql = AsyncMock(return_value=rows)
    return provider


@pytest.mark.asyncio
async def test_neo4j_removes_sync_written_gate_edges_of_unlisted_users() -> None:
    provider = _neo4j([{"removed": 3}])

    assert await provider.remove_app_users_except("conn-1", ["Ana@contoso.com"], transaction="tx") == 3

    call = provider.client.execute_query.await_args
    query, params = call.args[0], call.kwargs["parameters"]
    assert "(u:User)-[r:USER_APP_RELATION]->(:App {id: $connector_id})" in query
    assert "r.sourceUserId IS NOT NULL" in query
    assert "NOT toLower(coalesce(u.email, '')) IN $emails" in query
    assert "DELETE r" in query
    assert params == {"connector_id": "conn-1", "emails": ["ana@contoso.com"]}
    assert call.kwargs["txn_id"] == "tx"


@pytest.mark.asyncio
async def test_arango_removes_sync_written_gate_edges_of_unlisted_users() -> None:
    provider = _arango([1, 1])

    assert await provider.remove_app_users_except("conn-1", ["Ana@contoso.com"], transaction="tx") == 2

    call = provider.http_client.execute_aql.await_args
    query, bind = call.args[0], call.kwargs["bind_vars"]
    assert "FOR r IN userAppRelation" in query
    assert 'r._to == @app_id AND r.sourceUserId != null AND STARTS_WITH(r._from, "users/")' in " ".join(query.split())
    assert 'LOWER(NOT_NULL(DOCUMENT(r._from).email, "")) NOT IN @emails' in query
    assert "REMOVE r IN userAppRelation" in query
    assert bind == {"app_id": "apps/conn-1", "emails": ["ana@contoso.com"]}
    assert call.kwargs["txn_id"] == "tx"


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
async def test_no_listed_user_is_refused(make) -> None:
    provider = make([])
    with pytest.raises(ValueError):
        await provider.remove_app_users_except("conn-1", [])


@pytest.mark.asyncio
async def test_users_the_source_reports_deactivated_lose_only_their_own_gate() -> None:
    """R2-03: their gate edge, by node id, and nobody else's."""
    tx_store = AsyncMock()
    tx_store.delete_edge = AsyncMock(side_effect=[True, False])
    proc = _processor(tx_store)

    removed = await proc.remove_app_users_deactivated_at_source(
        "conn-1", [_user("ana@contoso.com", id="u-ana"), _user("ana@contoso.com", id="u-ana"), _user("gone@contoso.com", id="u-gone")],
    )

    assert removed == 1
    assert [c.args for c in tx_store.delete_edge.await_args_list] == [
        ("u-ana", "users", "conn-1", "apps", "userAppRelation"),
        ("u-gone", "users", "conn-1", "apps", "userAppRelation"),
    ]
    tx_store.remove_app_users_except.assert_not_awaited()
