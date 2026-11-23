"""Without a licence key a company-wide Box connector reads no access from Box through these methods."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.box.connector import BoxConnector
from app.ee.permission_sync.box import BoxPermissionsMixin


def _connector(*, gated: bool) -> object:
    """The mixin alone, with every way it reaches Box or the graph replaced by a mock."""
    connector = type("Connector", (BoxPermissionsMixin,), {})()
    connector.connector_id = "connector-1"
    connector.logger = MagicMock()
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    connector.data_source = MagicMock()
    connector._is_final_answer = MagicMock(return_value=False)
    connector._mark_read_incomplete = MagicMock()
    return connector


# Every gated method the free connector calls, and what it gets back when the gate holds.
ENTRY_POINTS = [
    # The same answer as an item with no collaborators of its own: the record is written, the processor keeps its grants.
    ("_get_permissions", ("file-1", "file"), []),
    ("_sync_user_groups", (), None),
    ("_remove_user_access_from_folder_recursively", ("folder-1", "user-1"), None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args", "expected"), ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
async def test_gated_connector_reads_nothing_from_box(name: str, args: tuple, expected: object) -> None:
    connector = _connector(gated=True)

    assert await getattr(connector, name)(*args) == expected

    connector.data_entities_processor.skips_permission_writes.assert_awaited_with("connector-1")
    assert connector.data_source.method_calls == []
    # Only the gate was asked: nothing was written, and no part of the run counts as read in part.
    assert [call[0] for call in connector.data_entities_processor.mock_calls] == ["skips_permission_writes"]
    connector._mark_read_incomplete.assert_not_called()


@pytest.mark.asyncio
async def test_licensed_connector_still_reads_collaborators() -> None:
    connector = _connector(gated=False)
    connector.data_source.collaborations_get_file_collaborations = AsyncMock(
        return_value=MagicMock(success=False, error="500 down")
    )

    assert await connector._get_permissions("file-1", "file") is None

    connector.data_source.collaborations_get_file_collaborations.assert_awaited_once_with(file_id="file-1", marker=None)


def test_the_connector_takes_its_permission_reading_from_ee() -> None:
    assert BoxPermissionsMixin in BoxConnector.__mro__
