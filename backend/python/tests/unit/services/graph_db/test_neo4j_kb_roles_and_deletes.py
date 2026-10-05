"""Collection roles and deletes on Neo4j. Retired roles (ORGANIZER, COMMENTER,
FILEORGANIZER, OTHERS) are never granted and read as READER wherever a stored
role is read; a collection folder is deleted with its contents."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.permission import RETIRED_ROLES, read_role
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _provider(rows: list) -> Neo4jProvider:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(return_value=rows)
    return provider


@pytest.mark.parametrize("role", sorted(RETIRED_ROLES))
def test_a_retired_role_reads_as_reader(role) -> None:
    assert read_role(role) == "READER"


@pytest.mark.parametrize("role", ["OWNER", "WRITER", "READER", None])
def test_a_model_role_reads_as_itself(role) -> None:
    assert read_role(role) == role


@pytest.mark.asyncio
async def test_the_collection_role_is_read_as_the_model_reads_it() -> None:
    provider = _provider([{"role": "FILEORGANIZER"}])
    assert await provider.get_user_kb_permission("kb1", "u1") == "READER"


@pytest.mark.asyncio
async def test_the_member_list_reads_a_retired_role_as_reader() -> None:
    provider = _provider([{
        "entity_props": {"id": "u1", "userId": "m1"}, "entity_labels": ["User"],
        "rel_props": {"type": "USER", "role": "ORGANIZER"}, "rel_id": 1,
    }])
    [member] = await provider.list_kb_permissions("kb1")
    assert member["role"] == "READER"


@pytest.mark.asyncio
async def test_a_retired_role_cannot_delete_a_collection_record() -> None:
    provider = _provider([])
    provider.get_document = AsyncMock(return_value={"id": "r1", "origin": "UPLOAD", "orgId": "o1"})
    provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})
    provider.client.execute_query = AsyncMock(return_value=[{"role": "FILEORGANIZER"}])
    provider.delete_records_and_relations = AsyncMock()

    provider.delete_records_recursive = AsyncMock()

    result = await provider.delete_record("r1", "m1", "o1")

    assert result["code"] == 403
    provider.delete_records_and_relations.assert_not_called()
    provider.delete_records_recursive.assert_not_called()


@pytest.mark.parametrize("node_type", ["record", "recordGroup", "kb"])
def test_every_role_fragment_maps_retired_roles(node_type) -> None:
    cypher = _provider([])._get_permission_role_cypher(node_type)
    retired = "[" + ", ".join(f"'{r}'" for r in sorted(RETIRED_ROLES)) + "]"
    assert f"IN {retired}" in cypher and "COMMENTER: 2" not in cypher


@pytest.mark.asyncio
async def test_a_collection_folder_is_deleted_with_its_contents() -> None:
    """Deleting only the folder would strand its children."""
    provider = _provider([])
    provider.get_document = AsyncMock(return_value={"id": "f1", "origin": "UPLOAD", "connectorId": "kb1", "orgId": "o1"})
    provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})
    provider.client.execute_query = AsyncMock(return_value=[{"role": "WRITER"}])
    events = {"eventType": "deleteRecord", "topic": "record-events", "payloads": [{"recordId": "f1"}, {"recordId": "c1"}]}
    provider.delete_records_recursive = AsyncMock(
        return_value={"success": True, "successfully_deleted": 1, "eventData": events},
    )
    provider.delete_records_and_relations = AsyncMock()

    result = await provider.delete_record("f1", "m1", "o1")

    provider.delete_records_recursive.assert_awaited_once_with(["f1"], "kb1", transaction=None)
    provider.delete_records_and_relations.assert_not_called()
    assert result["success"] and result["eventData"] == events and result["isKb"]
