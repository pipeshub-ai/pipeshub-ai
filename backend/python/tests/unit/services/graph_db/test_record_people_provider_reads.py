"""Provider reads the record-people backfill relies on."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _arango() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.http_client = MagicMock()
    p.http_client.execute_aql = AsyncMock(return_value=[])
    return p


def _neo4j() -> Neo4jProvider:
    p = Neo4jProvider.__new__(Neo4jProvider)
    p.logger = MagicMock()
    p.client = MagicMock()
    p.client.execute_query = AsyncMock(return_value=[])
    return p


class TestGetUserBySourceId:
    @pytest.mark.asyncio
    async def test_arango_matches_users_only(self) -> None:
        """A person holds app edges too (external collaborators); the lookup must
        not turn one into a user."""
        p = _arango()
        await p.get_user_by_source_id("src-1", "conn-1")
        query = p.http_client.execute_aql.await_args.args[0]
        assert 'FILTER STARTS_WITH(edge._from, "users/")' in query
        assert query.index("STARTS_WITH") < query.index("DOCUMENT(edge._from)")


@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
class TestPageRecordIdsByType:
    @pytest.mark.asyncio
    async def test_a_zero_limit_returns_no_ids_and_runs_no_query(self, make) -> None:
        p = make()
        assert await p.page_record_ids_by_type("org-1", ["MAIL"], limit=0) == []
        assert not (p.http_client.execute_aql if make is _arango else p.client.execute_query).await_count

    @pytest.mark.asyncio
    async def test_a_negative_limit_is_rejected(self, make) -> None:
        with pytest.raises(ValueError):
            await make().page_record_ids_by_type("org-1", ["MAIL"], limit=-1)

    @pytest.mark.asyncio
    async def test_a_positive_limit_is_passed_through(self, make) -> None:
        p = make()
        await p.page_record_ids_by_type("org-1", ["MAIL"], limit=7)
        call = (p.http_client.execute_aql if make is _arango else p.client.execute_query).await_args
        params = call.kwargs.get("bind_vars") or call.kwargs.get("parameters")
        assert params["limit"] == 7
