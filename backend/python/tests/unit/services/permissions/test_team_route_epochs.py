"""A team write moves the permission epoch of each person whose teams it changed.

What a person may open through a team changes with the team's members, so a reader check stored before the write must
not be used after it: the epoch of every member added, removed or given another role moves, of the creator and every
member of a new team, and of every member of a deleted team. A team whose members cannot be read is not deleted.
"""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes import entity as routes
from app.config.constants.arangodb import CollectionNames

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def _request(body: dict) -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "caller", "orgId": "org-1"}
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    return request


@pytest.fixture
def notified(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    moved = AsyncMock()
    monkeypatch.setattr(routes, "notify_principals_changed", moved)
    monkeypatch.setattr(routes, "current_org_id", lambda: "org-1")
    monkeypatch.setattr(routes, "get_user_or_503", AsyncMock(return_value={"_key": "caller-key"}))
    monkeypatch.setattr(routes, "_caller_is_an_administrator", AsyncMock(return_value=True))
    monkeypatch.setattr(routes, "_authorize_team_change", AsyncMock(return_value={"role": "OWNER"}))
    monkeypatch.setattr(routes, "_refuse_taken_team_name", AsyncMock())
    return moved


def _people(moved: AsyncMock) -> set[str]:
    people: set[str] = set()
    for call in moved.await_args_list:
        people.update(call.kwargs.get("user_ids") or [])
    return people


def _call(route, *args) -> Awaitable[object]:
    return getattr(route, "__wrapped__", route)(*args)


async def test_a_team_update_moves_the_epoch_of_everyone_added_removed_or_given_another_role(monkeypatch, notified) -> None:
    graph = MagicMock()
    graph.update_node = AsyncMock(return_value=True)
    graph.get_graph_user_keys_by_mongo_user_ids = AsyncMock(return_value={"gone": "k-gone", "new": "k-new", "moved": "k-moved"})
    graph.delete_team_member_edges = AsyncMock(return_value=["k-gone"])
    graph.batch_update_team_member_roles = AsyncMock(return_value=[{}])
    graph.batch_create_edges = AsyncMock(return_value=True)
    graph.get_team_with_users = AsyncMock(return_value={"id": "team-1"})
    monkeypatch.setattr(routes, "get_services", AsyncMock(return_value={"graph_provider": graph, "logger": MagicMock()}))
    monkeypatch.setattr(routes, "_validate_owner_removal", AsyncMock())
    monkeypatch.setattr(routes, "_validate_and_filter_owner_updates", AsyncMock(return_value=([{"userId": "k-moved", "role": "WRITER"}], 1)))

    class _OneTransaction:
        """The team's writes run in one graph transaction: here, the write once, on a stand-in for it."""

        async def execute_idempotent_in_transaction(self, write: Callable[[object], Awaitable[None]], **_: object) -> None:
            await write(MagicMock(txn="txn-1"))

    monkeypatch.setattr(routes, "GraphDataStore", MagicMock(return_value=_OneTransaction()))

    body = {"removeUserIds": ["gone"], "addUserRoles": [{"userId": "new", "role": "READER"}], "updateUserRoles": [{"userId": "moved", "role": "WRITER"}]}
    await _call(routes.update_team, _request(body), "team-1")

    assert _people(notified) == {"gone", "new", "moved"}


async def test_a_new_team_moves_the_epoch_of_its_creator_and_every_member(monkeypatch, notified) -> None:
    graph = MagicMock()
    graph.get_graph_user_keys_by_mongo_user_ids = AsyncMock(return_value={"m1": "k1", "m2": "k2"})
    monkeypatch.setattr(routes, "get_services", AsyncMock(return_value={"graph_provider": graph, "logger": MagicMock()}))
    store = MagicMock()
    store.execute_idempotent_in_transaction = AsyncMock(return_value=None)
    monkeypatch.setattr(routes, "GraphDataStore", MagicMock(return_value=store))

    body = {"name": "Board", "userRoles": [{"userId": "m1", "role": "READER"}, {"userId": "m2", "role": "WRITER"}]}
    # The response is built from more than this test stubs; only the epochs are asked here.
    with contextlib.suppress(Exception):
        await _call(routes.create_team, _request(body))

    assert _people(notified) == {"caller", "m1", "m2"}


def _team_graph(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    graph = MagicMock()
    graph.delete_all_team_permissions = AsyncMock(return_value=True)
    graph.delete_nodes = AsyncMock(return_value=True)
    monkeypatch.setattr(routes, "get_services", AsyncMock(return_value={"graph_provider": graph, "logger": MagicMock()}))
    return graph


async def test_a_deleted_team_moves_the_epoch_of_every_member_it_had(monkeypatch, notified) -> None:
    graph = _team_graph(monkeypatch)
    users = CollectionNames.USERS.value
    # Each store names an edge's ends its own way; an edge from anything but a user is no membership.
    graph.get_edges_to_node = AsyncMock(return_value=[
        {"_from": f"{users}/k-ana"},
        {"from_collection": users, "from_id": "k-bo"},
        {"_from": f"{CollectionNames.RECORDS.value}/not-a-member"},
    ])
    graph.get_nodes_by_field_in = AsyncMock(return_value=[{"id": "k-ana", "userId": "ana"}, {"id": "k-bo", "userId": "bo"}])

    await _call(routes.delete_team, _request({}), "team-1")

    assert _people(notified) == {"ana", "bo"}
    graph.delete_nodes.assert_awaited_once()


async def test_a_team_whose_members_cannot_be_read_is_not_deleted(monkeypatch, notified) -> None:
    graph = _team_graph(monkeypatch)

    async def edges(*_args: object, raise_on_error: bool = False, **_kwargs: object) -> list[dict]:
        if raise_on_error:
            raise RuntimeError("graph read failed")
        return []

    graph.get_edges_to_node = AsyncMock(side_effect=edges)

    with pytest.raises(HTTPException) as err:
        await _call(routes.delete_team, _request({}), "team-1")

    assert err.value.status_code == 500
    graph.delete_all_team_permissions.assert_not_awaited()
    graph.delete_nodes.assert_not_awaited()
