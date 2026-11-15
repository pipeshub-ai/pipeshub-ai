"""A brand-new account's first request writes its own graph user when its userAdded event is still in flight.

EntityEventService.ensure_caller_user runs the events' own handlers, for the caller the verified token names only,
in that token's org, from Node's live records. A deleted or disabled account, a record of another org, someone other
than the caller, or a service token writes nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import OrgContext, bind
from app.services.messaging.kafka.handlers.entity import EntityEventService

ORG, USER = "org-1", "user-1"
LIVE_USER = {"_id": USER, "orgId": ORG, "email": "Admin@Example.com", "fullName": "Admin", "isDeleted": False}
LIVE_ORG = {"_id": ORG, "accountType": "business", "registeredName": "Acme"}


def _service(graph: Any = None) -> EntityEventService:  # noqa: ANN401
    container = MagicMock()
    container.connectors_map = {}
    return EntityEventService(logger=MagicMock(), graph_provider=graph or AsyncMock(), app_container=container)


def _caller(user: str = USER, org: str = ORG, kind: str = "user") -> Any:  # noqa: ANN401
    return bind(OrgContext(org, user, kind, "jwt", "t"))  # type: ignore[arg-type]


def _node(user: dict | None = LIVE_USER, org: dict | None = LIVE_ORG) -> Any:  # noqa: ANN401
    async def fetch(_config: object, org_id: str, user_id: str, path: str) -> dict | None:
        assert (org_id, user_id) == (ORG, USER), "Node is asked only about the caller, in the caller's org"
        return org if path == "/api/v1/org/internal" else user

    return patch("app.services.messaging.kafka.handlers.entity.fetch_own_identity", AsyncMock(side_effect=fetch))


def _handlers(svc: EntityEventService) -> tuple[AsyncMock, AsyncMock]:
    svc._handle_org_created = AsyncMock(return_value=True)  # type: ignore[method-assign]
    svc._add_user = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return svc._handle_org_created, svc._add_user


class TestEnsureCallerUser:
    async def test_a_fresh_caller_gets_the_org_and_user_the_events_would_write(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value=None)
        svc = _service(graph)
        org_created, user_added = _handlers(svc)
        with _node() as fetch, _caller():
            assert await svc.ensure_caller_user(USER) is True
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
        with _node() as fetch, _caller():
            assert await svc.ensure_caller_user(USER) is True
        org_created.assert_not_awaited()
        user_added.assert_awaited_once()
        assert fetch.await_count == 1

    async def test_an_org_without_a_registered_name_gets_the_handlers_default(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value=None)
        svc = _service(graph)
        org_created, _ = _handlers(svc)
        with _node(org={"_id": ORG, "accountType": "individual", "registeredName": None}), _caller():
            assert await svc.ensure_caller_user(USER) is True
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
        with _node(user=record), _caller():
            assert await svc.ensure_caller_user(USER) is False
        org_created.assert_not_awaited()
        user_added.assert_not_awaited()
        graph.batch_upsert_nodes.assert_not_awaited()

    async def test_an_org_node_node_cannot_confirm_writes_nothing(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value=None)
        svc = _service(graph)
        org_created, user_added = _handlers(svc)
        for org in (None, {**LIVE_ORG, "_id": "org-2"}):
            with _node(org=org), _caller():
                assert await svc.ensure_caller_user(USER) is False
        org_created.assert_not_awaited()
        user_added.assert_not_awaited()

    @pytest.mark.parametrize(
        "context",
        [
            lambda: _caller(user="someone-else"),  # the token names another user
            lambda: _caller(kind="service"),  # a service token is no one's own request
        ],
        ids=["another-user", "service-token"],
    )
    async def test_only_the_tokens_own_user_is_written(self, context: Any) -> None:  # noqa: ANN401
        svc = _service()
        org_created, user_added = _handlers(svc)
        with _node() as fetch, context():
            assert await svc.ensure_caller_user(USER) is False
        fetch.assert_not_awaited()
        org_created.assert_not_awaited()
        user_added.assert_not_awaited()

    async def test_no_bound_request_writes_nothing(self) -> None:
        svc = _service()
        _, user_added = _handlers(svc)
        with _node() as fetch:
            assert await svc.ensure_caller_user(USER) is False
        fetch.assert_not_awaited()
        user_added.assert_not_awaited()

    async def test_node_unreachable_writes_nothing_and_does_not_raise(self) -> None:
        svc = _service()
        _, user_added = _handlers(svc)
        with patch(
            "app.services.messaging.kafka.handlers.entity.fetch_own_identity",
            AsyncMock(side_effect=OSError("connection refused")),
        ), _caller():
            assert await svc.ensure_caller_user(USER) is False
        user_added.assert_not_awaited()


class _Graph:
    """The user writes of userAdded, with a yield inside the email lookup so two writers interleave there."""

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}

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


async def test_concurrent_first_calls_and_the_event_write_one_user() -> None:
    graph = _Graph()
    svc = _service(graph)
    svc._adopt_existing_person = AsyncMock()  # type: ignore[method-assign]
    svc._get_or_create_knowledge_base = AsyncMock()  # type: ignore[method-assign]
    svc._get_or_create_all_team_and_add_user = AsyncMock()  # type: ignore[method-assign]
    event = {"orgId": ORG, "userId": USER, "email": "admin@example.com", "fullName": "Admin", "syncAction": "none"}

    async def first_call() -> bool:
        with _caller():
            return await svc.ensure_caller_user(USER)

    with _node(), patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()):
        results = await asyncio.gather(first_call(), first_call(), svc.process_event("userAdded", event))

    assert results == [True, True, True]
    assert len(graph.users) == 1, graph.users
    (user,) = graph.users.values()
    assert (user["userId"], user["orgId"]) == (USER, ORG)
