"""A group membership change moves the permission epoch of each person it concerns, after it has committed.

A reader check stored before the change is keyed on that person's epoch, so it is not used after it: a member removed
from a group, added to one, or left without one when it is deleted, and whoever joins or leaves a group when a full
sync replaces its members. A member list that could not be read is not an empty one: the change fails, nothing is
written, and the caller tries it again.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import User

ORG = "org-1"
CONNECTOR = "conn-1"
_MODULE = "app.connectors.core.base.data_processor.data_source_entities_processor"


@pytest.fixture
def events() -> list:
    return []


@pytest.fixture
def epochs(events) -> Iterator[AsyncMock]:
    async def record(org_id=None, *, user_ids=()) -> None:
        events.append(("epochs", org_id, tuple(sorted(user_ids))))

    with patch(f"{_MODULE}.notify_principals_changed", new=AsyncMock(side_effect=record)) as mock, \
            patch(f"{_MODULE}.notify_connector_permissions_changed", new=AsyncMock()):
        yield mock


def _processor(tx_store, events) -> DataSourceEntitiesProcessor:
    async def commit(*_exc_info) -> bool:
        events.append("commit")
        return False

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(side_effect=commit)
    provider = MagicMock()
    provider.transaction = MagicMock(return_value=ctx)

    async def in_transaction(work, *args) -> object:
        result = await work(tx_store, *args)
        events.append("commit")
        return result

    provider.execute_in_transaction = AsyncMock(side_effect=in_transaction)
    proc = DataSourceEntitiesProcessor(MagicMock(), provider, AsyncMock())
    proc.org_id = ORG
    proc.messaging_producer = AsyncMock()
    return proc


def _ana() -> User:
    return User(id="user-key", email="ana@example.com", org_id=ORG, user_id="ana-id")


def _moved_after_commit(events, people: tuple[str, ...]) -> None:
    moves = [e for e in events if isinstance(e, tuple) and e[0] == "epochs" and e[2]]
    assert moves, f"no person's epoch moved: {events}"
    assert moves[-1] == ("epochs", ORG, people), events
    assert "commit" in events[: events.index(moves[-1])], "an epoch moved before the write committed"


def _members(tx: AsyncMock) -> None:
    tx.get_edges_to_node = AsyncMock(return_value=[
        {"_from": f"{CollectionNames.USERS.value}/user-key"},
        {"from_collection": CollectionNames.USERS.value, "from_id": "user-2"},
        {"_from": f"{CollectionNames.RECORDS.value}/not-a-member"},
    ])
    tx.get_nodes_by_field_in = AsyncMock(return_value=[{"id": "user-key", "userId": "ana-id"}, {"id": "user-2", "userId": "bo-id"}])


async def test_a_member_removed_from_a_group_moves_their_epoch(epochs, events) -> None:
    tx = AsyncMock()
    tx.get_user_by_email = AsyncMock(return_value=_ana())
    tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(id="grp-key", name="Finance"))
    tx.batch_delete_edges = AsyncMock(return_value=1)

    assert await _processor(tx, events).on_user_group_member_removed("grp-ext", "ana@example.com", CONNECTOR) is True

    _moved_after_commit(events, ("ana-id",))


async def test_a_member_added_to_a_group_moves_their_epoch(epochs, events) -> None:
    from app.models.permission import PermissionType

    tx = AsyncMock()
    tx.get_user_by_email = AsyncMock(return_value=_ana())
    tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(id="grp-key", name="Finance"))
    tx.get_edge = AsyncMock(return_value=None)
    tx.batch_create_edges = AsyncMock(return_value=True)

    assert await _processor(tx, events).on_user_group_member_added("grp-ext", "ana@example.com", PermissionType.READ, CONNECTOR) is True

    _moved_after_commit(events, ("ana-id",))


async def test_a_deleted_group_moves_the_epoch_of_every_member_it_had(epochs, events) -> None:
    tx = AsyncMock()
    tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(id="grp-key", name="Finance"))
    _members(tx)

    await _processor(tx, events).on_user_group_deleted("grp-ext", CONNECTOR)

    _moved_after_commit(events, ("ana-id", "bo-id"))


async def test_a_group_deleted_by_id_moves_the_epoch_of_every_member_it_had(epochs, events) -> None:
    tx = AsyncMock()
    _members(tx)

    await _processor(tx, events).delete_user_group_by_id("grp-key")

    _moved_after_commit(events, ("ana-id", "bo-id"))


async def test_a_full_sync_moves_the_epoch_of_whoever_joins_or_leaves_a_group(epochs, events) -> None:
    tx = AsyncMock()
    tx.get_nodes_by_filters = AsyncMock(return_value=[{"_key": "grp-key"}])
    # Stored now: ana and user-2. The sync read ana and user-3: user-2 leaves, user-3 joins, ana stays.
    tx.get_edges_to_node = AsyncMock(return_value=[
        {"_from": f"{CollectionNames.USERS.value}/user-key"},
        {"_from": f"{CollectionNames.USERS.value}/user-2"},
    ])
    tx.get_nodes_by_field_in = AsyncMock(side_effect=lambda _c, _f, keys, **_kw: [
        {"id": k, "userId": {"user-2": "bo-id", "user-3": "cy-id", "user-key": "ana-id"}[k]} for k in keys
    ])
    tx.replace_edges_to = AsyncMock()
    proc = _processor(tx, events)
    run = proc.begin_full_sync_run(CONNECTOR)
    run.group_memberships["grp-ext"] = {"user-key": ("ana-src", "ana@example.com"), "user-3": ("cy-src", "cy@example.com")}

    await proc.finish_full_sync_memberships(CONNECTOR, run)

    _moved_after_commit(events, ("bo-id", "cy-id"))


def _unreadable_members(tx: AsyncMock) -> None:
    """The store raises on a failed read only when asked to; asked, it does here."""

    async def edges(*_args, raise_on_error: bool = False) -> list[dict]:
        if raise_on_error:
            raise RuntimeError("graph read failed")
        return []

    tx.get_edges_to_node = AsyncMock(side_effect=edges)


async def test_a_group_whose_members_cannot_be_read_is_not_deleted(epochs, events) -> None:
    tx = AsyncMock()
    tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(id="grp-key", name="Finance"))
    _unreadable_members(tx)

    with pytest.raises(RuntimeError):
        await _processor(tx, events).on_user_group_deleted("grp-ext", CONNECTOR)

    tx.delete_nodes_and_edges.assert_not_awaited()


async def test_a_group_whose_members_cannot_be_read_is_not_deleted_by_id(epochs, events) -> None:
    tx = AsyncMock()
    _unreadable_members(tx)

    with pytest.raises(RuntimeError):
        await _processor(tx, events).delete_user_group_by_id("grp-key")

    tx.delete_user_group_by_id.assert_not_awaited()


async def test_a_full_sync_that_cannot_read_a_groups_members_replaces_none_of_them(epochs, events) -> None:
    tx = AsyncMock()
    tx.get_nodes_by_filters = AsyncMock(return_value=[{"_key": "grp-key"}])
    _unreadable_members(tx)
    proc = _processor(tx, events)
    run = proc.begin_full_sync_run(CONNECTOR)
    run.group_memberships["grp-ext"] = {"user-3": ("cy-src", "cy@example.com")}

    with pytest.raises(RuntimeError):
        await proc.finish_full_sync_memberships(CONNECTOR, run)

    tx.replace_edges_to.assert_not_awaited()
