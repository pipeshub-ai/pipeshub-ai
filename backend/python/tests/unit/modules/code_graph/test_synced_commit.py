"""A code repo's record group keeps the commit its connector last synced it to (``lastSyncedCommit``)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.code_graph.synced_commit import (
    LAST_SYNCED_COMMIT,
    record_synced_commit,
)
from app.schema.arango.documents import record_group_schema

SHA = "c" * 40


def _processor(group: object = SimpleNamespace(id="g-code"), updated: object = True) -> MagicMock:
    p = MagicMock()
    p.get_record_group_by_external_id = AsyncMock(return_value=group)
    p.batch_update_nodes = AsyncMock(return_value=updated)
    return p


async def test_the_group_is_patched_by_its_own_id_with_only_the_commit() -> None:
    p = _processor()
    assert await record_synced_commit(p, "conn-1", "101-code-repository", SHA) is True
    p.get_record_group_by_external_id.assert_awaited_once_with("conn-1", "101-code-repository")
    p.batch_update_nodes.assert_awaited_once_with(
        [{"id": "g-code", LAST_SYNCED_COMMIT: SHA}], CollectionNames.RECORD_GROUPS.value,
    )


async def test_a_missing_group_is_not_created() -> None:
    p = _processor(group=None)
    assert await record_synced_commit(p, "conn-1", "101-code-repository", SHA) is False
    p.batch_update_nodes.assert_not_awaited()


@pytest.mark.parametrize("fail", ["raises", "refused"])
async def test_a_failed_write_is_reported_and_never_raised(fail) -> None:
    """The sync it follows has already succeeded; the next sync writes the stamp again."""
    p = _processor(updated=False)
    if fail == "raises":
        p.batch_update_nodes = AsyncMock(side_effect=RuntimeError("graph down"))
    assert await record_synced_commit(p, "conn-1", "101-code-repository", SHA) is False


def test_the_strict_record_group_schema_accepts_the_field() -> None:
    """Arango refuses a property the strict schema does not list, and Neo4j validates the same schema."""
    assert record_group_schema["rule"]["properties"][LAST_SYNCED_COMMIT] == {"type": ["string", "null"]}
