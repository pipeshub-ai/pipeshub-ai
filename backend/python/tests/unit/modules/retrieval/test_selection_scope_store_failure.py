"""A graph that cannot answer is an error the caller can show, never "outside the
limit", "nothing selected" or "no such user".

``Store`` answers as the Neo4j and ArangoDB providers do: a failed
``get_user_by_user_id`` is logged and read as no user unless ``raise_on_error``
is passed. ``Store.down`` turns every read on or off, so each case also shows the
same call answering correctly once the store is back.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.modules.retrieval.selection_scope as mod
from app.agents.agent_loop.error_classification import classify_exception
from app.api.connection_limit import (
    groups_outside_limit,
    ids_outside_connection,
)
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval.retrieval_service import PERMISSION_CHECK_UNAVAILABLE_MESSAGE
from app.services.graph_db.interface.graph_db_provider import AccessCheck

APP, OTHER_APP, GROUP = "app-1", "app-2", "grp-1"
LIMIT = {"apps": [APP]}
UNAVAILABLE = PermissionVerificationUnavailableError


class Store:
    def __init__(self) -> None:
        self.down = False
        self.user = {"_key": "ukey", "userId": "u"}

    def _read(self) -> None:
        if self.down:
            raise ConnectionError("store down")

    async def get_user_by_user_id(self, user_id, *, raise_on_error=False) -> dict | None:
        try:
            self._read()
        except ConnectionError:
            if raise_on_error:
                raise
            return None
        return self.user

    async def check_access(self, user_key, org_id, *, node_ids=(), scopes=(), **_) -> AccessCheck:
        try:
            self._read()
        except ConnectionError as exc:
            raise UNAVAILABLE(str(exc)) from exc
        rows = [{"id": i, "connectorId": APP if i != "elsewhere" else OTHER_APP} for i in node_ids]
        inside = frozenset(r["id"] for r in rows if all(s.admits(r) for s in scopes))
        return AccessCheck(node_ids=frozenset(node_ids), node_ids_in_scope=inside)

    async def get_selection_nodes(self, org_id, *, group_ids, record_ids, exact_record_ids, limit) -> dict:
        self._read()
        return {"groups": [{"id": g, "connectorId": APP} for g in group_ids], "records": []}

    async def get_nodes_by_field_in(self, collection, field, values, return_fields=None, *, raise_on_error=False) -> list[dict]:
        self._read()
        return [{"id": v, "name": v, "vectorMembershipBackfilled": True} for v in values]

    async def get_accessible_containers(self, *_, **__) -> None:
        raise AssertionError("not reached")


@pytest.fixture(autouse=True)
def _clear_memo() -> Iterator[None]:
    mod._memo.clear()
    yield
    mod._memo.clear()


@pytest.fixture
def store() -> Store:
    return Store()


CALLS = {
    "outside_connection": lambda s: mod.outside_connection(s, "u", "o", ["x"], LIMIT),
    "connection_coverage": lambda s: mod.connection_coverage(s, "u", "o", LIMIT),
    "connection_named_nodes": lambda s: mod.connection_named_nodes(s, "u", "o", LIMIT),
    "groups_outside_connection": lambda s: mod.groups_outside_connection(s, "u", "o", {GROUP: APP}, LIMIT),
    "prepare_turn_scope": lambda s: mod.prepare_turn_scope(s, "u", "o", {"recordGroups": [GROUP]}),
    "unreadable_scope_error": lambda s: mod.unreadable_scope_error(s, "u", "o", {"recordGroups": [GROUP]}),
}


@pytest.mark.parametrize("name", CALLS)
async def test_a_store_failure_raises_then_the_same_call_answers_once_it_is_back(store: Store, name: str) -> None:
    store.down = True
    with pytest.raises(UNAVAILABLE):
        await CALLS[name](store)
    store.down = False
    await CALLS[name](store)  # answers, no error


async def test_a_limit_answers_outside_and_inside_as_before(store: Store) -> None:
    assert await mod.outside_connection(store, "u", "o", ["x", "elsewhere"], LIMIT) == {"elsewhere"}
    assert await mod.outside_connection(store, "u", "o", ["x"], None) == set()


async def test_an_unknown_user_is_still_outside_every_limit(store: Store) -> None:
    store.user = None
    assert await mod.outside_connection(store, "u", "o", ["x"], LIMIT) == {"x"}
    assert await mod.groups_outside_connection(store, "u", "o", {GROUP: APP}, LIMIT) == {GROUP}


async def test_a_failed_selection_walk_is_unavailable_not_an_empty_selection(store: Store) -> None:
    original = store.get_selection_nodes
    store.get_selection_nodes = AsyncMock(side_effect=ConnectionError("walk failed"))
    with pytest.raises(UNAVAILABLE):
        await mod.resolve_selection_scope(store, "ukey", "o", {"recordGroups": [GROUP]})
    store.get_selection_nodes = original
    scope = await mod.resolve_selection_scope(store, "ukey", "o", {"recordGroups": [GROUP]})
    assert scope is not None and GROUP in scope.groups


async def test_a_failed_app_read_is_unavailable_not_a_ready_selection(store: Store) -> None:
    scope = mod.SelectionScope(app_ids=frozenset({APP}))
    store.down = True
    with pytest.raises(UNAVAILABLE):
        await mod.require_membership_ready(store, scope)


async def test_a_failed_name_read_is_unavailable_not_a_missing_node(store: Store) -> None:
    store.down = True
    with pytest.raises(UNAVAILABLE):
        await mod.readable_named_nodes(store, "ukey", "o", {"recordGroups": [GROUP]}, [])


async def test_a_backend_without_the_walk_still_raises_not_implemented(store: Store) -> None:
    store.get_selection_nodes = AsyncMock(side_effect=NotImplementedError)
    with pytest.raises(NotImplementedError):
        await mod.resolve_selection_scope(store, "ukey", "o", {"recordGroups": [GROUP]})


class TestGroupsOutsideConnection:
    """A repo is read through its files, so a limit that names its app covers it
    without the user holding a grant on the record group node."""

    async def test_a_group_of_a_named_app_is_inside(self, store: Store) -> None:
        assert await mod.groups_outside_connection(store, "u", "o", {GROUP: APP}, LIMIT) == set()

    async def test_a_group_of_another_app_is_outside(self, store: Store) -> None:
        assert await mod.groups_outside_connection(store, "u", "o", {GROUP: OTHER_APP}, LIMIT) == {GROUP}

    async def test_a_group_the_limit_names_is_inside_and_a_file_limit_leaves_it_outside(self, store: Store) -> None:
        named = {"recordGroups": [GROUP]}
        assert await mod.groups_outside_connection(store, "u", "o", {GROUP: APP}, named) == set()
        assert await mod.groups_outside_connection(store, "u", "o", {"other": APP}, named) == {"other"}
        assert await mod.groups_outside_connection(store, "u", "o", {GROUP: APP}, {"records": ["file-1"]}) == {GROUP}

    async def test_no_limit_and_no_groups_ask_nothing(self) -> None:
        assert await mod.groups_outside_connection(None, "u", "o", {GROUP: APP}, None) == set()
        assert await mod.groups_outside_connection(None, "u", "o", {}, LIMIT) == set()


class TestWhatTheCallersMake:
    """The routes' mapping: a 503 with the reader-facing message, never a 404/403."""

    class Request:
        class state:
            user = {"knowledgeLimit": {"apps": [APP], "recordGroups": [], "records": []}}

    async def test_a_node_check_is_a_503_with_the_retry_message(self, store: Store) -> None:
        store.down = True
        with pytest.raises(HTTPException) as err:
            await ids_outside_connection(self.Request(), store, "o", "u", ["x"])
        assert err.value.status_code == 503
        assert err.value.detail == PERMISSION_CHECK_UNAVAILABLE_MESSAGE

    async def test_a_group_check_is_a_503_then_answers_when_the_store_is_back(self, store: Store) -> None:
        store.down = True
        with pytest.raises(HTTPException) as err:
            await groups_outside_limit(self.Request(), store, "o", "u", {GROUP: APP})
        assert err.value.status_code == 503
        store.down = False
        assert await groups_outside_limit(self.Request(), store, "o", "u", {GROUP: APP}) == set()

    def test_a_chat_turn_is_told_to_try_again(self) -> None:
        code, message = classify_exception(UNAVAILABLE("store down"))
        assert (code, message) == ("permission_check_unavailable", PERMISSION_CHECK_UNAVAILABLE_MESSAGE)
