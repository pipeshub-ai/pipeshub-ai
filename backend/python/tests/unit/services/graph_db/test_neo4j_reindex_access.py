"""Neo4j reindex authorizes connector records and record groups through the
batch access check. The old checkers matched a grant on any record group
without requiring that the target inherit from it, so any group grant
authorized every record."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _provider(documents: dict, accessible: set[str]) -> Neo4jProvider:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    provider.get_document = AsyncMock(side_effect=lambda key, _collection, *_a, **_k: documents.get(key))
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "user-key"})
    provider.check_access = AsyncMock(
        side_effect=lambda _user_key, _org_id, node_ids=(), **_k: AccessCheck(
            node_ids=frozenset(node_ids) & accessible,
        ),
    )
    return provider


RECORD = {"id": "rec-1", "origin": "CONNECTOR", "connectorId": "app-1", "connectorName": "CONFLUENCE"}
GROUP = {"id": "rg-1", "connectorId": "app-1", "connectorName": "CONFLUENCE"}
APP = {"id": "app-1", "isActive": True, "name": "Confluence"}


class TestReindexRecord:
    @pytest.mark.asyncio
    async def test_an_inaccessible_connector_record_is_refused(self) -> None:
        provider = _provider({"rec-1": RECORD, "app-1": APP}, accessible=set())
        result = await provider.reindex_single_record("rec-1", "user-1", "org-1", MagicMock())
        assert result["success"] is False and result["code"] == 403
        provider.check_access.assert_awaited_once_with("user-key", "org-1", node_ids=["rec-1"])

    @pytest.mark.asyncio
    async def test_an_accessible_connector_record_is_reindexed(self) -> None:
        provider = _provider({"rec-1": RECORD, "app-1": APP}, accessible={"rec-1"})
        result = await provider.reindex_single_record("rec-1", "user-1", "org-1", MagicMock())
        assert result["success"] is True
        assert result["userRole"] is None


class TestReindexRecordGroup:
    @pytest.mark.asyncio
    async def test_an_inaccessible_group_is_refused(self) -> None:
        provider = _provider({"rg-1": GROUP, "app-1": APP}, accessible=set())
        result = await provider.reindex_record_group_records("rg-1", 0, "user-1", "org-1")
        assert result["success"] is False and result["code"] == 403

    @pytest.mark.asyncio
    async def test_an_accessible_group_is_reindexed(self) -> None:
        provider = _provider({"rg-1": GROUP, "app-1": APP}, accessible={"rg-1"})
        result = await provider.reindex_record_group_records("rg-1", 0, "user-1", "org-1")
        assert result["success"] is True
        provider.check_access.assert_awaited_once_with("user-key", "org-1", node_ids=["rg-1"])

    @pytest.mark.asyncio
    async def test_a_failed_check_is_a_500_without_the_exception_text(self) -> None:
        provider = _provider({"rg-1": GROUP, "app-1": APP}, accessible=set())
        provider.check_access = AsyncMock(side_effect=RuntimeError("bolt: connection reset"))
        result = await provider.reindex_record_group_records("rg-1", 0, "user-1", "org-1")
        assert result["code"] == 500
        assert "bolt" not in result["reason"]
