"""Who the directory of people lists.

``get_organization_users`` backs ``GET /users/graph/list``, and each row it
returns carries ``userId`` — the id an agent stores when it is bound to a
service account. A member who can read that listing can read which machine
identities exist, and the gateway then attaches the groups each one belongs to.
So the rows are filtered in the query, on both stores, unless the caller
administers the organisation.

The query is the thing under test here; that the right callers get the flag is
``tests/unit/api/routes/test_entity_user_list.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _arango() -> tuple[ArangoHTTPProvider, list[str]]:
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = MagicMock()
    queries: list[str] = []

    async def execute_query(query, bind_vars=None, transaction=None, **_) -> list:
        queries.append(query)
        return [0]

    provider.execute_query = AsyncMock(side_effect=execute_query)
    return provider, queries


def _neo4j() -> tuple[Neo4jProvider, list[str]]:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    provider.client = MagicMock()
    queries: list[str] = []

    async def execute_query(query, parameters=None, **_) -> list:
        queries.append(query)
        return [{"total_count": 0}]

    provider.client.execute_query = AsyncMock(side_effect=execute_query)
    return provider, queries


class TestArango:
    @pytest.mark.asyncio
    async def test_service_accounts_are_filtered_out_by_default(self) -> None:
        provider, queries = _arango()

        await provider.get_organization_users(org_id="org-1")

        assert queries, "no query ran"
        assert all("user.kind != 'service'" in q for q in queries), (
            "the listing and the count must agree on who is in the organisation"
        )

    @pytest.mark.asyncio
    async def test_an_administrator_can_ask_for_them(self) -> None:
        provider, queries = _arango()

        await provider.get_organization_users(org_id="org-1", include_service_accounts=True)

        assert all("kind" not in q for q in queries)


class TestNeo4j:
    @pytest.mark.asyncio
    async def test_service_accounts_are_filtered_out_by_default(self) -> None:
        provider, queries = _neo4j()

        await provider.get_organization_users(org_id="org-1")

        assert queries, "no query ran"
        assert all("u.kind <> 'service'" in q for q in queries)

    @pytest.mark.asyncio
    async def test_a_node_written_before_kind_existed_is_still_listed(self) -> None:
        """`u.kind <> 'service'` alone is null for a node with no such property,
        and a null predicate drops the row. Everyone who signed up before the
        field existed would vanish from the directory."""
        provider, queries = _neo4j()

        await provider.get_organization_users(org_id="org-1")

        assert all("u.kind IS NULL OR" in q for q in queries)

    @pytest.mark.asyncio
    async def test_an_administrator_can_ask_for_them(self) -> None:
        provider, queries = _neo4j()

        await provider.get_organization_users(org_id="org-1", include_service_accounts=True)

        assert all("kind" not in q for q in queries)
