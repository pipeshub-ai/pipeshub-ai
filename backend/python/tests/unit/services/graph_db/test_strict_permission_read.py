"""`get_accessible_virtual_record_ids(raise_on_error=True)` on both backends.

Every helper under this read used to answer a failure with an empty value, so a
graph outage and a user who can reach nothing were the same `{}`, and search
told that user to upload documents. These drive the real provider code and
stub only the database client, because stubbing the helpers would stub out the
very layer that swallowed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.unit.services.graph_db.test_accessible_records_provider_routing import (
    RecordingCache,
)


class GraphDown(RuntimeError):
    pass


def _neo4j(cache=None) -> Neo4jProvider:
    provider = Neo4jProvider(MagicMock(), MagicMock(), accessible_records_cache=cache)
    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(side_effect=GraphDown("graph down"))
    return provider


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = MagicMock()
    provider.http_client.execute_aql = AsyncMock(side_effect=GraphDown("graph down"))
    return provider


class TestAGraphOutage:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
    async def test_strict_read_raises(self, make) -> None:
        with pytest.raises(GraphDown):
            await make().get_accessible_virtual_record_ids("user-1", "org-1", raise_on_error=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
    async def test_default_read_is_unchanged(self, make) -> None:
        """Callers that did not opt in still get {} (chat, agents, the census)."""
        assert await make().get_accessible_virtual_record_ids("user-1", "org-1") == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
    async def test_strict_user_lookup_raises_and_default_returns_none(self, make) -> None:
        with pytest.raises(GraphDown):
            await make().get_user_by_user_id("user-1", raise_on_error=True)
        assert await make().get_user_by_user_id("user-1") is None


class TestOneSourceFailing:
    """The user and their apps read fine; one source's permission query fails.
    Without the flag that source silently drops out of what the user can search."""

    @pytest.mark.asyncio
    async def test_neo4j(self) -> None:
        provider = _neo4j()
        provider.get_user_by_user_id = AsyncMock(return_value={"id": "user-key-1"})
        provider.get_user_apps = AsyncMock(return_value=[{"id": "conn-1", "type": "DRIVE"}])

        assert await provider.get_accessible_virtual_record_ids("user-1", "org-1") == {}
        with pytest.raises(GraphDown):
            await provider.get_accessible_virtual_record_ids("user-1", "org-1", raise_on_error=True)

    @pytest.mark.asyncio
    async def test_arango(self) -> None:
        provider = _arango()
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        provider.get_user_apps = AsyncMock(return_value=[{"_key": "conn-1"}])

        assert await provider.get_accessible_virtual_record_ids("user-1", "org-1") == {}
        with pytest.raises(GraphDown):
            await provider.get_accessible_virtual_record_ids("user-1", "org-1", raise_on_error=True)


class TestTheCacheNeverKeepsAFailure:
    """The cache stores what its loader returns. A loader that swallowed would
    store "this user can reach nothing in this connector" for the whole TTL,
    and every search in that window would miss the connector, strict or not."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("strict", [False, True])
    async def test_a_failed_connector_read_is_not_cached(self, strict: bool) -> None:
        cache = RecordingCache()
        provider = _neo4j(cache)
        provider.get_user_by_user_id = AsyncMock(return_value={"id": "user-key-1"})
        provider.get_user_apps = AsyncMock(return_value=[{"id": "conn-1", "type": "DRIVE"}])
        provider._get_accessible_kb_ids = AsyncMock(return_value=[])

        if strict:
            with pytest.raises(GraphDown):
                await provider.get_accessible_virtual_record_ids(
                    "user-1", "org-1", raise_on_error=True
                )
        else:
            assert await provider.get_accessible_virtual_record_ids("user-1", "org-1") == {}

        assert ("cusr", "conn-1") in cache.routes, "the cached path must have been taken"
        assert cache.store == {}
