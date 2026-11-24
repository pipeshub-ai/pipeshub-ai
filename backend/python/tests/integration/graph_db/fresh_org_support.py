"""The first calls of a brand-new organisation's admin, before its sign-up events are consumed (shared by the
real-graph tests: test_fresh_org_first_call_real_backends.py and postgres/test_fresh_org_first_call.py)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.core.org_context import OrgContext, bind
from app.services.messaging.kafka.handlers.entity import EntityEventService
from app.utils import user_lookup

logger = logging.getLogger("fresh-org-first-call-it")


async def first_calls_after_signup(graph: Any, org_id: str, *, events_after_creates: bool = False) -> None:  # noqa: ANN401
    """Two knowledge-base creates by the new admin, straight after sign-up, then the sign-up events themselves.

    With ``events_after_creates`` both creates finish before any event is delivered, so the creates alone must write
    the user and their default, and the events must then add no second of either."""
    run = uuid.uuid4().hex[:10]
    user_id, email = f"uid-fresh-{run}", f"admin-{run}@example.com"
    live = {
        "/api/v1/org/internal": {"_id": org_id, "accountType": "business", "registeredName": f"Fresh {run}"},
        f"/api/v1/users/internal/{user_id}": {"_id": user_id, "orgId": org_id, "email": email, "fullName": "Fresh Admin"},
    }

    async def node(_config: object, asked_org: str, asked_user: str, path: str) -> dict | None:
        assert (asked_org, asked_user) == (org_id, user_id)
        return live.get(path)

    def worker() -> EntityEventService:
        """One connectors-service process; two of them write at once, as two replicas would."""
        container = MagicMock()
        container.connectors_map = {}
        container.data_store = AsyncMock()
        return EntityEventService(logger, graph, container)

    first, second = worker(), worker()
    kb = KnowledgeBaseService(logger, graph, MagicMock(), processor_for_kb=AsyncMock())
    org_event = {"orgId": org_id, "accountType": "business", "registeredName": f"Fresh {run}"}
    user_event = {"orgId": org_id, "userId": user_id, "email": email, "fullName": "Fresh Admin", "syncAction": "none"}

    async def events_arrive(events: EntityEventService) -> None:
        assert await events.process_event("orgCreated", dict(org_event))
        assert await events.process_event("userAdded", dict(user_event))

    with (
        patch("app.services.messaging.kafka.handlers.entity.fetch_own_identity", AsyncMock(side_effect=node)),
        patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()),
        patch.dict(user_lookup._ensurer, {"caller_node": first.ensure_user}),
        bind(OrgContext(org_id, user_id, "user", "jwt", "t")),
    ):
        assert await graph.get_user_by_user_id(user_id=user_id, raise_on_error=True) is None
        assert await graph.get_document(org_id, CollectionNames.ORGS.value) is None

        creates = [kb.create_knowledge_base(user_id=user_id, org_id=org_id, name=f"First {i}") for i in range(2)]
        if events_after_creates:
            results = await asyncio.gather(*creates)
            await events_arrive(second)
            await events_arrive(first)
        else:
            # The admin's first two creates, the sign-up events on the other worker and a redelivery on this one, at once.
            *results, _, _ = await asyncio.gather(*creates, events_arrive(second), events_arrive(first))
        assert all(r.get("success") for r in results), results

        users = await graph.get_nodes_by_filters(CollectionNames.USERS.value, {"userId": user_id}, raise_on_error=True)
        assert len(users) == 1, users
        assert (users[0]["orgId"], users[0]["email"]) == (org_id, email)
        apps = await graph.get_nodes_by_filters(
            CollectionNames.APPS.value,
            {"createdBy": user_id, "orgId": org_id, "type": Connectors.KNOWLEDGE_BASE.value},
            raise_on_error=True,
        )
        assert sorted(a["name"] for a in apps) == ["First 0", "First 1", "Fresh Admin's Private"], apps
        listed = await kb.list_user_knowledge_bases(user_id=user_id, org_id=org_id, page=1, limit=100)
        assert {k["name"] for k in listed["knowledgeBases"]} == {"First 0", "First 1", "Fresh Admin's Private"}, listed


async def a_new_users_writers_race(graph: Any, org_id: str) -> None:  # noqa: ANN401
    """Four writers of one new user (two workers' events and redeliveries) at once, after the org exists: one user,
    one default knowledge base, reachable by that user."""
    run = uuid.uuid4().hex[:10]
    user_id, email = f"uid-race-{run}", f"race-{run}@example.com"
    workers = []
    for _ in range(2):
        container = MagicMock()
        container.connectors_map = {}
        container.data_store = AsyncMock()
        workers.append(EntityEventService(logger, graph, container))
    event = {"orgId": org_id, "userId": user_id, "email": email, "fullName": "Race", "syncAction": "none"}
    with (
        patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()),
        bind(OrgContext(org_id, None, "job", "job:test", "t")),
    ):
        assert await workers[0].process_event("orgCreated", {"orgId": org_id, "accountType": "business", "registeredName": "Race"})
        assert await asyncio.gather(*(w.process_event("userAdded", dict(event)) for w in workers + workers)) == [True] * 4
        users = await graph.get_nodes_by_filters(CollectionNames.USERS.value, {"userId": user_id}, raise_on_error=True)
        assert len(users) == 1, users
        apps = await graph.get_nodes_by_filters(
            CollectionNames.APPS.value,
            {"createdBy": user_id, "orgId": org_id, "type": Connectors.KNOWLEDGE_BASE.value},
            raise_on_error=True,
        )
        assert [a["name"] for a in apps] == ["Race's Private"], apps
        user_key = users[0].get("id") or users[0].get("_key")
        assert await graph.get_user_kb_permission(apps[0].get("id") or apps[0].get("_key"), user_key) == "OWNER"


async def a_readded_user_keeps_one_node(graph: Any, org_id: str, *, older_random_key: bool) -> None:  # noqa: ANN401
    """A user whose address changed while they were away is re-added: the same node, under whatever key it has, with
    the new address and the knowledge base it already owned; no second node."""
    run = uuid.uuid4().hex[:10]
    user_id = f"uid-readd-{run}"
    old_email, new_email = f"old-{run}@example.com", f"new-{run}@example.com"
    container = MagicMock()
    container.connectors_map = {}
    container.data_store = AsyncMock()
    events = EntityEventService(logger, graph, container)
    event = {"orgId": org_id, "userId": user_id, "email": old_email, "fullName": "Readd", "syncAction": "none"}
    with (
        patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()),
        bind(OrgContext(org_id, None, "job", "job:test", "t")),
    ):
        assert await events.process_event("orgCreated", {"orgId": org_id, "accountType": "business", "registeredName": "Readd"})
        if older_random_key:  # a user written before keys were derived from (org, userId)
            key = f"older-{run}"
            now = 1
            assert await graph.batch_upsert_nodes([{
                "id": key, "userId": user_id, "orgId": org_id, "email": old_email, "fullName": "Readd", "firstName": "",
                "middleName": "", "lastName": "", "designation": "", "businessPhones": [], "isActive": True, "kind": "human",
                "createdAtTimestamp": now, "updatedAtTimestamp": now,
            }], CollectionNames.USERS.value)
            await events._get_or_create_knowledge_base(key, user_id, org_id, name="Readd's Private")
        else:
            assert await events.process_event("userAdded", dict(event))
        (before,) = await graph.get_nodes_by_filters(CollectionNames.USERS.value, {"userId": user_id}, raise_on_error=True)
        key = before.get("id") or before.get("_key")
        (kb_before,) = await graph.get_nodes_by_filters(
            CollectionNames.APPS.value,
            {"createdBy": user_id, "orgId": org_id, "type": Connectors.KNOWLEDGE_BASE.value},
            raise_on_error=True,
        )

        assert await events.process_event("userAdded", {**event, "email": new_email})

        users = await graph.get_nodes_by_filters(CollectionNames.USERS.value, {"userId": user_id}, raise_on_error=True)
        assert [(u.get("id") or u.get("_key"), u["email"]) for u in users] == [(key, new_email)], users
        apps = await graph.get_nodes_by_filters(
            CollectionNames.APPS.value,
            {"createdBy": user_id, "orgId": org_id, "type": Connectors.KNOWLEDGE_BASE.value},
            raise_on_error=True,
        )
        kb_key = kb_before.get("id") or kb_before.get("_key")
        assert [(a.get("id") or a.get("_key"), a["name"]) for a in apps] == [(kb_key, "Readd's Private")], apps
        assert await graph.get_user_kb_permission(kb_key, key) == "OWNER"


def _worker(graph: Any) -> EntityEventService:  # noqa: ANN401
    container = MagicMock()
    container.connectors_map = {}
    container.data_store = AsyncMock()
    return EntityEventService(logger, graph, container)


async def a_first_request_after_add_and_delete_writes_nothing(graph: Any, org_id: str) -> None:  # noqa: ANN401
    """Node answered that the caller is live; before the first request wrote anything, their userAdded and userDeleted
    were consumed. The first request must not bring the deleted user back."""
    run = uuid.uuid4().hex[:10]
    user_id, email = f"uid-gone-{run}", f"gone-{run}@example.com"
    live = {
        "/api/v1/org/internal": {"_id": org_id, "accountType": "business", "registeredName": "Gone"},
        f"/api/v1/users/internal/{user_id}": {"_id": user_id, "orgId": org_id, "email": email, "fullName": "Gone"},
    }
    events = _worker(graph)

    async def node_then_events(_config: object, asked_org: str, asked_user: str, path: str) -> dict | None:
        answer = live.get(path)
        if path.startswith("/api/v1/users/internal/"):  # what is consumed while the request is in flight
            assert await events.process_event(
                "userAdded", {"orgId": org_id, "userId": user_id, "email": email, "fullName": "Gone", "syncAction": "none"},
            )
            (user,) = await graph.get_nodes_by_filters(CollectionNames.USERS.value, {"userId": user_id}, raise_on_error=True)
            await graph.batch_upsert_nodes(
                [{"id": user.get("id") or user.get("_key"), "isActive": False, "updatedAtTimestamp": 2}],
                CollectionNames.USERS.value,
            )
        return answer

    with (
        patch("app.services.messaging.kafka.handlers.entity.fetch_own_identity", AsyncMock(side_effect=node_then_events)),
        patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()),
        bind(OrgContext(org_id, None, "job", "job:test", "t")),
    ):
        assert await events.process_event("orgCreated", {"orgId": org_id, "accountType": "business", "registeredName": "Gone"})
        assert await events.ensure_user(org_id, user_id) is False
        users = await graph.get_nodes_by_filters(CollectionNames.USERS.value, {"userId": user_id}, raise_on_error=True)
        assert [u.get("isActive") for u in users] == [False], users


async def a_removed_default_is_not_made_again_under_its_key(graph: Any, org_id: str) -> None:  # noqa: ANN401
    """A default knowledge base deleted with its row gone; cleanup queued for it is keyed by its id. A later userAdded
    makes a new default under a key of its own, which that cleanup cannot reach."""
    run = uuid.uuid4().hex[:10]
    user_id = f"uid-kb-{run}"
    events = _worker(graph)
    event = {"orgId": org_id, "userId": user_id, "email": f"kb-{run}@example.com", "fullName": "Kb", "syncAction": "none"}

    async def defaults() -> list[dict]:
        return await graph.get_nodes_by_filters(
            CollectionNames.APPS.value,
            {"createdBy": user_id, "orgId": org_id, "type": Connectors.KNOWLEDGE_BASE.value},
            raise_on_error=True,
        )

    with (
        patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()),
        bind(OrgContext(org_id, None, "job", "job:test", "t")),
    ):
        assert await events.process_event("orgCreated", {"orgId": org_id, "accountType": "business", "registeredName": "Kb"})
        assert await events.process_event("userAdded", dict(event))
        (first,) = await defaults()
        first_key = first.get("id") or first.get("_key")
        (user,) = await graph.get_nodes_by_filters(CollectionNames.USERS.value, {"userId": user_id}, raise_on_error=True)
        user_key = user.get("id") or user.get("_key")
        # A collection of their own, which must not be taken for their default.
        sibling_key = f"sibling-{run}"
        assert await graph.batch_upsert_nodes([{
            "id": sibling_key, "createdBy": user_id, "orgId": org_id, "name": "Sibling",
            "type": Connectors.KNOWLEDGE_BASE.value, "appGroup": first["appGroup"], "authType": "NONE",
            "scope": first["scope"], "isActive": True, "isAgentActive": True, "isConfigured": True, "isAuthenticated": True,
            "vectorMembershipBackfilled": True, "hideConnector": True, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        }], CollectionNames.APPS.value)
        await graph.delete_nodes_and_edges([first_key], CollectionNames.APPS.value)

        assert await events.process_event("userAdded", dict(event))
        keys = {kb.get("id") or kb.get("_key") for kb in await defaults()}
        (second_key,) = keys - {sibling_key}
        assert second_key not in (first_key, sibling_key)
        (user,) = await graph.get_nodes_by_filters(CollectionNames.USERS.value, {"userId": user_id}, raise_on_error=True)
        assert user.get("defaultKnowledgeBaseId") == second_key
        assert await graph.get_user_kb_permission(second_key, user_key) == "OWNER"


async def a_failed_default_write_is_made_by_the_redelivery(graph: Any, org_id: str) -> None:  # noqa: ANN401
    """The default's write fails once (the event is then delivered again): nothing of it stays, the user's name for
    their default included, and the redelivery makes exactly one, which the user names and owns."""
    run = uuid.uuid4().hex[:10]
    user_id = f"uid-retry-{run}"
    events = _worker(graph)
    event = {"orgId": org_id, "userId": user_id, "email": f"retry-{run}@example.com", "fullName": "Retry", "syncAction": "none"}
    upsert = graph.batch_upsert_nodes
    failures = {"left": 1}

    async def fails_once(nodes: list[dict], collection: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if collection == CollectionNames.APPS.value and failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("connection reset by peer")
        return await upsert(nodes, collection, *args, **kwargs)

    async def defaults() -> list[dict]:
        return await graph.get_nodes_by_filters(
            CollectionNames.APPS.value, {"createdBy": user_id, "orgId": org_id, "type": Connectors.KNOWLEDGE_BASE.value},
            raise_on_error=True,
        )

    with (
        patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()),
        bind(OrgContext(org_id, None, "job", "job:test", "t")),
    ):
        assert await events.process_event("orgCreated", {"orgId": org_id, "accountType": "business", "registeredName": "Retry"})
        with patch.object(graph, "batch_upsert_nodes", fails_once), pytest.raises(RuntimeError, match="connection reset"):
            await events.process_event("userAdded", dict(event))
        (user,) = await graph.get_nodes_by_filters(CollectionNames.USERS.value, {"userId": user_id}, raise_on_error=True)
        assert (await defaults(), user.get("defaultKnowledgeBaseId")) == ([], None)

        assert await events.process_event("userAdded", dict(event))  # the redelivery
        (default,) = await defaults()
        key = default.get("id") or default.get("_key")
        (user,) = await graph.get_nodes_by_filters(CollectionNames.USERS.value, {"userId": user_id}, raise_on_error=True)
        assert user.get("defaultKnowledgeBaseId") == key
        assert await graph.get_user_kb_permission(key, user.get("id") or user.get("_key")) == "OWNER"
