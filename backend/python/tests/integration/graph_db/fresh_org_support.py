"""The first calls of a brand-new organisation's admin, before its sign-up events are consumed (shared by the
real-graph tests: test_fresh_org_first_call_real_backends.py and postgres/test_fresh_org_first_call.py)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.core.org_context import OrgContext, bind
from app.services.messaging.kafka.handlers.entity import EntityEventService
from app.utils import user_lookup

logger = logging.getLogger("fresh-org-first-call-it")


async def first_calls_after_signup(graph: Any, org_id: str) -> None:  # noqa: ANN401
    """Two knowledge-base creates by the new admin, straight after sign-up, then the sign-up events themselves."""
    run = uuid.uuid4().hex[:10]
    user_id, email = f"uid-fresh-{run}", f"admin-{run}@example.com"
    live = {
        "/api/v1/org/internal": {"_id": org_id, "accountType": "business", "registeredName": f"Fresh {run}"},
        f"/api/v1/users/internal/{user_id}": {"_id": user_id, "orgId": org_id, "email": email, "fullName": "Fresh Admin"},
    }

    async def node(_config: object, asked_org: str, asked_user: str, path: str) -> dict | None:
        assert (asked_org, asked_user) == (org_id, user_id)
        return live.get(path)

    container = MagicMock()
    container.connectors_map = {}
    container.data_store = AsyncMock()
    events = EntityEventService(logger, graph, container)
    kb = KnowledgeBaseService(logger, graph, MagicMock(), processor_for_kb=AsyncMock())

    with (
        patch("app.services.messaging.kafka.handlers.entity.fetch_own_identity", AsyncMock(side_effect=node)),
        patch("app.services.messaging.kafka.handlers.entity.notify_principals_changed", AsyncMock()),
        patch.dict(user_lookup._ensurer, {"caller_node": events.ensure_caller_user}),
        bind(OrgContext(org_id, user_id, "user", "jwt", "t")),
    ):
        assert await graph.get_user_by_user_id(user_id=user_id, raise_on_error=True) is None
        assert await graph.get_document(org_id, CollectionNames.ORGS.value) is None

        results = await asyncio.gather(*(
            kb.create_knowledge_base(user_id=user_id, org_id=org_id, name=f"First {i}") for i in range(2)
        ))
        assert all(r.get("success") for r in results), results

        # The queued events reach the consumer afterwards and find everything in place.
        assert await events.process_event(
            "orgCreated", {"orgId": org_id, "accountType": "business", "registeredName": f"Fresh {run}"},
        )
        assert await events.process_event(
            "userAdded", {"orgId": org_id, "userId": user_id, "email": email, "fullName": "Fresh Admin", "syncAction": "none"},
        )

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
        assert {k["name"] for k in listed["knowledgeBases"]} >= {"First 0", "First 1"}, listed
