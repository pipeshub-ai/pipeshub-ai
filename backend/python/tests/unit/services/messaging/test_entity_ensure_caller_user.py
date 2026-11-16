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
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING
from app.services.messaging.kafka.handlers.entity import (
    EntityEventService,
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
        org_created.assert_awaited_once_with(
            {"orgId": ORG, "accountType": "business", "registeredName": "Acme"}, bootstrap=True
        )
        user_added.assert_awaited_once_with(
            {"orgId": ORG, "userId": USER, "email": "Admin@Example.com", "fullName": "Admin"}, bootstrap=True
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
        org_created.assert_awaited_once_with({"orgId": ORG, "accountType": "individual"}, bootstrap=True)

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

    async def create_node_if_absent(self, key: str, collection: str, node: dict, transaction: str | None = None) -> bool:
        if collection != CollectionNames.USERS.value or key in self.users:
            return False
        self.users[key] = {**node, "id": key}
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

    assert results[2:] == [True, True]  # the events; a first request that finds the user there writes nothing
    assert len(graph.users) == 1, graph.users
    (user,) = graph.users.values()
    assert (user["userId"], user["orgId"]) == (USER, ORG)


def test_the_key_of_a_new_user_depends_only_on_org_and_user() -> None:
    assert _login_user_key(ORG, USER) == _login_user_key(ORG, USER) != _login_user_key("org-2", USER)


def _kb_service(existing: list[dict], marker: str | None = None) -> tuple[EntityEventService, dict]:
    """A user's knowledge bases and the default their node names, with transactions as the stores give them: what a
    transaction writes shows only once it commits, and a conditional write on the user holds the user until then."""
    state: dict[str, Any] = {"marker": marker, "apps": list(existing), "fail_app_writes": 0}
    held = asyncio.Lock()  # the user node's row lock
    staged: dict[str, dict] = {}
    svc = _service()
    graph = svc.graph_provider

    async def apps(**_kwargs: Any) -> list[dict]:  # noqa: ANN401
        return list(state["apps"])

    async def document(key: str, collection: str, **_kwargs: Any) -> dict:  # noqa: ANN401
        return {"id": key, **({"defaultKnowledgeBaseId": state["marker"]} if state["marker"] else {})}

    async def begin(*_a: Any, **_k: Any) -> str:  # noqa: ANN401
        txn = f"txn-{len(staged)}-{id(object())}"
        staged[txn] = {"apps": [], "marker": None, "held": False}
        return txn

    async def name(key: str, collection: str, updates: dict, expected: dict, transaction: str | None = None, **_k: Any) -> bool:  # noqa: ANN401
        if transaction is None:  # a statement of its own, committed at once
            async with held:
                if state["marker"] != expected["defaultKnowledgeBaseId"]:
                    return False
                state["marker"] = updates["defaultKnowledgeBaseId"]
                return True
        await held.acquire()  # a second writer waits here until the first commits or rolls back
        staged[transaction]["held"] = True
        await asyncio.sleep(0.01)
        if state["marker"] != expected["defaultKnowledgeBaseId"]:
            return False
        staged[transaction]["marker"] = updates["defaultKnowledgeBaseId"]
        return True

    async def upsert(nodes: list[dict], collection: str, transaction: str | None = None, **_k: Any) -> bool:  # noqa: ANN401
        if collection == CollectionNames.APPS.value:
            if state["fail_app_writes"]:
                state["fail_app_writes"] -= 1
                raise RuntimeError("connection reset by peer")
            staged[transaction]["apps"].append(dict(nodes[0]))
        return True

    def end(transaction: str, *, apply: bool) -> None:
        tx = staged.pop(transaction)
        if apply:
            if tx["marker"] is not None:
                state["marker"] = tx["marker"]
            for app in tx["apps"]:
                state["apps"] = [a for a in state["apps"] if a["id"] != app["id"]] + [app]
        if tx["held"]:
            held.release()

    async def commit(transaction: str, *_a: Any, **_k: Any) -> None:  # noqa: ANN401
        end(transaction, apply=True)

    async def rollback(transaction: str, *_a: Any, **_k: Any) -> None:  # noqa: ANN401
        end(transaction, apply=False)

    graph.get_nodes_by_filters = AsyncMock(side_effect=apps)
    graph.get_document = AsyncMock(side_effect=document)
    graph.begin_transaction = AsyncMock(side_effect=begin)
    graph.update_node_fields_if_match = AsyncMock(side_effect=name)
    graph.batch_upsert_nodes = AsyncMock(side_effect=upsert)
    graph.batch_create_edges = AsyncMock(return_value=True)
    graph.commit_transaction = AsyncMock(side_effect=commit)
    graph.rollback_transaction = AsyncMock(side_effect=rollback)
    graph.is_write_conflict = MagicMock(return_value=False)
    graph.opens_explicit_transaction = MagicMock(return_value=True)
    svc.app_container.data_store = AsyncMock(return_value=AsyncMock())
    svc.app_container.connector_notification_service.return_value = MagicMock()
    return svc, state


def _no_connector() -> Any:  # noqa: ANN401
    return patch(
        "app.services.messaging.kafka.handlers.entity.ConnectorFactory.create_and_start_sync", AsyncMock(return_value=None)
    )


NEW_USER = _login_user_key(ORG, USER)


def _written(svc: EntityEventService) -> list[str]:
    return [c.args[0][0]["id"] for c in svc.graph_provider.batch_upsert_nodes.await_args_list]


class TestDefaultKnowledgeBase:
    async def test_two_writers_of_one_users_default_make_one_and_build_one_connector(self) -> None:
        svc, state = _kb_service([])

        async def start(**kwargs: Any) -> MagicMock:  # noqa: ANN401
            await asyncio.sleep(0.01)  # the second writer reaches the map check meanwhile
            return MagicMock(name=kwargs["connector_id"])

        with patch(
            "app.services.messaging.kafka.handlers.entity.ConnectorFactory.create_and_start_sync",
            AsyncMock(side_effect=start),
        ) as create:
            first, second = await asyncio.gather(
                svc._get_or_create_knowledge_base(NEW_USER, USER, ORG, name="Admin's Private"),
                svc._get_or_create_knowledge_base(NEW_USER, USER, ORG, name="Admin's Private"),
            )
        (app,) = state["apps"]
        assert {first.get("kb_id") or first.get("id"), second.get("kb_id") or second.get("id")} == {app["id"]}
        assert state["marker"] == app["id"]
        create.assert_awaited_once()
        assert list(svc.app_container.connectors_map) == [app["id"]]

    async def test_a_default_another_writer_commits_between_the_reads_is_found_not_replaced(self) -> None:
        """The other writer's default (its name and its knowledge base, one commit) lands just after this writer's
        first read. Read in the wrong order, it looked named but missing, and a second one was made."""
        svc, state = _kb_service([])
        graph = svc.graph_provider
        reads = {"done": 0}
        read_user, read_apps = graph.get_document.side_effect, graph.get_nodes_by_filters.side_effect

        def other_writer_commits_after_the_first_read() -> None:
            reads["done"] += 1
            if reads["done"] == 1:
                state["marker"] = "theirs"
                state["apps"] = [{"id": "theirs", "createdBy": USER, "name": "Admin's Private"}]

        async def user(*a: Any, **k: Any) -> dict:  # noqa: ANN401
            answer = await read_user(*a, **k)
            other_writer_commits_after_the_first_read()
            return answer

        async def apps(*a: Any, **k: Any) -> list[dict]:  # noqa: ANN401
            answer = await read_apps(*a, **k)
            other_writer_commits_after_the_first_read()
            return answer

        graph.get_document.side_effect = user
        graph.get_nodes_by_filters.side_effect = apps
        with _no_connector():
            result = await svc._get_or_create_knowledge_base(NEW_USER, USER, ORG, name="Admin's Private")
        assert (result.get("kb_id") or result.get("id")) == "theirs"
        assert [a["id"] for a in state["apps"]] == ["theirs"]
        assert state["marker"] == "theirs"

    async def test_a_write_that_fails_leaves_no_name_and_the_redelivery_makes_exactly_one(self) -> None:
        # Nothing of the failed write stays, the name included, so the retried event writes the default.
        svc, state = _kb_service([])
        state["fail_app_writes"] = 1
        with _no_connector():
            with pytest.raises(RuntimeError, match="connection reset"):
                await svc._get_or_create_knowledge_base(NEW_USER, USER, ORG, name="Admin's Private")
            assert (state["marker"], state["apps"]) == (None, [])
            result = await svc._get_or_create_knowledge_base(NEW_USER, USER, ORG, name="Admin's Private")
        (app,) = state["apps"]
        assert result["kb_id"] == app["id"] == state["marker"]

    @pytest.mark.parametrize("left", [[{"id": "gone", "status": APP_STATUS_DELETING}], []], ids=["deleting", "removed"])
    async def test_a_named_default_that_is_gone_is_replaced_under_a_fresh_key_never_by_a_sibling(self, left: list[dict]) -> None:
        # Cleanup queued for the deleted one is keyed by its id; a collection the user made is not their default.
        sibling = {"id": "own-kb", "name": "First", "createdBy": USER}
        svc, state = _kb_service([*left, sibling], marker="gone")
        with _no_connector():
            result = await svc._get_or_create_knowledge_base(NEW_USER, USER, ORG, name="Admin's Private")
        assert result["kb_id"] not in ("gone", "own-kb")
        assert _written(svc) == [result["kb_id"]]
        assert state["marker"] == result["kb_id"]

    async def test_a_default_removed_and_made_again_gets_a_new_key(self) -> None:
        svc, state = _kb_service([])
        with _no_connector():
            first = await svc._get_or_create_knowledge_base(NEW_USER, USER, ORG)
            state["apps"] = [{"id": "own-kb", "createdBy": USER}]  # deleted with its row; cleanup may still be queued
            second = await svc._get_or_create_knowledge_base(NEW_USER, USER, ORG)
        assert second["kb_id"] not in (first["kb_id"], "own-kb")

    async def test_a_new_user_who_already_made_a_knowledge_base_still_gets_the_default(self) -> None:
        # Their first create can land between their user and their default being written.
        svc, state = _kb_service([{"id": "own-kb", "name": "First", "createdBy": USER}])
        with _no_connector():
            result = await svc._get_or_create_knowledge_base(NEW_USER, USER, ORG, name="Admin's Private")
        assert result["kb_id"] != "own-kb"
        assert state["marker"] == result["kb_id"]
        (call,) = svc.graph_provider.batch_upsert_nodes.await_args_list
        assert call.args[0][0]["name"] == "Admin's Private"

    async def test_the_default_the_user_names_is_found(self) -> None:
        svc, _ = _kb_service([{"id": "own-kb", "createdBy": USER}, {"id": "the-default", "createdBy": USER}], marker="the-default")
        result = await svc._get_or_create_knowledge_base(NEW_USER, USER, ORG)
        assert result["id"] == "the-default"
        svc.graph_provider.batch_upsert_nodes.assert_not_awaited()

    async def test_a_user_under_an_older_key_keeps_the_knowledge_base_they_have(self) -> None:
        svc, _ = _kb_service([{"id": "their-default", "name": "Admin's Private", "createdBy": USER}])
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



@pytest.mark.parametrize(
    "between",
    ["added and deleted", "added"],
    ids=["added-and-deleted-meanwhile", "added-meanwhile"],
)
async def test_a_first_request_never_rewrites_a_user_the_events_wrote_meanwhile(between: str) -> None:
    """Node answered for the caller; before the write, their userAdded (and maybe userDeleted) was consumed. A
    deleted user stays deleted, and nothing the event wrote is overwritten."""
    graph = _Graph()
    svc = _service(graph)
    svc._get_or_create_knowledge_base = AsyncMock()  # type: ignore[method-assign]
    svc._get_or_create_all_team_and_add_user = AsyncMock()  # type: ignore[method-assign]
    real_fetch = AsyncMock(side_effect=lambda _c, _o, _u, path: LIVE_ORG if path == "/api/v1/org/internal" else LIVE_USER)

    async def fetch_then_events(*args: Any) -> dict | None:  # noqa: ANN401
        answer = await real_fetch(*args)
        key = _login_user_key(ORG, USER)
        graph.users[key] = {"id": key, "userId": USER, "orgId": ORG, "email": "admin@example.com",
                            "isActive": between == "added", "fullName": "From the event"}
        return answer

    with patch("app.services.messaging.kafka.handlers.entity.fetch_own_identity", AsyncMock(side_effect=fetch_then_events)), \
            patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()):
        assert await svc.ensure_user(ORG, USER) is False
    (user,) = graph.users.values()
    assert (user["isActive"], user["fullName"]) == (between == "added", "From the event")


async def test_a_first_request_lost_to_the_event_by_a_hair_overwrites_nothing() -> None:
    graph = _Graph()
    svc = _service(graph)
    key = _login_user_key(ORG, USER)

    async def created_meanwhile(k: str, collection: str, node: dict, transaction: str | None = None) -> bool:
        graph.users[k] = {"id": k, "userId": USER, "orgId": ORG, "email": "admin@example.com", "isActive": False}
        return False

    graph.create_node_if_absent = created_meanwhile  # type: ignore[method-assign]
    with _node(), patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()):
        assert await svc.ensure_user(ORG, USER) is False
    assert graph.users[key]["isActive"] is False


async def test_a_first_request_writes_no_user_into_an_org_deleted_meanwhile() -> None:
    graph = _Graph()
    graph.get_document = AsyncMock(return_value={"_key": ORG, "isActive": False})  # type: ignore[method-assign]
    svc = _service(graph)
    with _node(), patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()):
        assert await svc.ensure_user(ORG, USER) is False
    assert graph.users == {}
