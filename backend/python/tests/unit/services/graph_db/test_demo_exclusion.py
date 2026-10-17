"""Switched-off demo data is left out of what a user can search, in both graph providers. The knowledge hub leaves it out of its gate in the service (test_knowledge_hub_service_v2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

OFF = frozenset({"demo-1"})


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(MagicMock(), MagicMock(), accessible_records_cache=None)
    provider.client = MagicMock()
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "user-key-1"})
    provider.get_user_apps = AsyncMock(
        return_value=[{"id": "jira-1", "type": "JIRA"}, {"id": "demo-1", "type": "Demo"}, {"id": "kb-1", "type": "KB"}]
    )
    provider.get_gated_apps = provider.get_user_apps
    provider._get_virtual_ids_for_connector = AsyncMock(return_value={})
    provider._get_kb_virtual_ids = AsyncMock(return_value={})
    return provider


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql = AsyncMock(return_value=["kb-1"])  # the KB-type lookup
    provider._get_user_app_ids = AsyncMock(return_value=["jira-1", "demo-1", "kb-1"])
    provider._get_virtual_ids_for_connector = AsyncMock(return_value={})
    provider._get_kb_virtual_ids = AsyncMock(return_value={})
    return provider


def _connectors_queried(provider) -> list[str]:
    return sorted(c.args[2] for c in provider._get_virtual_ids_for_connector.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
async def test_search_skips_the_switched_off_demo(make) -> None:
    provider = make()
    await provider.get_accessible_virtual_record_ids("user-1", "org-1", exclude_app_ids=OFF)
    assert _connectors_queried(provider) == ["jira-1"]
    provider._get_kb_virtual_ids.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
async def test_naming_the_demo_as_a_source_does_not_bring_it_back(make) -> None:
    provider = make()
    await provider.get_accessible_virtual_record_ids("user-1", "org-1", {"apps": ["demo-1"]}, exclude_app_ids=OFF)
    assert _connectors_queried(provider) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
async def test_nothing_is_left_out_by_default(make) -> None:
    provider = make()
    await provider.get_accessible_virtual_record_ids("user-1", "org-1")
    assert _connectors_queried(provider) == ["demo-1", "jira-1"]
