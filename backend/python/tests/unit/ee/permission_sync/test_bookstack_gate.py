"""Without a licence key a company-wide BookStack connector reads no access through these methods."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.bookstack.reads import ReadOutcome
from app.ee.permission_sync.bookstack import BookStackPermissionsMixin
from app.ee.permission_sync.bookstack_access import ItemAccess


def _connector(*, gated: bool) -> BookStackPermissionsMixin:
    """The mixin alone, with every way it reaches BookStack or the graph replaced by a mock."""
    connector = type("Connector", (BookStackPermissionsMixin,), {})()
    connector.connector_id = "connector-1"
    connector.logger = MagicMock()
    connector._health = MagicMock()
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    connector.data_source = MagicMock()
    connector._read_listing = AsyncMock(return_value=([], ReadOutcome.COMPLETE))
    connector._handle_user_create_event = AsyncMock()
    connector._users_stored = True
    return connector


def _assert_nothing_read(connector: BookStackPermissionsMixin) -> None:
    connector.data_entities_processor.skips_permission_writes.assert_awaited_with("connector-1")
    assert connector.data_source.method_calls == []
    connector._read_listing.assert_not_awaited()
    # Only the gate was asked: nothing was written either.
    assert [call[0] for call in connector.data_entities_processor.mock_calls] == ["skips_permission_writes"]


PAGE = {"id": 7, "book_id": 1, "chapter_id": 2, "owned_by": 3}

# Every gated method the free connector calls, and what it gets back when the gate holds.
ENTRY_POINTS = [
    ("_access", ("page", PAGE), ItemAccess()),
    ("_load_roles_for_access", (), True),
    ("_sync_user_roles_full", (), True),
    ("_withdraw_user_roles", (3, "Ann"), None),
    ("_handle_role_delete_event", (4,), None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args", "expected"), ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
async def test_gated_connector_reads_nothing_from_bookstack(name: str, args: tuple, expected: object) -> None:
    connector = _connector(gated=True)

    assert await getattr(connector, name)(*args) == expected

    _assert_nothing_read(connector)


@pytest.mark.asyncio
async def test_gated_item_access_is_empty_not_unreadable() -> None:
    access = await _connector(gated=True)._access("page", PAGE)

    # None would count the page as unreadable and leave it unsynced; empty grants let it sync.
    assert access is not None and access.permissions == [] and not access.membership_dependent


@pytest.mark.asyncio
async def test_gated_user_event_saves_the_user_and_reads_no_roles() -> None:
    connector = _connector(gated=True)
    events = [{"type": "user_update", "loggable_id": 3, "detail": "(3) Ann"}]

    assert await connector._handle_user_upsert_event(events, []) is True

    connector._handle_user_create_event.assert_awaited_once_with(events, [])
    _assert_nothing_read(connector)


@pytest.mark.asyncio
async def test_licensed_connector_still_reads_the_roles() -> None:
    connector = _connector(gated=False)

    await connector._load_roles_for_access()

    connector._read_listing.assert_awaited_once()
