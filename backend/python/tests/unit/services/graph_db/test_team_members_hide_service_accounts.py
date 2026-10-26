"""Who a team's member list names.

Every account joins the organisation's All team when it is created, service
accounts included (`entity.py` `_get_or_create_all_team_and_add_user` on
`userAdded`). Each member row carries `userId` — the id an agent stores when it
is bound to a service account — next to the address that gives it away as a
machine. `GET /teams/{teamId}` has no membership check, and `GET /teams/user/teams`
embeds the same list per team, so both are filtered in the query unless the
caller administers the organisation.

In the member list rather than around the team, so `memberCount` counts what
the caller was actually given.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.service_accounts import SERVICE_ACCOUNT_EMAIL_DOMAIN
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

RESERVED_DOMAIN = f"@{SERVICE_ACCOUNT_EMAIL_DOMAIN}"


def _member_queries(queries: list[str]) -> list[str]:
    """Only the queries that build a member list. `get_user_teams` also runs a
    count over the teams themselves, which names no member."""
    return [q for q in queries if "member_user" in q]


def _arango() -> tuple[ArangoHTTPProvider, list[str]]:
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = MagicMock()
    queries: list[str] = []

    async def execute_query(query, bind_vars=None, transaction=None, **_) -> list:
        queries.append(query)
        return []

    provider.execute_query = AsyncMock(side_effect=execute_query)
    return provider, queries


def _neo4j() -> tuple[Neo4jProvider, list[str]]:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    provider.client = MagicMock()
    queries: list[str] = []

    async def execute_query(query, parameters=None, **_) -> list:
        queries.append(query)
        return []

    provider.client.execute_query = AsyncMock(side_effect=execute_query)
    return provider, queries


class TestOneTeam:
    @pytest.mark.asyncio
    async def test_arango_leaves_service_accounts_out_by_default(self) -> None:
        provider, queries = _arango()

        await provider.get_team_with_users(team_id="all_org-1", org_id="org-1", user_key="k")

        assert queries
        assert all("user.kind != 'service'" in q for q in queries)
        assert all(RESERVED_DOMAIN in q for q in queries)

    @pytest.mark.asyncio
    async def test_arango_keeps_them_for_an_administrator(self) -> None:
        provider, queries = _arango()

        await provider.get_team_with_users(
            team_id="all_org-1", org_id="org-1", user_key="k",
            include_service_accounts=True,
        )

        assert all(RESERVED_DOMAIN not in q for q in queries)

    @pytest.mark.asyncio
    async def test_neo4j_leaves_service_accounts_out_by_default(self) -> None:
        provider, queries = _neo4j()

        await provider.get_team_with_users(team_id="all_org-1", org_id="org-1", user_key="k")

        assert queries
        assert all("member_user.kind IS NULL OR" in q for q in queries)
        assert all(RESERVED_DOMAIN in q for q in queries)

    @pytest.mark.asyncio
    async def test_neo4j_keeps_them_for_an_administrator(self) -> None:
        provider, queries = _neo4j()

        await provider.get_team_with_users(
            team_id="all_org-1", org_id="org-1", user_key="k",
            include_service_accounts=True,
        )

        assert all(RESERVED_DOMAIN not in q for q in queries)


class TestTheTeamsAUserIsIn:
    @pytest.mark.asyncio
    async def test_arango_leaves_service_accounts_out_by_default(self) -> None:
        provider, queries = _arango()

        await provider.get_user_teams(user_key="k")

        listing = _member_queries(queries)
        assert listing
        assert all("member_user.kind != 'service'" in q for q in listing)
        assert all(RESERVED_DOMAIN in q for q in listing)

    @pytest.mark.asyncio
    async def test_neo4j_leaves_service_accounts_out_by_default(self) -> None:
        provider, queries = _neo4j()

        await provider.get_user_teams(user_key="k")

        listing = _member_queries(queries)
        assert listing
        assert all("member_user.kind IS NULL OR" in q for q in listing)
        assert all(RESERVED_DOMAIN in q for q in listing)

    @pytest.mark.asyncio
    async def test_an_administrator_sees_them(self) -> None:
        arango, arango_queries = _arango()
        neo4j, neo4j_queries = _neo4j()

        await arango.get_user_teams(user_key="k", include_service_accounts=True)
        await neo4j.get_user_teams(user_key="k", include_service_accounts=True)

        assert all(RESERVED_DOMAIN not in q for q in arango_queries + neo4j_queries)
