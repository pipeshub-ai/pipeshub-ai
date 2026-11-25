"""The virtual-record-id queries find the asking user through one method per provider, so a subclass that
narrows the match (or the filter, with what it binds) narrows both queries."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ORG, USER = "org-1", "user-1"


class _NarrowNeo4j(Neo4jProvider):
    def _user_anchor(self, var: str) -> str:
        return f"MATCH ({var}:User {{userId: $userId, orgId: $orgId}})"


class _NarrowArango(ArangoHTTPProvider):
    def _user_anchor_filter(self, var: str, bind_vars: dict[str, Any], org_id: str) -> str:
        bind_vars["orgId"] = org_id
        return f"FILTER {var}.userId == @userId AND {var}.orgId == @orgId"


def _neo4j(cls: type[Neo4jProvider]) -> Neo4jProvider:
    p = cls(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=[])
    return p


def _arango(cls: type[ArangoHTTPProvider]) -> ArangoHTTPProvider:
    p = cls.__new__(cls)
    p.logger = MagicMock()
    p.execute_query = AsyncMock(return_value=[])
    return p


@pytest.mark.parametrize("read", ["connector", "kb"])
async def test_the_default_matches_the_user_by_id(read: str) -> None:
    p = _neo4j(Neo4jProvider)
    await (p._get_virtual_ids_for_connector(USER, ORG, "conn-1") if read == "connector" else p._get_kb_virtual_ids(USER, ORG))
    assert ":User {userId: $userId})" in p.client.execute_query.await_args.args[0]


@pytest.mark.parametrize("read", ["connector", "kb"])
async def test_a_subclass_narrows_both_neo4j_queries(read: str) -> None:
    p = _neo4j(_NarrowNeo4j)
    await (p._get_virtual_ids_for_connector(USER, ORG, "conn-1") if read == "connector" else p._get_kb_virtual_ids(USER, ORG))
    call = p.client.execute_query.await_args
    assert ":User {userId: $userId, orgId: $orgId})" in call.args[0]
    assert (call.kwargs.get("parameters") or call.args[1])["orgId"] == ORG


@pytest.mark.parametrize("read", ["connector", "kb"])
async def test_a_subclass_narrows_both_arango_queries_and_binds_what_it_adds(read: str) -> None:
    p = _arango(_NarrowArango)
    await (p._get_virtual_ids_for_connector(USER, ORG, "conn-1") if read == "connector" else p._get_kb_virtual_ids(USER, ORG))
    call = p.execute_query.await_args
    assert "user.orgId == @orgId" in call.args[0]
    assert (call.kwargs.get("bind_vars") or call.args[1])["orgId"] == ORG


async def test_the_arango_default_binds_nothing_new() -> None:
    p = _arango(ArangoHTTPProvider)
    await p._get_kb_virtual_ids(USER, ORG)
    call = p.execute_query.await_args
    assert "FILTER user.userId == @userId\n" in call.args[0]
    assert "orgId" not in (call.kwargs.get("bind_vars") or call.args[1])


async def test_the_accessible_read_resolves_the_asking_user_through_the_extension_point() -> None:
    class _Resolving(Neo4jProvider):
        async def _asking_user(self, user_id: str, org_id: str, *, raise_on_error: bool = False) -> dict[str, Any]:
            seen.append((user_id, org_id))
            return {"id": "user-key"}

    seen: list[tuple[str, str]] = []
    p = _neo4j(_Resolving)
    p.get_user_by_user_id = AsyncMock(side_effect=AssertionError("not through the extension point"))
    p.get_gated_apps = AsyncMock(return_value=[])

    assert await p.get_accessible_virtual_record_ids(USER, ORG, raise_on_error=True) == {}
    assert seen == [(USER, ORG)]
