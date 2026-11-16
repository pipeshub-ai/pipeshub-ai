"""The user a permission query anchors on is matched by userId AND orgId.

The same external userId can exist as one User node per organisation, and the
permission edges hang off the org-specific node. A match on the userId alone
anchors on an arbitrary one of them: it can leak another organisation's records
or hide the caller's own. Covered here, on each provider:

* the virtual-record-id queries (knowledge bases and connectors) and the
  accessible-KB id lookup match the user within the org they were asked for;
* ``get_user_by_user_id`` takes an optional ``org_id`` and, when given, only
  returns a user of that organisation;
* the reindex paths pass the org they were asked for to that lookup.
"""
from __future__ import annotations

import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import for_org
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.identity import IdentityMixin

USER_ID = "ext-user-1"
ORG_A = "org-a"
ORG_B = "org-b"

# The same external userId, one user per organisation.
USERS = [
    {"_key": "key-a", "id": "key-a", "userId": USER_ID, "orgId": ORG_A},
    {"_key": "key-b", "id": "key-b", "userId": USER_ID, "orgId": ORG_B},
]

_NEO4J_ORG_SCOPED_USER = re.compile(r"MATCH \(\w+:User \{userId: \$userId, orgId: \$orgId\}\)")


def _arango() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.execute_query = AsyncMock(return_value=[])
    p.http_client = MagicMock(execute_aql=AsyncMock(return_value=[]))
    return p


def _neo4j() -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = MagicMock(execute_query=AsyncMock(return_value=[]))
    return p


async def _user_lookup_by_org(user_id: str, *, raise_on_error: bool = False, org_id: str | None = None) -> dict | None:
    """What a store holding USERS answers; an unscoped lookup takes the first match."""
    return next((u for u in USERS if u["userId"] == user_id and org_id in (None, u["orgId"])), None)


def _aql(p: ArangoHTTPProvider) -> tuple[str, dict]:
    call = p.execute_query.await_args
    return call.args[0], call.kwargs.get("bind_vars") or call.args[1]


class TestVirtualIdQueriesMatchTheUserWithinTheOrg:
    async def test_arango_kb_query_filters_the_user_by_org(self) -> None:
        p = _arango()
        await p._get_kb_virtual_ids(USER_ID, ORG_B)
        query, bind_vars = _aql(p)
        assert "FILTER user.orgId == @orgId" in query
        assert bind_vars["orgId"] == ORG_B
        assert bind_vars["userId"] == USER_ID

    async def test_arango_connector_query_filters_the_user_by_org(self) -> None:
        p = _arango()
        await p._get_virtual_ids_for_connector(USER_ID, ORG_B, "conn-1")
        query, bind_vars = _aql(p)
        assert "FILTER user.orgId == @orgId" in query
        assert bind_vars["orgId"] == ORG_B

    async def test_neo4j_kb_query_matches_the_user_within_the_org(self) -> None:
        p = _neo4j()
        await p._get_kb_virtual_ids(USER_ID, ORG_B)
        call = p.client.execute_query.await_args
        assert _NEO4J_ORG_SCOPED_USER.search(call.args[0])
        assert call.kwargs["parameters"]["orgId"] == ORG_B

    async def test_neo4j_connector_query_matches_the_user_within_the_org(self) -> None:
        p = _neo4j()
        await p._get_virtual_ids_for_connector(USER_ID, ORG_B, "conn-1")
        call = p.client.execute_query.await_args
        assert _NEO4J_ORG_SCOPED_USER.search(call.args[0])
        assert call.kwargs["parameters"]["orgId"] == ORG_B

    async def test_neo4j_accessible_kb_ids_match_the_user_within_the_org(self) -> None:
        p = _neo4j()
        await p._get_accessible_kb_ids(USER_ID, ORG_B)
        call = p.client.execute_query.await_args
        assert _NEO4J_ORG_SCOPED_USER.search(call.args[0])
        assert call.kwargs["parameters"]["orgId"] == ORG_B

    async def test_neo4j_cached_kb_lookup_passes_the_org_to_the_kb_ids(self) -> None:
        p = _neo4j()
        p._get_accessible_kb_ids = AsyncMock(return_value=[])
        await p._get_kb_virtual_ids_cached(USER_ID, ORG_B, None)
        assert p._get_accessible_kb_ids.await_args.args[:2] == (USER_ID, ORG_B)


class TestGetUserByUserIdOrgScope:
    async def test_arango_filters_by_org_when_given(self) -> None:
        p = _arango()
        await p.get_user_by_user_id(USER_ID, org_id=ORG_B)
        call = p.http_client.execute_aql.await_args
        assert "user.orgId == @org_id" in call.args[0]
        assert call.kwargs["bind_vars"] == {"user_id": USER_ID, "org_id": ORG_B}

    async def test_arango_without_org_is_unchanged(self) -> None:
        p = _arango()
        await p.get_user_by_user_id(USER_ID)
        call = p.http_client.execute_aql.await_args
        assert "orgId" not in call.args[0]
        assert call.kwargs["bind_vars"] == {"user_id": USER_ID}

    async def test_neo4j_filters_by_org_when_given(self) -> None:
        p = _neo4j()
        await p.get_user_by_user_id(USER_ID, org_id=ORG_B)
        call = p.client.execute_query.await_args
        assert "u.orgId = $org_id" in call.args[0]
        assert call.kwargs["parameters"] == {"user_id": USER_ID, "org_id": ORG_B}

    async def test_neo4j_without_org_is_unchanged(self) -> None:
        p = _neo4j()
        await p.get_user_by_user_id(USER_ID)
        call = p.client.execute_query.await_args
        assert "orgId" not in call.args[0]
        assert call.kwargs["parameters"] == {"user_id": USER_ID}

    async def test_postgres_reads_nothing_for_another_org(self) -> None:
        p = object.__new__(IdentityMixin)
        p.logger = logging.getLogger("t")
        p._one = AsyncMock(return_value=None)
        with for_org(ORG_A, job="t"):
            assert await p.get_user_by_user_id(USER_ID, org_id=ORG_B) is None
            p._one.assert_not_awaited()
            await p.get_user_by_user_id(USER_ID, org_id=ORG_A)
            p._one.assert_awaited_once()


class TestReindexPathsPassTheOrg:
    """Two users share a userId; the caller of org B must not be resolved to the user of org A."""

    @pytest.mark.parametrize("make", [_arango, _neo4j])
    async def test_reindex_single_record_resolves_the_user_in_the_asked_org(self, make) -> None:
        p = make()
        p.get_document = AsyncMock(return_value={"id": "r1", "_key": "r1", "origin": "CONNECTOR", "connectorName": "X", "connectorId": "c1"})
        p.get_user_by_user_id = AsyncMock(side_effect=_user_lookup_by_org)
        result = await p.reindex_single_record("r1", USER_ID, ORG_B, MagicMock())
        assert p.get_user_by_user_id.await_args.kwargs["org_id"] == ORG_B
        # A user of the org that is not asked about is never the caller.
        p.get_user_by_user_id = AsyncMock(side_effect=_user_lookup_by_org)
        result = await p.reindex_single_record("r1", USER_ID, "org-without-the-user", MagicMock())
        assert result["code"] == 404
        assert result["reason"] == f"User not found: {USER_ID}"

    @pytest.mark.parametrize("make", [_arango, _neo4j])
    async def test_reindex_record_group_records_resolves_the_user_in_the_asked_org(self, make) -> None:
        p = make()
        p.get_document = AsyncMock(return_value={"id": "g1", "_key": "g1", "connectorId": "c1", "connectorName": "X"})
        p.get_user_by_user_id = AsyncMock(side_effect=_user_lookup_by_org)
        result = await p.reindex_record_group_records("g1", 0, USER_ID, "org-without-the-user")
        assert p.get_user_by_user_id.await_args.kwargs["org_id"] == "org-without-the-user"
        assert result["code"] == 404
        assert result["reason"] == f"User not found: {USER_ID}"


class TestCallSitesPassTheOrg:
    """A method that is given the org passes it on to the user lookup, so the caller is never taken from another org."""

    async def test_arango_check_record_access_looks_the_user_up_in_the_asked_org(self) -> None:
        p = _arango()
        p.get_user_by_user_id = AsyncMock(return_value=None)
        assert await p.check_record_access_with_details(USER_ID, ORG_B, "r1") is None
        assert p.get_user_by_user_id.await_args.kwargs["org_id"] == ORG_B

    @pytest.mark.parametrize("make", [_arango, _neo4j])
    async def test_upload_validation_looks_the_user_up_in_the_asked_org(self, make) -> None:
        p = make()
        p.get_user_by_user_id = AsyncMock(side_effect=_user_lookup_by_org)
        result = await p._validate_upload_context("kb1", USER_ID, "org-without-the-user")
        assert p.get_user_by_user_id.await_args.kwargs["org_id"] == "org-without-the-user"
        assert result["valid"] is False
        assert result["code"] == 404
