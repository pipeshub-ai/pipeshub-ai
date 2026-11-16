"""A brand-new account's first request writes its own graph user when its userAdded event is still in flight.

EntityEventService.ensure_user runs the events' own handlers from Node's live records of exactly the user and org it
is given (app.utils.user_lookup.get_caller_user gives it only the request's verified caller; tests/unit/utils/
test_user_lookup.py). A deleted or disabled account or a record of another org writes nothing. Any number of writers
of one new user (the event, its redelivery, first requests, other workers) end with one user and one default
knowledge base, because both are keyed by (org, userId) and the stores upsert on the key.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.messaging.kafka.handlers.entity import (
    EntityEventService,
    _default_kb_key,
    _login_user_key,
)

ORG, USER = "org-1", "user-1"
LIVE_USER = {"_id": USER, "orgId": ORG, "email": "Admin@Example.com", "fullName": "Admin", "isDeleted": False}
LIVE_ORG = {"_id": ORG, "accountType": "business", "registeredName": "Acme"}


def _service(graph: Any = None) -> EntityEventService:  # noqa: ANN401
    container = MagicMock()
    container.connectors_map = {}
    return EntityEventService(logger=MagicMock(), graph_provider=graph or AsyncMock(), app_container=container)


def _node(user: dict | None = LIVE_USER, org: dict | None = LIVE_ORG) -> Any:  # noqa: ANN401
    async def fetch(_config: object, org_id: str, user_id: str, path: str) -> dict | None:
        assert (org_id, user_id) == (ORG, USER), "Node is asked only about the caller, in the caller's org"
        return org if path == "/api/v1/org/internal" else user

    return patch("app.services.messaging.kafka.handlers.entity.fetch_own_identity", AsyncMock(side_effect=fetch))


def _handlers(svc: EntityEventService) -> tuple[AsyncMock, AsyncMock]:
    svc._handle_org_created = AsyncMock(return_value=True)  # type: ignore[method-assign]
    svc._handle_user_added = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return svc._handle_org_created, svc._handle_user_added


class TestEnsureCallerUser:
    async def test_a_fresh_caller_gets_the_org_and_user_the_events_would_write(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value=None)
        svc = _service(graph)
        org_created, user_added = _handlers(svc)
        with _node() as fetch:
            assert await svc.ensure_user(ORG, USER) is True
        graph.get_document.assert_awaited_once_with(ORG, CollectionNames.ORGS.value, raise_on_error=True)
        org_created.assert_awaited_once_with({"orgId": ORG, "accountType": "business", "registeredName": "Acme"})
        user_added.assert_awaited_once_with(
            {"orgId": ORG, "userId": USER, "email": "Admin@Example.com", "fullName": "Admin"}
        )
        assert [c.args[3] for c in fetch.await_args_list] == [f"/api/v1/users/internal/{USER}", "/api/v1/org/internal"]

    async def test_an_org_already_in_the_graph_is_not_rewritten(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value={"_key": ORG})
        svc = _service(graph)
        org_created, user_added = _handlers(svc)
        with _node() as fetch:
            assert await svc.ensure_user(ORG, USER) is True
        org_created.assert_not_awaited()
        user_added.assert_awaited_once()
        assert fetch.await_count == 1

    async def test_an_org_without_a_registered_name_gets_the_handlers_default(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value=None)
        svc = _service(graph)
        org_created, _ = _handlers(svc)
        with _node(org={"_id": ORG, "accountType": "individual", "registeredName": None}):
            assert await svc.ensure_user(ORG, USER) is True
        org_created.assert_awaited_once_with({"orgId": ORG, "accountType": "individual"})

    @pytest.mark.parametrize(
        "record",
        [
            None,  # Node has no live record: deleted, or not in this org
            {**LIVE_USER, "isDeleted": True},
            {**LIVE_USER, "isDisabled": True},
            {**LIVE_USER, "orgId": "org-2"},  # a record of another org
            {**LIVE_USER, "email": ""},
        ],
        ids=["no-live-record", "deleted", "disabled", "other-org", "no-email"],
    )
    async def test_an_account_that_may_not_sign_in_here_writes_nothing(self, record: dict | None) -> None:
        graph = AsyncMock()
        svc = _service(graph)
        org_created, user_added = _handlers(svc)
        with _node(user=record):
            assert await svc.ensure_user(ORG, USER) is False
        org_created.assert_not_awaited()
        user_added.assert_not_awaited()
        graph.batch_upsert_nodes.assert_not_awaited()

    async def test_an_org_node_node_cannot_confirm_writes_nothing(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value=None)
        svc = _service(graph)
        org_created, user_added = _handlers(svc)
        for org in (None, {**LIVE_ORG, "_id": "org-2"}):
            with _node(org=org):
                assert await svc.ensure_user(ORG, USER) is False
        org_created.assert_not_awaited()
        user_added.assert_not_awaited()

    async def test_node_unreachable_writes_nothing_and_does_not_raise(self) -> None:
        svc = _service()
        _, user_added = _handlers(svc)
        with patch(
            "app.services.messaging.kafka.handlers.entity.fetch_own_identity",
            AsyncMock(side_effect=OSError("connection refused")),
        ):
            assert await svc.ensure_user(ORG, USER) is False
        user_added.assert_not_awaited()


class _Graph:
    """The user writes of userAdded, with a yield inside the email lookup so two writers interleave there."""

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}

    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = False) -> dict | None:
        found = next((u for u in self.users.values() if u.get("userId") == user_id), None)
        await asyncio.sleep(0.01)
        return found

    async def get_user_by_email(self, email: str, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        found = next((u for u in self.users.values() if u["email"].lower() == email.lower()), None)
        await asyncio.sleep(0.01)
        return MagicMock(id=found["id"]) if found else None

    async def get_document(self, key: str, collection: str, *, raise_on_error: bool = False) -> dict | None:
        return {"_key": key} if collection == CollectionNames.ORGS.value else None

    async def batch_upsert_nodes(self, nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        if collection == CollectionNames.USERS.value:
            for n in nodes:
                self.users[n["id"]] = {**self.users.get(n["id"], {}), **n}
        return True

    async def batch_create_edges(self, edges: list[dict], collection: str, transaction: str | None = None) -> bool:
        return True


async def test_any_number_of_writers_of_one_new_user_write_one_node() -> None:
    """The event, its redelivery and two first requests, through two service instances (two workers), at once."""
    graph = _Graph()
    workers = [_service(graph), _service(graph)]
    for svc in workers:
        svc._adopt_existing_person = AsyncMock()  # type: ignore[method-assign]
        svc._get_or_create_knowledge_base = AsyncMock()  # type: ignore[method-assign]
        svc._get_or_create_all_team_and_add_user = AsyncMock()  # type: ignore[method-assign]
    event = {"orgId": ORG, "userId": USER, "email": "admin@example.com", "fullName": "Admin", "syncAction": "none"}

    with _node(), patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()):
        results = await asyncio.gather(
            workers[0].ensure_user(ORG, USER), workers[1].ensure_user(ORG, USER),
            workers[0].process_event("userAdded", event), workers[1].process_event("userAdded", dict(event)),
        )

    assert results == [True, True, True, True]
    assert len(graph.users) == 1, graph.users
    (user,) = graph.users.values()
    assert (user["userId"], user["orgId"]) == (USER, ORG)


def test_the_key_of_a_new_user_and_of_their_default_knowledge_base_depend_only_on_org_and_user() -> None:
    assert _login_user_key(ORG, USER) == _login_user_key(ORG, USER) != _login_user_key("org-2", USER)
    assert _default_kb_key(ORG, USER) == _default_kb_key(ORG, USER) != _login_user_key(ORG, USER)


def _kb_service(existing: list[dict]) -> EntityEventService:
    svc = _service()
    svc.graph_provider.get_nodes_by_filters = AsyncMock(return_value=existing)
    svc.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
    svc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
    svc.graph_provider.batch_create_edges = AsyncMock(return_value=True)
    svc.graph_provider.commit_transaction = AsyncMock()
    svc.app_container.data_store = AsyncMock(return_value=AsyncMock())
    svc.app_container.connector_notification_service.return_value = MagicMock()
    return svc


class TestDefaultKnowledgeBase:
    async def test_two_writers_of_one_users_default_create_the_same_one_and_build_one_connector(self) -> None:
        svc = _kb_service([])

        async def start(**kwargs: Any) -> MagicMock:  # noqa: ANN401
            await asyncio.sleep(0.01)  # the second writer reaches the map check meanwhile
            return MagicMock(name=kwargs["connector_id"])

        with patch(
            "app.services.messaging.kafka.handlers.entity.ConnectorFactory.create_and_start_sync",
            AsyncMock(side_effect=start),
        ) as create:
            first, second = await asyncio.gather(
                svc._get_or_create_knowledge_base(_login_user_key(ORG, USER), USER, ORG, name="Admin's Private"),
                svc._get_or_create_knowledge_base(_login_user_key(ORG, USER), USER, ORG, name="Admin's Private"),
            )
        assert first["kb_id"] == second["kb_id"] == _default_kb_key(ORG, USER)
        written = {c.args[0][0]["id"] for c in svc.graph_provider.batch_upsert_nodes.await_args_list}
        assert written == {_default_kb_key(ORG, USER)}
        create.assert_awaited_once()
        assert list(svc.app_container.connectors_map) == [_default_kb_key(ORG, USER)]

    async def test_a_deleted_default_stays_deleted_and_a_new_one_gets_its_own_key(self) -> None:
        svc = _kb_service([{"id": _default_kb_key(ORG, USER), "isDeleted": True}])
        with patch(
            "app.services.messaging.kafka.handlers.entity.ConnectorFactory.create_and_start_sync",
            AsyncMock(return_value=None),
        ):
            result = await svc._get_or_create_knowledge_base(_login_user_key(ORG, USER), USER, ORG)
        assert result["kb_id"] != _default_kb_key(ORG, USER)
        (call,) = svc.graph_provider.batch_upsert_nodes.await_args_list
        assert call.args[0][0]["id"] == result["kb_id"]

    async def test_a_new_user_who_already_made_a_knowledge_base_still_gets_the_default(self) -> None:
        # Their first create can land between their user and their default being written.
        svc = _kb_service([{"id": "own-kb", "name": "First", "createdBy": USER}])
        with patch(
            "app.services.messaging.kafka.handlers.entity.ConnectorFactory.create_and_start_sync",
            AsyncMock(return_value=None),
        ):
            result = await svc._get_or_create_knowledge_base(_login_user_key(ORG, USER), USER, ORG, name="Admin's Private")
        assert result["kb_id"] == _default_kb_key(ORG, USER)
        (call,) = svc.graph_provider.batch_upsert_nodes.await_args_list
        assert call.args[0][0]["name"] == "Admin's Private"

    async def test_a_user_under_an_older_key_keeps_the_knowledge_base_they_have(self) -> None:
        svc = _kb_service([{"id": "their-default", "name": "Admin's Private", "createdBy": USER}])
        result = await svc._get_or_create_knowledge_base("older-random-key", USER, ORG)
        assert result["id"] == "their-default"
        svc.graph_provider.batch_upsert_nodes.assert_not_awaited()


@pytest.mark.parametrize("key", ["older-random-key", _login_user_key(ORG, USER)], ids=["older-random-key", "derived-key"])
async def test_a_user_readded_under_a_new_address_keeps_their_one_node(key: str) -> None:
    graph = _Graph()
    graph.users[key] = {"id": key, "userId": USER, "orgId": ORG, "email": "old@example.com", "isActive": False}
    svc = _service(graph)
    svc._adopt_existing_person = AsyncMock()  # type: ignore[method-assign]
    svc._get_or_create_knowledge_base = AsyncMock()  # type: ignore[method-assign]
    svc._get_or_create_all_team_and_add_user = AsyncMock()  # type: ignore[method-assign]
    event = {"orgId": ORG, "userId": USER, "email": "new@example.com", "fullName": "Admin", "syncAction": "none"}

    with patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()):
        assert await svc.process_event("userAdded", event) is True

    assert list(graph.users) == [key]
    assert (graph.users[key]["email"], graph.users[key]["isActive"]) == ("new@example.com", True)
    svc._get_or_create_knowledge_base.assert_awaited_once_with(key, USER, ORG, name="Admin's Private")
